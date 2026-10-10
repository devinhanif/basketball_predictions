"""Tests for the exponential-tilt possession-outcome model."""

from __future__ import annotations

import numpy as np

from research.sim.possession_model import (
    BASE_MEAN_PPP,
    BASE_OUTCOME_PROBS,
    BASE_OUTCOME_SUPPORT,
    tilt_outcome_probs,
    tilted_mean,
)


def test_base_outcome_probs_sum_to_one_and_are_nonnegative() -> None:
    assert np.isclose(BASE_OUTCOME_PROBS.sum(), 1.0)
    assert np.all(BASE_OUTCOME_PROBS >= 0.0)


def test_base_mean_ppp_matches_documented_league_average() -> None:
    # Loosely: should be in the realistic ORtg-per-possession range.
    assert 1.0 < BASE_MEAN_PPP < 1.3


def test_tilt_to_base_mean_is_near_identity() -> None:
    probs = tilt_outcome_probs(BASE_MEAN_PPP)
    np.testing.assert_allclose(probs, BASE_OUTCOME_PROBS, atol=1e-3)


def test_tilt_hits_target_mean_exactly() -> None:
    for target in (0.9, 1.0, 1.1, 1.2, 1.4):
        probs = tilt_outcome_probs(target)
        achieved = float(np.sum(BASE_OUTCOME_SUPPORT * probs))
        assert abs(achieved - target) < 1e-6


def test_tilted_probs_stay_nonnegative_and_sum_to_one() -> None:
    for target in (0.5, 0.8, 1.0, 1.3, 1.6, 2.0):
        probs = tilt_outcome_probs(target)
        assert np.all(probs >= 0.0)
        assert np.isclose(probs.sum(), 1.0)


def test_higher_target_mean_shifts_mass_toward_higher_scoring_outcomes() -> None:
    low = tilt_outcome_probs(0.8)
    high = tilt_outcome_probs(1.4)
    # P(3-point outcome) should rise and P(0-point outcome) should fall.
    idx_3 = int(np.where(BASE_OUTCOME_SUPPORT == 3)[0][0])
    idx_0 = int(np.where(BASE_OUTCOME_SUPPORT == 0)[0][0])
    assert high[idx_3] > low[idx_3]
    assert high[idx_0] < low[idx_0]


def test_tilted_mean_is_monotonic_in_theta() -> None:
    thetas = np.linspace(-2.0, 2.0, 9)
    means = [tilted_mean(t, BASE_OUTCOME_SUPPORT, BASE_OUTCOME_PROBS) for t in thetas]
    assert all(a < b for a, b in zip(means, means[1:], strict=False))


def test_extreme_target_does_not_raise() -> None:
    # Targets at/beyond the support edges are clamped, not fatal.
    probs_hi = tilt_outcome_probs(100.0)
    probs_lo = tilt_outcome_probs(-100.0)
    assert np.isclose(probs_hi.sum(), 1.0)
    assert np.isclose(probs_lo.sum(), 1.0)
