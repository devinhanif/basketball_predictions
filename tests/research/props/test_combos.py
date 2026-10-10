"""Tests for combo (PRA/P+R/P+A/R+A) distributions (CLAUDE.md Phase 2, milestone 8).

Fixture-independent except for the correlation-estimation function, which
is exercised separately against the committed fixture DB.
"""

from __future__ import annotations

import math

import numpy as np

from research.props.combos import (
    COMBOS,
    DEFAULT_CORRELATION,
    build_combo_distributions,
    combo_moments,
    estimate_within_player_correlation,
)
from tests.fixtures.loader import build_fixture_db


def test_combo_mean_equals_sum_of_component_means() -> None:
    """Moment-matched sum: mean is exact regardless of the correlation used."""
    means = {
        "pts": np.array([20.0, 10.0, 5.0]),
        "reb": np.array([8.0, 4.0, 2.0]),
        "ast": np.array([5.0, 3.0, 1.0]),
    }
    variances = {
        "pts": np.array([30.0, 15.0, 5.0]),
        "reb": np.array([6.0, 3.0, 1.0]),
        "ast": np.array([4.0, 2.0, 0.5]),
    }
    for combo_name, components in COMBOS.items():
        moments = combo_moments(means, variances, components, DEFAULT_CORRELATION)
        expected_mean = sum(means[s] for s in components)
        assert np.allclose(moments.mean, expected_mean), combo_name


def test_combo_variance_at_least_sum_of_marginal_variances_for_positive_correlation() -> None:
    """Positive within-player correlation should only ever *increase* the
    combo's variance relative to a naive independence assumption -- the
    whole point of not assuming independence."""
    means = {"pts": np.array([20.0]), "reb": np.array([8.0]), "ast": np.array([5.0])}
    variances = {"pts": np.array([30.0]), "reb": np.array([6.0]), "ast": np.array([4.0])}
    independent = combo_moments(means, variances, ("pts", "reb", "ast"), {})
    correlated = combo_moments(means, variances, ("pts", "reb", "ast"), DEFAULT_CORRELATION)
    assert correlated.var[0] > independent.var[0]
    assert math.isclose(independent.var[0], 30.0 + 6.0 + 4.0)


def test_combo_distributions_pass_property_invariants() -> None:
    rng = np.random.default_rng(0)
    means = {s: rng.uniform(1.0, 20.0, size=10) for s in ("pts", "reb", "ast")}
    variances = {s: rng.uniform(1.0, 15.0, size=10) for s in ("pts", "reb", "ast")}
    for combo_name, components in COMBOS.items():
        moments = combo_moments(means, variances, components, DEFAULT_CORRELATION)
        dists = build_combo_distributions(moments)
        for dist in dists:
            # CDF monotone, probabilities in [0, 1].
            xs = sorted([0.0, dist.mean() * 0.5, dist.mean(), dist.mean() * 2.0, 200.0])
            prev = -1.0
            for x in xs:
                c = dist.cdf(x)
                assert -1e-9 <= c <= 1.0 + 1e-9
                assert c >= prev - 1e-9
                prev = c
            # P(>=N) non-increasing in N.
            prev_p = math.inf
            for n in range(0, 100, 5):
                p = dist.p_ge(n)
                assert -1e-9 <= p <= 1.0 + 1e-9
                assert p <= prev_p + 1e-9
                prev_p = p
            # Quantiles ordered.
            q10, q50, q90 = dist.ppf(0.10), dist.ppf(0.50), dist.ppf(0.90)
            assert q10 <= q50 + 1e-6
            assert q50 <= q90 + 1e-6
            assert math.isfinite(dist.mean())
        assert combo_name in COMBOS


def test_combo_moments_are_nan_free_and_deterministic() -> None:
    means = {"pts": np.array([10.0, 0.0]), "reb": np.array([5.0, 0.0]), "ast": np.array([3.0, 0.0])}
    variances = {
        "pts": np.array([10.0, 0.0]),
        "reb": np.array([5.0, 0.0]),
        "ast": np.array([3.0, 0.0]),
    }
    m1 = combo_moments(means, variances, ("pts", "reb", "ast"), DEFAULT_CORRELATION)
    m2 = combo_moments(means, variances, ("pts", "reb", "ast"), DEFAULT_CORRELATION)
    assert np.array_equal(m1.mean, m2.mean)
    assert np.array_equal(m1.var, m2.var)
    assert not np.isnan(m1.mean).any()
    assert not np.isnan(m1.var).any()
    assert (m1.var > 0).all()  # variance floor even for an all-zero player


def test_estimate_within_player_correlation_falls_back_on_tiny_fixture() -> None:
    """The committed fixture (18 player-games) is far below MIN_PAIRS_FOR_CORR,
    so this must return the documented default, not a noisy estimate."""
    con = build_fixture_db(":memory:")
    try:
        corr = estimate_within_player_correlation(con)
    finally:
        con.close()
    assert corr == DEFAULT_CORRELATION


def test_estimate_within_player_correlation_handles_empty_db() -> None:
    import duckdb

    from nba.db.connect import apply_schema

    con = duckdb.connect(":memory:")
    apply_schema(con)
    corr = estimate_within_player_correlation(con)
    con.close()
    assert corr == DEFAULT_CORRELATION
