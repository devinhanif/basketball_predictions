# ruff: noqa: E501
"""Coach habits and phase features for COACH_PHASE (docs/prereg/COACH_PHASE.md, FROZEN sha256 8111c026; this module implements the rule).

Everything is strictly as-of: a (game, team) row sees only games dated STRICTLY EARLIER than its own date, and the coach
looked up is the coach of the team's PREVIOUS game. No tonight's box, minutes, starters, score or coach enters a row.

Habits (post-game boxes of earlier games), kept separately for REG (``game_id`` prefix 002) and PO (004); play-in (005) is in neither:
``depth`` (players with >= 10 min), ``top5`` / ``top8`` (share of team minutes), ``starter_min`` (mean minutes of starters),
``bench_share`` (share of minutes by non-starters), ``blowout`` (top-5 minutes in |margin| >= 20 games minus <= 10 games).
Each is an exponentially weighted mean over the coach's career games of that phase on ANY team, half-life 20 of his games of
that phase, shrunk to the league as-of mean with K = 10 pseudo-games: ``(S + K L) / (W + K)``. The league mean of PO falls back
to the REG value while fewer than 30 earlier league PO team-games exist.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import polars as pl

from research.features.phase import SEASON_GAMES, clinch_state, series_state

HABITS: tuple[str, ...] = ("depth", "top5", "top8", "starter_min", "bench_share", "blowout")
HALF_LIFE = 20.0
K_PSEUDO = 10.0
PO_LEAGUE_MIN = 30
LATE_FROM = 67
DEPTH_MIN = 10.0
BLOWOUT_MARGIN = 20.0
CLOSE_MARGIN = 10.0
#: league priors used ONLY before any earlier league event exists (the first days of 2022-23, warm-up, never scored)
DEFAULT_LEAGUE: dict[str, float] = {
    "depth": 10.0, "top5": 0.6, "top8": 0.85, "starter_min": 30.0, "bench_share": 0.3,
    "blowout_hi": 150.0, "blowout_lo": 150.0,
}  # fmt: skip
DEC = 0.5 ** (1.0 / HALF_LIFE)

HABIT_COLS: tuple[str, ...] = tuple(
    [f"h_reg_{h}" for h in HABITS] + [f"h_po_{h}" for h in HABITS] + [f"d_{h}" for h in HABITS]
)
INTER_COLS: tuple[str, ...] = tuple(
    [f"gpx_d_{h}" for h in HABITS] + [f"latex_d_{h}" for h in HABITS]
)
PHASE_COLS: tuple[str, ...] = (
    "gp_frac", "late15", "clinched_6", "clinched_10", "elim_6", "elim_10", "po", "series_game",
    "wins_for", "wins_against", "elim_game", "close_out_chance", "is_home", "home_court", "round",
)  # fmt: skip


def phase_of(game_id: str) -> str | None:
    p = game_id[:3]
    return "REG" if p == "002" else "PO" if p == "004" else None


# --------------------------------------------------------------------------- post-game inputs
def team_game_inputs(box: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    """Per (game_id, team_id) post-game quantities used ONLY as history for later games.

    ``box``: game_id, team_id, player_id, minutes, starter. ``games``: game_id, game_date, home_team, away_team, home_pts, away_pts."""
    b = box.filter(pl.col("minutes") > 0)
    tot = b.group_by("game_id", "team_id").agg(pl.col("minutes").sum().alias("_tot"))
    j = b.join(tot, on=["game_id", "team_id"])
    agg = j.group_by("game_id", "team_id").agg(
        (pl.col("minutes") >= DEPTH_MIN).sum().cast(pl.Float64).alias("depth"),
        (pl.col("minutes").sort(descending=True).head(5).sum() / pl.col("_tot").first()).alias(
            "top5"
        ),
        (pl.col("minutes").sort(descending=True).head(8).sum() / pl.col("_tot").first()).alias(
            "top8"
        ),
        pl.col("minutes").sort(descending=True).head(5).sum().alias("top5_min"),
        pl.col("minutes").filter(pl.col("starter")).mean().alias("starter_min"),
        (pl.col("minutes").filter(~pl.col("starter")).sum() / pl.col("_tot").first()).alias(
            "bench_share"
        ),
    )
    g = games.select(
        "game_id",
        "game_date",
        pl.col("home_team").alias("team_id"),
        (pl.col("home_pts") - pl.col("away_pts")).abs().cast(pl.Float64).alias("abs_margin"),
    )
    g = pl.concat(
        [
            g,
            games.select(
                "game_id",
                "game_date",
                pl.col("away_team").alias("team_id"),
                (pl.col("home_pts") - pl.col("away_pts"))
                .abs()
                .cast(pl.Float64)
                .alias("abs_margin"),
            ),
        ]
    )
    return g.join(agg, on=["game_id", "team_id"], how="left").sort(
        ["game_date", "game_id", "team_id"]
    )


def previous_game_coach(tg: pl.DataFrame) -> pl.DataFrame:
    """``tg``: game_id, team_id, game_date, coach_key (the ACTUAL coach). Returns game_id, team_id, prev_coach: the coach of the
    team's previous game (null for its first game in the data)."""
    t = tg.sort(["team_id", "game_date", "game_id"]).with_columns(
        pl.col("coach_key").shift(1).over("team_id").alias("prev_coach")
    )
    return t.select("game_id", "team_id", "prev_coach")


# --------------------------------------------------------------------------- EWMA states
class _Series:
    """Per-coach EWMA states after each event, plus league as-of cumulative sums for one (habit, phase) series."""

    def __init__(self, coach: np.ndarray, day: np.ndarray, x: np.ndarray) -> None:
        order = np.lexsort((np.arange(len(day)), day))
        coach, day, x = coach[order], day[order], x[order]
        self.states: dict[Any, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        by: dict[Any, list[int]] = {}
        for i, c in enumerate(coach):
            by.setdefault(c, []).append(i)
        for c, idx in by.items():
            s = w = 0.0
            ds, ss, ws, ns = [], [], [], []
            for n, i in enumerate(idx, start=1):
                s = s * DEC + float(x[i])
                w = w * DEC + 1.0
                ds.append(day[i])
                ss.append(s)
                ws.append(w)
                ns.append(n)
            self.states[c] = (np.array(ds), np.array(ss), np.array(ws), np.array(ns))
        self.day = day
        self.cum = np.concatenate([[0.0], np.cumsum(x)])

    def league(self, qday: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mean, count) of the league events dated strictly before each query day."""
        idx = np.searchsorted(self.day, qday, side="left")
        with np.errstate(divide="ignore", invalid="ignore"):
            m = np.where(idx > 0, self.cum[idx] / np.maximum(idx, 1), np.nan)
        return m, idx.astype(float)

    def coach_state(
        self, coach: np.ndarray, qday: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        s = np.zeros(len(qday))
        w = np.zeros(len(qday))
        n = np.zeros(len(qday))
        for c in {x for x in coach.tolist() if x is not None}:
            st = self.states.get(c)
            if st is None:
                continue
            m = np.array([x == c for x in coach.tolist()])
            k = np.searchsorted(st[0], qday[m], side="left")
            have = k > 0
            kk = np.maximum(k - 1, 0)
            s[m] = np.where(have, st[1][kk], 0.0)
            w[m] = np.where(have, st[2][kk], 0.0)
            n[m] = np.where(have, st[3][kk], 0.0)
        return s, w, n


def _days(d: Any) -> np.ndarray:
    return np.array([(x - dt.date(1970, 1, 1)).days for x in d], dtype=np.int64)


class HabitTable:
    """Coach habits as-of. Built from ``inputs`` (team_game_inputs) joined with the ACTUAL coach of each game (``coach_key``)."""

    def __init__(self, inputs: pl.DataFrame) -> None:
        self.series: dict[tuple[str, str], _Series] = {}
        ph = inputs.with_columns(
            pl.col("game_id").map_elements(phase_of, return_dtype=pl.Utf8).alias("_phase")
        ).filter(pl.col("coach_key").is_not_null() & pl.col("_phase").is_not_null())
        for phase in ("REG", "PO"):
            p = ph.filter(pl.col("_phase") == phase)
            for h in HABITS:
                if h == "blowout":
                    for tag, mask in (
                        ("blowout_hi", pl.col("abs_margin") >= BLOWOUT_MARGIN),
                        ("blowout_lo", pl.col("abs_margin") <= CLOSE_MARGIN),
                    ):
                        q = p.filter(mask & pl.col("top5_min").is_not_null())
                        self.series[(tag, phase)] = _Series(
                            q["coach_key"].to_numpy(),
                            _days(q["game_date"].to_list()),
                            q["top5_min"].to_numpy().astype(float),
                        )
                else:
                    q = p.filter(pl.col(h).is_not_null())
                    self.series[(h, phase)] = _Series(
                        q["coach_key"].to_numpy(),
                        _days(q["game_date"].to_list()),
                        q[h].to_numpy().astype(float),
                    )

    def _shrunk(
        self, name: str, phase: str, coach: np.ndarray, qday: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        ser = self.series[(name, phase)]
        reg = self.series[(name, "REG")]
        lm, ln = ser.league(qday)
        if phase == "PO":
            rm, _ = reg.league(qday)
            lm = np.where(ln >= PO_LEAGUE_MIN, lm, rm)
        lm = np.where(np.isnan(lm), DEFAULT_LEAGUE[name], lm)
        s, w, n = ser.coach_state(coach, qday)
        return (s + K_PSEUDO * lm) / (w + K_PSEUDO), n

    def lookup(self, coach: list[Any], day: list[dt.date]) -> pl.DataFrame:
        """18 habit columns plus the coach's earlier game counts per phase, for (coach, date) queries; null coach = league means."""
        c = np.array(coach, dtype=object)
        q = _days(day)
        out: dict[str, np.ndarray] = {}
        counts: dict[str, np.ndarray] = {}
        for phase, tag in (("REG", "reg"), ("PO", "po")):
            for h in HABITS:
                if h == "blowout":
                    hi, nh = self._shrunk("blowout_hi", phase, c, q)
                    lo, nl = self._shrunk("blowout_lo", phase, c, q)
                    out[f"h_{tag}_{h}"] = hi - lo
                else:
                    out[f"h_{tag}_{h}"], n = self._shrunk(h, phase, c, q)
                    if h == "depth":
                        counts[f"coach_n_{tag}"] = n
        for h in HABITS:
            out[f"d_{h}"] = out[f"h_po_{h}"] - out[f"h_reg_{h}"]
        return pl.DataFrame({**out, **counts})


# --------------------------------------------------------------------------- reliability screen (before any stat error)
def reliability_screen(
    inputs: pl.DataFrame,
    season: int = 2022,
    min_games: int = 60,
    min_cond: int = 3,
    keep_at: float = 0.30,
) -> dict[str, Any]:
    """Odd/even alternate-game split-half of career-pooled REG habits over the coach's games of ``season``; Spearman-Brown
    ``2r/(1+r)`` across coaches with >= ``min_games``; keep a habit iff the corrected value >= ``keep_at``.

    ``inputs``: team_game_inputs joined with ``coach_key`` and ``season``."""
    d = inputs.filter(
        (pl.col("season") == season)
        & pl.col("coach_key").is_not_null()
        & (pl.col("game_id").str.slice(0, 3) == "002")
    ).sort(["coach_key", "game_date", "game_id"])
    d = d.with_columns((pl.int_range(pl.len()).over("coach_key") % 2).alias("_half"))
    big = d.group_by("coach_key").len().filter(pl.col("len") >= min_games)["coach_key"].to_list()
    d = d.filter(pl.col("coach_key").is_in(big))
    out: dict[str, Any] = {}
    for h in HABITS:
        vals: dict[int, dict[Any, float]] = {0: {}, 1: {}}
        for (c, half), g in d.group_by("coach_key", "_half"):
            if h == "blowout":
                hi = g.filter(pl.col("abs_margin") >= BLOWOUT_MARGIN)["top5_min"].drop_nulls()
                lo = g.filter(pl.col("abs_margin") <= CLOSE_MARGIN)["top5_min"].drop_nulls()
                if len(hi) >= min_cond and len(lo) >= min_cond:
                    vals[int(half)][c] = float(hi.to_numpy().mean()) - float(lo.to_numpy().mean())
            else:
                v = g[h].drop_nulls()
                if len(v):
                    vals[int(half)][c] = float(v.to_numpy().mean())
        both = sorted(set(vals[0]) & set(vals[1]), key=str)
        if len(both) < 5:
            out[h] = {"coaches": len(both), "r": None, "corrected": None, "keep": False}
            continue
        r = float(np.corrcoef([vals[0][c] for c in both], [vals[1][c] for c in both])[0, 1])
        corr = 2 * r / (1 + r) if r > -1 else float("nan")
        out[h] = {"coaches": len(both), "r": r, "corrected": corr, "keep": bool(corr >= keep_at)}
    return out


# --------------------------------------------------------------------------- team-game feature frame
def series_prev_game(po_games: pl.DataFrame) -> pl.DataFrame:
    """game_id, prev_game_id: the previous game of the same series (same season, same team pair), null for game 1."""
    g = po_games.sort(["game_date", "game_id"]).with_columns(
        (
            pl.min_horizontal("home_team", "away_team").cast(pl.Utf8)
            + pl.lit("_")
            + pl.max_horizontal("home_team", "away_team").cast(pl.Utf8)
        ).alias("_pair")
    )
    g = g.with_columns(pl.col("game_id").shift(1).over("season", "_pair").alias("prev_game_id"))
    return g.select("game_id", "prev_game_id")


def placebo_map(keys: pl.DataFrame, seed: int = 0) -> dict[tuple[int, str, Any], Any]:
    """Permute coaches within (season, phase). ``keys``: season, phase, coach (non-null). Returns (season, phase, coach) -> other coach."""
    rng = np.random.default_rng(seed)
    out: dict[tuple[int, str, Any], Any] = {}
    for (season, phase), g in keys.group_by("season", "phase", maintain_order=True):
        cs = sorted(set(g["coach"].to_list()), key=str)
        perm = rng.permutation(len(cs))
        for i, c in enumerate(cs):
            out[(int(season), str(phase), c)] = cs[int(perm[i])]
    return out


def team_game_features(
    games: pl.DataFrame,
    coach: pl.DataFrame,
    box: pl.DataFrame,
    placebo_seed: int | None = None,
    table: HabitTable | None = None,
) -> pl.DataFrame:
    """One row per (game_id, team_id): phase columns, series columns, coach habit columns, interactions.

    ``games``: game_id, game_date, season, home_team, away_team, home_pts, away_pts (ALL stages). ``coach``: game_id, team_id, coach_key
    (actual coach of each game). ``box``: game_id, team_id, player_id, minutes, starter. ``placebo_seed``: replace each row's coach by
    another coach of the same season and phase (the placebo arm)."""
    g = games.sort(["game_date", "game_id"])
    tgs = pl.concat(
        [
            g.select(
                "game_id",
                "game_date",
                "season",
                pl.col("home_team").alias("team_id"),
                pl.lit(True).alias("is_home_game"),
            ),
            g.select(
                "game_id",
                "game_date",
                "season",
                pl.col("away_team").alias("team_id"),
                pl.lit(False).alias("is_home_game"),
            ),
        ]
    ).sort(["game_date", "game_id", "team_id"])
    reg = tgs.filter(pl.col("game_id").str.starts_with("002")).with_columns(
        (pl.int_range(pl.len()).over("team_id", "season")).alias("games_prior")
    )
    gp = reg.select("game_id", "team_id", "games_prior")
    late = reg.group_by("game_id").agg((pl.col("games_prior").max() >= LATE_FROM).alias("late15"))
    rs_games = g.filter(pl.col("game_id").str.starts_with("002"))
    cs = clinch_state(rs_games)
    po_games = g.filter(pl.col("game_id").str.starts_with("004"))
    ss = series_state(po_games) if po_games.height else pl.DataFrame()
    f = tgs.join(gp, on=["game_id", "team_id"], how="left").join(late, on="game_id", how="left")
    f = f.join(cs, on=["game_id", "team_id"], how="left")
    if ss.height:
        f = f.join(ss, on=["game_id", "team_id"], how="left")
    for c in ("series_game", "wins_for", "wins_against"):
        if c not in f.columns:
            f = f.with_columns(pl.lit(0).alias(c))
    for c in ("is_home", "home_court", "elim_game", "close_out_chance"):
        if c not in f.columns:
            f = f.with_columns(pl.lit(False).alias(c))
    f = f.with_columns(
        pl.col("game_id").str.starts_with("004").alias("po"),
        pl.when(pl.col("game_id").str.starts_with("002"))
        .then(pl.col("games_prior").cast(pl.Float64) / SEASON_GAMES)
        .otherwise(1.0)
        .alias("gp_frac"),
        pl.col("late15").fill_null(False),
        pl.when(pl.col("game_id").str.starts_with("004"))
        .then(pl.col("game_id").str.slice(7, 1).cast(pl.Int64, strict=False))
        .otherwise(0)
        .fill_null(0)
        .alias("round"),
    )
    bools = [
        "late15",
        "clinched_6",
        "clinched_10",
        "elim_6",
        "elim_10",
        "po",
        "elim_game",
        "close_out_chance",
        "is_home",
        "home_court",
    ]
    f = f.with_columns(
        *[pl.col(c).fill_null(False).cast(pl.Float64) for c in bools],
        *[
            pl.col(c).fill_null(0).cast(pl.Float64)
            for c in ("series_game", "wins_for", "wins_against", "round")
        ],
    )
    # coach habits: previous-game coach
    cg = coach.join(g.select("game_id", "game_date"), on="game_id")
    prev = previous_game_coach(cg.select("game_id", "team_id", "game_date", "coach_key"))
    f = f.join(prev, on=["game_id", "team_id"], how="left")
    if table is None:
        inp = team_game_inputs(box, g).join(
            coach.select("game_id", "team_id", "coach_key"), on=["game_id", "team_id"], how="left"
        )
        table = HabitTable(inp)
    q = f.select("game_id", "season", "prev_coach")
    keys = q.with_columns(
        pl.col("game_id")
        .map_elements(lambda x: phase_of(x) or "OTHER", return_dtype=pl.Utf8)
        .alias("phase")
    )
    who = keys["prev_coach"].to_list()
    if placebo_seed is not None:
        ok = keys.filter(pl.col("prev_coach").is_not_null()).select(
            "season", "phase", pl.col("prev_coach").alias("coach")
        )
        pm = placebo_map(ok, placebo_seed)
        who = [
            None if c is None else pm[(int(s), str(p), c)]
            for c, s, p in zip(who, keys["season"].to_list(), keys["phase"].to_list(), strict=True)
        ]
    hab = table.lookup(who, f["game_date"].to_list())
    f = pl.concat([f, hab], how="horizontal")
    f = f.with_columns(
        *[(pl.col("gp_frac") * pl.col(f"d_{h}")).alias(f"gpx_d_{h}") for h in HABITS],
        *[(pl.col("late15") * pl.col(f"d_{h}")).alias(f"latex_d_{h}") for h in HABITS],
    )
    return f.select(
        "game_id",
        "team_id",
        "game_date",
        "season",
        "prev_coach",
        *PHASE_COLS,
        *HABIT_COLS,
        *INTER_COLS,
        "coach_n_reg",
        "coach_n_po",
    )
