"""Split-conformal prediction intervals (CLAUDE.md "conformal prediction":
"train quantile regression ... and wrap with split-conformal prediction so
intervals have honest coverage without distributional assumptions").

This package's quantile heads are the distribution families' ``ppf(0.10)``/
``ppf(0.90)`` rather than a separately-trained quantile regressor, but the
conformal wrapper is identical either way: it only needs a pair of
quantile predictions and the realized outcome.

Design: a **chronological** calibration/test split (never random -- CLAUDE.md
"All validation is walk-forward"). The calibration half is always the
*earlier* games; the correction learned there is applied only to the later
("test") half, so no calibration score ever touches a point it will be used
to correct. The nonconformity score is the CQR (conformalized quantile
regression) score ``max(q10 - y, y - q90)``, and the correction uses the
finite-sample-exact quantile level from Lei et al. (2018),
``ceil((n+1)(1-alpha)) / n``, not the naive ``(1-alpha)`` quantile.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def chronological_split(dates: np.ndarray, cal_frac: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Boolean (calibration_mask, test_mask) splitting ``dates`` into an
    earlier calibration half and a later test half -- never a random split.
    Ties are broken by the stable sort order, so index order among equal
    dates is preserved deterministically.
    """
    dates = np.asarray(dates)
    n = len(dates)
    order = np.argsort(dates, kind="stable")
    cut = int(np.floor(n * cal_frac))
    cut = min(max(cut, 0), n)
    cal_idx = order[:cut]
    test_idx = order[cut:]
    cal_mask = np.zeros(n, dtype=bool)
    test_mask = np.zeros(n, dtype=bool)
    cal_mask[cal_idx] = True
    test_mask[test_idx] = True
    return cal_mask, test_mask


def conformal_adjustment(
    q10_cal: np.ndarray, q90_cal: np.ndarray, y_cal: np.ndarray, alpha: float = 0.2
) -> float:
    """Split-conformal correction for interval ``[q10, q90]``.

    Nonconformity score ``max(q10 - y, y - q90)`` (>=0 iff y falls outside
    the raw interval, the standard CQR score). Returns the
    ``ceil((n+1)(1-alpha))/n``-quantile of the calibration scores, clipped
    to the largest observed score at the top of the valid range and floored
    at 0 (never *shrinks* a parametric interval below itself). Returns 0.0
    (no-op) when there is no calibration data.
    """
    q10_cal = np.asarray(q10_cal, dtype=float)
    q90_cal = np.asarray(q90_cal, dtype=float)
    y_cal = np.asarray(y_cal, dtype=float)
    n = len(y_cal)
    if n == 0:
        return 0.0
    scores = np.maximum(q10_cal - y_cal, y_cal - q90_cal)
    level = min(1.0, float(np.ceil((n + 1) * (1 - alpha))) / n)
    return float(max(np.quantile(scores, level), 0.0))


def apply_conformal(
    q10: np.ndarray, q90: np.ndarray, adjustment: float
) -> tuple[np.ndarray, np.ndarray]:
    """Widen ``[q10, q90]`` by the conformal ``adjustment`` on each side."""
    lo = np.asarray(q10, dtype=float) - adjustment
    hi = np.asarray(q90, dtype=float) + adjustment
    return lo, hi


def interval_coverage(lo: np.ndarray, hi: np.ndarray, y: np.ndarray) -> float:
    """Fraction of ``y`` landing inside ``[lo, hi]``."""
    y = np.asarray(y, dtype=float)
    if len(y) == 0:
        return float("nan")
    inside = (y >= np.asarray(lo, dtype=float)) & (y <= np.asarray(hi, dtype=float))
    return float(np.mean(inside))


@dataclass
class ConformalResult:
    stat: str
    n_cal: int
    n_test: int
    adjustment: float
    coverage_parametric: float
    coverage_conformal: float
    note: str


#: Below this many test-split rows, coverage numbers are reported but
#: flagged as not statistically meaningful (same bar as the rest of
#: ``nba/props/`` -- see ``nba.props.run.MIN_RELIABLE_N``).
MIN_RELIABLE_TEST_N = 500


def evaluate_conformal(
    q10: np.ndarray,
    q90: np.ndarray,
    y: np.ndarray,
    dates: np.ndarray,
    stat: str,
    alpha: float = 0.2,
    cal_frac: float = 0.5,
) -> ConformalResult:
    """End-to-end split-conformal evaluation for one stat's q10/q90 predictions.

    Splits chronologically, fits the adjustment on the calibration half,
    and reports both the raw parametric 80% coverage and the
    conformal-adjusted coverage -- both measured on the *same* (test-half)
    rows, so they are directly comparable.
    """
    q10 = np.asarray(q10, dtype=float)
    q90 = np.asarray(q90, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n == 0:
        return ConformalResult(stat, 0, 0, 0.0, float("nan"), float("nan"), "no data")

    cal_mask, test_mask = chronological_split(dates, cal_frac)
    n_cal = int(cal_mask.sum())
    n_test = int(test_mask.sum())
    adjustment = conformal_adjustment(q10[cal_mask], q90[cal_mask], y[cal_mask], alpha=alpha)
    lo_test, hi_test = apply_conformal(q10[test_mask], q90[test_mask], adjustment)

    cov_param = interval_coverage(q10[test_mask], q90[test_mask], y[test_mask])
    cov_conf = interval_coverage(lo_test, hi_test, y[test_mask])
    note = (
        f"only {n_test} test-split player-games (<{MIN_RELIABLE_TEST_N}); conformal and "
        "parametric coverage numbers are NOT statistically meaningful at this sample size."
        if n_test < MIN_RELIABLE_TEST_N
        else ""
    )
    return ConformalResult(stat, n_cal, n_test, adjustment, cov_param, cov_conf, note)
