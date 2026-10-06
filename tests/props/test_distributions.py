"""Property tests for every distribution family (CLAUDE.md CI/CD "unit" step:
"property-based tests ... for distribution fitting (CDF monotone,
probabilities in [0,1], P(>=N) non-increasing in N)").

Fixture-independent by design: every test constructs distribution objects
directly from random valid parameters via Hypothesis, never touching
DuckDB or the fixture dataset.
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from nba.props.distributions import (
    GammaDist,
    MinutesHurdleDist,
    NegBinDist,
    NormalDist,
    ZeroInflatedNegBinDist,
)

_SETTINGS = settings(max_examples=50, deadline=None)

_grid_ints = st.integers(min_value=0, max_value=60)


def _assert_cdf_monotone(dist: object, xs: list[float]) -> None:
    prev = -1.0
    for x in sorted(xs):
        c = dist.cdf(x)  # type: ignore[attr-defined]
        assert 0.0 <= c <= 1.0 + 1e-9
        assert c >= prev - 1e-9
        prev = c


def _assert_p_ge_non_increasing(dist: object, ns: list[int]) -> None:
    prev = math.inf
    for n in sorted(ns):
        p = dist.p_ge(n)  # type: ignore[attr-defined]
        assert -1e-9 <= p <= 1.0 + 1e-9
        assert p <= prev + 1e-9
        prev = p


def _assert_quantiles_ordered(dist: object) -> None:
    q10 = dist.ppf(0.10)  # type: ignore[attr-defined]
    q50 = dist.ppf(0.50)  # type: ignore[attr-defined]
    q90 = dist.ppf(0.90)  # type: ignore[attr-defined]
    assert q10 <= q50 + 1e-6
    assert q50 <= q90 + 1e-6


@given(mean=st.floats(0.1, 50.0), std=st.floats(0.1, 20.0))
@_SETTINGS
def test_normal_dist_invariants(mean: float, std: float) -> None:
    dist = NormalDist(mean_=mean, std=std)
    _assert_cdf_monotone(dist, [mean - 3 * std, mean - std, mean, mean + std, mean + 3 * std])
    _assert_p_ge_non_increasing(dist, list(range(0, 61, 5)))
    _assert_quantiles_ordered(dist)


@given(shape=st.floats(0.1, 30.0), scale=st.floats(0.1, 10.0))
@_SETTINGS
def test_gamma_dist_invariants(shape: float, scale: float) -> None:
    dist = GammaDist(shape=shape, scale=scale)
    _assert_cdf_monotone(dist, [0.0, dist.mean() * 0.5, dist.mean(), dist.mean() * 2, 100.0])
    _assert_p_ge_non_increasing(dist, list(range(0, 61, 5)))
    _assert_quantiles_ordered(dist)
    assert dist.mean() >= 0.0


@given(mu=st.floats(0.01, 20.0), alpha=st.floats(0.001, 2.0))
@_SETTINGS
def test_negbin_dist_invariants(mu: float, alpha: float) -> None:
    dist = NegBinDist(mu=mu, alpha=alpha)
    _assert_cdf_monotone(dist, [float(n) for n in range(0, 61)])
    _assert_p_ge_non_increasing(dist, list(range(0, 61)))
    _assert_quantiles_ordered(dist)
    assert dist.p_ge(0) == 1.0


@given(mu=st.floats(0.01, 20.0), alpha=st.floats(0.001, 2.0), pi=st.floats(0.0, 0.9))
@_SETTINGS
def test_zero_inflated_negbin_invariants(mu: float, alpha: float, pi: float) -> None:
    base = NegBinDist(mu=mu, alpha=alpha)
    dist = ZeroInflatedNegBinDist(pi=pi, base=base)
    _assert_cdf_monotone(dist, [float(n) for n in range(0, 61)])
    _assert_p_ge_non_increasing(dist, list(range(0, 61)))
    _assert_quantiles_ordered(dist)
    assert dist.p_ge(0) == 1.0
    # Zero-inflation can only raise P(X=0), never lower the mean below the base's.
    assert dist.mean() <= base.mean() + 1e-9


@given(
    p_play=st.floats(0.0, 1.0),
    mu=st.floats(0.0, 48.0),
    sigma=st.floats(0.5, 15.0),
)
@_SETTINGS
def test_minutes_hurdle_invariants(p_play: float, mu: float, sigma: float) -> None:
    dist = MinutesHurdleDist(p_play=p_play, mu=mu, sigma=sigma)
    _assert_cdf_monotone(dist, [0.0, 5.0, 12.0, 24.0, 36.0, 48.0])
    _assert_p_ge_non_increasing(dist, list(range(0, 50, 2)))
    _assert_quantiles_ordered(dist)
    assert dist.p_ge(0) == 1.0
    assert 0.0 <= dist.mean() <= 48.0
    assert dist.var() >= 0.0
