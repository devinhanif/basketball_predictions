"""Tests for hierarchical (MinT forecast-proportions) coherence reconciliation."""

from __future__ import annotations

import numpy as np

from nba.props.distributions import GammaDist, NegBinDist, NormalDist, ZeroInflatedNegBinDist
from research.props.coherence import (
    CoherenceCheck,
    check_coherence,
    reconcile_group,
    reconciliation_scale,
    shift_distribution,
)


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
    from research.props.coherence import rescale_distribution

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


def test_shift_distribution_preserves_family_sets_mean_and_variance() -> None:
    for dist, variance in (
        (GammaDist(shape=4.0, scale=5.0), 50.0),
        (NormalDist(mean_=10.0, std=3.0), 16.0),
        (NegBinDist(mu=6.0, alpha=0.2), 10.0),
        (ZeroInflatedNegBinDist(pi=0.3, base=NegBinDist(mu=4.0, alpha=0.3)), 20.0),
    ):
        shifted = shift_distribution(dist, new_mean=12.0, variance=variance)
        assert type(shifted) is type(dist)
        assert shifted.mean() == __import__("pytest").approx(12.0, rel=1e-6)


def test_shift_distribution_never_divides_by_a_near_zero_mean() -> None:
    """The whole point of an additive (not multiplicative) correction:
    a player predicted essentially 0 can still be shifted to a small
    positive target mean without blowing up."""
    dist = GammaDist(shape=1.0, scale=1e-7)
    shifted = shift_distribution(dist, new_mean=0.5, variance=0.3)
    assert np.isfinite(shifted.mean())
    assert shifted.mean() == __import__("pytest").approx(0.5, rel=1e-3)


def test_reconcile_group_variance_weighted_sums_to_team_total() -> None:
    """Property check mirroring the multiplicative test above, but for the
    variance-weighted path."""
    dists = [GammaDist(shape=10.0, scale=2.5), GammaDist(shape=2.0, scale=5.0)]
    variances = np.array([5.0, 40.0])
    reconciled, _scale = reconcile_group(dists, team_total=20.0, variances=variances)
    means = np.array([d.mean() for d in reconciled])
    assert np.isclose(float(np.sum(means)), 20.0, atol=1e-6)


def test_reconcile_group_variance_weighted_protects_the_low_variance_player() -> None:
    """Root-cause regression test for the real-data star-undershoot: given
    two players with the *same* raw mean but very different variance, a
    downward team-total correction should fall mostly on the high-variance
    (less confident) player, not be split evenly/proportionally as the old
    uniform-multiplicative method would."""
    star = GammaDist(shape=100.0, scale=0.25)  # mean=25, low relative variance
    bench = GammaDist(shape=2.0, scale=12.5)  # mean=25, high relative variance
    variances = np.array([star.shape * star.scale**2, bench.shape * bench.scale**2])
    assert variances[0] < variances[1]  # star really is lower-variance here

    team_total = 40.0  # a large downward correction from raw sum 50 -> 40
    reconciled, _ = reconcile_group([star, bench], team_total, variances=variances)
    star_new, bench_new = (d.mean() for d in reconciled)

    star_cut = 25.0 - star_new
    bench_cut = 25.0 - bench_new
    assert np.isclose(star_new + bench_new, team_total, atol=1e-6)
    assert bench_cut > star_cut  # bench absorbs most of the correction

    # Compare against what the old uniform-multiplicative method would have
    # done: both players cut by the *same* proportion (50% each -> -12.5).
    uniform_reconciled, _ = reconcile_group([star, bench], team_total, variances=None)
    uniform_star_new, uniform_bench_new = (d.mean() for d in uniform_reconciled)
    uniform_star_cut = 25.0 - uniform_star_new
    # The variance-weighted method cuts the star strictly less than the old
    # uniform method did.
    assert star_cut < uniform_star_cut


def test_variance_weighted_reconciliation_does_not_change_pooled_mean_bias() -> None:
    """Guard for the star-undershoot fix: reconciliation always forces
    sum(reconciled means) == team_total *exactly*, for either weighting
    method -- so switching from uniform-multiplicative to variance-weighted
    cannot change the *pooled* (summed-over-every-player) prediction error
    at all, only which players it falls on. This is the mathematical
    guarantee behind "fixes the sliced star-bias without moving the
    overall bias, by construction" -- not an empirical approximation.
    """
    rng = np.random.default_rng(5)
    n_groups = 50
    pooled_uniform = 0.0
    pooled_weighted = 0.0
    pooled_actual = 0.0
    for _ in range(n_groups):
        k = rng.integers(3, 8)
        means = rng.uniform(1.0, 30.0, size=k)
        variances = rng.uniform(0.5, 50.0, size=k)
        dists = [
            GammaDist(shape=float(m**2 / v), scale=float(v / m))
            for m, v in zip(means, variances, strict=True)
        ]
        team_total = float(np.sum(means) * rng.uniform(0.6, 1.3))  # a realistic over/under gap
        actual_total = team_total  # the team-total forecast IS the thing being matched exactly

        uniform_reconciled, _ = reconcile_group(dists, team_total, variances=None)
        weighted_reconciled, _ = reconcile_group(dists, team_total, variances=variances)

        pooled_uniform += float(np.sum([d.mean() for d in uniform_reconciled]))
        pooled_weighted += float(np.sum([d.mean() for d in weighted_reconciled]))
        pooled_actual += actual_total

    assert np.isclose(pooled_uniform, pooled_actual, atol=1e-5)
    assert np.isclose(pooled_weighted, pooled_actual, atol=1e-5)
    assert np.isclose(pooled_uniform, pooled_weighted, atol=1e-4)


def test_reconcile_group_variance_weighted_clips_at_zero_not_negative() -> None:
    """A gap larger than one player's whole mean must still land at exactly
    0 for that player (never negative), with the remainder absorbed by the
    other (lower-weight) player while still hitting the team total exactly."""
    small = GammaDist(shape=4.0, scale=0.5)  # mean=2
    big = GammaDist(shape=4.0, scale=10.0)  # mean=40
    variances = np.array([4.0 * 0.5**2, 4.0 * 10.0**2])  # equal relative dispersion (CV)
    team_total = 10.0  # needs a steep cut from raw sum 42; small's naive share goes negative
    reconciled, _ = reconcile_group([small, big], team_total, variances=variances)
    means = np.array([d.mean() for d in reconciled])
    assert np.all(means >= 0.0)
    assert np.isclose(float(np.sum(means)), team_total, atol=1e-6)
