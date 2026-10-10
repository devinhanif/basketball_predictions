"""Poisson total head: pmf, cdf, CRPS, randomized PIT, adapter default, bivariate normal cdf."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from research.parlay.totals_poisson import (
    NormalTotal,
    PoissonTotal,
    TotalsHeadConfig,
    bvn_cdf,
    make_total_head,
    total_pmf,
)


def test_pmf_sums_to_one_and_mean_matches_formula() -> None:
    x, p = total_pmf(110.0, 1.05, 3.0)
    assert p.sum() == pytest.approx(1.0)
    assert float(np.sum(x * p)) == pytest.approx(1.05 * (110.0 + 2 * 3.0), rel=1e-6)
    var = float(np.sum(p * (x - np.sum(x * p)) ** 2))
    assert var == pytest.approx(1.05**2 * (110.0 + 4 * 3.0), rel=1e-4)


def test_cdf_monotone_and_p_over_complement() -> None:
    h = PoissonTotal(55.0, 57.0, 1.0, 2.0)
    xs = np.arange(150, 280, 3.0)
    c = np.array([h.cdf(v) for v in xs])
    assert np.all(np.diff(c) >= -1e-12)
    assert h.p_over(224.5) == pytest.approx(1.0 - h.cdf(224.5))
    assert 0.0 <= h.p_over(224.5) <= 1.0


def test_crps_matches_bruteforce() -> None:
    h = PoissonTotal(40.0, 42.0, 1.0, 1.0)
    y = 90.0
    # CRPS = sum_x (F(x) - 1{x >= y})^2 over unit-spaced support
    brute = float(np.sum((np.cumsum(h.pmf) - (h.x >= y)) ** 2))
    assert h.crps(y) == pytest.approx(brute, rel=1e-6)


def test_normal_crps_zero_sd_limit_and_symmetry() -> None:
    n = NormalTotal(220.0, 18.0)
    assert n.crps(230.0) == pytest.approx(n.crps(210.0))
    assert n.p_over(220.0) == pytest.approx(0.5)


def test_randomized_pit_is_standard_normal() -> None:
    rng = np.random.default_rng(1)
    h = PoissonTotal(55.0, 57.0, 1.0, 2.0)
    draws = rng.choice(h.x, size=4000, p=h.pmf)
    z = np.array([h.z_of(float(d), float(rng.random())) for d in draws])
    assert abs(z.mean()) < 0.06
    assert z.std() == pytest.approx(1.0, abs=0.06)


def test_default_head_is_normal_and_poisson_needs_params() -> None:
    assert TotalsHeadConfig().kind == "normal"
    h = make_total_head(TotalsHeadConfig(), mu=225.0, sd=18.0)
    assert isinstance(h, NormalTotal)
    with pytest.raises(ValueError):
        make_total_head(TotalsHeadConfig(kind="poisson"), mu=225.0, sd=18.0)
    p = make_total_head(
        TotalsHeadConfig(kind="poisson"), mu=0.0, sd=0.0, poisson=(55.0, 57.0, 1.0, 2.0)
    )
    assert isinstance(p, PoissonTotal)


@pytest.mark.parametrize("rho", [-0.6, 0.0, 0.35, 0.8])
def test_bvn_cdf_matches_scipy(rho: float) -> None:
    a = np.array([-1.0, 0.0, 0.7, 2.0])
    c = np.array([0.5, -0.3, 1.1, 0.0])
    got = bvn_cdf(a, c, rho)
    mv = stats.multivariate_normal(mean=[0, 0], cov=[[1, rho], [rho, 1]])
    want = np.array([mv.cdf([ai, ci]) for ai, ci in zip(a, c, strict=True)])
    assert np.allclose(got, want, atol=1e-5)


def test_bvn_independent_factorises() -> None:
    a, c = np.array([0.3]), np.array([-0.4])
    assert bvn_cdf(a, c, 0.0)[0] == pytest.approx(
        float(stats.norm.cdf(0.3) * stats.norm.cdf(-0.4)), abs=1e-6
    )
    assert math.isfinite(float(bvn_cdf(np.array([-12.0]), c, 0.5)[0]))
