"""Metric sanity: finite, in-range, and converge to the right thing on
known toy data.
"""

from __future__ import annotations

import numpy as np

from nba.eval.metrics import (
    bootstrap_ci,
    brier_score,
    calibration_curve,
    log_loss,
    paired_bootstrap_compare,
)


def test_log_loss_and_brier_are_finite_and_in_range() -> None:
    y = np.array([1, 0, 1, 1, 0])
    p = np.array([0.9, 0.1, 0.8, 0.6, 0.4])
    ll = log_loss(y, p)
    b = brier_score(y, p)
    assert np.isfinite(ll)
    assert ll >= 0.0
    assert np.isfinite(b)
    assert 0.0 <= b <= 1.0


def test_log_loss_clips_extreme_probabilities_instead_of_inf() -> None:
    y = np.array([1, 0])
    p = np.array([0.0, 1.0])  # worst possible predictions
    ll = log_loss(y, p)
    assert np.isfinite(ll)
    assert ll > 0.0


def test_perfect_predictions_have_near_zero_loss() -> None:
    y = np.array([1, 0, 1, 0])
    p = np.array([0.98, 0.02, 0.98, 0.02])
    assert log_loss(y, p) < 0.05
    assert brier_score(y, p) < 0.01


def test_calibration_curve_ece_in_unit_range_and_bins_well_formed() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, size=200)
    y = (rng.uniform(0, 1, size=200) < p).astype(int)
    curve = calibration_curve(y, p, n_bins=10)
    assert 0.0 <= curve.ece <= 1.0
    assert sum(curve.bin_count) == 200
    assert len(curve.bin_mean_pred) == len(curve.bin_observed_rate) == len(curve.bin_count)


def test_calibration_curve_degrades_gracefully_on_empty_input() -> None:
    curve = calibration_curve(np.array([]), np.array([]))
    assert curve.ece != curve.ece or curve.ece == 0.0  # NaN or defined-as-zero, never crashes


def test_bootstrap_ci_contains_point_estimate() -> None:
    values = np.array([0.1, 0.2, 0.3, 0.4, 0.5] * 10)
    ci = bootstrap_ci(values, stat_fn=np.mean, n_boot=500, seed=1)
    assert ci.lo <= ci.point <= ci.hi


def test_bootstrap_ci_single_point_has_zero_width_and_a_note() -> None:
    ci = bootstrap_ci(np.array([0.42]), seed=1)
    assert ci.lo == ci.hi == 0.42
    assert "insufficient" in ci.note


def test_paired_bootstrap_prefers_better_calibrated_model_with_enough_data() -> None:
    rng = np.random.default_rng(0)
    n = 500
    y = rng.integers(0, 2, size=n)
    p_good = np.where(y == 1, 0.8, 0.2)
    p_bad = np.full(n, 0.5)
    cmp = paired_bootstrap_compare(y, p_good, p_bad, log_loss, "good_vs_bad", n_boot=500, seed=0)
    assert cmp.delta.hi < 0.0  # good model's log loss is reliably lower
    assert cmp.kept_a_over_b is True


def test_paired_bootstrap_does_not_claim_a_win_on_tiny_n() -> None:
    y = np.array([1])
    p_a = np.array([0.9])
    p_b = np.array([0.5])
    cmp = paired_bootstrap_compare(y, p_a, p_b, log_loss, "a_vs_b", n_boot=200, seed=0)
    assert cmp.kept_a_over_b is False
    assert "insufficient" in cmp.delta.note


# ---------------------------------------------------------------------------
# Performance-fix correctness guard: the vectorized bootstrap in
# nba/eval/metrics.py must produce *exactly* the same numbers as a plain
# Python ``for`` loop over replicates, for the same seed -- proof the speedup
# changed runtime, not results (see nba.eval.metrics._batched_resample_stat
# / _batched_paired_delta docstrings for why the per-replicate `rng.integers`
# draws line up one-for-one with a loop's).
# ---------------------------------------------------------------------------


def _reference_bootstrap_ci(values: np.ndarray, n_boot: int, seed: int, alpha: float = 0.05):
    """Plain Python loop over replicates -- the pre-vectorization behavior."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    rng = np.random.default_rng(seed)
    boot_stats = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        boot_stats[i] = np.mean(values[idx])
    point = float(np.mean(values))
    lo = float(np.quantile(boot_stats, alpha / 2))
    hi = float(np.quantile(boot_stats, 1 - alpha / 2))
    return point, lo, hi


def _reference_paired_delta(y, p_a, p_b, metric_fn, n_boot: int, seed: int, alpha: float = 0.05):
    """Plain Python loop over paired replicates -- the pre-vectorization behavior."""
    y = np.asarray(y, dtype=float)
    p_a = np.asarray(p_a, dtype=float)
    p_b = np.asarray(p_b, dtype=float)
    n = len(y)
    rng = np.random.default_rng(seed)
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        deltas[i] = metric_fn(y[idx], p_a[idx]) - metric_fn(y[idx], p_b[idx])
    lo = float(np.quantile(deltas, alpha / 2))
    hi = float(np.quantile(deltas, 1 - alpha / 2))
    return lo, hi


def test_vectorized_bootstrap_ci_matches_reference_loop_exactly() -> None:
    rng = np.random.default_rng(7)
    values = rng.normal(size=83)
    ci = bootstrap_ci(values, stat_fn=np.mean, n_boot=500, seed=42)
    ref_point, ref_lo, ref_hi = _reference_bootstrap_ci(values, n_boot=500, seed=42)
    assert ci.point == ref_point
    assert abs(ci.lo - ref_lo) < 1e-12
    assert abs(ci.hi - ref_hi) < 1e-12


def test_vectorized_bootstrap_ci_matches_reference_loop_with_batching() -> None:
    """Forces multiple internal batches (small ``_MAX_BOOTSTRAP_CELLS``
    relative to n * n_boot) to prove chunking doesn't change the result."""
    import nba.eval.metrics as metrics_mod

    rng = np.random.default_rng(3)
    values = rng.normal(size=1000)
    old_cap = metrics_mod._MAX_BOOTSTRAP_CELLS
    try:
        metrics_mod._MAX_BOOTSTRAP_CELLS = 5_000  # force many small batches
        ci = bootstrap_ci(values, stat_fn=np.mean, n_boot=300, seed=11)
    finally:
        metrics_mod._MAX_BOOTSTRAP_CELLS = old_cap
    ref_point, ref_lo, ref_hi = _reference_bootstrap_ci(values, n_boot=300, seed=11)
    assert ci.point == ref_point
    assert abs(ci.lo - ref_lo) < 1e-12
    assert abs(ci.hi - ref_hi) < 1e-12


def test_vectorized_paired_bootstrap_matches_reference_loop_exactly() -> None:
    rng = np.random.default_rng(9)
    n = 97
    y = rng.integers(0, 2, size=n).astype(float)
    p_a = np.clip(rng.uniform(size=n), 0.01, 0.99)
    p_b = np.clip(rng.uniform(size=n), 0.01, 0.99)
    cmp = paired_bootstrap_compare(y, p_a, p_b, log_loss, "a_vs_b", n_boot=400, seed=5)
    ref_lo, ref_hi = _reference_paired_delta(y, p_a, p_b, log_loss, n_boot=400, seed=5)
    assert abs(cmp.delta.lo - ref_lo) < 1e-9
    assert abs(cmp.delta.hi - ref_hi) < 1e-9


def test_bootstrap_ci_is_deterministic_given_same_seed() -> None:
    rng = np.random.default_rng(1)
    values = rng.normal(size=250)
    ci_a = bootstrap_ci(values, stat_fn=np.mean, n_boot=500, seed=123)
    ci_b = bootstrap_ci(values, stat_fn=np.mean, n_boot=500, seed=123)
    assert ci_a.lo == ci_b.lo
    assert ci_a.hi == ci_b.hi


def test_paired_bootstrap_is_deterministic_given_same_seed() -> None:
    rng = np.random.default_rng(2)
    n = 60
    y = rng.integers(0, 2, size=n).astype(float)
    p_a = np.clip(rng.uniform(size=n), 0.01, 0.99)
    p_b = np.clip(rng.uniform(size=n), 0.01, 0.99)
    cmp_a = paired_bootstrap_compare(y, p_a, p_b, log_loss, "a_vs_b", n_boot=300, seed=77)
    cmp_b = paired_bootstrap_compare(y, p_a, p_b, log_loss, "a_vs_b", n_boot=300, seed=77)
    assert cmp_a.delta.lo == cmp_b.delta.lo
    assert cmp_a.delta.hi == cmp_b.delta.hi


def test_log_loss_and_brier_axis_matches_scalar_per_row() -> None:
    """``axis=1`` reduction (used internally by the vectorized paired
    bootstrap) must agree with calling the scalar (``axis=None``) form
    row-by-row."""
    rng = np.random.default_rng(4)
    y = rng.integers(0, 2, size=(5, 11)).astype(float)
    p = np.clip(rng.uniform(size=(5, 11)), 0.01, 0.99)
    ll_batched = log_loss(y, p, axis=1)
    brier_batched = brier_score(y, p, axis=1)
    for i in range(5):
        assert abs(ll_batched[i] - log_loss(y[i], p[i])) < 1e-12
        assert abs(brier_batched[i] - brier_score(y[i], p[i])) < 1e-12
