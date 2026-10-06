"""Tests for hierarchical (MinT forecast-proportions) coherence reconciliation."""

from __future__ import annotations

import numpy as np

from nba.props.coherence import (
    CoherenceCheck,
    check_coherence,
    reconcile_group,
    reconciliation_scale,
)
from nba.props.distributions import GammaDist, NegBinDist, NormalDist, ZeroInflatedNegBinDist


def test_reconciliation_scale_matches_target_total() -> None:
    means = np.array([10.0, 20.0, 5.0])
    scale = reconciliation_scale(means, team_total=105.0)
    assert np.isclose(np.sum(means) * scale, 105.0)


def test_reconciliation_scale_is_noop_on_degenerate_inputs() -> None:
    assert reconciliation_scale(np.array([0.0, 0.0]), team_total=50.0) == 1.0
    assert reconciliation_scale(np.array([10.0, 5.0]), team_total=0.0) == 1.0


def test_reconcile_group_sums_to_team_total_for_every_family() -> None:
    """Property check (synthetic, every distribution family this package
    produces): after reconciliation, player means for one (game, team)
    group sum to the team-total forecast within a tiny tolerance."""
    groups: list[list[object]] = [
        [
            GammaDist(shape=4.0, scale=5.0),
            GammaDist(shape=2.0, scale=3.0),
            GammaDist(shape=1.0, scale=2.0),
        ],
        [NegBinDist(mu=6.0, alpha=0.2), NegBinDist(mu=3.0, alpha=0.3)],
        [NormalDist(mean_=20.0, std=5.0), NormalDist(mean_=10.0, std=3.0)],
        [
            ZeroInflatedNegBinDist(pi=0.2, base=NegBinDist(mu=2.0, alpha=0.4)),
            NegBinDist(mu=1.0, alpha=0.5),
        ],
    ]
    totals = [110.0, 44.0, 25.0, 12.0]
    group_means = []
    for dists, total in zip(groups, totals, strict=True):
        reconciled, _scale = reconcile_group(dists, total)
        means = np.array([d.mean() for d in reconciled])
        assert np.isclose(float(np.sum(means)), total, atol=1e-6)
        group_means.append(means)

    check = check_coherence(group_means, totals, stat="test")
    assert isinstance(check, CoherenceCheck)
    assert check.n_groups == len(totals)
    assert check.max_abs_gap < 1e-6
    assert check.mean_abs_gap < 1e-6


def test_check_coherence_detects_an_un_reconciled_group() -> None:
    group_means = [np.array([5.0, 5.0])]  # sums to 10, not the stated 100 total
    check = check_coherence(group_means, [100.0], stat="pts")
    assert check.max_abs_gap > 50.0


def test_rescale_distribution_preserves_family_and_scales_mean() -> None:
    from nba.props.coherence import rescale_distribution

    for dist in (
        GammaDist(shape=4.0, scale=5.0),
        NormalDist(mean_=10.0, std=3.0),
        NegBinDist(mu=6.0, alpha=0.2),
        ZeroInflatedNegBinDist(pi=0.3, base=NegBinDist(mu=4.0, alpha=0.3)),
    ):
        rescaled = rescale_distribution(dist, 2.0)
        assert type(rescaled) is type(dist)
        assert rescaled.mean() == __import__("pytest").approx(dist.mean() * 2.0, rel=1e-6)


def test_check_coherence_empty_groups_is_nan_free() -> None:
    check = check_coherence([], [], stat="pts")
    assert check.n_groups == 0
    assert check.max_abs_gap == 0.0
    assert check.mean_abs_gap == 0.0
