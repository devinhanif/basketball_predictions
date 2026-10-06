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


def mean_bias_ci(
    pred_mean: np.ndarray, y: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> ConfidenceInterval:
    """Bootstrap CI on mean(pred_mean - y) -- CLAUDE.md's +/-0.5 target metric."""
    bias = np.asarray(pred_mean, dtype=float) - np.asarray(y, dtype=float)
    return bootstrap_ci(bias, stat_fn=np.mean, n_boot=n_boot, seed=seed)


def interval_coverage(q10: np.ndarray, q90: np.ndarray, y: np.ndarray) -> float:
    """Fraction of games where the actual value lands in [q10, q90] (target ~0.80)."""
    y_arr = np.asarray(y, dtype=float)
    inside = (y_arr >= np.asarray(q10, dtype=float)) & (y_arr <= np.asarray(q90, dtype=float))
    if len(inside) == 0:
        return float("nan")
    return float(np.mean(inside))


def paired_score_delta_ci(
    score_a: np.ndarray, score_b: np.ndarray, n_boot: int = 2000, seed: int = 0
) -> ConfidenceInterval:
    """Bootstrap CI on mean(score_a - score_b) for two arrays of *already computed*
    per-game scores (e.g. CRPS_model vs CRPS_baseline) that are paired by game.

    Lower-is-better convention (CRPS, log loss): a negative CI entirely
    below 0 means ``a`` beats ``b`` at this sample size.
    """
    delta = np.asarray(score_a, dtype=float) - np.asarray(score_b, dtype=float)
    return bootstrap_ci(delta, stat_fn=np.mean, n_boot=n_boot, seed=seed)
