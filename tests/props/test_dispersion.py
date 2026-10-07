"""Tests for native predictive-dispersion (variance) calibration."""

from __future__ import annotations

import datetime as dt

import numpy as np
from scipy import stats

from nba.props.dispersion import (
    apply_dispersion_multiplier,
    fit_dispersion_multiplier,
)


def _dates(n: int) -> np.ndarray:
    start = dt.date(2023, 10, 1)
    return np.array([start + dt.timedelta(days=i) for i in range(n)])


def test_apply_dispersion_multiplier_scales_and_floors() -> None:
    var = np.array([1.0, 4.0, 0.0])
    out = apply_dispersion_multiplier(var, 2.0)
    assert np.allclose(out[:2], [2.0, 8.0])
    assert out[2] > 0.0  # floored, never exactly 0 or negative


def test_dispersion_multiplier_widens_a_too_narrow_gamma_fit() -> None:
    """Points-like synthetic: true spread is wider than the claimed
    variance -- coverage should start well below 80% and the fit multiplier
    should move it back toward 80%, i.e. a multiplier > 1 (widen)."""
    rng = np.random.default_rng(0)
    n = 4000
    true_mean = rng.uniform(5, 30, size=n)
    true_shape = 8.0
    true_scale = true_mean / true_shape
    y = rng.gamma(true_shape, true_scale)
    # Deliberately under-stated variance: 1/4 of the true variance.
    claimed_var = (true_shape * true_scale**2) / 4.0
    dates = _dates(n)

    result = fit_dispersion_multiplier(
        "gamma", true_mean, claimed_var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50
    )
    assert result.multiplier > 1.5  # must widen substantially
    assert abs(result.coverage_cal_after - 0.8) < abs(result.coverage_cal_before - 0.8)
    assert abs(result.coverage_cal_after - 0.8) < 0.07


def test_dispersion_multiplier_tightens_a_too_wide_negbin_fit() -> None:
    """Rebounds-like synthetic: claimed variance is far larger than the
    true NegBin variance -- coverage should start well above 80% and the
    fit multiplier should move it back down, i.e. a multiplier < 1 (tighten)."""
    rng = np.random.default_rng(1)
    n = 4000
    true_mean = rng.uniform(2, 12, size=n)
    true_alpha = 0.1
    true_var = true_mean + true_alpha * true_mean**2
    n_param = 1.0 / true_alpha
    p_param = 1.0 / (1.0 + true_alpha * true_mean)
    y = rng.negative_binomial(n_param, p_param).astype(float)
    # Deliberately over-stated variance: 6x the true variance.
    claimed_var = true_var * 6.0
    dates = _dates(n)

    result = fit_dispersion_multiplier(
        "negbin", true_mean, claimed_var, y, dates, stat="reb", cal_frac=0.5, min_cal_n=50
    )
    assert result.multiplier < 0.7  # must tighten substantially
    assert abs(result.coverage_cal_after - 0.8) < abs(result.coverage_cal_before - 0.8)
    assert abs(result.coverage_cal_after - 0.8) < 0.07


def test_dispersion_multiplier_is_noop_when_already_well_calibrated() -> None:
    rng = np.random.default_rng(2)
    n = 4000
    mu = rng.uniform(5, 25, size=n)
    sigma = np.full(n, 5.0)
    y = rng.normal(mu, sigma)
    var = sigma**2
    dates = _dates(n)
    result = fit_dispersion_multiplier(
        "gamma", mu, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50
    )
    assert 0.5 < result.multiplier < 2.0


def test_dispersion_multiplier_degrades_gracefully_on_tiny_sample() -> None:
    n = 10
    mean = np.full(n, 10.0)
    var = np.full(n, 5.0)
    y = np.full(n, 10.0)
    dates = _dates(n)
    result = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50
    )
    assert result.multiplier == 1.0
    assert "dispersion multiplier left at the no-op default" in result.note


def _under_dispersed_gamma_case(n: int = 4000, seed: int = 0):
    rng = np.random.default_rng(seed)
    true_mean = rng.uniform(5, 30, size=n)
    true_shape = 8.0
    true_scale = true_mean / true_shape
    y = rng.gamma(true_shape, true_scale)
    claimed_var = (true_shape * true_scale**2) / 4.0  # under-stated -> needs widening
    dates = _dates(n)
    return true_mean, claimed_var, y, dates


def test_strength_zero_reproduces_original_multiplier() -> None:
    """``dispersion_strength = 0.0`` must be a pure no-op: multiplier stays
    at 1.0 (the original fitted dispersion) regardless of how miscalibrated
    the data is."""
    mean, var, y, dates = _under_dispersed_gamma_case()
    result = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=0.0
    )
    assert result.multiplier == 1.0
    assert result.multiplier_full > 1.5  # the full recalibration still widens substantially


def test_strength_one_reproduces_full_recalibration() -> None:
    mean, var, y, dates = _under_dispersed_gamma_case()
    full = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=1.0
    )
    assert full.multiplier == full.multiplier_full
    assert full.multiplier > 1.5


def test_strength_is_monotonic_between_original_and_full() -> None:
    mean, var, y, dates = _under_dispersed_gamma_case()
    strengths = [0.0, 0.25, 0.5, 0.75, 1.0]
    multipliers = [
        fit_dispersion_multiplier(
            "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=s
        ).multiplier
        for s in strengths
    ]
    # Widening case: multiplier should be non-decreasing as strength rises.
    assert all(a <= b + 1e-12 for a, b in zip(multipliers, multipliers[1:], strict=False))
    assert multipliers[0] == 1.0
    assert multipliers[-1] > multipliers[0]


def test_strength_moves_coverage_toward_target_as_it_increases() -> None:
    from nba.props.conformal import chronological_split
    from nba.props.dispersion import _coverage_for_multiplier

    mean, var, y, dates = _under_dispersed_gamma_case()
    cal_mask, _ = chronological_split(dates, 0.5)
    mean_c, var_c, y_c = mean[cal_mask], var[cal_mask], y[cal_mask]
    strengths = [0.0, 0.25, 0.5, 0.75, 1.0]
    gaps = []
    for s in strengths:
        result = fit_dispersion_multiplier(
            "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=s
        )
        cov = _coverage_for_multiplier("gamma", mean_c, var_c, y_c, result.multiplier)
        gaps.append(abs(cov - 0.8))
    # Gap to the 80% target should shrink (or stay flat) monotonically as
    # strength rises from 0 (original, badly under-covered) to 1 (fully
    # recalibrated).
    assert all(a >= b - 1e-9 for a, b in zip(gaps, gaps[1:], strict=False))
    assert gaps[-1] < gaps[0]


def test_strength_out_of_range_is_clamped() -> None:
    mean, var, y, dates = _under_dispersed_gamma_case()
    lo = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=-5.0
    )
    hi = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=5.0
    )
    assert lo.multiplier == 1.0
    assert hi.multiplier == hi.multiplier_full


def test_strength_knob_is_deterministic_given_seed() -> None:
    mean, var, y, dates = _under_dispersed_gamma_case(seed=7)
    r1 = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=0.5
    )
    r2 = fit_dispersion_multiplier(
        "gamma", mean, var, y, dates, stat="pts", cal_frac=0.5, min_cal_n=50, strength=0.5
    )
    assert r1.multiplier == r2.multiplier


def test_default_config_dispersion_strength_is_gentle() -> None:
    from nba.props.config import DispersionConfig

    cfg = DispersionConfig()
    assert 0.0 < cfg.dispersion_strength < 1.0


def test_coverage_helper_matches_direct_scipy_quantiles() -> None:
    """Sanity check: the internal gamma-family coverage search uses the same
    shape/scale method-of-moments fit as the rest of the pipeline."""
    mean = np.array([10.0])
    var = np.array([4.0])
    shape = mean**2 / var
    scale = var / mean
    q10 = stats.gamma.ppf(0.10, a=shape, scale=scale)
    q90 = stats.gamma.ppf(0.90, a=shape, scale=scale)
    y_inside = np.array([float((q10[0] + q90[0]) / 2.0)])
    from nba.props.dispersion import _coverage_for_multiplier

    cov = _coverage_for_multiplier("gamma", mean, var, y_inside, 1.0)
    assert cov == 1.0
