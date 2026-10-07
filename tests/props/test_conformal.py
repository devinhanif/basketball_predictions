"""Tests for split-conformal interval wrapping (CLAUDE.md "conformal prediction")."""

from __future__ import annotations

import datetime as dt

import numpy as np

from nba.props.conformal import (
    apply_conformal,
    chronological_split,
    conformal_adjustment,
    evaluate_conformal,
    interval_coverage,
)


def _dates(n: int) -> np.ndarray:
    start = dt.date(2023, 10, 1)
    return np.array([start + dt.timedelta(days=i) for i in range(n)])


def test_chronological_split_never_random_and_earlier_half_is_calibration() -> None:
    dates = _dates(10)
    cal_mask, test_mask = chronological_split(dates, cal_frac=0.5)
    assert cal_mask.sum() + test_mask.sum() == 10
    assert not (cal_mask & test_mask).any()
    assert dates[cal_mask].max() <= dates[test_mask].min()


def test_conformal_adjustment_never_reads_the_test_label() -> None:
    """conformal_adjustment only ever receives the calibration arrays --
    this test documents/asserts the call contract (the test split's y is
    simply never passed in)."""
    import inspect

    sig = inspect.signature(conformal_adjustment)
    assert list(sig.parameters) == ["q10_cal", "q90_cal", "y_cal", "alpha"]


def test_intervals_are_always_valid_lo_le_hi() -> None:
    rng = np.random.default_rng(0)
    q10 = rng.uniform(0, 10, size=50)
    q90 = q10 + rng.uniform(0.1, 10, size=50)
    lo, hi = apply_conformal(q10, q90, adjustment=2.5)
    assert (lo <= hi).all()
    # Even a huge adjustment keeps lo <= hi (both sides widen symmetrically).
    lo2, hi2 = apply_conformal(q10, q90, adjustment=1000.0)
    assert (lo2 <= hi2).all()


def test_split_conformal_achieves_approximately_nominal_coverage_on_synthetic_data() -> None:
    """Known-noise synthetic data: y = normal(mu, sigma) with a *correct*
    parametric interval already at ~80% nominal coverage by construction --
    conformal correction on top should still land within a generous
    finite-sample tolerance of 80%, and must never read the test labels to
    compute its own correction."""
    rng = np.random.default_rng(42)
    n = 4000
    mu = rng.uniform(5, 25, size=n)
    sigma = np.full(n, 5.0)
    y = rng.normal(mu, sigma)
    # True 80% interval under this exact generative model.
    from scipy import stats

    q10 = stats.norm.ppf(0.10, loc=mu, scale=sigma)
    q90 = stats.norm.ppf(0.90, loc=mu, scale=sigma)
    dates = _dates(n)

    result = evaluate_conformal(q10, q90, y, dates, stat="synthetic", alpha=0.2, cal_frac=0.5)
    assert result.n_cal + result.n_test == n
    assert 0.0 <= result.coverage_parametric <= 1.0
    assert 0.0 <= result.coverage_conformal <= 1.0
    # Generous tolerance for finite-sample noise in a backtest-scale check.
    assert abs(result.coverage_conformal - 0.8) < 0.05
    assert abs(result.coverage_parametric - 0.8) < 0.05


def test_split_conformal_widens_intervals_when_parametric_is_too_narrow() -> None:
    """If the parametric quantiles are deliberately too narrow (badly
    mis-specified model), conformal coverage on the test split should beat
    (exceed) the raw parametric coverage."""
    rng = np.random.default_rng(7)
    n = 3000
    mu = rng.uniform(5, 25, size=n)
    sigma = np.full(n, 5.0)
    y = rng.normal(mu, sigma)
    from scipy import stats

    # Deliberately too-narrow interval: use a 50% interval but claim it's 80%.
    q10 = stats.norm.ppf(0.30, loc=mu, scale=sigma)
    q90 = stats.norm.ppf(0.70, loc=mu, scale=sigma)
    dates = _dates(n)

    result = evaluate_conformal(q10, q90, y, dates, stat="synthetic", alpha=0.2, cal_frac=0.5)
    assert result.coverage_conformal > result.coverage_parametric
    assert abs(result.coverage_conformal - 0.8) < 0.05


def test_apply_conformal_tightens_with_a_negative_adjustment() -> None:
    q10 = np.array([5.0])
    q90 = np.array([15.0])
    lo, hi = apply_conformal(q10, q90, adjustment=-2.0)
    assert lo[0] > q10[0]
    assert hi[0] < q90[0]
    assert np.isclose(hi[0] - lo[0], (q90[0] - q10[0]) - 4.0)


def test_apply_conformal_tightening_never_crosses_the_midpoint() -> None:
    q10 = np.array([5.0])
    q90 = np.array([15.0])  # width 10, midpoint 10
    lo, hi = apply_conformal(q10, q90, adjustment=-1000.0)
    assert lo[0] <= hi[0]
    assert np.isclose(lo[0], 10.0)
    assert np.isclose(hi[0], 10.0)


def test_conformal_adjustment_can_be_negative_for_an_over_wide_interval() -> None:
    """If the parametric interval is already much wider than the data
    needs, the learned adjustment should be negative (a tightening
    correction), not floored at 0."""
    from scipy import stats

    rng = np.random.default_rng(3)
    n = 2000
    mu = rng.uniform(5, 25, size=n)
    sigma = np.full(n, 5.0)
    y_cal = rng.normal(mu, sigma)
    # Deliberately too-wide: claim a 99% interval is the 80% one.
    q10_cal = stats.norm.ppf(0.005, loc=mu, scale=sigma)
    q90_cal = stats.norm.ppf(0.995, loc=mu, scale=sigma)
    adjustment = conformal_adjustment(q10_cal, q90_cal, y_cal, alpha=0.2)
    assert adjustment < 0.0


def test_split_conformal_tightens_intervals_when_parametric_is_too_wide() -> None:
    """Mirror of the existing too-narrow test: when the parametric interval
    is deliberately far too wide, two-sided conformal coverage on the test
    split should land near 80%, strictly below the (near-100%) raw
    parametric coverage."""
    from scipy import stats

    rng = np.random.default_rng(11)
    n = 3000
    mu = rng.uniform(5, 25, size=n)
    sigma = np.full(n, 5.0)
    y = rng.normal(mu, sigma)
    # Deliberately too-wide interval: claim a 99.9% interval is the 80% one.
    q10 = stats.norm.ppf(0.0005, loc=mu, scale=sigma)
    q90 = stats.norm.ppf(0.9995, loc=mu, scale=sigma)
    dates = _dates(n)

    result = evaluate_conformal(q10, q90, y, dates, stat="synthetic", alpha=0.2, cal_frac=0.5)
    assert result.coverage_conformal < result.coverage_parametric
    assert abs(result.coverage_conformal - 0.8) < 0.05


def test_evaluate_conformal_handles_empty_input() -> None:
    result = evaluate_conformal(np.array([]), np.array([]), np.array([]), np.array([]), stat="pts")
    assert result.n_cal == 0
    assert result.n_test == 0
    assert result.coverage_parametric != result.coverage_parametric  # NaN


def test_interval_coverage_is_deterministic_and_nan_free_on_nonempty_input() -> None:
    lo = np.array([0.0, 1.0, 2.0])
    hi = np.array([5.0, 5.0, 5.0])
    y = np.array([1.0, 6.0, 3.0])
    cov = interval_coverage(lo, hi, y)
    assert cov == interval_coverage(lo, hi, y)
    assert cov == cov  # noqa: PLR0124 -- explicit NaN-free check
    assert abs(cov - 2 / 3) < 1e-9
