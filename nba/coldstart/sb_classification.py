"""Syntetos-Boylan (SB) ADI/CV^2 volatility classification (cold-start routing input).

Borrowed from intermittent-demand forecasting (inventory/supply-chain):
classify a time series into four quadrants by how *sparse* (ADI) and how
*spiky* (CV^2) it is, then route it to a different predictor/method per
quadrant. Here the "demand series" is a player's own game-by-game stat
series (pts/reb/ast) and the four buckets are a volatility-routing input
for :mod:`nba.eval.model_routing` -- separate from, and complementary to,
``nba.props.volatility``'s single coefficient-of-variation bucket (that
module answers "how volatile", this one additionally answers "how sparse",
which CV alone cannot distinguish: a player who scores 20 every game they
play but misses half the season on rest days has a very different failure
mode than one who scores 0-40 every single game).

## Adapting SB to NBA box scores (documented, not the inventory textbook case)

Classic SB defines, per SKU, over a fixed number of time periods:

- **ADI** (average inter-demand interval) = (number of periods) / (number
  of periods with nonzero demand) -- how sparse demand is;
- **CV^2** = (std / mean)^2 of the *nonzero* demand sizes -- how spiky
  demand is, when it occurs.

NBA adaptation: "periods" = the player's own game-by-game
``player_game_stats`` rows (one row per game they appear in at all, which
is already a mild filter vs. the full schedule -- see the "DATA REALITY"
note below); "nonzero demand" = a game where the stat (pts/reb/ast) is
> 0. Most games a player appears in have a nonzero stat (points
especially), so ADI for most rotation players will cluster near 1.0
("smooth" region of the ADI axis) -- ADI only climbs meaningfully for
players who log DNP-adjacent appearances (garbage-time cameos with 0
boxscore production) or very low-usage bench players who are more often
scoreless than not. This is the intended, documented behavior: ADI here
measures "how often does this player produce *at all* when they appear",
not minutes availability (that is ``nba.props.minutes``'s job).

**DATA REALITY (corrected 2026-10-08)**: DNPs are NOT invisible. The ingest
stores a DNP as a ``player_game_stats`` row with ``minutes`` NULL and
pts/reb/ast = 0 (not NULL), ~18.6% of all rows (docs/DNP_AUDIT_2026-10-08.md).
In the default (historical) series these rows are ordinary zero-demand
periods, so ADI largely measures DNP frequency, not stat sparsity: the
"intermittent"/"lumpy" buckets run a 25-36% target-game DNP rate vs 5-7% for
"smooth". ``played_only=True`` (default OFF so historical results reproduce)
restricts the prior series to games the player played (``minutes > 0``), so
ADI/CV^2 describe the stat GIVEN the player plays (availability belongs to
the minutes model); ``min_games`` and ``n_prior`` then count played games.
Target rows (including DNP target rows) still receive a bucket.

Standard SB cutoffs (unchanged from the inventory literature, not re-tuned
here): ``ADI_CUTOFF = 1.32``, ``CV2_CUTOFF = 0.49``.

    smooth:       ADI <  1.32, CV^2 <  0.49
    erratic:      ADI <  1.32, CV^2 >= 0.49
    intermittent: ADI >= 1.32, CV^2 <  0.49
    lumpy:        ADI >= 1.32, CV^2 >= 0.49

As-of discipline: every ``(game_id, player_id)`` row's ADI/CV^2 is computed
over that player's **strictly prior** games only (``ROWS BETWEEN
UNBOUNDED PRECEDING AND 1 PRECEDING``, same window-frame discipline as
every other as-of feature in this project -- full career-to-date history,
not a fixed lookback, since the classic SB statistic is defined over the
whole observed series). Fewer than ``min_games`` prior games, or fewer
than 2 nonzero prior games (ADI/CV^2 undefined), falls into
``INSUFFICIENT_HISTORY_BUCKET`` rather than being forced into a quadrant
on noise.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

ADI_CUTOFF = 1.32
CV2_CUTOFF = 0.49

INSUFFICIENT_HISTORY_BUCKET = "insufficient_history"
SB_CLASSES: list[str] = ["smooth", "erratic", "intermittent", "lumpy"]

_ALLOWED_COLUMNS = {"pts", "reb", "ast"}

_WINDOW = "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"


def _raw_prior_series(
    con: duckdb.DuckDBPyConnection, column: str, played_only: bool = False
) -> pl.DataFrame:
    """``(game_id, player_id, prior_vals)``: the player's full,
    strictly-prior game-by-game ``column`` series, ordered by
    ``(game_date, game_id)`` -- as-of by construction (the target row's own
    value is excluded via the window frame)."""
    if column not in _ALLOWED_COLUMNS:
        raise ValueError(f"column must be one of {_ALLOWED_COLUMNS}, got {column!r}")
    # played_only: non-played games become NULL in the series (dropped by the
    # caller), so they never count as zero demand; rows themselves are kept.
    val_sql = (
        f"CASE WHEN pgs.minutes IS NOT NULL AND pgs.minutes > 0 THEN pgs.{column} END"
        if played_only
        else f"pgs.{column}"
    )

    query = f"""
        WITH ordered AS (
            SELECT pgs.game_id, pgs.player_id, g.game_date, {val_sql} AS val
            FROM player_game_stats pgs
            JOIN games g USING (game_id)
        )
        SELECT
            game_id,
            player_id,
            list(val) OVER (
                PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW}
            ) AS prior_vals
        FROM ordered
        ORDER BY game_date, game_id, player_id
    """
    con.execute(query)
    rows = con.fetchall()
    return pl.DataFrame(
        rows,
        schema={"game_id": pl.Utf8, "player_id": pl.Int64, "prior_vals": pl.List(pl.Float64)},
        orient="row",
    )


def compute_adi_cv2(values: list[float], min_games: int) -> tuple[float, float, int]:
    """ADI and CV^2 of a strictly-prior game-by-game series.

    Returns ``(nan, nan, n)`` when there are fewer than ``min_games`` prior
    games, or fewer than 2 nonzero prior values (CV^2/ADI undefined on <2
    nonzero observations -- std of one value isn't meaningful).
    """
    n = len(values)
    if n < min_games:
        return float("nan"), float("nan"), n
    arr = np.asarray(values, dtype=float)
    nonzero = arr[arr > 0]
    n_events = len(nonzero)
    if n_events < 2:
        return float("nan"), float("nan"), n
    adi = float(n) / float(n_events)
    mean_v = float(nonzero.mean())
    if abs(mean_v) < 1e-9:
        return adi, float("nan"), n
    std_v = float(nonzero.std(ddof=1))
    cv2 = (std_v / mean_v) ** 2
    return adi, cv2, n


def classify_sb(adi: float, cv2: float) -> str:
    """Map (ADI, CV^2) to one of the four SB quadrants, or
    :data:`INSUFFICIENT_HISTORY_BUCKET` when either is NaN."""
    if adi != adi or cv2 != cv2:  # NaN check (NaN != NaN)
        return INSUFFICIENT_HISTORY_BUCKET
    if adi < ADI_CUTOFF:
        return "smooth" if cv2 < CV2_CUTOFF else "erratic"
    return "intermittent" if cv2 < CV2_CUTOFF else "lumpy"


def build_sb_classification(
    con: duckdb.DuckDBPyConnection, stat: str, min_games: int = 10, played_only: bool = False
) -> pl.DataFrame:
    """One row per (game, player): as-of ADI, CV^2, SB quadrant, and
    ``n_prior`` games for ``stat`` in ``{"pts", "reb", "ast"}``.

    This is the per-row builder; for routing analysis the caller typically
    wants only each player's *current* (most recent) bucket -- see
    ``nba.eval.model_routing`` for how buckets are consumed downstream.
    ``played_only``: see the module docstring (ADI over games played).
    """
    raw = _raw_prior_series(con, stat, played_only)
    prior_lists = raw.get_column("prior_vals").to_list()
    adis = np.empty(len(prior_lists), dtype=float)
    cv2s = np.empty(len(prior_lists), dtype=float)
    n_priors = np.empty(len(prior_lists), dtype=np.int64)
    for i, vals in enumerate(prior_lists):
        clean = [float(v) for v in vals if v is not None] if vals else []
        adi, cv2, n = compute_adi_cv2(clean, min_games)
        adis[i] = adi
        cv2s[i] = cv2
        n_priors[i] = n
    sb_class = [classify_sb(a, c) for a, c in zip(adis, cv2s, strict=True)]
    return raw.drop("prior_vals").with_columns(
        stat=pl.lit(stat),
        adi=pl.Series(adis),
        cv2=pl.Series(cv2s),
        sb_class=pl.Series(sb_class),
        n_prior=pl.Series(n_priors),
    )


@dataclass
class SBClassSummary:
    stat: str
    class_counts: dict[str, int]
    n_total: int


def summarize_sb_classes(df: pl.DataFrame) -> SBClassSummary:
    """Count of (game, player) rows per SB class -- a quick sanity check
    that a given stat/sample actually spreads across more than one
    bucket (small samples, e.g. the committed fixture, often collapse into
    a single bucket; report this honestly rather than letting a one-bucket
    "classification" masquerade as a real split)."""
    stat = df.get_column("stat")[0] if df.height else ""
    counts = (
        df.group_by("sb_class").agg(pl.len().alias("n")).sort("sb_class").to_dicts()
        if df.height
        else []
    )
    return SBClassSummary(
        stat=str(stat),
        class_counts={row["sb_class"]: int(row["n"]) for row in counts},
        n_total=df.height,
    )


__all__ = [
    "ADI_CUTOFF",
    "CV2_CUTOFF",
    "INSUFFICIENT_HISTORY_BUCKET",
    "SB_CLASSES",
    "SBClassSummary",
    "build_sb_classification",
    "classify_sb",
    "compute_adi_cv2",
    "summarize_sb_classes",
]
