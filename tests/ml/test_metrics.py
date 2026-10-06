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
