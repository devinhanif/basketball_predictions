# ruff: noqa: E501
"""WOWY redistribution features: WHO absorbs the minutes and production of the teammates OUT tonight.

Candidate columns for docs/prereg/WOWY_REDISTRIBUTION.md (DRAFT). Production props already carry team
totals of the rotation teammates OUT (``vac_min``, ``vac_{stat}``, given equally to every teammate); these
columns are per (player i, tonight's OUT teammate j) and say how i's own minutes and production moved in
the past team games that j missed entirely, against the past team games that j played, shrunk toward
"no difference" by the number of games without j.

Everything is as-of. For a target game g of team T (``kg`` = T's global game order across seasons):

* the OUT set O(g, T) is the production one: ``player_availability`` rows of the official report usable at
  ``real tip - 60`` (``flagged``), restricted to rotation players (as-of ``min40 >= ROTATION_MIN40`` and
  ``n_after >= MIN_PRIOR_PLAYED``), exactly ``context_residual.vacated_features``;
* j is a teammate in a past game h only from the first game of his CURRENT stint with T (the run of his
  appearances for T that ends at his last appearance before g; he must not have appeared for another team
  since). A game h in that stint where j has no minutes is a game WITHOUT j; a game where he played is WITH;
* h ranges over T's previous ``WINDOW_GAMES`` games (strictly before g, across the season boundary);
* only games where i played (minutes > 0) enter either side (P(play) is a separate model);
* ``delta_q = n_wo / (n_wo + SHRINK_K) * (mean_q[without] - mean_q[with])`` per quantity q; per-36 rates use
  summed stats over summed minutes; zero when either side is empty (no information is 0 plus
  ``wo_conf = 0``, never NaN);
* the columns are the sum over j in O \\ {i}.

Nothing from game g's box is read except which players have a row to be scored (the caller's A0 rows) and,
for the PLACEBO, nothing at all (the placebo pool is the as-of roster, never tonight's box). ``assister_id``
is not used: assists are the player's own box assists in games he played.
"""

from __future__ import annotations

import zlib
from bisect import bisect_left
from collections import defaultdict
from typing import Any

import numpy as np
import polars as pl

WINDOW_GAMES = 100
SHRINK_K = 6.0
PLACEBO_ROSTER_GAMES = 10
PLACEBO_MIN_MPG = 12.0
PLACEBO_MIN_APPS = 5

#: per-game quantities, in the order of the stat matrix
QUANTS = ("min", "pts", "reb", "ast", "fg3m", "fga", "fg3a")
_Q = {q: i for i, q in enumerate(QUANTS)}
RATE_QUANTS = ("fga", "fg3a", "reb", "ast")
GAME_COLS = tuple(f"wo_d_{q}" for q in ("min", "pts", "reb", "ast", "fg3m"))
RATE_COLS = tuple(f"wo_r_{q}" for q in RATE_QUANTS)
WOWY_COLS = (*GAME_COLS, *RATE_COLS, "wo_conf", "wo_n")

#: columns each stat head receives in the WOWY arm (5 per head)
HEAD_COLS: dict[str, tuple[str, ...]] = {
    "pts": ("wo_d_min", "wo_d_pts", "wo_r_fga", "wo_conf", "wo_n"),
    "reb": ("wo_d_min", "wo_d_reb", "wo_r_reb", "wo_conf", "wo_n"),
    "ast": ("wo_d_min", "wo_d_ast", "wo_r_ast", "wo_conf", "wo_n"),
    "fg3m": ("wo_d_min", "wo_d_fg3m", "wo_r_fg3a", "wo_conf", "wo_n"),
}

REQUIRED_BOX = ("game_id", "team_id", "player_id", "minutes", "game_date", *QUANTS[1:])


def out_rotation_players(
    played: pl.DataFrame,
    states: dict[str, np.ndarray],
    flagged: dict[str, set[int]],
    game_info: dict[str, tuple[object, int, int]],
    min_prior_played: int,
    rotation_min40: float,
) -> pl.DataFrame:
    """The rotation players OUT per (game_id, team_id), one row each: the join of
    ``context_residual.vacated_features`` before its group-by (same as-of state: after the player's latest
    game strictly before the game date). Columns game_id, team_id, player_id, min10."""
    post = states["post"]
    from nba.props.context_residual import _sel  # noqa: PLC0415 (module-private helper)

    st = pl.DataFrame(
        {
            "player_id": played["player_id"].cast(pl.Int64),
            "game_date": played["game_date"],
            "team_id": played["team_id"],
            "n_after": states["n_prior"] + 1,
            "min40": _sel(post, 40.0, "min"),
            "min10": _sel(post, 10.0, "min"),
        }
    ).sort("game_date")
    recs = [
        (gid, pid, game_info[gid][0])
        for gid, pids in flagged.items()
        if gid in game_info
        for pid in pids
    ]
    schema = {"game_id": pl.Utf8, "player_id": pl.Int64, "game_date": pl.Date}
    empty = pl.DataFrame(
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "min10": pl.Float64,
        }
    )
    if not recs:
        return empty
    left = pl.DataFrame(recs, schema=schema, orient="row")
    left = left.with_columns((pl.col("game_date") - pl.duration(days=1)).alias("_key")).sort("_key")
    j = left.join_asof(
        st.rename({"game_date": "_pd"}),
        left_on="_key",
        right_on="_pd",
        by="player_id",
        strategy="backward",
    ).filter((pl.col("n_after") >= min_prior_played) & (pl.col("min40") >= rotation_min40))
    gi = pl.DataFrame(
        [(g, v[1], v[2]) for g, v in game_info.items()],
        schema={"game_id": pl.Utf8, "home": pl.Int64, "away": pl.Int64},
        orient="row",
    )
    j = j.join(gi, on="game_id").filter(
        (pl.col("team_id") == pl.col("home")) | (pl.col("team_id") == pl.col("away"))
    )
    return j.select("game_id", "team_id", "player_id", "min10")


class _TeamHistory:
    """One team's global game order and a (game x player x quantity) cube of box stats."""

    def __init__(self, games: list[tuple[str, Any]], rows: pl.DataFrame) -> None:
        self.gid = [g for g, _ in games]
        self.date = [d for _, d in games]
        self.k_of = {g: k for k, g in enumerate(self.gid)}
        pids = sorted({int(p) for p in rows["player_id"].to_list()})
        self.pix = {p: i for i, p in enumerate(pids)}
        n_g, n_p = len(games), len(pids)
        self.played = np.zeros((n_g, n_p), dtype=bool)
        self.cube = np.zeros((n_g, n_p, len(QUANTS)))
        mins = rows["minutes"].cast(pl.Float64).fill_null(0.0).to_numpy()
        cols = [mins] + [rows[q].cast(pl.Float64).fill_null(0.0).to_numpy() for q in QUANTS[1:]]
        gk = np.array([self.k_of[str(g)] for g in rows["game_id"].to_list()])
        pk = np.array([self.pix[int(p)] for p in rows["player_id"].to_list()])
        ok = mins > 0
        self.played[gk[ok], pk[ok]] = True
        for qi, c in enumerate(cols):
            self.cube[gk[ok], pk[ok], qi] = c[ok]


def _run_starts(teams: list[int]) -> list[int]:
    """Index in the list where the contiguous run of equal team ids containing each position begins."""
    out: list[int] = []
    for i, t in enumerate(teams):
        out.append(out[-1] if i > 0 and teams[i - 1] == t else i)
    return out


def build_wowy(
    box: pl.DataFrame,
    out_sets: dict[tuple[str, int], list[int]],
    window: int = WINDOW_GAMES,
    shrink_k: float = SHRINK_K,
    target_rows: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """WOWY columns for every (game_id, team_id, player_id) with a played box row in a game whose
    ``out_sets`` entry is non-empty (``target_rows`` restricts the rows scored: columns game_id, player_id).

    ``box``: game_id, team_id, player_id, minutes (NULL or 0 = did not play), game_date, pts, reb, ast, fg3m,
    fga, fg3a; every season the caller is allowed to see (the caller caps ``season <= 2024``).
    ``out_sets``: (game_id, team_id) -> OUT rotation player ids, pre-tip, from the report (or a placebo).
    Reads only box rows of games strictly before g (by the team's global order) plus g's roster membership
    for the rows to score."""
    missing = [c for c in REQUIRED_BOX if c not in box.columns]
    if missing:
        raise ValueError(f"box lacks {missing}")
    b = box.with_columns(pl.col("game_id").cast(pl.Utf8))
    # every player's appearances (minutes > 0) in date order across teams, for stints
    apps = (
        b.filter(pl.col("minutes").fill_null(0.0) > 0)
        .sort(["player_id", "game_date", "game_id"])
        .select("player_id", "team_id", "game_date", "game_id")
    )
    p_dates: dict[int, list[Any]] = defaultdict(list)
    p_teams: dict[int, list[int]] = defaultdict(list)
    p_games: dict[int, list[str]] = defaultdict(list)
    for pid, tid, d, g in apps.iter_rows():
        p_dates[int(pid)].append(d)
        p_teams[int(pid)].append(int(tid))
        p_games[int(pid)].append(str(g))
    p_run = {p: _run_starts(t) for p, t in p_teams.items()}

    wanted: set[tuple[str, int]] = {k for k, v in out_sets.items() if v}
    tr: set[tuple[str, int]] | None = None
    if target_rows is not None:
        tr = {(str(g), int(p)) for g, p in target_rows.select("game_id", "player_id").iter_rows()}
    out: list[tuple[Any, ...]] = []
    for tid_raw, grp in b.group_by("team_id", maintain_order=True):
        tid = int(tid_raw[0] if isinstance(tid_raw, tuple) else tid_raw)
        gl = grp.select("game_id", "game_date").unique().sort(["game_date", "game_id"]).rows()
        if not any((str(g), tid) in wanted for g, _ in gl):
            continue
        th = _TeamHistory([(str(g), d) for g, d in gl], grp)
        by_g = {
            str(g): [int(p) for p in pl_]
            for g, pl_ in grp.group_by("game_id").agg(pl.col("player_id")).iter_rows()
        }
        for g, gdate in zip(th.gid, th.date, strict=True):
            if (g, tid) not in wanted:
                continue
            kg = th.k_of[g]
            lo = max(0, kg - window)
            # stint start (team-order index) for every OUT teammate with a current stint at T
            stints: dict[int, int] = {}
            for j in out_sets[(g, tid)]:
                ds = p_dates.get(j)
                if not ds:
                    continue
                pos = bisect_left(ds, gdate) - 1  # last appearance strictly before g's date
                if pos < 0 or p_teams[j][pos] != tid:
                    continue
                first_gid = p_games[j][p_run[j][pos]]
                s0 = th.k_of.get(first_gid)
                if s0 is not None and j in th.pix:
                    stints[j] = s0
            for i in by_g[g]:
                if tr is not None and (g, i) not in tr:
                    continue
                if i not in th.pix:
                    continue
                ii = th.pix[i]
                if not th.played[kg, ii]:
                    continue  # only played rows are scored
                res = _pair_features(th, ii, kg, lo, stints, i, shrink_k)
                out.append((g, tid, i, *res))
    schema: dict[str, Any] = {"game_id": pl.Utf8, "team_id": pl.Int64, "player_id": pl.Int64}
    schema.update({c: pl.Float64 for c in WOWY_COLS})
    return pl.DataFrame(out, schema=schema, orient="row")


def _pair_features(
    th: _TeamHistory,
    ii: int,
    kg: int,
    lo: int,
    stints: dict[int, int],
    i: int,
    shrink_k: float,
) -> tuple[float, ...]:
    d_game = np.zeros(len(GAME_COLS))
    d_rate = np.zeros(len(RATE_COLS))
    conf = 0.0
    n_tot = 0.0
    for j, s0 in stints.items():
        if j == i:
            continue
        a = max(lo, s0)
        if a >= kg:
            continue
        ji = th.pix[j]
        pi = th.played[a:kg, ii]
        pj = th.played[a:kg, ji]
        wo = pi & ~pj
        wi = pi & pj
        n_wo, n_wi = int(wo.sum()), int(wi.sum())
        if n_wo == 0 or n_wi == 0:
            continue
        w = n_wo / (n_wo + shrink_k)
        cube = th.cube[a:kg, ii, :]
        mean_wo = cube[wo].mean(axis=0)
        mean_wi = cube[wi].mean(axis=0)
        for c, q in enumerate(("min", "pts", "reb", "ast", "fg3m")):
            d_game[c] += w * (mean_wo[_Q[q]] - mean_wi[_Q[q]])
        sum_wo = cube[wo].sum(axis=0)
        sum_wi = cube[wi].sum(axis=0)
        for c, q in enumerate(RATE_QUANTS):
            m_wo, m_wi = sum_wo[0], sum_wi[0]
            if m_wo > 0 and m_wi > 0:
                d_rate[c] += w * 36.0 * (sum_wo[_Q[q]] / m_wo - sum_wi[_Q[q]] / m_wi)
        conf += w
        n_tot += n_wo
    return (*(float(x) for x in d_game), *(float(x) for x in d_rate), conf, n_tot)


def asof_rotation_pool(box: pl.DataFrame) -> dict[tuple[str, int], list[int]]:
    """Placebo pool per (game_id, team_id): players who appeared for T in at least ``PLACEBO_MIN_APPS`` of T's
    previous ``PLACEBO_ROSTER_GAMES`` games with a mean of ``PLACEBO_MIN_MPG`` minutes over those appearances.
    Strictly earlier games only; tonight's box is never read."""
    pool: dict[tuple[str, int], list[int]] = {}
    b = box.with_columns(pl.col("game_id").cast(pl.Utf8))
    for tid_raw, grp in b.group_by("team_id", maintain_order=True):
        tid = int(tid_raw[0] if isinstance(tid_raw, tuple) else tid_raw)
        gl = grp.select("game_id", "game_date").unique().sort(["game_date", "game_id"])["game_id"]
        gl_list = [str(g) for g in gl.to_list()]
        mins: dict[str, dict[int, float]] = defaultdict(dict)
        for g, p, m in grp.select("game_id", "player_id", "minutes").iter_rows():
            if m is not None and m > 0:
                mins[str(g)][int(p)] = float(m)
        for k, g in enumerate(gl_list):
            tot: dict[int, list[float]] = defaultdict(list)
            for h in gl_list[max(0, k - PLACEBO_ROSTER_GAMES) : k]:
                for p, m in mins[h].items():
                    tot[p].append(m)
            pool[(g, tid)] = sorted(
                p
                for p, v in tot.items()
                if len(v) >= PLACEBO_MIN_APPS and float(np.mean(v)) >= PLACEBO_MIN_MPG
            )
    return pool


def placebo_out_sets(
    real: dict[tuple[str, int], list[int]],
    pool: dict[tuple[str, int], list[int]],
    seed: int = 0,
) -> dict[tuple[str, int], list[int]]:
    """Same number of OUT players per (game, team), but drawn from the as-of rotation pool minus the real
    OUT set: the identity of who is out is wrong, everything else (count, timing, machinery) is real."""
    out: dict[tuple[str, int], list[int]] = {}
    for (g, t), real_set in real.items():
        if not real_set:
            out[(g, t)] = []
            continue
        cand = [p for p in pool.get((g, t), []) if p not in set(real_set)]
        rng = np.random.default_rng([seed, zlib.crc32(str(g).encode()), t])
        k = min(len(real_set), len(cand))
        out[(g, t)] = sorted(int(x) for x in rng.choice(cand, size=k, replace=False)) if k else []
    return out


def attach_wowy(feats: pl.DataFrame, wowy: pl.DataFrame) -> pl.DataFrame:
    """Left-join the WOWY columns onto the production frame with production's missingness rule: games with a
    report and nobody rotation-OUT get explicit zeros; games with OUT players but no pair information get 0
    and ``wo_conf = 0``; games WITHOUT a usable report stay NULL (never imputed as 'nobody out')."""
    j = feats.join(wowy, on=["game_id", "team_id", "player_id"], how="left")
    return j.with_columns(
        [
            pl.when(pl.col("has_report") == 1)
            .then(pl.col(c).fill_null(0.0))
            .otherwise(None)
            .alias(c)
            for c in WOWY_COLS
        ]
    )


__all__ = [
    "GAME_COLS",
    "HEAD_COLS",
    "RATE_COLS",
    "SHRINK_K",
    "WINDOW_GAMES",
    "WOWY_COLS",
    "asof_rotation_pool",
    "attach_wowy",
    "build_wowy",
    "out_rotation_players",
    "placebo_out_sets",
]
