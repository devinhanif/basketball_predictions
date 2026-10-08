"""Player-level, strictly as-of rebound + assist rates for rung-3 player attribution.

CLAUDE.md architecture ladder, rung 3 ("Full-game distributions, player
lines"). Companion to ``nba.features.player_possession_features`` (points
rates): this module supplies the per-player as-of rates
``nba.sim.player_attribution`` consumes to attribute simulated **rebounds**
and **assists** to on-court players, the same way the points head already
attributes points. Scope matches the points module's documented design:
strictly as-of, shrinkage-blended, with the same two documented data-reality
proxies (no true on-court lineup fraction, no reliable position data yet).

## Rate definitions

- ``orb_rate_prior``: P(this player grabs the rebound | their team just
  missed a field goal). Denominator ("rebound chances") is the player's
  *team's* own missed-FGA count for that game (``team_fga - team_fgm``,
  aggregated from ``player_game_stats``) -- the same "team box score, not
  true on-court possession" proxy ``player_possession_features`` uses for
  shot-share, documented there and not re-litigated here.
- ``drb_rate_prior``: P(this player grabs the rebound | the opponent just
  missed a field goal). Denominator is the *opponent's* missed-FGA count
  for that game.
- ``ast_rate_prior``: P(this player gets the assist | a teammate just made
  a field goal). Denominator is "teammate makes" = ``team_fgm - player_fgm``
  (a player can never assist their own basket).
- ``assisted_fg_rate_prior``: team-level (not player-level; duplicated
  across every player row for that team/game) as-of rate "fraction of this
  team's made field goals that are assisted" = cumulative ``team_ast /
  team_fgm``. This is the "team/shooter" assisted-FG probability the sim's
  assist-attribution step (CLAUDE.md milestone spec) needs to decide
  *whether* a given made shot gets an assist at all, before picking *who*
  gets it via ``ast_rate_prior``.

No-leakage: every ``*_prior`` column is a cumulative window aggregate over
strictly earlier ``(game_date, game_id)`` rows, ``ROWS BETWEEN UNBOUNDED
PRECEDING AND 1 PRECEDING`` -- identical discipline to every other as-of
feature in this project. See
``tests/ml/test_player_rebound_assist_features.py`` for the planted-future
proof.

Shrinkage (cold-start method 1, ``nba.coldstart.shrinkage.shrink_rate``,
reused unchanged): each player rate is blended toward its position-level
as-of prior (pooled across players sharing a position, degrading to one
league-wide "UNK" bucket when no position data exists -- same documented
limitation as ``player_possession_features``); if the position bucket
itself has zero observations, it falls back to the hardcoded league
constants below.

League-wide default constants below are documented approximate NBA-wide
averages (not fit from this project's own tables, since a true on-court
lineup fraction is unavailable -- same spirit/limitation as
``player_possession_features.LEAGUE_SHOT_SHARE_DEFAULT``): team offensive
rebound rate is roughly 25-28% of own misses, split roughly evenly across
~5 players on the floor (`LEAGUE_ORB_RATE_DEFAULT` ~= 0.05); team defensive
rebound rate is the complement of the opponent's OREB rate, roughly 72-75%
of opponent misses, split similarly (`LEAGUE_DRB_RATE_DEFAULT` ~= 0.15);
a league-average rotation player assists on roughly 10-15% of a teammate's
makes they're on court for (`LEAGUE_AST_RATE_DEFAULT` ~= 0.12); and roughly
60% of all made NBA field goals are assisted
(`LEAGUE_ASSISTED_FG_RATE_DEFAULT` ~= 0.60, a commonly cited league
average). Replace with values fit from this project's own multi-season
history once a tuning pass is run (same caveat ``tune_pseudo_count``
documents for pseudo-counts generally).
"""

from __future__ import annotations

import duckdb
import numpy as np
import polars as pl

from nba.coldstart.shrinkage import shrink_rate

LEAGUE_ORB_RATE_DEFAULT = 0.05
LEAGUE_DRB_RATE_DEFAULT = 0.15
LEAGUE_AST_RATE_DEFAULT = 0.12
LEAGUE_ASSISTED_FG_RATE_DEFAULT = 0.60

#: Pseudo-counts (empirical-Bayes shrinkage, method 1). Same order of
#: magnitude as ``player_possession_features``'s shot-share/zone-mix
#: pseudo-counts (150) for the player-level chance-denominated rates;
#: the team-level assisted-FG rate uses a smaller pseudo-count (100,
#: matching that module's ft_pct/zone_fg_pct choice) since it accumulates
#: against FGM, not FGA, and converges faster.
ORB_RATE_PSEUDO_COUNT = 150.0
DRB_RATE_PSEUDO_COUNT = 150.0
AST_RATE_PSEUDO_COUNT = 150.0
ASSISTED_FG_RATE_PSEUDO_COUNT = 100.0

#: Columns this module produces, one row per (game_id, player_id).
PLAYER_REB_AST_RATE_COLUMNS: list[str] = [
    "orb_rate_prior",
    "drb_rate_prior",
    "ast_rate_prior",
    "assisted_fg_rate_prior",
    "n_oreb_chances_prior",
    "n_dreb_chances_prior",
    "n_ast_chances_prior",
]

_WINDOW = "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"

_PLAYER_REB_AST_SQL = f"""
WITH team_box AS (
    SELECT game_id, team_id,
           SUM(COALESCE(fgm, 0)) AS team_fgm,
           SUM(COALESCE(fga, 0)) AS team_fga,
           SUM(COALESCE(ast, 0)) AS team_ast
    FROM player_game_stats
    GROUP BY 1, 2
),
opp_box AS (
    -- Opponent's made/missed FGA for the same game -- the DREB chance
    -- denominator. Self-join on game_id with the other team_id (exactly
    -- two teams per game in this schema).
    SELECT tb.game_id, tb.team_id,
           tb2.team_fgm AS opp_fgm, tb2.team_fga AS opp_fga
    FROM team_box tb
    JOIN team_box tb2 ON tb2.game_id = tb.game_id AND tb2.team_id != tb.team_id
),
per_game AS (
    SELECT
        pgs.game_id,
        pgs.player_id,
        pgs.team_id,
        g.game_date,
        g.season,
        COALESCE(ps.position, 'UNK') AS position,
        COALESCE(pgs.oreb, 0) AS oreb,
        COALESCE(pgs.dreb, 0) AS dreb,
        COALESCE(pgs.ast, 0) AS ast,
        COALESCE(pgs.fgm, 0) AS fgm,
        COALESCE(tb.team_fgm, 0) AS team_fgm,
        COALESCE(tb.team_fga, 0) AS team_fga,
        COALESCE(tb.team_ast, 0) AS team_ast,
        COALESCE(ob.opp_fgm, 0) AS opp_fgm,
        COALESCE(ob.opp_fga, 0) AS opp_fga,
        GREATEST(COALESCE(tb.team_fga, 0) - COALESCE(tb.team_fgm, 0), 0) AS team_missed_fga,
        GREATEST(COALESCE(ob.opp_fga, 0) - COALESCE(ob.opp_fgm, 0), 0) AS opp_missed_fga,
        GREATEST(COALESCE(tb.team_fgm, 0) - COALESCE(pgs.fgm, 0), 0) AS teammate_fgm
    FROM player_game_stats pgs
    JOIN games g USING (game_id)
    LEFT JOIN players_static ps ON ps.player_id = pgs.player_id
    LEFT JOIN team_box tb ON tb.game_id = pgs.game_id AND tb.team_id = pgs.team_id
    LEFT JOIN opp_box ob ON ob.game_id = pgs.game_id AND ob.team_id = pgs.team_id
),
player_prior AS (
    SELECT
        game_id, player_id, team_id, game_date, season, position,
        SUM(oreb) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS oreb_prior,
        SUM(dreb) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS dreb_prior,
        SUM(ast) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS ast_prior,
        SUM(team_missed_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS team_missed_fga_prior,
        SUM(opp_missed_fga) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS opp_missed_fga_prior,
        SUM(teammate_fgm) OVER (PARTITION BY player_id ORDER BY game_date, game_id {_WINDOW})
            AS teammate_fgm_prior
    FROM per_game
),
position_prior AS (
    SELECT
        game_id, player_id, position,
        SUM(oreb) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_oreb_prior,
        SUM(dreb) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_dreb_prior,
        SUM(ast) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_ast_prior,
        SUM(team_missed_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_team_missed_fga_prior,
        SUM(opp_missed_fga) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_opp_missed_fga_prior,
        SUM(teammate_fgm) OVER (
            PARTITION BY position ORDER BY game_date, game_id, player_id {_WINDOW}
        ) AS pos_teammate_fgm_prior
    FROM per_game
),
team_prior AS (
    SELECT
        game_id, team_id,
        SUM(team_ast) OVER (PARTITION BY team_id ORDER BY game_date, game_id {_WINDOW})
            AS team_ast_prior,
        SUM(team_fgm) OVER (PARTITION BY team_id ORDER BY game_date, game_id {_WINDOW})
            AS team_fgm_prior
    FROM (SELECT DISTINCT game_id, team_id, game_date, team_ast, team_fgm FROM per_game)
)
SELECT
    pp.game_id, pp.player_id, pp.team_id, pp.game_date, pp.season,
    pp.oreb_prior, pp.dreb_prior, pp.ast_prior,
    pp.team_missed_fga_prior, pp.opp_missed_fga_prior, pp.teammate_fgm_prior,
    pos.pos_oreb_prior, pos.pos_dreb_prior, pos.pos_ast_prior,
    pos.pos_team_missed_fga_prior, pos.pos_opp_missed_fga_prior, pos.pos_teammate_fgm_prior,
    tp.team_ast_prior, tp.team_fgm_prior
FROM player_prior pp
JOIN position_prior pos USING (game_id, player_id)
JOIN team_prior tp USING (game_id, team_id)
ORDER BY pp.game_date, pp.game_id, pp.player_id
"""


def _safe_ratio(num: pl.Expr, den: pl.Expr, default: float) -> pl.Expr:
    return pl.when(den > 0).then(num / den).otherwise(pl.lit(default))


def build_player_reb_ast_rates(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, player): strictly as-of, shrinkage-blended
    rebound + assist rates. See module docstring for rate definitions, the
    documented box-score proxies, and the shrinkage design.
    """
    con.execute(_PLAYER_REB_AST_SQL)
    columns = [d[0] for d in con.description]
    rows = con.fetchall()
    int_cols = {
        "oreb_prior",
        "dreb_prior",
        "ast_prior",
        "team_missed_fga_prior",
        "opp_missed_fga_prior",
        "teammate_fgm_prior",
        "pos_oreb_prior",
        "pos_dreb_prior",
        "pos_ast_prior",
        "pos_team_missed_fga_prior",
        "pos_opp_missed_fga_prior",
        "pos_teammate_fgm_prior",
        "team_ast_prior",
        "team_fgm_prior",
    }
    schema = {
        "game_id": pl.Utf8,
        "player_id": pl.Int64,
        "team_id": pl.Int64,
        "game_date": pl.Date,
        "season": pl.Int64,
    }
    for c in int_cols:
        schema[c] = pl.Float64
    df = pl.DataFrame(rows, schema={c: schema[c] for c in columns}, orient="row")
    if df.height == 0:
        return df.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(c) for c in PLAYER_REB_AST_RATE_COLUMNS]
        )
    df = df.with_columns([pl.col(c).fill_null(0.0) for c in int_cols])

    # Stage 1: position-level prior rates (fall back to the hardcoded
    # league constant when the position bucket itself has 0 observations).
    pos_orb = _safe_ratio(
        pl.col("pos_oreb_prior"), pl.col("pos_team_missed_fga_prior"), LEAGUE_ORB_RATE_DEFAULT
    )
    pos_drb = _safe_ratio(
        pl.col("pos_dreb_prior"), pl.col("pos_opp_missed_fga_prior"), LEAGUE_DRB_RATE_DEFAULT
    )
    pos_ast = _safe_ratio(
        pl.col("pos_ast_prior"), pl.col("pos_teammate_fgm_prior"), LEAGUE_AST_RATE_DEFAULT
    )
    df = df.with_columns(
        [
            pos_orb.alias("_pos_orb"),
            pos_drb.alias("_pos_drb"),
            pos_ast.alias("_pos_ast"),
        ]
    )

    # Stage 2: player's own observed rate, shrunk toward the position prior.
    orb_obs = _safe_ratio(pl.col("oreb_prior"), pl.col("team_missed_fga_prior"), 0.0)
    drb_obs = _safe_ratio(pl.col("dreb_prior"), pl.col("opp_missed_fga_prior"), 0.0)
    ast_obs = _safe_ratio(pl.col("ast_prior"), pl.col("teammate_fgm_prior"), 0.0)
    df = df.with_columns(
        [
            orb_obs.alias("_orb_obs"),
            drb_obs.alias("_drb_obs"),
            ast_obs.alias("_ast_obs"),
        ]
    )

    team_missed_fga_prior = df.get_column("team_missed_fga_prior").to_numpy()
    opp_missed_fga_prior = df.get_column("opp_missed_fga_prior").to_numpy()
    teammate_fgm_prior = df.get_column("teammate_fgm_prior").to_numpy()

    orb_shrunk = shrink_rate(
        df.get_column("_orb_obs").to_numpy(),
        team_missed_fga_prior,
        df.get_column("_pos_orb").to_numpy(),
        ORB_RATE_PSEUDO_COUNT,
    )
    drb_shrunk = shrink_rate(
        df.get_column("_drb_obs").to_numpy(),
        opp_missed_fga_prior,
        df.get_column("_pos_drb").to_numpy(),
        DRB_RATE_PSEUDO_COUNT,
    )
    ast_shrunk = shrink_rate(
        df.get_column("_ast_obs").to_numpy(),
        teammate_fgm_prior,
        df.get_column("_pos_ast").to_numpy(),
        AST_RATE_PSEUDO_COUNT,
    )

    # Team-level assisted-FG rate: cumulative team ast / team fgm, shrunk
    # toward the hardcoded league constant (no position-level pooling
    # stage needed -- this is already team-wide, not player-wide).
    assisted_fg_obs = _safe_ratio(pl.col("team_ast_prior"), pl.col("team_fgm_prior"), 0.0)
    df = df.with_columns([assisted_fg_obs.alias("_assisted_fg_obs")])
    team_fgm_prior = df.get_column("team_fgm_prior").to_numpy()
    assisted_fg_shrunk = shrink_rate(
        df.get_column("_assisted_fg_obs").to_numpy(),
        team_fgm_prior,
        np.full(df.height, LEAGUE_ASSISTED_FG_RATE_DEFAULT),
        ASSISTED_FG_RATE_PSEUDO_COUNT,
    )

    out = df.select(["game_id", "player_id", "team_id", "game_date", "season"]).with_columns(
        [
            pl.Series("orb_rate_prior", np.clip(orb_shrunk, 0.0, 1.0)),
            pl.Series("drb_rate_prior", np.clip(drb_shrunk, 0.0, 1.0)),
            pl.Series("ast_rate_prior", np.clip(ast_shrunk, 0.0, 1.0)),
            pl.Series("assisted_fg_rate_prior", np.clip(assisted_fg_shrunk, 0.0, 1.0)),
            pl.Series("n_oreb_chances_prior", team_missed_fga_prior),
            pl.Series("n_dreb_chances_prior", opp_missed_fga_prior),
            pl.Series("n_ast_chances_prior", teammate_fgm_prior),
        ]
    )
    return out


__all__ = [
    "LEAGUE_ASSISTED_FG_RATE_DEFAULT",
    "LEAGUE_AST_RATE_DEFAULT",
    "LEAGUE_DRB_RATE_DEFAULT",
    "LEAGUE_ORB_RATE_DEFAULT",
    "PLAYER_REB_AST_RATE_COLUMNS",
    "build_player_reb_ast_rates",
]
