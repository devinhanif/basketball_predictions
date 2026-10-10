"""Calibration/scoring metrics for win-probability predictions.

Primary per CLAUDE.md: log loss and Brier score. Secondary: a reliability
(calibration) curve and expected calibration error (ECE). Every metric is
NaN-safe for tiny samples -- callers should check ``n`` before trusting a
number, and the eval report explicitly flags low-``n`` slices rather than
printing a misleadingly precise figure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, overload

import numpy as np

EPS = 1e-15


@overload
def log_loss(y_true: np.ndarray, p: np.ndarray, axis: None = None) -> float: ...
@overload
def log_loss(y_true: np.ndarray, p: np.ndarray, axis: int) -> np.ndarray: ...


def log_loss(y_true: np.ndarray, p: np.ndarray, axis: int | None = None) -> float | np.ndarray:
    """Binary log loss, clipped to avoid -inf on a perfect-looking fold.

    ``axis=None`` (default, every existing call site) reduces over the
    whole array and returns a ``float`` -- unchanged behavior. Passing an
    ``axis`` reduces only along that axis and returns an ``ndarray``; this
    is what lets the bootstrap below evaluate the metric for a whole batch
    of resampled replicates (shape ``(n_boot, n)``) in one vectorized call
    instead of ``n_boot`` individual Python-level calls.
    """
    y = np.asarray(y_true, dtype=float)
    pr = np.asarray(p, dtype=float)
    if axis is None and y.size == 0:
        return float("nan")
    p_clipped = np.clip(pr, EPS, 1 - EPS)
    losses = -(y * np.log(p_clipped) + (1 - y) * np.log(1 - p_clipped))
    if axis is None:
        return float(np.mean(losses))
    return np.mean(losses, axis=axis)


@overload
def brier_score(y_true: np.ndarray, p: np.ndarray, axis: None = None) -> float: ...
@overload
def brier_score(y_true: np.ndarray, p: np.ndarray, axis: int) -> np.ndarray: ...


def brier_score(y_true: np.ndarray, p: np.ndarray, axis: int | None = None) -> float | np.ndarray:
    """Brier score; see :func:`log_loss` for the ``axis`` contract."""
    y = np.asarray(y_true, dtype=float)
    pr = np.asarray(p, dtype=float)
    if axis is None and y.size == 0:
        return float("nan")
    sq = (pr - y) ** 2
    if axis is None:
        return float(np.mean(sq))
    return np.mean(sq, axis=axis)


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

#: Caps the size (in float64 elements) of a single resample-index /
#: resampled-values matrix built per bootstrap batch. A full ``(n_boot, n)``
#: matrix for ``n_boot=1000, n=138_000`` is ~1.4e8 elements (~1.1GB as
#: float64) -- too much to hold at once alongside the DB connection and the
#: rest of the props pipeline's arrays. Replicates are instead processed in
#: batches of ``max(1, _MAX_BOOTSTRAP_CELLS // n)`` rows at a time, each
#: batch still fully vectorized (no per-replicate Python loop); only the
#: *number of batches* scales with n_boot, not the per-replicate cost.
#: 20M elements is ~160MB per batch -- small enough to not pressure memory
#: even with several bootstrap calls alive at once, large enough that most
#: real calls (n_boot<=2000, n<=138_000) finish in 1-7 batches.
_MAX_BOOTSTRAP_CELLS = 20_000_000


def _batched_resample_stat(
    values: np.ndarray,
    stat_fn: Callable[..., Any],
    n_boot: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Vectorized percentile-bootstrap replicates of ``stat_fn(values)``.

    Draws the full ``(batch, n)`` resample-index matrix for each batch in
    one call (``rng.integers``), gathers it in one vectorized indexing op,
    and evaluates ``stat_fn`` once per batch along ``axis=1`` -- no Python
    ``for`` loop over individual bootstrap replicates. Falls back to a
    per-row Python loop *within a batch* only if ``stat_fn`` does not
    support an ``axis`` keyword (every call site in this codebase uses
    ``np.mean``, which does; the fallback exists purely so a future caller
    passing an exotic ``stat_fn`` degrades to "slow but correct" instead of
    raising).

    Batching into chunks of at most ``_MAX_BOOTSTRAP_CELLS // n`` rows is
    numerically identical to drawing the whole ``(n_boot, n)`` matrix in a
    single ``rng.integers`` call: a ``Generator``'s output is a
    deterministic function of its consumed-so-far state, independent of
    how a given number of draws is chunked across calls.
    """
    n = len(values)
    out = np.empty(n_boot, dtype=float)
    if n_boot == 0:
        return out
    chunk = max(1, min(n_boot, _MAX_BOOTSTRAP_CELLS // max(n, 1)))
    start = 0
    while start < n_boot:
        b = min(chunk, n_boot - start)
        idx = rng.integers(0, n, size=(b, n))
        resampled = values[idx]
        try:
            chunk_stats = np.asarray(stat_fn(resampled, axis=1), dtype=float)
            if chunk_stats.shape != (b,):
                raise TypeError("stat_fn did not reduce along axis=1 to one value per row")
        except TypeError:
            chunk_stats = np.array(
                [float(stat_fn(resampled[i])) for i in range(b)],
                dtype=float,
            )
        out[start : start + b] = chunk_stats
        start += b
    return out


def bootstrap_ci(
    values: np.ndarray,
    stat_fn: Callable[..., Any] = np.mean,
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
    point = float(stat_fn(values))
    if n == 1:
        return ConfidenceInterval(
            point=point,
            lo=point,
            hi=point,
            n=1,
            note="insufficient data for CI (n=1): point estimate only",
        )
    rng = np.random.default_rng(seed)
    boot_stats = _batched_resample_stat(values, stat_fn, n_boot, rng)
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


def _batched_paired_delta(
    y: np.ndarray,
    p_a: np.ndarray,
    p_b: np.ndarray,
    metric_fn: Callable[..., Any],
    n_boot: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Vectorized paired bootstrap of ``metric_fn(y, p_a) - metric_fn(y, p_b)``.

    Same batching/determinism contract as :func:`_batched_resample_stat`,
    specialized to the *paired* case: one shared ``(batch, n)`` index
    matrix resamples ``y``, ``p_a``, and ``p_b`` together per batch
    (preserving the game-level pairing every replicate), and ``metric_fn``
    is evaluated once per batch via its ``axis`` keyword if it has one
    (``log_loss`` and ``brier_score`` both do). Falls back to a per-row
    Python loop within a batch for a ``metric_fn`` that doesn't support
    ``axis`` -- correct but slow, and not hit by any current call site.
    """
    n = len(y)
    out = np.empty(n_boot, dtype=float)
    if n_boot == 0:
        return out
    chunk = max(1, min(n_boot, _MAX_BOOTSTRAP_CELLS // max(n, 1)))
    start = 0
    while start < n_boot:
        b = min(chunk, n_boot - start)
        idx = rng.integers(0, n, size=(b, n))
        y_m, pa_m, pb_m = y[idx], p_a[idx], p_b[idx]
        try:
            a_vals = np.asarray(metric_fn(y_m, pa_m, axis=1), dtype=float)
            b_vals = np.asarray(metric_fn(y_m, pb_m, axis=1), dtype=float)
            if a_vals.shape != (b,) or b_vals.shape != (b,):
                raise TypeError("metric_fn did not reduce along axis=1 to one value per row")
            out[start : start + b] = a_vals - b_vals
        except TypeError:
            for i in range(b):
                out[start + i] = float(metric_fn(y_m[i], pa_m[i])) - float(
                    metric_fn(y_m[i], pb_m[i])
                )
        start += b
    return out


def paired_bootstrap_compare(
    y_true: np.ndarray,
    p_a: np.ndarray,
    p_b: np.ndarray,
    metric_fn: Callable[..., Any],
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
    a_point = float(metric_fn(y, p_a))
    b_point = float(metric_fn(y, p_b))
    if n == 0:
        delta = ConfidenceInterval(
            point=float("nan"), lo=float("nan"), hi=float("nan"), n=0, note="no data"
        )
        return PairedBootstrapComparison(metric_name, delta, a_point, b_point, False)

    rng = np.random.default_rng(seed)
    deltas = _batched_paired_delta(y, p_a, p_b, metric_fn, n_boot, rng)
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
