"""Volatility-bucket slicing for the props calibration report (CLAUDE.md
Phase 2 ask: "let us test the hypothesis 'the model's edge concentrates in
high-volatility players'").

Mirrors the win-probability cold-start-bucket pattern in
``nba.eval.slices.cold_start_bucket_slice`` / ``nba.eval.report``, but keyed
on a player's own recent **stat volatility** rather than career possession
count, and scoped to ``nba/props/`` (this package does not import or modify
``nba.eval``).

Volatility metric: coefficient of variation (``SD / mean``) of a stat (or,
as an alternate variant, of minutes) over the player's **strictly prior**
``lookback_games`` games -- same as-of discipline as
``nba.props.minutes.build_minutes_features``: the SQL window frame is
``ROWS BETWEEN lookback_games PRECEDING AND 1 PRECEDING``, ordered by
``(game_date, game_id)``, so the target game's own value is never read.
Guarded against:

- divide-by-zero (mean ~= 0 -> CV is undefined, reported as NaN/"unknown"
  rather than +/-inf);
- tiny samples (fewer than ``min_games_for_cv`` strictly-prior games ->
  CV is undefined, row falls in the ``insufficient_history`` bucket rather
  than being forced into low/high on noise).
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.eval.metrics import ConfidenceInterval, bootstrap_ci
from nba.props.config import VolatilityBucketSpec, VolatilityConfig
from nba.props.metrics import paired_score_delta_ci

#: Columns the volatility CV is allowed to be computed over (whitelisted so
#: the stat name is never interpolated into SQL from an untrusted source).
_ALLOWED_COLUMNS = {"pts", "reb", "ast", "fg3m", "minutes"}

#: Bucket label used when a player-game has fewer than
#: ``min_games_for_cv`` strictly-prior games (or an undefined CV because the
#: prior mean is ~0) -- never silently forced into "low".
INSUFFICIENT_HISTORY_BUCKET = "insufficient_history"


def _cv_from_prior_values(values: list[float], min_games_for_cv: int) -> tuple[float, int]:
    """Coefficient of variation of a list of strictly-prior values.

    Returns ``(nan, n)`` when ``n < min_games_for_cv`` (too few games to
    trust a CV) or when the prior mean is ~0 (CV undefined -- avoids a
    divide-by-zero blowing up into +/-inf).
    """
    n = len(values)
    if n < min_games_for_cv:
        return float("nan"), n
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    if abs(mean) < 1e-9:
        return float("nan"), n
    std = float(arr.std(ddof=1)) if n >= 2 else 0.0
    return std / mean, n


def _raw_prior_values(
    con: duckdb.DuckDBPyConnection, column: str, lookback_games: int
) -> pl.DataFrame:
    """``(game_id, player_id, prior_vals)`` where ``prior_vals`` is the list
    of ``column`` values over the player's strictly-prior ``lookback_games``
    games (as-of; the target game's own row is excluded by construction --
    see module docstring). No leakage-sensitive logic lives below this
    function; everything else just reduces the already-as-of list.
    """
    if column not in _ALLOWED_COLUMNS:
        raise ValueError(f"column must be one of {_ALLOWED_COLUMNS}, got {column!r}")
    if lookback_games < 1:
        raise ValueError("lookback_games must be >= 1")

    query = f"""
        WITH ordered AS (
            SELECT pgs.game_id, pgs.player_id, g.game_date, pgs.{column} AS val
            FROM player_game_stats pgs
            JOIN games g USING (game_id)
        )
        SELECT
            game_id,
            player_id,
            list(val) OVER (
                PARTITION BY player_id ORDER BY game_date, game_id
                ROWS BETWEEN {lookback_games} PRECEDING AND 1 PRECEDING
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


def build_volatility_features(
    con: duckdb.DuckDBPyConnection, column: str, lookback_games: int, min_games_for_cv: int
) -> pl.DataFrame:
    """One row per (game, player): as-of coefficient-of-variation of
    ``column`` over the player's strictly-prior ``lookback_games`` games,
    plus ``n_prior`` (how many prior games fed it, for the bucketing /
    sample-size guard downstream)."""
    raw = _raw_prior_values(con, column, lookback_games)
    prior_lists = raw.select("prior_vals").to_series().to_list()
    cvs = np.empty(len(prior_lists), dtype=float)
    n_prior = np.empty(len(prior_lists), dtype=np.int64)
    for i, vals in enumerate(prior_lists):
        arr = [float(v) for v in vals if v is not None] if vals else []
        cv, n = _cv_from_prior_values(arr, min_games_for_cv)
        cvs[i] = cv
        n_prior[i] = n
    return raw.drop("prior_vals").with_columns(cv=pl.Series(cvs), n_prior=pl.Series(n_prior))


def assign_buckets(cv: np.ndarray, buckets: list[VolatilityBucketSpec]) -> np.ndarray:
    """Map each (possibly NaN) CV value to a bucket name.

    NaN (undefined CV: too little history or a ~0 prior mean) always maps
    to :data:`INSUFFICIENT_HISTORY_BUCKET`, never into ``low``/``high`` on
    noise. Otherwise the first bucket spec whose ``[lo, hi)`` interval
    contains the value wins; a value matching no configured bucket also
    falls back to :data:`INSUFFICIENT_HISTORY_BUCKET` (should not happen
    with the default full-coverage buckets, but fails safe rather than
    raising mid-report).
    """
    cv = np.asarray(cv, dtype=float)
    labels = np.full(cv.shape, INSUFFICIENT_HISTORY_BUCKET, dtype=object)
    for i, v in enumerate(cv):
        if v != v:  # NaN
            continue
        for spec in buckets:
            if spec.lo <= v < spec.hi:
                labels[i] = spec.name
                break
    return labels


def quantile_buckets(cv: np.ndarray) -> list[VolatilityBucketSpec]:
    """Median-split buckets computed from the *observed* (non-NaN) CV
    distribution in this sample -- an alternative to fixed cutoffs,
    selected via ``VolatilityConfig.use_quantile_cutoffs``. Falls back to
    the module default fixed cutoffs when fewer than 2 finite CVs are
    available (not enough data to estimate a median)."""
    cv = np.asarray(cv, dtype=float)
    finite = cv[np.isfinite(cv)]
    if len(finite) < 2:
        return list(VolatilityConfig().buckets)
    median = float(np.median(finite))
    return [
        VolatilityBucketSpec(name="low", lo=float(finite.min()), hi=median),
        VolatilityBucketSpec(name="high", lo=median, hi=float(finite.max()) + 1e-9),
    ]


@dataclass
class VolatilityBucketResult:
    stat: str
    bucket: str
    cv_variant: str
    n: int
    sufficient: bool
    note: str
    mean_bias: ConfidenceInterval
    coverage: float
    crps_vs_season_avg: ConfidenceInterval
    crps_vs_last10_avg: ConfidenceInterval
    log_loss_vs_season_avg: ConfidenceInterval
    log_loss_vs_last10_avg: ConfidenceInterval


def _insufficient_ci(n: int, note: str) -> ConfidenceInterval:
    return ConfidenceInterval(point=float("nan"), lo=float("nan"), hi=float("nan"), n=n, note=note)


def evaluate_volatility_buckets(
    stat: str,
    cv_variant: str,
    bucket_labels: np.ndarray,
    pred_mean: np.ndarray,
    q10: np.ndarray,
    q90: np.ndarray,
    y: np.ndarray,
    crps_model: np.ndarray,
    crps_season: np.ndarray,
    crps_last10: np.ndarray,
    ll_model: np.ndarray,
    ll_season: np.ndarray,
    ll_last10: np.ndarray,
    buckets: list[VolatilityBucketSpec],
    min_bucket_n: int,
    n_boot: int,
    seed: int,
) -> list[VolatilityBucketResult]:
    """Per-bucket CRPS/log-loss-vs-baseline deltas (existing paired
    per-game bootstrap), mean-bias CI, and 80% coverage -- same metrics as
    the top-level per-stat report, sliced by volatility bucket.

    Buckets with fewer than ``min_bucket_n`` player-games get an explicit
    "insufficient data for a trustworthy CI" note and NaN-point
    ConfidenceIntervals instead of a numeric point estimate that would
    look precise but isn't (CLAUDE.md milestone 7, point 4's honesty
    guard, applied per-bucket).
    """
    bucket_names = [INSUFFICIENT_HISTORY_BUCKET, *(b.name for b in buckets)]
    results: list[VolatilityBucketResult] = []
    for name in bucket_names:
        mask = bucket_labels == name
        n = int(mask.sum())
        if n < min_bucket_n:
            note = f"insufficient data for a trustworthy CI (n={n} < min_bucket_n={min_bucket_n})"
            results.append(
                VolatilityBucketResult(
                    stat=stat,
                    bucket=name,
                    cv_variant=cv_variant,
                    n=n,
                    sufficient=False,
                    note=note,
                    mean_bias=_insufficient_ci(n, note),
                    coverage=float("nan"),
                    crps_vs_season_avg=_insufficient_ci(n, note),
                    crps_vs_last10_avg=_insufficient_ci(n, note),
                    log_loss_vs_season_avg=_insufficient_ci(n, note),
                    log_loss_vs_last10_avg=_insufficient_ci(n, note),
                )
            )
            continue

        bias_ci = bootstrap_ci(pred_mean[mask] - y[mask], stat_fn=np.mean, n_boot=n_boot, seed=seed)
        inside = (y[mask] >= q10[mask]) & (y[mask] <= q90[mask])
        coverage = float(np.mean(inside)) if n else float("nan")
        crps_vs_season = paired_score_delta_ci(
            crps_model[mask], crps_season[mask], n_boot=n_boot, seed=seed
        )
        crps_vs_last10 = paired_score_delta_ci(
            crps_model[mask], crps_last10[mask], n_boot=n_boot, seed=seed
        )
        ll_vs_season = paired_score_delta_ci(
            ll_model[mask], ll_season[mask], n_boot=n_boot, seed=seed
        )
        ll_vs_last10 = paired_score_delta_ci(
            ll_model[mask], ll_last10[mask], n_boot=n_boot, seed=seed
        )

        results.append(
            VolatilityBucketResult(
                stat=stat,
                bucket=name,
                cv_variant=cv_variant,
                n=n,
                sufficient=True,
                note="",
                mean_bias=bias_ci,
                coverage=coverage,
                crps_vs_season_avg=crps_vs_season,
                crps_vs_last10_avg=crps_vs_last10,
                log_loss_vs_season_avg=ll_vs_season,
                log_loss_vs_last10_avg=ll_vs_last10,
            )
        )
    return results
