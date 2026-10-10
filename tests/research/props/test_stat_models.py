"""Frequency-severity decomposition + zero-inflation tests.

Fixture-independent: builds small synthetic feature frames / arrays
directly rather than going through the fixture DB, per the task's
guidance to keep distribution logic tests fixture-independent.
"""

from __future__ import annotations

import numpy as np

from nba.props.config import CountStatConfig, ZeroInflationConfig
from nba.props.distributions import NegBinDist
from research.props.stat_models import (
    build_negbin_distributions,
    build_zinb_distributions,
    count_stat_moments,
    fit_zero_inflation,
)


def test_count_stat_moments_scale_with_minutes() -> None:
    """More projected minutes -> higher expected count, all else equal."""
    import polars as pl

    config = CountStatConfig(default_rate_per_min=0.2, default_alpha=0.2)
    feats = pl.DataFrame(
        {
            "minutes_prior": [0.0, 0.0],
            "stat_sum_prior": [0.0, 0.0],
            "games_played_prior": [0, 0],
        }
    )
    minutes_mean = np.array([10.0, 30.0])
    minutes_var = np.array([4.0, 4.0])
    moments = count_stat_moments(feats, minutes_mean, minutes_var, config)
    assert moments.mean[1] > moments.mean[0]
    assert np.all(np.isfinite(moments.mean))
    assert np.all(np.isfinite(moments.var))
    assert np.all(moments.var >= moments.mean - 1e-9)


def test_build_negbin_distributions_handles_degenerate_variance() -> None:
    import polars as pl

    config = CountStatConfig(default_rate_per_min=0.0, default_alpha=0.1)
    feats = pl.DataFrame(
        {"minutes_prior": [0.0], "stat_sum_prior": [0.0], "games_played_prior": [0]}
    )
    moments = count_stat_moments(feats, np.array([0.0]), np.array([0.0]), config)
    dists = build_negbin_distributions(moments)
    assert len(dists) == 1
    assert np.isfinite(dists[0].mean())
    assert not np.isnan(dists[0].p_ge(1))


def test_zero_inflation_detected_for_many_zero_games() -> None:
    """A player with far more zero-3PM games than a plain NegBin implies should
    trigger a positive pi, and NaN/inf-free output either way."""
    rng = np.random.default_rng(0)
    base = [NegBinDist(mu=1.0, alpha=0.3) for _ in range(40)]
    # Observed: 30 zeros out of 40 (75%), far above what mu=1 NegBin implies.
    y = np.array([0] * 30 + list(rng.integers(1, 4, size=10)), dtype=float)
    cfg = ZeroInflationConfig(tolerance=0.05, min_games=10)
    pi = fit_zero_inflation(y, base, cfg)
    assert np.all(np.isfinite(pi))
    assert np.all((pi >= 0.0) & (pi <= 1.0))
    assert np.mean(pi) > 0.0


def test_zero_inflation_not_triggered_with_too_few_games() -> None:
    base = [NegBinDist(mu=1.0, alpha=0.3) for _ in range(5)]
    y = np.array([0.0, 0.0, 0.0, 1.0, 2.0])
    cfg = ZeroInflationConfig(tolerance=0.05, min_games=10)
    pi = fit_zero_inflation(y, base, cfg)
    assert np.all(pi == 0.0)


def test_zinb_distributions_are_nan_free() -> None:
    base = [NegBinDist(mu=0.5, alpha=0.4) for _ in range(5)]
    pi = np.array([0.0, 0.2, 0.5, 0.8, 0.95])
    dists = build_zinb_distributions(base, pi)
    for d in dists:
        assert np.isfinite(d.mean())
        for n in range(0, 10):
            p = d.p_ge(n)
            assert np.isfinite(p)
            assert 0.0 <= p <= 1.0
