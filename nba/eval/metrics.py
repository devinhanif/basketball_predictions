"""Calibration/scoring metrics for win-probability predictions.

Primary per CLAUDE.md: log loss and Brier score. Secondary: a reliability
(calibration) curve and expected calibration error (ECE). Every metric is
NaN-safe for tiny samples -- callers should check ``n`` before trusting a
number, and the eval report explicitly flags low-``n`` slices rather than
printing a misleadingly precise figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

EPS = 1e-15


def log_loss(y_true: np.ndarray, p: np.ndarray) -> float:
    """Binary log loss, clipped to avoid -inf on a perfect-looking fold."""
    if len(y_true) == 0:
        return float("nan")
    p_clipped = np.clip(p, EPS, 1 - EPS)
    y = np.asarray(y_true, dtype=float)
    losses = -(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped))
    return float(np.mean(losses))


def brier_score(y_true: np.ndarray, p: np.ndarray) -> float:
    if len(y_true) == 0:
        return float("nan")
    y = np.asarray(y_true, dtype=float)
    return float(np.mean((p - y) ** 2))


def accuracy(y_true: np.ndarray, p: np.ndarray) -> float:
    if len(y_true) == 0:
        return float("nan")
    y = np.asarray(y_true, dtype=float)
    pred = (p >= 0.5).astype(float)
    return float(np.mean(pred == y))


@dataclass
class CalibrationCurve:
    """Reliability-diagram bins: predicted-probability mean vs. observed rate."""

    bin_edges: list[float]
    bin_mean_pred: list[float]
    bin_observed_rate: list[float]
    bin_count: list[int]
    ece: float


def calibration_curve(y_true: np.ndarray, p: np.ndarray, n_bins: int = 10) -> CalibrationCurve:
    """Equal-width reliability diagram + expected calibration error.

    Degrades gracefully on tiny samples: bins with zero observations are
    simply omitted from the per-bin arrays (not fabricated as 0s), and if
    there isn't enough data for *any* non-empty bin pair this returns
    ``ece = nan`` rather than a misleading precise number.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    n_bins = max(1, min(n_bins, len(p))) if len(p) else n_bins
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    means: list[float] = []
    rates: list[float] = []
    counts: list[int] = []
    weighted_abs_gap = 0.0
    total = len(p)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p >= lo) & (p <= hi) if hi == edges[-1] else (p >= lo) & (p < hi)
        count = int(mask.sum())
        if count == 0:
            continue
        mean_pred = float(p[mask].mean())
        observed = float(y[mask].mean())
        means.append(mean_pred)
        rates.append(observed)
        counts.append(count)
        weighted_abs_gap += abs(mean_pred - observed) * count
    ece = float(weighted_abs_gap / total) if total else float("nan")
    return CalibrationCurve(
        bin_edges=list(edges),
        bin_mean_pred=means,
        bin_observed_rate=rates,
        bin_count=counts,
        ece=ece,
    )


@dataclass
class ConfidenceInterval:
    point: float
    lo: float
    hi: float
    n: int
    note: str = ""


#: Below this many paired observations, bootstrap CIs are reported but
#: flagged as unreliable rather than withheld (see report.md "insufficient
#: data for CI" notes referenced in CLAUDE.md milestone 4).
MIN_RELIABLE_N = 30


def bootstrap_ci(
    values: np.ndarray,
    stat_fn: object = np.mean,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> ConfidenceInterval:
    """Percentile bootstrap CI for ``stat_fn(values)``."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return ConfidenceInterval(
            point=float("nan"), lo=float("nan"), hi=float("nan"), n=0, note="no data"
        )
    point = float(stat_fn(values))  # type: ignore[operator]
    if n == 1:
        return ConfidenceInterval(
            point=point,
            lo=point,
            hi=point,
            n=1,
            note="insufficient data for CI (n=1): point estimate only",
        )
    rng = np.random.default_rng(seed)
    boot_stats = np.empty(n_boot)
    for i in range(n_boot):
        sample = rng.choice(values, size=n, replace=True)
        boot_stats[i] = stat_fn(sample)  # type: ignore[operator]
    lo = float(np.quantile(boot_stats, alpha / 2))
    hi = float(np.quantile(boot_stats, 1 - alpha / 2))
    note = "" if n >= MIN_RELIABLE_N else f"insufficient data for a reliable CI (n={n})"
    return ConfidenceInterval(point=point, lo=lo, hi=hi, n=n, note=note)


@dataclass
class PairedBootstrapComparison:
    """Paired per-game bootstrap comparison of a metric between two rungs.

    ``delta = metric(model_a) - metric(model_b)``; negative delta means
    model A has lower (better, for log loss/Brier) loss than B. CI
    excluding 0 is evidence of a real difference at the fixture's sample
    size -- but see ``note`` for tiny-``n`` caveats.
    """

    metric_name: str
    delta: ConfidenceInterval
    a_point: float
    b_point: float
    kept_a_over_b: bool = field(default=False)


def paired_bootstrap_compare(
    y_true: np.ndarray,
    p_a: np.ndarray,
    p_b: np.ndarray,
    metric_fn: object,
    metric_name: str,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> PairedBootstrapComparison:
    """Bootstrap the per-game difference in ``metric_fn`` between two models' predictions.

    Resamples game indices (not raw losses) so the pairing between a
    game's true outcome and each model's prediction on *that same game*
    is preserved every resample -- this is what makes the comparison
    "paired".
    """
    y = np.asarray(y_true, dtype=float)
    p_a = np.asarray(p_a, dtype=float)
    p_b = np.asarray(p_b, dtype=float)
    n = len(y)
    a_point = float(metric_fn(y, p_a))  # type: ignore[operator]
    b_point = float(metric_fn(y, p_b))  # type: ignore[operator]
    if n == 0:
        delta = ConfidenceInterval(
            point=float("nan"), lo=float("nan"), hi=float("nan"), n=0, note="no data"
        )
        return PairedBootstrapComparison(metric_name, delta, a_point, b_point, False)

    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        d_a = float(metric_fn(y[idx], p_a[idx]))  # type: ignore[operator]
        d_b = float(metric_fn(y[idx], p_b[idx]))  # type: ignore[operator]
        deltas[i] = d_a - d_b
    point_delta = a_point - b_point
    if n == 1:
        lo = hi = point_delta
        note = "insufficient data for CI (n=1): point estimate only"
    else:
        lo = float(np.quantile(deltas, alpha / 2))
        hi = float(np.quantile(deltas, 1 - alpha / 2))
        note = "" if n >= MIN_RELIABLE_N else f"insufficient data for a reliable CI (n={n})"
    delta_ci = ConfidenceInterval(point=point_delta, lo=lo, hi=hi, n=n, note=note)
    # "Kept" (per CLAUDE.md ladder rule): A beats B on this (lower-is-better)
    # metric with the CI strictly below 0. On tiny samples this will almost
    # never trigger -- which is the correct, honest behavior.
    kept = bool(hi < 0.0) if n >= MIN_RELIABLE_N else False
    return PairedBootstrapComparison(metric_name, delta_ci, a_point, b_point, kept)
