"""Mandatory property tests for cold-start method 1 (empirical-Bayes shrinkage).

Pure math, independent of the fixture per the task spec: as n -> infinity
the posterior converges to r_obs; as n -> 0 it converges to r_prior; the
posterior always lies between r_obs and r_prior.
"""

from __future__ import annotations

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from nba.coldstart.shrinkage import classify_source, shrink_rate, tune_pseudo_count

rates = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
pseudo_counts = st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False)


@given(r_obs=rates, r_prior=rates, k=pseudo_counts)
def test_posterior_converges_to_prior_as_n_goes_to_zero(
    r_obs: float, r_prior: float, k: float
) -> None:
    posterior = shrink_rate(r_obs, n=0.0, r_prior=r_prior, k=k)
    assert np.isclose(float(posterior), r_prior, atol=1e-9)


@given(r_obs=rates, r_prior=rates, k=pseudo_counts)
def test_posterior_converges_to_observed_as_n_grows(r_obs: float, r_prior: float, k: float) -> None:
    # n scaled relative to k (not a fixed constant) so the prior's weight
    # k/(n+k) is driven toward 0 regardless of how large k itself is.
    n_small = k * 10.0
    n_huge = k * 1e9
    posterior_small_n = shrink_rate(r_obs, n=n_small, r_prior=r_prior, k=k)
    posterior_huge_n = shrink_rate(r_obs, n=n_huge, r_prior=r_prior, k=k)
    assert np.isclose(float(posterior_huge_n), r_obs, atol=1e-6)
    # moving from small n to huge n should only move the posterior closer to r_obs
    assert abs(float(posterior_huge_n) - r_obs) <= abs(float(posterior_small_n) - r_obs) + 1e-9


@given(r_obs=rates, r_prior=rates, n=st.floats(min_value=0.0, max_value=1e6), k=pseudo_counts)
def test_posterior_always_between_observed_and_prior(
    r_obs: float, r_prior: float, n: float, k: float
) -> None:
    posterior = float(shrink_rate(r_obs, n=n, r_prior=r_prior, k=k))
    lo, hi = min(r_obs, r_prior), max(r_obs, r_prior)
    assert lo - 1e-9 <= posterior <= hi + 1e-9


def test_zero_n_and_zero_k_falls_back_to_prior_without_dividing_by_zero() -> None:
    posterior = shrink_rate(0.7, n=0.0, r_prior=0.3, k=0.0)
    assert np.isclose(float(posterior), 0.3)


def test_vectorized_shrink_rate_matches_scalar_elementwise() -> None:
    r_obs = np.array([0.9, 0.1, 0.5])
    n = np.array([0.0, 100.0, 10.0])
    r_prior = np.array([0.4, 0.4, 0.4])
    k = np.array([50.0, 50.0, 50.0])
    vec = shrink_rate(r_obs, n, r_prior, k)
    for i in range(3):
        scalar = shrink_rate(r_obs[i], n[i], r_prior[i], k[i])
        assert np.isclose(vec[i], scalar)


def test_classify_source_prior_blended_observed() -> None:
    labels = classify_source(np.array([0, 50, 1000, 2000]), full_trust_n=1000.0)
    assert list(labels) == ["prior", "blended", "observed", "observed"]


def test_tune_pseudo_count_degrades_gracefully_on_tiny_data() -> None:
    """Mirrors the committed fixture: too few walk-forward folds to tune k."""
    result = tune_pseudo_count(
        n_obs=np.array([10.0, 20.0]),
        r_obs=np.array([0.2, 0.25]),
        r_prior=np.array([0.3, 0.3]),
        r_future=np.array([0.22, 0.24]),
        stat="usage",
        default_k=150.0,
    )
    assert result.k == 150.0
    assert "insufficient data" in result.note


def test_tune_pseudo_count_picks_a_candidate_with_enough_folds() -> None:
    rng = np.random.default_rng(0)
    n_obs = rng.uniform(50, 500, size=40)
    r_prior = np.full(40, 0.3)
    true_rate = rng.uniform(0.1, 0.5, size=40)
    r_obs = true_rate + rng.normal(0, 0.01, size=40)
    r_future = true_rate + rng.normal(0, 0.01, size=40)
    result = tune_pseudo_count(
        n_obs=n_obs,
        r_obs=r_obs,
        r_prior=r_prior,
        r_future=r_future,
        stat="usage",
        default_k=150.0,
    )
    assert result.note == ""
    assert result.k in result.candidates_tried
