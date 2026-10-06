"""Property tests for every distribution family (CLAUDE.md CI/CD "unit" step:
"property-based tests ... for distribution fitting (CDF monotone,
probabilities in [0,1], P(>=N) non-increasing in N)").

Fixture-independent by design: every test constructs distribution objects
directly from random valid parameters via Hypothesis, never touching
DuckDB or the fixture dataset.
"""

from __future__ import annotations

import math

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.props.distributions import (
    GammaDist,
    MinutesHurdleDist,
    NegBinDist,
    NormalDist,
    ZeroInflatedNegBinDist,
    batch_minutes_mean_var,
    batch_p_ge,
    batch_ppf,
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


# ---------------------------------------------------------------------------
# Performance-fix correctness guard: the vectorized batch_* helpers (used by
# nba/props/metrics.py's crps_array / threshold log loss and nba/props/run.py
# to avoid one scipy call per row) must match the scalar per-element loop
# exactly, for every family they handle.
# ---------------------------------------------------------------------------

_TAUS = (np.arange(1, 20) - 0.5) / 19.0


def test_batch_ppf_matches_scalar_loop_normal() -> None:
    dists = [NormalDist(mean_=m, std=s) for m, s in [(10.0, 3.0), (22.5, 7.1), (0.5, 0.2)]]
    batched = batch_ppf(dists, _TAUS)
    assert batched is not None
    expected = np.array([[d.ppf(float(t)) for t in _TAUS] for d in dists])
    assert np.allclose(batched, expected, atol=1e-9)


def test_batch_ppf_matches_scalar_loop_gamma() -> None:
    dists = [GammaDist(shape=sh, scale=sc) for sh, sc in [(2.0, 1.5), (9.0, 0.4), (0.3, 3.0)]]
    batched = batch_ppf(dists, _TAUS)
    assert batched is not None
    expected = np.array([[d.ppf(float(t)) for t in _TAUS] for d in dists])
    assert np.allclose(batched, expected, atol=1e-9)


def test_batch_ppf_matches_scalar_loop_negbin() -> None:
    dists = [NegBinDist(mu=mu, alpha=a) for mu, a in [(5.0, 0.3), (12.0, 0.9), (1.0, 0.05)]]
    batched = batch_ppf(dists, _TAUS)
    assert batched is not None
    expected = np.array([[d.ppf(float(t)) for t in _TAUS] for d in dists])
    assert np.allclose(batched, expected, atol=1e-9)


def test_batch_ppf_matches_scalar_loop_zinb() -> None:
    dists = [
        ZeroInflatedNegBinDist(pi=pi, base=NegBinDist(mu=mu, alpha=a))
        for pi, mu, a in [(0.2, 5.0, 0.3), (0.0, 2.0, 0.5), (0.6, 8.0, 0.1)]
    ]
    batched = batch_ppf(dists, _TAUS)
    assert batched is not None
    expected = np.array([[d.ppf(float(t)) for t in _TAUS] for d in dists])
    assert np.allclose(batched, expected, atol=1e-9)


def test_batch_ppf_returns_none_for_mixed_family() -> None:
    dists = [NormalDist(mean_=1.0, std=1.0), GammaDist(shape=1.0, scale=1.0)]
    assert batch_ppf(dists, _TAUS) is None


def test_batch_ppf_returns_none_for_empty_list() -> None:
    assert batch_ppf([], _TAUS) is None


def test_batch_p_ge_matches_scalar_loop_every_family() -> None:
    groups: list[list[object]] = [
        [NormalDist(mean_=m, std=s) for m, s in [(10.0, 3.0), (22.5, 7.1)]],
        [GammaDist(shape=sh, scale=sc) for sh, sc in [(2.0, 1.5), (9.0, 0.4)]],
        [NegBinDist(mu=mu, alpha=a) for mu, a in [(5.0, 0.3), (12.0, 0.9)]],
        [
            ZeroInflatedNegBinDist(pi=pi, base=NegBinDist(mu=mu, alpha=a))
            for pi, mu, a in [(0.2, 5.0, 0.3), (0.0, 2.0, 0.5)]
        ],
    ]
    for dists in groups:
        for thresh in (0.0, 1.0, 5.0, 12.5):
            batched = batch_p_ge(dists, thresh)
            assert batched is not None
            expected = np.array([d.p_ge(thresh) for d in dists])  # type: ignore[attr-defined]
            assert np.allclose(batched, expected, atol=1e-9)


def test_batch_minutes_mean_var_matches_scalar_loop() -> None:
    dists = [
        MinutesHurdleDist(p_play=p, mu=mu, sigma=sigma)
        for p, mu, sigma in [(0.9, 28.0, 6.0), (0.3, 10.0, 4.0), (0.6, 35.0, 8.0)]
    ]
    result = batch_minutes_mean_var(dists)
    assert result is not None
    mean_batched, var_batched = result
    mean_expected = np.array([d.mean() for d in dists])
    var_expected = np.array([d.var() for d in dists])
    assert np.allclose(mean_batched, mean_expected, atol=1e-9)
    assert np.allclose(var_batched, var_expected, atol=1e-9)
