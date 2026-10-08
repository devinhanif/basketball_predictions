"""Props-specific scoring: CRPS, threshold log loss, bias, interval coverage.

Reuses ``nba.eval.metrics`` for everything that's already generic there
(``log_loss``, ``bootstrap_ci``, ``calibration_curve``) rather than
re-implementing it. Only the genuinely prop-specific pieces (CRPS over a
full distribution, 80% interval coverage) live here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from nba.eval.metrics import (
    MIN_RELIABLE_N,
    CalibrationCurve,
    ConfidenceInterval,
    bootstrap_ci,
    calibration_curve,
    log_loss,
)
from nba.props.distributions import Distribution, batch_p_ge, batch_ppf

__all__ = [
    "CalibrationCurve",
    "ConfidenceInterval",
    "ThresholdCalibration",
    "avg_threshold_log_loss_per_game",
    "crps_array",
    "crps_from_dist",
    "interval_coverage",
    "mean_bias_ci",
    "paired_score_delta_ci",
    "pooled_threshold_ece",
    "threshold_log_loss_and_calibration",
]

#: Quadrature taus (midpoint rule) for the quantile-based CRPS
#: approximation below. One implementation, reused across every
#: distribution family (Normal/Gamma/NegBin/ZINB/MinutesHurdle) -- all
#: that's required is a working ``ppf``.
_N_QUANTILES = 199
_TAUS = (np.arange(1, _N_QUANTILES + 1) - 0.5) / _N_QUANTILES


def crps_from_dist(dist: Distribution, y: float) -> float:
    """Quantile-based CRPS approximation: ``CRPS ~= 2 * mean_tau(pinball_tau(y, F^-1(tau)))``.

    Exact in the limit of infinitely many quantiles; ``_N_QUANTILES=199``
    is more than enough precision for a backtest report. NaN/inf-safe:
    any non-finite quantile is dropped from the mean rather than
    propagating a NaN into the whole score.
    """
    qs = np.array([dist.ppf(float(t)) for t in _TAUS])
    finite = np.isfinite(qs)
    if not finite.any():
        return float("nan")
    taus = _TAUS[finite]
    qs = qs[finite]
    pinball = np.where(y >= qs, taus * (y - qs), (1 - taus) * (qs - y))
    return float(2.0 * pinball.mean())


def crps_array(dists: list[Distribution], y: np.ndarray) -> np.ndarray:
    """CRPS for every (distribution, outcome) pair -- vectorized.

    Uses :func:`nba.props.distributions.batch_ppf` to get every
    distribution's quantiles at every ``_TAUS`` tau in a handful of
    vectorized scipy calls (instead of ``crps_from_dist``'s
    one-``ppf()``-call-per-``(row, quantile)`` Python loop, which is the
    dominant cost of the whole props pipeline at 100k+ player-games).
    Falls back to the per-row loop for any family ``batch_ppf`` doesn't
    recognize (safety net; not expected to trigger on the stat/combo/
    baseline distributions this is called with today).
    """
    n = len(dists)
    if n == 0:
        return np.array([])
    qs = batch_ppf(dists, _TAUS)
    if qs is None:
        return np.array([crps_from_dist(d, float(yi)) for d, yi in zip(dists, y, strict=True)])
    y_arr = np.asarray(y, dtype=float)
    finite = np.isfinite(qs)
    taus_row = np.broadcast_to(_TAUS[None, :], qs.shape)
    y_col = y_arr[:, None]
    pinball = np.where(y_col >= qs, taus_row * (y_col - qs), (1 - taus_row) * (qs - y_col))
    pinball_masked = np.where(finite, pinball, 0.0)
    counts = finite.sum(axis=1)
    row_mean = np.full(n, np.nan)
    np.divide(pinball_masked.sum(axis=1), counts, out=row_mean, where=counts > 0)
    return 2.0 * row_mean


def avg_threshold_log_loss_per_game(
    dists: list[Distribution], y: np.ndarray, thresholds: list[int]
) -> np.ndarray:
    """Per-game log loss averaged over ``thresholds`` -- the per-game score
    array paired-bootstrap comparisons need (one scalar per game, not one
    aggregate over the whole sample)."""
    y_arr = np.asarray(y, dtype=float)
    n = len(y_arr)
    if n == 0 or not thresholds:
        return np.zeros(0)
    eps = 1e-15
    losses = np.zeros(n)
    for thresh in thresholds:
        p_arr = batch_p_ge(dists, float(thresh))
        if p_arr is None:
            p_arr = np.array([d.p_ge(float(thresh)) for d in dists])
        p = np.clip(p_arr, eps, 1 - eps)
        indicator = (y_arr >= thresh).astype(float)
        losses += -(indicator * np.log(p) + (1 - indicator) * np.log(1 - p))
    return losses / len(thresholds)


@dataclass
class ThresholdCalibration:
    threshold: float
    n: int
    log_loss: float
    calibration: CalibrationCurve


def threshold_log_loss_and_calibration(
    dists: list[Distribution], y: np.ndarray, thresholds: list[int]
) -> list[ThresholdCalibration]:
    """Per-threshold log loss + reliability curve on the event ``stat >= N``."""
    out = []
    y_arr = np.asarray(y, dtype=float)
    for n in thresholds:
        p = batch_p_ge(dists, float(n))
        if p is None:
            p = np.array([d.p_ge(float(n)) for d in dists])
        indicator = (y_arr >= n).astype(float)
        out.append(
            ThresholdCalibration(
                threshold=float(n),
                n=len(y_arr),
                log_loss=log_loss(indicator, p),
                calibration=calibration_curve(indicator, p),
            )
        )
    return out


def pooled_threshold_ece(threshold_results: list[ThresholdCalibration]) -> float:
    """Pool every threshold's (prediction, outcome) pairs into one ECE number."""
    usable = [t for t in threshold_results if t.n and t.calibration.ece == t.calibration.ece]
    total_n = sum(t.n for t in threshold_results if t.n)
    if not usable or not total_n:
        return float("nan")
    weighted = sum(t.calibration.ece * t.n for t in usable)
    return float(weighted / total_n)


#: Mirrors ``nba.eval.metrics._MAX_BOOTSTRAP_CELLS`` (not imported -- that
#: name is private to the win-probability bootstrap module, which this
#: clustered path deliberately does not touch). Same batching rationale:
#: caps the size of a single resample-index matrix per bootstrap batch so
#: large props runs (100k+ rows) don't build one giant ``(n_boot, G)``
#: array in memory at once.
_MAX_BOOTSTRAP_CELLS = 20_000_000


def _clustered_boot_means(
    values: np.ndarray,
    cluster_ids: np.ndarray,
    n_boot: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Vectorized cluster (block) bootstrap replicates of ``mean(values)``.

    Resamples whole clusters (e.g. ``game_id``) with replacement instead of
    individual rows, so every row of a drawn cluster is included together --
    this is what makes same-game teammate correlation show up as wider CIs
    instead of being averaged away as if each player-game were an
    independent draw (see docs/FDR_AUDIT_2026-10-08.md section 0).

    Implemented via per-cluster sufficient statistics (sum, count) rather
    than materializing resampled row arrays: for the mean statistic,
    ``mean(resampled rows) == sum(drawn cluster sums) / sum(drawn cluster
    counts)``, which is exact and lets the whole resample be a handful of
    vectorized gather/sum calls instead of a per-replicate concatenation
    loop. When every cluster is a singleton (one row per cluster, cluster
    ids sorted in the same order as the rows), this reduces to exactly the
    same arithmetic as :func:`nba.eval.metrics._batched_resample_stat` with
    ``stat_fn=np.mean`` -- see the degenerate-case unit test.
    """
    values = np.asarray(values, dtype=float)
    unique_clusters, inverse = np.unique(np.asarray(cluster_ids), return_inverse=True)
    g = len(unique_clusters)
    group_sums = np.zeros(g, dtype=float)
    group_counts = np.zeros(g, dtype=float)
    np.add.at(group_sums, inverse, values)
    np.add.at(group_counts, inverse, 1.0)

    out = np.empty(n_boot, dtype=float)
    if n_boot == 0:
        return out
    chunk = max(1, min(n_boot, _MAX_BOOTSTRAP_CELLS // max(g, 1)))
    start = 0
    while start < n_boot:
        b = min(chunk, n_boot - start)
        idx = rng.integers(0, g, size=(b, g))
        sums = group_sums[idx].sum(axis=1)
        counts = group_counts[idx].sum(axis=1)
        out[start : start + b] = sums / counts
        start += b
    return out


def _bootstrap_ci_clustered(
    values: np.ndarray,
    cluster_ids: np.ndarray,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> ConfidenceInterval:
    """Percentile cluster-bootstrap CI on ``mean(values)``, resampling whole
    ``cluster_ids`` groups (e.g. games) with replacement. Mirrors
    :func:`nba.eval.metrics.bootstrap_ci`'s contract exactly (same
    percentile behavior); only the resampling unit differs -- and,
    crucially, so does the "reliable CI" gate: a cluster bootstrap's
    effective sample size is the number of distinct *clusters* (``g``,
    e.g. distinct games), not the number of rows (``n``, e.g.
    player-games). A 5-game slate with 10 players each has ``n=50`` (which
    would clear a row-count gate with no warning) but only ``g=5``
    independent resampling units -- too few for the bootstrap distribution
    to approximate the true sampling distribution reliably. See
    docs/QA_AUDIT_2026-10-08.md item 2c; this gates on ``g``, not ``n``."""
    values = np.asarray(values, dtype=float)
    cluster_ids = np.asarray(cluster_ids)
    n = len(values)
    if n == 0:
        return ConfidenceInterval(
            point=float("nan"), lo=float("nan"), hi=float("nan"), n=0, note="no data"
        )
    if len(cluster_ids) != n:
        raise ValueError(f"cluster_ids length ({len(cluster_ids)}) must match values length ({n})")
    point = float(np.mean(values))
    if n == 1:
        return ConfidenceInterval(
            point=point,
            lo=point,
            hi=point,
            n=1,
            note="insufficient data for CI (n=1): point estimate only",
        )
    rng = np.random.default_rng(seed)
    boot_stats = _clustered_boot_means(values, cluster_ids, n_boot, rng)
    lo = float(np.quantile(boot_stats, alpha / 2))
    hi = float(np.quantile(boot_stats, 1 - alpha / 2))
    g = int(len(np.unique(cluster_ids)))
    note = (
        ""
        if g >= MIN_RELIABLE_N
        else f"insufficient data for a reliable CI (g={g} distinct clusters < "
        f"{MIN_RELIABLE_N}, despite n={n} rows -- cluster bootstrap reliability "
        "depends on cluster count, not row count)"
    )
    return ConfidenceInterval(point=point, lo=lo, hi=hi, n=n, note=note)


def mean_bias_ci(
    pred_mean: np.ndarray,
    y: np.ndarray,
    n_boot: int = 2000,
    seed: int = 0,
    cluster_ids: np.ndarray | None = None,
) -> ConfidenceInterval:
    """Bootstrap CI on mean(pred_mean - y) -- CLAUDE.md's +/-0.5 target metric.

    ``cluster_ids`` (optional, one id per row aligned with ``pred_mean``/
    ``y``, typically ``game_id``) switches to a cluster/block bootstrap that
    resamples whole games instead of individual player-games -- teammates in
    the same game are correlated (pace, blowouts, foul trouble), so
    resampling player-games independently understates the true standard
    error (docs/FDR_AUDIT_2026-10-08.md section 0). ``None`` (default)
    preserves the exact prior row-level behavior for backward compatibility.
    """
    bias = np.asarray(pred_mean, dtype=float) - np.asarray(y, dtype=float)
    if cluster_ids is None:
        return bootstrap_ci(bias, stat_fn=np.mean, n_boot=n_boot, seed=seed)
    return _bootstrap_ci_clustered(bias, cluster_ids, n_boot=n_boot, seed=seed)


def interval_coverage(q10: np.ndarray, q90: np.ndarray, y: np.ndarray) -> float:
    """Fraction of games where the actual value lands in [q10, q90] (target ~0.80)."""
    y_arr = np.asarray(y, dtype=float)
    inside = (y_arr >= np.asarray(q10, dtype=float)) & (y_arr <= np.asarray(q90, dtype=float))
    if len(inside) == 0:
        return float("nan")
    return float(np.mean(inside))


def paired_score_delta_ci(
    score_a: np.ndarray,
    score_b: np.ndarray,
    n_boot: int = 2000,
    seed: int = 0,
    cluster_ids: np.ndarray | None = None,
) -> ConfidenceInterval:
    """Bootstrap CI on mean(score_a - score_b) for two arrays of *already computed*
    per-player-game scores (e.g. CRPS_model vs CRPS_baseline) that are paired row
    for row (not necessarily paired by *game* -- several rows can share a game
    when there are multiple players).

    Lower-is-better convention (CRPS, log loss): a negative CI entirely
    below 0 means ``a`` beats ``b`` at this sample size.

    ``cluster_ids`` (optional, one id per row aligned with ``score_a``/
    ``score_b``, typically ``game_id``) switches to a cluster/block
    bootstrap that resamples whole games instead of individual rows --
    see :func:`mean_bias_ci` docstring for why this matters.  ``None``
    (default) preserves the exact prior row-level behavior for backward
    compatibility.
    """
    delta = np.asarray(score_a, dtype=float) - np.asarray(score_b, dtype=float)
    if cluster_ids is None:
        return bootstrap_ci(delta, stat_fn=np.mean, n_boot=n_boot, seed=seed)
    return _bootstrap_ci_clustered(delta, cluster_ids, n_boot=n_boot, seed=seed)
