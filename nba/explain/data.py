"""Read-only loaders for the game explainer.

Every function takes an already-open **read-only** DuckDB connection (see :func:`open_ro`) and
returns plain Python values. Nothing here fits a model; the only arithmetic is the scoring of
stored distributions against box-score outcomes and the de-vig of stored prices.

Time conventions (docs/DAILY_PIPELINE.md): ``forward_predictions.made_at`` and ``tipoff`` are
naive **UTC**; ``player_availability.as_of`` is naive **Eastern**. The page shows ET only.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import numpy as np

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

#: The T-60 rule shared with the settle step: a row is usable if made_at <= tipoff - 60 min.
LEAD_MINUTES = 60
PRIMARY_PROPS = "props_context_residual"
BASELINE_PROPS = "props_recency_v1"
WIN_MODELS = ("rung0_injury_elo", "rung0_mov_elo")
STATS = ("pts", "reb", "ast", "fg3m")
#: odds_history market name for each stat.
MARKET_OF = {
    "pts": "player_points",
    "reb": "player_rebounds",
    "ast": "player_assists",
    "fg3m": "player_threes",
}
#: Line sources in order of preference; anything else is pooled into a median "consensus".
BOOK_PREFERENCE = ("pinnacle", "draftkings")
#: Integer-support scoring is only exact when the stored tail is below this (full_support.py).
TAIL_EPS = 1e-3


def open_ro(path: str | Path) -> Any:
    """Open ``path`` read-only. The explainer never holds a write connection."""
    return duckdb.connect(str(path), read_only=True)


def utc_to_et(ts: datetime) -> datetime:
    """Naive UTC -> aware ET."""
    return ts.replace(tzinfo=UTC).astimezone(ET)


def et_naive(ts: datetime) -> datetime:
    """Naive UTC -> naive ET (the convention of ``player_availability.as_of``)."""
    return utc_to_et(ts).replace(tzinfo=None)


# --------------------------------------------------------------------------------------- game


@dataclass
class Game:
    game_id: str
    game_date: date
    season: int
    home_team: int
    away_team: int
    home_pts: int | None
    away_pts: int | None
    tip_utc: datetime | None  # real tip-off from the stored forecast rows; None if never forecast

    @property
    def final(self) -> bool:
        return bool(self.home_pts and self.away_pts)

    @property
    def margin(self) -> int | None:
        """Final margin, always >= 0 (winner minus loser)."""
        if not self.final:
            return None
        assert self.home_pts is not None and self.away_pts is not None
        return abs(self.home_pts - self.away_pts)

    @property
    def home_won(self) -> bool | None:
        if not self.final:
            return None
        assert self.home_pts is not None and self.away_pts is not None
        return self.home_pts > self.away_pts


def load_game(con: Any, game_id: str) -> Game | None:
    row = con.execute(
        "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
        "FROM games WHERE game_id = ?",
        [game_id],
    ).fetchone()
    if row is None:
        return None
    tip = con.execute(
        "SELECT max(tipoff) FROM forward_predictions WHERE game_id = ?", [game_id]
    ).fetchone()
    return Game(
        game_id=row[0],
        game_date=row[1],
        season=int(row[2]),
        home_team=int(row[3]),
        away_team=int(row[4]),
        home_pts=int(row[5]) if row[5] else None,
        away_pts=int(row[6]) if row[6] else None,
        tip_utc=tip[0] if tip and tip[0] is not None else None,
    )


# ---------------------------------------------------------------------------------- forecasts


@dataclass
class Stored:
    """One stored prediction row: who made it, when, and its parsed JSON."""

    model: str
    target: str
    player_id: int
    made_at: datetime  # naive UTC
    tipoff: datetime  # naive UTC
    pred: dict[str, Any]


def load_forecasts(con: Any, game_id: str) -> list[Stored]:
    """Latest stored row per (model, target, player) with ``made_at <= tipoff - 60 min``.

    The same eligibility the settle step uses (``forward_scores_elig``); later rows are ignored
    because the page shows only what we said before tip.
    """
    models = (PRIMARY_PROPS, BASELINE_PROPS, *WIN_MODELS)
    marks = ",".join("?" for _ in models)
    rows = con.execute(
        f"""
        WITH e AS (
          SELECT model_name, target, player_id, made_at, tipoff, prediction,
                 row_number() OVER (PARTITION BY model_name, target, player_id
                                    ORDER BY made_at DESC, run_id DESC) AS rn
          FROM forward_predictions
          WHERE game_id = ? AND model_name IN ({marks})
            AND made_at <= tipoff - {LEAD_MINUTES} * INTERVAL 1 MINUTE
        )
        SELECT model_name, target, player_id, made_at, tipoff, prediction FROM e WHERE rn = 1
        """,
        [game_id, *models],
    ).fetchall()
    return [Stored(r[0], r[1], int(r[2]), r[3], r[4], json.loads(r[5])) for r in rows]


def count_late_only(con: Any, game_id: str) -> int:
    """Number of stored rows for the game that exist only after the T-60 cutoff (not shown)."""
    n = con.execute(
        f"SELECT count(*) FROM forward_predictions WHERE game_id = ? "
        f"AND made_at > tipoff - {LEAD_MINUTES} * INTERVAL 1 MINUTE",
        [game_id],
    ).fetchone()
    return int(n[0]) if n else 0


# -------------------------------------------------------------------------------------- box


@dataclass
class Box:
    player_id: int
    team_id: int
    minutes: float | None
    starter: bool
    stats: dict[str, int | None]

    @property
    def played(self) -> bool:
        return self.minutes is not None and self.minutes > 0


def load_box(con: Any, game_id: str) -> dict[int, Box]:
    rows = con.execute(
        "SELECT player_id, team_id, minutes, starter, pts, reb, ast, fg3m "
        "FROM player_game_stats WHERE game_id = ?",
        [game_id],
    ).fetchall()
    out: dict[int, Box] = {}
    for r in rows:
        out[int(r[0])] = Box(
            player_id=int(r[0]),
            team_id=int(r[1]),
            minutes=float(r[2]) if r[2] is not None else None,
            starter=bool(r[3]),
            stats={
                "pts": None if r[4] is None else int(r[4]),
                "reb": None if r[5] is None else int(r[5]),
                "ast": None if r[6] is None else int(r[6]),
                "fg3m": None if r[7] is None else int(r[7]),
            },
        )
    return out


# ----------------------------------------------------------------------------------- report


@dataclass
class ReportLine:
    player_id: int
    status: str
    reason: str | None
    team_id: int | None


@dataclass
class Report:
    as_of_et: datetime | None  # naive ET stamp of the snapshot used; None = no usable report
    lines: list[ReportLine] = field(default_factory=list)


def load_report(con: Any, game_id: str, tip_utc: datetime | None, game_date: date) -> Report:
    """The official injury report as the model saw it: latest snapshot for THIS game with
    ``as_of <= tip - 60 min`` (ET) and not older than 36 h before that cutoff
    (``nba.features.injury_report.serve_pretip_flagged``)."""
    if tip_utc is None:
        return Report(None)
    cutoff = et_naive(tip_utc) - timedelta(minutes=LEAD_MINUTES)
    row = con.execute(
        "SELECT max(as_of) FROM player_availability "
        "WHERE game_id = ? AND as_of <= ? AND as_of >= ?",
        [game_id, cutoff, cutoff - timedelta(hours=36)],
    ).fetchone()
    if not row or row[0] is None:
        return Report(None)
    as_of = row[0]
    rows = con.execute(
        "SELECT player_id, status, reason FROM player_availability "
        "WHERE game_id = ? AND as_of = ? ORDER BY player_id",
        [game_id, as_of],
    ).fetchall()
    ids = sorted({int(r[0]) for r in rows})
    teams: dict[int, int] = {}
    if ids:
        marks = ",".join("?" for _ in ids)
        for pid, tid in con.execute(
            f"SELECT p.player_id, arg_max(p.team_id, g.game_date) FROM player_game_stats p "
            f"JOIN games g USING (game_id) WHERE p.player_id IN ({marks}) AND g.game_date <= ? "
            f"GROUP BY p.player_id",
            [*ids, game_date],
        ).fetchall():
            teams[int(pid)] = int(tid)
    return Report(
        as_of,
        [ReportLine(int(r[0]), str(r[1]), r[2], teams.get(int(r[0]))) for r in rows],
    )


# ------------------------------------------------------------------------- integer support


def survival(full: dict[str, Any] | None) -> np.ndarray | None:
    """``[P(Y>=1), ..., P(Y>=K)]`` from the stored ``p_ge_full`` field, or None."""
    if not full or not full.get("c") or not full.get("n"):
        return None
    return np.asarray(full["c"], dtype=float) / float(full["n"])


def is_complete(surv: np.ndarray | None) -> bool:
    return surv is not None and surv.size > 0 and float(surv[-1]) <= TAIL_EPS


def crps_int(surv: np.ndarray, y: int) -> float:
    """Integer-support CRPS (RPS): ``sum_k (F(k) - 1[y <= k])^2`` with ``F(k) = 1 - P(Y>=k+1)``.

    Same formula as ``nba.props.full_support.crps_int_surv`` (cross-checked in the tests)."""
    big_k = int(surv.size)
    m = max(big_k, int(y))
    p = np.zeros(m)
    p[:big_k] = surv
    f = 1.0 - p
    obs = (np.arange(m) >= int(y)).astype(float)
    return float(np.sum((f - obs) ** 2))


def p_over(surv: np.ndarray | None, line: float) -> float | None:
    """``P(Y > line)`` on integer support, i.e. ``P(Y >= floor(line) + 1)`` (a push is not an over).

    Beyond the stored tail the probability is below ``TAIL_EPS``; returned as 0.0 only when the
    stored tail is complete, else None."""
    if surv is None:
        return None
    k = int(np.floor(line)) + 1
    if k < 1:
        return 1.0
    if k <= surv.size:
        return float(surv[k - 1])
    return 0.0 if is_complete(surv) else None


def pit_interval(surv: np.ndarray, y: int) -> tuple[float, float]:
    """``(F(y-1), F(y))``: the continuity-corrected PIT of integer ``y`` is uniform on it."""

    def cdf(k: int) -> float:
        if k < 0:
            return 0.0
        return 1.0 - (float(surv[k]) if k < surv.size else 0.0)

    return cdf(int(y) - 1), cdf(int(y))


def interval_status(y: float, lo: float, hi: float) -> str:
    """Where an outcome fell against the stored 80% interval ``[q10, q90]``.

    ``inside`` (bounds inclusive), ``above`` or ``below``. The glyph on the page is a pure
    function of this, so a miss is drawn the same way every time."""
    if y < lo:
        return "below"
    if y > hi:
        return "above"
    return "inside"


# ------------------------------------------------------------------------------------ market


@dataclass
class MarketWin:
    book: str
    p_home: float  # de-vigged, T-60 snapshot
    snapshot_ts: datetime  # naive UTC
    price_home: int | None
    price_away: int | None


@dataclass
class MarketLine:
    stat: str
    book: str  # "pinnacle" | "draftkings" | "consensus of k books"
    line: float
    p_over: float | None  # de-vigged market probability of the over


def load_market_win(odds: Any, game_id: str) -> MarketWin | None:
    """Pinnacle moneyline at the T-60 snapshot, de-vigged proportionally (sides sum to 1)."""
    rows = odds.execute(
        "SELECT side, price_american, implied_prob_raw, snapshot_ts FROM odds_history "
        "WHERE game_id = ? AND market = 'h2h' AND book = 'pinnacle' AND snapshot_kind = 't60'",
        [game_id],
    ).fetchall()
    by_side = {r[0]: r for r in rows}
    if "home" not in by_side or "away" not in by_side:
        return None
    h, a = by_side["home"], by_side["away"]
    if h[2] is None or a[2] is None:
        return None
    total = float(h[2]) + float(a[2])
    return MarketWin("pinnacle", float(h[2]) / total, h[3], h[1], a[1])


def _devig_pair(over_price: float, under_price: float) -> float:
    return over_price / (over_price + under_price)


def load_market_lines(odds: Any, game_id: str) -> dict[tuple[int, str], MarketLine]:
    """Main points/rebounds/assists/threes line per (player, stat) at T-60.

    Per book the main line is the one whose over/under raw implied probabilities are closest
    (alternate lines are skewed). Pinnacle, else DraftKings, else the median line of the other
    books (market p_over averaged over the books that offer exactly that line)."""
    markets = list(MARKET_OF.values())
    marks = ",".join("?" for _ in markets)
    rows = odds.execute(
        f"SELECT market, player_id, book, point, side, implied_prob_raw FROM odds_history "
        f"WHERE game_id = ? AND snapshot_kind = 't60' AND market IN ({marks}) "
        f"AND player_id IS NOT NULL AND point IS NOT NULL AND implied_prob_raw IS NOT NULL",
        [game_id, *markets],
    ).fetchall()
    stat_of = {v: k for k, v in MARKET_OF.items()}
    # (stat, player, book) -> {point: {side: raw prob}}
    by: dict[tuple[str, int, str], dict[float, dict[str, float]]] = {}
    for market, pid, book, point, side, raw in rows:
        by.setdefault((stat_of[market], int(pid), str(book)), {}).setdefault(float(point), {})[
            str(side)
        ] = float(raw)
    main: dict[tuple[str, int], dict[str, tuple[float, float]]] = {}
    for (stat, pid, book), points in by.items():
        best: tuple[float, float, float] | None = None  # (gap, point, devigged p_over)
        for pt, sides in points.items():
            if "over" in sides and "under" in sides:
                gap = abs(sides["over"] - sides["under"])
                if best is None or gap < best[0]:
                    best = (gap, pt, _devig_pair(sides["over"], sides["under"]))
        if best is not None:
            main.setdefault((stat, pid), {})[book] = (best[1], best[2])
    out: dict[tuple[int, str], MarketLine] = {}
    for (stat, pid), books in main.items():
        chosen = next((b for b in BOOK_PREFERENCE if b in books), None)
        if chosen is not None:
            pt, p = books[chosen]
            out[(pid, stat)] = MarketLine(stat, chosen, pt, p)
            continue
        pts = [v[0] for v in books.values()]
        med = float(statistics.median(pts))
        agree = [v[1] for v in books.values() if v[0] == med]
        out[(pid, stat)] = MarketLine(
            stat,
            f"consensus of {len(books)} book{'' if len(books) == 1 else 's'}",
            med,
            float(statistics.fmean(agree)) if agree else None,
        )
    return out


def load_market_names(odds: Any, game_id: str) -> dict[int, str]:
    """player_id -> name as the books spelled it (fallback when the static list lacks someone)."""
    rows = odds.execute(
        "SELECT DISTINCT player_id, player_name FROM odds_history "
        "WHERE game_id = ? AND player_id IS NOT NULL AND player_name IS NOT NULL",
        [game_id],
    ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


# ------------------------------------------------------------------------------ season bias


@dataclass
class PlayerBias:
    """Mean of (actual - forecast mean) for one player's scored games, with a bootstrap CI."""

    player_id: int
    n: int
    mean: float
    lo: float
    hi: float


def player_points_bias(con: Any, player_id: int, *, draws: int = 2000) -> PlayerBias | None:
    """Descriptive bias of the primary points forecast for one player over the stored scores.

    One row per game per player, so resampling rows IS resampling games. Seed 0, 95% percentile."""
    rows = con.execute(
        "SELECT y - pred FROM forward_scores WHERE model_name = ? AND target = 'pts' "
        "AND status = 'scored' AND player_id = ?",
        [PRIMARY_PROPS, player_id],
    ).fetchall()
    d = np.asarray([float(r[0]) for r in rows], dtype=float)
    if d.size < 20:
        return None
    rng = np.random.default_rng(0)
    means = d[rng.integers(0, d.size, size=(draws, d.size))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return PlayerBias(player_id, int(d.size), float(d.mean()), float(lo), float(hi))


def bootstrap_mean_ci(
    x: np.ndarray, *, draws: int = 2000, seed: int = 0
) -> tuple[float, float, float]:
    """(mean, lo, hi) percentile 95% CI resampling the entries of ``x`` (here: players)."""
    rng = np.random.default_rng(seed)
    m = float(x.mean())
    if x.size < 2:
        return m, m, m
    means = x[rng.integers(0, x.size, size=(draws, x.size))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return m, float(lo), float(hi)
