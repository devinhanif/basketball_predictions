"""Unit tests for cold-start method 4 (time-decay carryover + age curve)."""

from __future__ import annotations

import numpy as np

from nba.coldstart.carryover import age_curve_multiplier, carryover_blend


def test_higher_decay_weight_moves_blend_toward_prior() -> None:
    last = 0.9
    prior = 0.2
    blend_low = carryover_blend(last, prior, decay_weight=0.1)
    blend_mid = carryover_blend(last, prior, decay_weight=0.5)
    blend_high = carryover_blend(last, prior, decay_weight=0.9)
    # strictly monotonic toward prior as decay_weight increases
    assert (
        abs(float(blend_low) - prior)
        > abs(float(blend_mid) - prior)
        > abs(float(blend_high) - prior)
    )


def test_decay_weight_zero_and_one_are_the_extremes() -> None:
    last = 0.9
    prior = 0.2
    assert np.isclose(carryover_blend(last, prior, decay_weight=0.0), last)
    assert np.isclose(carryover_blend(last, prior, decay_weight=1.0), prior)


def test_decay_weight_is_clipped_to_unit_interval() -> None:
    last = 0.9
    prior = 0.2
    assert np.isclose(carryover_blend(last, prior, decay_weight=-5.0), last)
    assert np.isclose(carryover_blend(last, prior, decay_weight=5.0), prior)


def test_age_curve_monotonic_increasing_then_decreasing_around_peak() -> None:
    ages = np.array([19.0, 23.0, 27.0, 31.0, 38.0])
    mult = age_curve_multiplier(ages, peak_age=27.0, width=9.0)
    # increasing up to the peak
    assert mult[0] < mult[1] < mult[2]
    # decreasing after the peak
    assert mult[2] > mult[3] > mult[4]
    # peak itself is exactly 1.0
    assert np.isclose(mult[2], 1.0)


def test_age_curve_never_goes_below_floor() -> None:
    mult = age_curve_multiplier(np.array([10.0, 60.0]), peak_age=27.0, width=9.0)
    assert np.all(mult >= 0.5)


def test_carryover_blend_applies_age_curve_to_last_season_component_only() -> None:
    last = 1.0
    prior = 0.0
    blended_at_peak = carryover_blend(last, prior, decay_weight=0.0, age=27.0)
    blended_off_peak = carryover_blend(last, prior, decay_weight=0.0, age=60.0)
    assert np.isclose(blended_at_peak, 1.0)
    assert blended_off_peak < 1.0
