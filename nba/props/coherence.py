"""Hierarchical coherence (CLAUDE.md "borrowed from retail demand forecasting",
MinT-style): "player-level predictions must sum to a coherent team total;
reconcile with a top-down/bottom-up step so player and team forecasts do not
contradict each other."

Method: **forecast-proportions reconciliation**, the special case of MinT
that uses the bottom-up (player-level) forecasts purely as *relative
weights* and an independently-produced top-level (team) forecast as the
*level*:

    scale        = team_total_forecast / sum(player_means)
    player_mean' = player_mean * scale   (applied to every moment, so the
                                           whole distribution rescales, not
                                           just its mean)

This is the correct generic MinT case here because the full
covariance-weighted MinT optimal reconciliation needs a joint covariance
matrix across *all* series in the hierarchy (team and every player), which
this module -- and this milestone's data -- does not have. Forecast
proportions still guarantees exact bottom-up-sums-to-top coherence, which is
the property CLAUDE.md actually requires ("player and team forecasts do not
contradict each other").

**Root cause of the real-data star-undershoot (fixed here).** Pure
forecast-proportions distributes the whole-team correction *proportionally
to each player's own predicted mean*, i.e. a single multiplicative ``scale``
applied uniformly across the group. On the real 4-season DuckDB, every
team's *raw* bottom-up sum of per-player point projections overshoots the
team-total forecast by ~22% on average (roster-wide: many bench/thin-sample
players are individually only mildly over-projected, but there are a lot of
them). A uniform multiplicative cut absorbs that overshoot *in absolute
points* almost entirely out of whichever players happen to have the largest
predicted mean -- the team's steady, high-usage stars (the volatility-bucket
report's "low CV" bucket) -- even though their own per-player model was
comparatively well-calibrated before reconciliation ever ran. Meanwhile
genuinely-uncertain (cold-start / high-CV) players, whose raw means are the
actual source of the roster-wide overshoot, are protected from correction
in exact proportion to how large their own (already-inflated) prediction is.

The fix is the single-level MinT "variance scaling" variant (Hyndman et al.):
reconcile with an **additive** correction allocated in proportion to each
player's own *relative* dispersion (``variance / mean^2``, i.e. squared
coefficient of variation) rather than their absolute mean share, then refit
each player's distribution at its new target mean while holding its
originally-fitted variance fixed (:func:`shift_distribution`). High-relative-
uncertainty rows (small mean, large variance -- exactly cold-start/high-CV
players) absorb most of the correction; low-relative-uncertainty rows
(stars) absorb almost none. The team-level sum is still forced to match the
team-total forecast exactly (same ``check_coherence`` contract as before);
only *which* players absorb the gap changes. Passing ``variances=None``
(the default) keeps the original uniform-multiplicative behavior for any
caller that does not have a variance array handy.

The "independently produced top-level forecast" is each team's own trailing
average of *actual* team totals for that stat (strictly prior games, same
as-of discipline as every other feature here) -- a genuinely different
estimator from "sum of today's per-player projections", so the two really
can (and in general will) disagree before this step runs.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate
from nba.props.distributions import (
    Distribution,
    GammaDist,
    NegBinDist,
    NormalDist,
    ZeroInflatedNegBinDist,
    gamma_from_moments,
    negbin_from_moments,
)

#: League-average team totals, used only when a team has zero prior games
#: this season to shrink toward (same documented-default pattern as every
#: other config default in this package).
DEFAULT_TEAM_TOTAL: dict[str, float] = {"pts": 112.0, "reb": 44.0, "ast": 25.0, "fg3m": 12.5}

_TEAM_TOTAL_SQL = """
WITH team_game_totals AS (
    SELECT pgs.game_id, pgs.team_id, g.game_date, SUM(pgs.{stat}) AS team_total
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
    GROUP BY pgs.game_id, pgs.team_id, g.game_date
)
SELECT
    game_id,
    team_id,
    game_date,
    COUNT(*) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS team_games_prior,
    COALESCE(
        AVG(team_total) OVER (
            PARTITION BY team_id ORDER BY game_date, game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS team_total_avg_prior
FROM team_game_totals
ORDER BY game_date, game_id, team_id
"""


def build_team_total_features(con: duckdb.DuckDBPyConnection, stat: str) -> pl.DataFrame:
    """As-of trailing average of a team's own actual total for ``stat``."""
    if stat not in DEFAULT_TEAM_TOTAL:
        raise ValueError(f"unsupported stat {stat!r}")
    con.execute(_TEAM_TOTAL_SQL.format(stat=stat))
    cols = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "team_id": pl.Int64,
        "game_date": pl.Date,
        "team_games_prior": pl.Int64,
        "team_total_avg_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in cols}, orient="row")


def team_total_forecast(
    features: pl.DataFrame, stat: str, k_team_games: float = 10.0
) -> np.ndarray:
    """Empirical-Bayes-shrunk team-total forecast per row of ``features``."""
    n = features.select("team_games_prior").to_series().fill_null(0).to_numpy()
    obs = features.select("team_total_avg_prior").to_series().fill_null(0.0).to_numpy()
    return shrink_rate(obs, n, DEFAULT_TEAM_TOTAL[stat], k_team_games)


def reconciliation_scale(player_means: np.ndarray, team_total: float) -> float:
    """Forecast-proportions scale factor for one (game, team) group.

    Degenerate inputs (a non-positive bottom-up sum or a non-positive team
    total -- e.g. an all-DNP lineup slice) return the no-op scale ``1.0``
    rather than dividing by zero or producing a meaningless negative scale.
    """
    total = float(np.sum(player_means))
    if total <= 1e-9 or team_total <= 1e-9:
        return 1.0
    return float(team_total / total)


def _negbin_var(dist: NegBinDist) -> float:
    return float(dist.mu + dist.alpha * dist.mu**2)


def rescale_distribution(dist: Distribution, factor: float) -> Distribution:
    """Linearly rescale a fitted distribution by ``factor >= 0``, preserving family.

    Exact for Normal/Gamma: a positive scalar multiple of a Gamma(shape,
    scale) random variable is Gamma(shape, scale*factor); same algebra for
    Normal(mean, std) -> Normal(mean*factor, std*factor). NegBin/ZINB are not
    closed under scalar scaling, so those are refit by method-of-moments on
    the scaled moments (mean*factor, var*factor^2) via the already-tested
    ``negbin_from_moments`` -- an approximation, documented here, consistent
    with every other moment-matched fit in this package.
    """
    factor = max(float(factor), 0.0)
    if isinstance(dist, GammaDist):
        return GammaDist(shape=dist.shape, scale=dist.scale * factor)
    if isinstance(dist, NormalDist):
        return NormalDist(mean_=dist.mean_ * factor, std=dist.std * factor)
    if isinstance(dist, NegBinDist):
        return negbin_from_moments(dist.mean() * factor, _negbin_var(dist) * factor**2)
    if isinstance(dist, ZeroInflatedNegBinDist):
        base = negbin_from_moments(dist.base.mean() * factor, _negbin_var(dist.base) * factor**2)
        return ZeroInflatedNegBinDist(pi=dist.pi, base=base)
    raise TypeError(f"rescale_distribution: unsupported family {type(dist).__name__}")


def shift_distribution(dist: Distribution, new_mean: float, variance: float) -> Distribution:
    """Re-center ``dist`` at ``new_mean`` while holding ``variance`` fixed.

    The additive-correction analogue of :func:`rescale_distribution`: used
    by the variance-weighted reconciliation below, where the *target mean*
    comes from an additive share of the team-level gap rather than a
    uniform multiplicative scale. Refit by method of moments for every
    family this package produces (same pattern as ``gamma_from_moments`` /
    ``negbin_from_moments`` everywhere else in ``nba/props/``) -- this
    never divides by a player's own (possibly near-zero) mean, unlike a
    per-player multiplicative factor would, so it stays numerically safe
    for thin-sample / near-zero-projection players.
    """
    new_mean = max(float(new_mean), 0.0)
    variance = max(float(variance), 1e-9)
    if isinstance(dist, GammaDist):
        return gamma_from_moments(new_mean, variance)
    if isinstance(dist, NormalDist):
        return NormalDist(mean_=new_mean, std=float(np.sqrt(variance)))
    if isinstance(dist, NegBinDist):
        return negbin_from_moments(new_mean, variance)
    if isinstance(dist, ZeroInflatedNegBinDist):
        pi = dist.pi
        denom = max(1.0 - pi, 1e-6)
        base_mean = new_mean / denom
        base_var = variance / denom
        return ZeroInflatedNegBinDist(pi=pi, base=negbin_from_moments(base_mean, base_var))
    raise TypeError(f"shift_distribution: unsupported family {type(dist).__name__}")


def _variance_weighted_targets(
    means: np.ndarray, variances: np.ndarray, team_total: float
) -> np.ndarray:
    """New per-player target means for one (game, team) group: the
    team-level gap (``team_total - sum(means)``) allocated in proportion to
    each player's *relative* dispersion (squared coefficient of variation,
    ``variance / mean^2``), floored against a fraction of the group's own
    mean scale so near-zero-mean players don't produce a divide-by-zero
    blowup. Guarantees ``sum(result) == team_total`` (up to floating-point
    tolerance) and every entry ``>= 0`` via iterative clip-and-redistribute:
    any player whose naive allocation would go negative is pinned at 0 and
    removed from the active set, and the remaining gap is reallocated among
    the still-active players -- at most ``len(means)`` players can ever be
    clipped, so the loop is bounded by that.
    """
    means = np.asarray(means, dtype=float)
    variances = np.asarray(variances, dtype=float)
    total_mean = float(np.sum(means))
    if total_mean <= 1e-9 or team_total <= 1e-9:
        return means.copy()

    eps = max(1e-6, 0.1 * float(np.mean(means)))
    weights = variances / (means**2 + eps**2)
    active = np.ones(len(means), dtype=bool)
    new_means = means.copy()

    for _ in range(len(means) + 1):
        remaining_gap = team_total - float(np.sum(new_means))
        if abs(remaining_gap) < 1e-9:
            break
        w = np.where(active, weights, 0.0)
        w_sum = float(np.sum(w))
        n_active = int(np.sum(active))
        if n_active == 0:
            break
        if w_sum <= 1e-12:
            # Every still-active row has ~zero relative-dispersion weight
            # (e.g. every remaining mean/variance pair is ~identical) --
            # fall back to an equal split among them so the gap still
            # closes exactly rather than silently stopping short.
            new_means[active] += remaining_gap / n_active
            break
        alloc = np.zeros(len(means))
        alloc[active] = w[active] / w_sum * remaining_gap
        candidate = new_means + alloc
        newly_negative = active & (candidate < 0.0)
        if not np.any(newly_negative):
            new_means = candidate
            break
        new_means = np.where(newly_negative, 0.0, candidate)
        active = active & ~newly_negative
    return new_means


def reconcile_group(
    dists: list[Distribution], team_total: float, variances: np.ndarray | None = None
) -> tuple[list[Distribution], float]:
    """Reconcile one (game, team) group's distributions so their means sum
    exactly to ``team_total``. Returns (reconciled distributions, scale)
    where ``scale`` is the implied uniform-equivalent multiplicative factor
    (``team_total / sum(raw means)``), reported for logging/backward
    compatibility regardless of which method below actually produced the
    reconciled distributions.

    ``variances is None`` (default): the original uniform-multiplicative
    forecast-proportions method (:func:`rescale_distribution`) -- used by
    any caller without a variance array handy.

    ``variances`` given: the variance-weighted additive method (see module
    docstring's "root cause of the real-data star-undershoot" section) --
    :func:`_variance_weighted_targets` picks each player's new target mean,
    then :func:`shift_distribution` refits each one at that mean while
    holding its own originally-fitted variance fixed.
    """
    means = np.array([d.mean() for d in dists])
    scale = reconciliation_scale(means, team_total)
    if variances is None:
        return [rescale_distribution(d, scale) for d in dists], scale
    variances_arr = np.asarray(variances, dtype=float)
    new_means = _variance_weighted_targets(means, variances_arr, team_total)
    reconciled = [
        shift_distribution(d, nm, v)
        for d, nm, v in zip(dists, new_means, variances_arr, strict=True)
    ]
    return reconciled, scale


@dataclass
class CoherenceCheck:
    stat: str
    n_groups: int
    max_abs_gap: float
    mean_abs_gap: float


def check_coherence(
    reconciled_means_by_group: list[np.ndarray], team_totals: list[float], stat: str
) -> CoherenceCheck:
    """``|sum(reconciled player means) - team_total|`` across every group --
    should be ~0 (floating-point tolerance) after :func:`reconcile_group`."""
    if not reconciled_means_by_group:
        return CoherenceCheck(stat=stat, n_groups=0, max_abs_gap=0.0, mean_abs_gap=0.0)
    gaps = np.array(
        [
            abs(float(np.sum(m)) - t)
            for m, t in zip(reconciled_means_by_group, team_totals, strict=True)
        ]
    )
    return CoherenceCheck(
        stat=stat,
        n_groups=len(gaps),
        max_abs_gap=float(gaps.max()),
        mean_abs_gap=float(gaps.mean()),
    )
