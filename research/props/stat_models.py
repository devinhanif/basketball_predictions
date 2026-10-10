"""Frequency-severity stat distributions (CLAUDE.md "Phase 2 -- Props").

"Frequency-severity decomposition (borrowed from insurance): model minutes
x per-minute rate x efficiency instead of predicting the stat directly.
Points = attempts (frequency) x make rate and value (severity)."

Schema note / documented proxy: ``player_game_stats`` has no
``fga``/``fta`` (attempts) columns and the ``possessions`` table is empty
(the play-by-play parser is a separate, not-yet-built milestone -- see
``nba/features/player_features.py`` for the same constraint). "Attempts"
therefore can't be observed directly. Points are decomposed instead into
an **implied scoring-event count** (frequency) and a **value per event**
(severity), both derived from exactly-known box-score fields (``pts``,
``fg3m``):

    made_3s            = fg3m                              (known exactly)
    made_2s_or_fts      = max(pts - 3*fg3m, 0) / 2           (implied, makes-equivalent)
    events              = made_3s + made_2s_or_fts
    value_per_event     = pts / events   (~2.0-3.0; captures 3PT mix + FT trips)

This is a legitimate frequency-severity split (count of scoring events x
value per event) even though it is not literally attempts x make-rate --
replace with a true attempts-based model once the PBP parser lands.

Rebounds/assists/3PM have severity = 1 per occurrence (a board is a
board), so they collapse to a direct Negative Binomial: mean = projected
minutes x per-minute rate (frequency only), dispersion fit per stat with
empirical-Bayes shrinkage toward one global default (CLAUDE.md asks for
"per archetype"; a full archetype clustering is owned by
``research.coldstart.archetypes`` and out of scope for this milestone -- using
one global per-stat default is a documented, explicitly-flagged scope cut,
consistent with this codebase's existing proxy pattern).

MIS-CENTERING FIX: see ``nba.props.minutes`` module docstring -- every
rolling sum below is bounded to a trailing ``lookback_games`` window
(default 150) rather than a player's full career, so a durable role
change (more usage, more minutes) isn't permanently diluted by stale
early-career games.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import numpy as np
import polars as pl

from nba.features.shrinkage import shrink_rate
from nba.props.config import CountStatConfig, PointsConfig, ZeroInflationConfig
from nba.props.distributions import (
    GammaDist,
    NegBinDist,
    ZeroInflatedNegBinDist,
    gamma_from_moments,
)

#: Default trailing-games window, mirrors ``PointsConfig``/``CountStatConfig``
#: ``lookback_games`` (kept as a literal default too so the existing
#: ``build_points_features(con)`` / ``build_count_stat_features(con, stat)``
#: call sites -- used throughout the tests -- keep working unchanged).
DEFAULT_LOOKBACK_GAMES = 150

_COUNT_FEATURES_SQL = """
SELECT
    pgs.game_id,
    pgs.player_id,
    g.game_date,
    COUNT(*) OVER (
        PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    COALESCE(
        SUM(pgs.minutes) OVER (
            PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS minutes_prior,
    COALESCE(
        SUM(pgs.{stat}) OVER (
            PARTITION BY pgs.player_id ORDER BY g.game_date, pgs.game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS stat_sum_prior
FROM player_game_stats pgs
JOIN games g USING (game_id)
ORDER BY g.game_date, pgs.game_id, pgs.player_id
"""

_POINTS_FEATURES_SQL = """
WITH per_player AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        g.game_date,
        pgs.minutes,
        pgs.pts,
        pgs.fg3m,
        GREATEST(pgs.pts - 3 * pgs.fg3m, 0) / 2.0 AS implied_2pt_or_ft_events
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
)
SELECT
    game_id,
    player_id,
    game_date,
    COUNT(*) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    COALESCE(
        SUM(minutes) OVER (
            PARTITION BY player_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS minutes_prior,
    COALESCE(
        SUM(pts) OVER (
            PARTITION BY player_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS pts_sum_prior,
    COALESCE(
        SUM(fg3m + implied_2pt_or_ft_events) OVER (
            PARTITION BY player_id ORDER BY game_date, game_id
            ROWS BETWEEN {lookback} PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS events_sum_prior
FROM per_player
ORDER BY game_date, game_id, player_id
"""


def _query_schema(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    con.execute(sql)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    types: dict[str, type[pl.DataType]] = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "game_date": pl.Date,
        "games_played_prior": pl.Int64,
        "minutes_prior": pl.Float64,
        "stat_sum_prior": pl.Float64,
        "pts_sum_prior": pl.Float64,
        "events_sum_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: types[c] for c in columns}, orient="row")


def build_count_stat_features(
    con: duckdb.DuckDBPyConnection, stat: str, lookback_games: int = DEFAULT_LOOKBACK_GAMES
) -> pl.DataFrame:
    """As-of rolling (minutes, stat) sums for a direct-count stat (reb/ast/fg3m).

    Strictly-prior window, same discipline as ``nba.features.team_features``
    -- see module docstring for why rate = sum(stat)/sum(minutes) rather
    than an average of per-game rates (pools small-minute games correctly).

    ``lookback_games`` bounds the window to the trailing N games (default
    150, ``CountStatConfig.lookback_games``) instead of a player's entire
    career -- see ``nba.props.minutes`` module docstring for the
    mis-centering bug this avoids (a stale career-to-date average
    permanently drags down a player's rate after a durable role change).
    """
    if stat not in {"reb", "ast", "fg3m"}:
        raise ValueError(f"build_count_stat_features only supports reb/ast/fg3m, got {stat!r}")
    sql = _COUNT_FEATURES_SQL.format(stat=stat, lookback=int(lookback_games))
    return _query_schema(con, sql)


def build_points_features(
    con: duckdb.DuckDBPyConnection, lookback_games: int = DEFAULT_LOOKBACK_GAMES
) -> pl.DataFrame:
    """As-of rolling points frequency-severity inputs -- see module docstring.

    ``lookback_games`` -- see :func:`build_count_stat_features`.
    """
    sql = _POINTS_FEATURES_SQL.format(lookback=int(lookback_games))
    return _query_schema(con, sql)


@dataclass
class CountStatMoments:
    mean: np.ndarray
    var: np.ndarray


def count_stat_moments(
    features: pl.DataFrame,
    minutes_mean: np.ndarray,
    minutes_var: np.ndarray,
    config: CountStatConfig,
    k_multiplier: np.ndarray | None = None,
) -> CountStatMoments:
    """Mean/variance of a direct-count stat (reb/ast/fg3m) given projected minutes.

    Rate (per minute) is empirical-Bayes shrunk toward ``config.default_rate_per_min``
    using minutes played as the sample-size unit. Mean/variance combine the
    rate with the *minutes model's* own uncertainty via the law of total
    variance (``Var[N] = E[Var[N|min]] + Var[E[N|min]]``), approximating
    the conditional-on-minutes process as Poisson-with-NegBin-overdispersion
    (``Var[N|min] = rate*min + alpha*(rate*min)^2``):

        mean        = rate * E[minutes]
        var         ~= mean + alpha*mean^2 + rate^2 * Var[minutes]

    (the small alpha*rate^2*Var[minutes] cross-term is dropped -- second
    order relative to the other terms at realistic minutes variances).

    ``k_multiplier`` (optional, one per row, default all-ones): role-change
    boost from ``nba.props.role_change`` -- multiplies both pseudo-counts
    so a flagged change pulls the rate/dispersion harder toward the prior.
    """
    n_minutes = features.select("minutes_prior").to_series().fill_null(0.0).to_numpy()
    stat_sum = features.select("stat_sum_prior").to_series().fill_null(0.0).to_numpy()
    k_mult = (
        np.ones(features.height) if k_multiplier is None else np.asarray(k_multiplier, dtype=float)
    )
    rate_obs = np.divide(stat_sum, n_minutes, out=np.zeros_like(stat_sum), where=n_minutes > 0)
    rate = shrink_rate(
        rate_obs, n_minutes, config.default_rate_per_min, config.k_rate_minutes * k_mult
    )

    n_games = features.select("games_played_prior").to_series().fill_null(0).to_numpy()
    # Per-player dispersion method-of-moments estimate needs per-game
    # residuals; with only cumulative sums available here we shrink
    # straight to the documented archetype-free default (see module
    # docstring "scope cut") rather than re-deriving alpha from raw rows.
    alpha = shrink_rate(
        np.full_like(rate, config.default_alpha),
        np.zeros_like(n_games, dtype=float),
        config.default_alpha,
        config.k_alpha_games * k_mult,
    )

    mean = rate * minutes_mean
    var = mean + alpha * mean**2 + (rate**2) * minutes_var
    var = np.clip(var, mean, None)  # NegBin requires var >= mean
    return CountStatMoments(mean=mean, var=var)


def build_negbin_distributions(moments: CountStatMoments) -> list[NegBinDist]:
    out = []
    for mu, var in zip(moments.mean, moments.var, strict=True):
        if var <= mu:
            out.append(NegBinDist(mu=float(mu), alpha=1e-6))
        else:
            alpha = (var - mu) / (mu * mu) if mu > 1e-9 else 1e-6
            out.append(NegBinDist(mu=float(mu), alpha=float(alpha)))
    return out


def fit_zero_inflation(
    y: np.ndarray, base: list[NegBinDist], config: ZeroInflationConfig
) -> np.ndarray:
    """Per-row extra-zero probability ``pi`` for a count stat.

    Compares the *observed* zero rate (pooled across ``y``, a simple but
    honest estimate given how little data a single player-game run has)
    against the base NegBin's implied zero probability. If the observed
    rate exceeds it by more than ``config.tolerance`` and there are at
    least ``config.min_games`` observations, the excess is attributed to
    zero-inflation (``pi = (p_obs - p_nb) / (1 - p_nb)``, the standard
    method-of-moments zero-inflation estimator); otherwise ``pi = 0`` for
    every row (no zero-inflation detected / not enough data to tell).
    """
    n = len(y)
    if n < config.min_games:
        return np.zeros(n, dtype=float)
    observed_zero_rate = float(np.mean(y == 0))
    nb_zero_probs = np.array([d.cdf(0) for d in base])  # P(X=0) for NegBin
    nb_zero_mean = float(np.mean(nb_zero_probs))
    if observed_zero_rate - nb_zero_mean <= config.tolerance:
        return np.zeros(n, dtype=float)
    pi = np.array(
        [max((observed_zero_rate - p) / (1.0 - p), 0.0) if p < 1.0 else 0.0 for p in nb_zero_probs]
    )
    return np.asarray(np.clip(pi, 0.0, 0.95), dtype=float)


def build_zinb_distributions(
    base: list[NegBinDist], pi: np.ndarray
) -> list[ZeroInflatedNegBinDist]:
    return [ZeroInflatedNegBinDist(pi=float(p), base=b) for p, b in zip(pi, base, strict=True)]


@dataclass
class PointsMoments:
    mean: np.ndarray
    var: np.ndarray


def points_moments(
    features: pl.DataFrame,
    minutes_mean: np.ndarray,
    minutes_var: np.ndarray,
    config: PointsConfig,
    k_multiplier: np.ndarray | None = None,
) -> PointsMoments:
    """Compound frequency-severity moments for points -- see module docstring.

    N (scoring events) | minutes ~ approximately Poisson(events_rate * minutes);
    X (value per event) ~ mean/var fit from history, shrunk toward the
    league default. Aggregate S = sum_{i=1}^{N} X_i, the classic
    actuarial "aggregate loss" compound-distribution moments:

        E[S]   = E[N] * E[X]
        Var[S] = E[N] * Var[X] + E[X]^2 * Var[N]

    with ``E[N]``/``Var[N]`` themselves propagating the minutes model's
    own mean/variance via the law of total variance (see
    :func:`count_stat_moments` for the same pattern).

    ``k_multiplier`` (optional, one per row, default all-ones): role-change
    boost from ``nba.props.role_change``, same wiring as :func:`count_stat_moments`.
    """
    n_minutes = features.select("minutes_prior").to_series().fill_null(0.0).to_numpy()
    pts_sum = features.select("pts_sum_prior").to_series().fill_null(0.0).to_numpy()
    events_sum = features.select("events_sum_prior").to_series().fill_null(0.0).to_numpy()
    k_mult = (
        np.ones(features.height) if k_multiplier is None else np.asarray(k_multiplier, dtype=float)
    )

    events_rate_obs = np.divide(
        events_sum, n_minutes, out=np.zeros_like(events_sum), where=n_minutes > 0
    )
    events_rate = shrink_rate(
        events_rate_obs, n_minutes, config.default_events_per_min, config.k_events_minutes * k_mult
    )

    value_obs = np.divide(pts_sum, events_sum, out=np.zeros_like(pts_sum), where=events_sum > 0)
    value_mean = shrink_rate(
        value_obs, events_sum, config.default_value_per_event, config.k_value_events * k_mult
    )
    # Per-event value variance: shrunk straight to the documented default
    # (per-player variance of value-per-event needs raw per-game samples,
    # not available from the cumulative sums here -- see the same scope
    # cut documented on count_stat_moments).
    value_var = np.full_like(value_mean, config.default_value_variance)

    mean_events = events_rate * minutes_mean
    var_events = mean_events + (events_rate**2) * minutes_var  # alpha≈0 (near-Poisson) for events
    var_events = np.clip(var_events, mean_events, None)

    mean_pts = mean_events * value_mean
    var_pts = mean_events * value_var + (value_mean**2) * var_events
    var_pts = np.clip(var_pts, 1e-6, None)
    return PointsMoments(mean=mean_pts, var=var_pts)


def build_points_distributions(moments: PointsMoments) -> list[GammaDist]:
    pairs = zip(moments.mean, moments.var, strict=True)
    return [gamma_from_moments(float(m), float(v)) for m, v in pairs]
