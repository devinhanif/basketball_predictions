"""Minutes model (CLAUDE.md "Minutes model first").

"Most prop error comes from minutes uncertainty (injuries, blowouts,
rest). Model P(minutes) with a separate head, including a DNP/low-minute
branch." Implemented as a hurdle model:

    P(minutes = 0)      = 1 - p_play
    minutes | plays > 0  ~ Normal(mu, sigma) truncated to [0, max_minutes]

-- see ``nba.props.distributions.MinutesHurdleDist`` for the distribution
object this module produces.

CRITICAL NO-LEAKAGE RULE (CLAUDE.md risk #3): :func:`build_minutes_features`
returns **no** column derived from the target game's own minutes -- every
``*_prior`` column is a window aggregate over strictly earlier
``(game_date, game_id)`` rows for that player (same discipline as
``nba.features.team_features``). :func:`predict_minutes` only ever reads
that feature frame, never ``player_game_stats.minutes`` for the row being
predicted. ``tests/props/test_minutes_no_leakage.py`` proves this by
planting a wild future box score and asserting the earlier prediction is
byte-for-byte unchanged, plus a structural check that the feature frame
has no raw ``minutes`` column at all.
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate
from nba.props.config import MinutesModelConfig
from nba.props.distributions import MinutesHurdleDist

#: Identifier / as-of columns always present; everything else is a
#: strictly-prior rolling aggregate. No raw ``minutes`` column on purpose.
MINUTES_FEATURE_COLUMNS: list[str] = [
    "games_played_prior",
    "n_played_prior",
    "play_rate_prior",
    "avg_minutes_given_played_prior",
    "std_minutes_given_played_prior",
    "starter_rate_prior",
]

_MINUTES_FEATURES_SQL = """
WITH per_player AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        pgs.minutes,
        CASE WHEN pgs.minutes > 0 THEN 1.0 ELSE 0.0 END AS played,
        CASE WHEN pgs.starter THEN 1.0 ELSE 0.0 END AS started
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
)
SELECT
    game_id,
    player_id,
    team_id,
    game_date AS as_of,
    game_date,
    season,
    COUNT(*) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    COALESCE(
        SUM(played) OVER (
            PARTITION BY player_id ORDER BY game_date, game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ),
        0.0
    ) AS n_played_prior,
    AVG(played) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS play_rate_prior,
    AVG(CASE WHEN played = 1.0 THEN minutes END) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS avg_minutes_given_played_prior,
    STDDEV_SAMP(CASE WHEN played = 1.0 THEN minutes END) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS std_minutes_given_played_prior,
    AVG(started) OVER (
        PARTITION BY player_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS starter_rate_prior
FROM per_player
ORDER BY game_date, game_id, player_id
"""


def build_minutes_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, player): strictly as-of minutes-history features.

    Deliberately excludes the target game's own ``minutes`` -- see module
    docstring. Every ``*_prior`` column is NULL on a player's first
    appearance (handled by :func:`predict_minutes` via shrinkage toward
    the league default, never by fabricating a non-null value here).
    """
    con.execute(_MINUTES_FEATURES_SQL)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "as_of": pl.Date,
        "game_date": pl.Date,
        "season": pl.Int64,
        "games_played_prior": pl.Int64,
        "n_played_prior": pl.Float64,
        "play_rate_prior": pl.Float64,
        "avg_minutes_given_played_prior": pl.Float64,
        "std_minutes_given_played_prior": pl.Float64,
        "starter_rate_prior": pl.Float64,
    }
    return pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")


def predict_minutes(
    features: pl.DataFrame, config: MinutesModelConfig | None = None
) -> list[MinutesHurdleDist]:
    """Build one :class:`MinutesHurdleDist` per row of ``features``.

    Empirical-Bayes shrinkage (``nba.coldstart.shrinkage.shrink_rate``)
    blends each player's as-of observed rate toward the league default as
    ``n -> 0`` and toward the observed rate as ``n -> infinity`` -- the
    same discipline CLAUDE.md requires for every ``player_rates`` column.
    """
    cfg = config or MinutesModelConfig()
    n_games = features.select("games_played_prior").to_series().fill_null(0).to_numpy()
    n_played = features.select("n_played_prior").to_series().fill_null(0.0).to_numpy()
    play_rate_obs = features.select("play_rate_prior").to_series().fill_null(0.0).to_numpy()
    mu_obs = features.select("avg_minutes_given_played_prior").to_series().fill_null(0.0).to_numpy()
    sigma_obs = (
        features.select("std_minutes_given_played_prior").to_series().fill_null(0.0).to_numpy()
    )

    p_play = shrink_rate(play_rate_obs, n_games, cfg.default_p_play, cfg.k_play)
    mu = shrink_rate(mu_obs, n_played, cfg.default_mu, cfg.k_mu)
    sigma = shrink_rate(sigma_obs, n_played, cfg.default_sigma, cfg.k_sigma)
    sigma = np.clip(sigma, cfg.min_sigma, None)
    p_play = np.clip(p_play, 0.02, 0.99)
    mu = np.clip(mu, 0.0, cfg.max_minutes)

    return [
        MinutesHurdleDist(
            p_play=float(p_play[i]),
            mu=float(mu[i]),
            sigma=float(sigma[i]),
            max_minutes=cfg.max_minutes,
        )
        for i in range(features.height)
    ]
