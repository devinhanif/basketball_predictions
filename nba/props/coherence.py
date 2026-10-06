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


def reconcile_group(
    dists: list[Distribution], team_total: float
) -> tuple[list[Distribution], float]:
    """Rescale every distribution in one (game, team) group so their means
    sum exactly to ``team_total``. Returns (rescaled distributions, scale)."""
    means = np.array([d.mean() for d in dists])
    scale = reconciliation_scale(means, team_total)
    return [rescale_distribution(d, scale) for d in dists], scale


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
