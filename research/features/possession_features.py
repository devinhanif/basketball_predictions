"""Rung-3 as-of team possession rates (CLAUDE.md architecture ladder, rung 3).

True per-possession ratings, built directly from the ``possessions`` table
(not the box-score proxies in ``nba.features.team_features``, which exist
because the parser hadn't shipped yet). Everything here keeps the same
no-leakage guarantee as ``team_features.py``: every ``*_prior`` column is a
window aggregate over strictly earlier ``(game_date, game_id)`` rows for
that team, ordered the same way, so the current game's own possessions
never contribute to its own feature row.

Rates are blended with a league-average prior via the cold-start method-1
empirical-Bayes shrinkage already shipped in ``nba.coldstart.shrinkage``
(reused as-is -- it is a generic ``shrink_rate(r_obs, n, r_prior, k)``, not
player-specific), so a team's very first games (``n_poss`` near 0) predict
close to league average instead of an overconfident small-sample rate.
"""

from __future__ import annotations

import duckdb
import polars as pl

from nba.coldstart.shrinkage import shrink_rate

#: Pseudo-count (in possessions) for the offensive/defensive rating
#: shrinkage -- roughly 3 games' worth of possessions (~100/game) before a
#: team's own observed rate dominates the league-average prior. Not tuned
#: by walk-forward CV yet (CLAUDE.md flags ``k`` as "tunable by walk-forward
#: CV"); a documented, round default, same spirit as
#: ``research.coldstart.config.DEFAULT_PSEUDO_COUNTS``.
POSSESSION_RATING_PSEUDO_COUNT = 300.0

#: Fallback league-average points-per-possession, used only when there is
#: no as-of history at all yet (the very first possessions ever observed
#: in the dataset) so the global as-of window average is null. Computed
#: once from the full historical ``possessions`` table (~1.05M rows,
#: 2022-23 through 2025-26 seasons): empirical mean points per possession
#: is ~1.146 (ORtg ~114.6), consistent with published league efficiency
#: for this era. This is a *global shape* constant (how many points a
#: possession is worth on average, across the whole league and all
#: seasons) -- not derived from, or predictive of, any single team or
#: game, so it carries no game-level leakage risk; it only matters for the
#: handful of possessions at the very start of the dataset where the as-of
#: window is empty.
LEAGUE_AVG_PPP_DEFAULT = 1.146

#: Fallback possessions-per-game pace, used the same way as
#: ``LEAGUE_AVG_PPP_DEFAULT`` for a team's first game (no prior pace
#: history). ~99.6 possessions/team/game is the documented reconciliation
#: target in CLAUDE.md's data schema section.
PACE_DEFAULT = 99.6

_TEAM_POSSESSION_RATES_SQL = """
WITH off_agg AS (
    SELECT game_id, off_team AS team_id, COUNT(*) AS off_poss, SUM(pts) AS off_pts
    FROM possessions
    GROUP BY game_id, off_team
),
def_agg AS (
    SELECT game_id, def_team AS team_id, COUNT(*) AS def_poss, SUM(pts) AS pts_allowed
    FROM possessions
    GROUP BY game_id, def_team
),
team_games AS (
    SELECT game_id, game_date, season, home_team AS team_id, TRUE AS is_home
    FROM games
    UNION ALL
    SELECT game_id, game_date, season, away_team AS team_id, FALSE AS is_home
    FROM games
),
joined AS (
    SELECT
        tg.game_id, tg.game_date, tg.season, tg.team_id, tg.is_home,
        COALESCE(oa.off_poss, 0) AS off_poss,
        COALESCE(oa.off_pts, 0) AS off_pts,
        COALESCE(da.def_poss, 0) AS def_poss,
        COALESCE(da.pts_allowed, 0) AS pts_allowed
    FROM team_games tg
    LEFT JOIN off_agg oa ON oa.game_id = tg.game_id AND oa.team_id = tg.team_id
    LEFT JOIN def_agg da ON da.game_id = tg.game_id AND da.team_id = tg.team_id
),
-- One row per *game* (not per team-side) -- both teams' possessions
-- combined. Needed so the league-wide as-of window below has a genuine
-- total order by (game_date, game_id): the per-team-side `joined` table
-- above has two rows sharing an identical (game_date, game_id) for every
-- game, so a window ordered by just those two columns over `joined`
-- cannot deterministically place both of a game's own rows after each
-- other -- DuckDB is free to interleave them, which would leak one side's
-- own points into the other side's "prior" sum for the *same* game. A
-- per-game (unique game_id) table has no such tie.
game_totals AS (
    SELECT game_id, ANY_VALUE(game_date) AS game_date, SUM(off_pts) AS game_pts,
           SUM(off_poss) AS game_poss
    FROM joined
    GROUP BY game_id
),
league_prior AS (
    SELECT
        game_id,
        SUM(game_pts) OVER (
            ORDER BY game_date, game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_pts_prior_sum,
        SUM(game_poss) OVER (
            ORDER BY game_date, game_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS league_poss_prior_sum
    FROM game_totals
)
SELECT
    j.game_id,
    j.game_date,
    j.season,
    j.team_id,
    j.is_home,
    j.off_poss,
    j.off_pts,
    j.def_poss,
    j.pts_allowed,
    -- strictly-prior possession counts/sums, per team (no leakage: this
    -- game's own off_poss/off_pts never enter its own row).
    SUM(j.off_poss) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS off_poss_prior_sum,
    SUM(j.off_pts) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS off_pts_prior_sum,
    SUM(j.def_poss) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS def_poss_prior_sum,
    SUM(j.pts_allowed) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS pts_allowed_prior_sum,
    AVG(j.off_poss) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS pace_prior,
    COUNT(*) OVER (
        PARTITION BY j.team_id ORDER BY j.game_date, j.game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    lp.league_pts_prior_sum,
    lp.league_poss_prior_sum
FROM joined j
JOIN league_prior lp ON lp.game_id = j.game_id
ORDER BY j.game_date, j.game_id, j.team_id
"""

#: DuckDB cursor-description type name -> polars dtype, mirroring
#: ``nba.features.team_features._DUCKDB_TO_POLARS`` (duplicated rather
#: than imported to keep this module's only dependency on
#: ``team_features`` at zero -- see that module's own duplication
#: comment for the same rationale re: avoiding tight coupling/circular
#: imports between the two as-of feature builders).
_DUCKDB_TO_POLARS: dict[str, type[pl.DataType]] = {
    "BIGINT": pl.Int64,
    "INTEGER": pl.Int64,
    "SMALLINT": pl.Int64,
    "HUGEINT": pl.Int64,
    "DOUBLE": pl.Float64,
    "FLOAT": pl.Float64,
    "DATE": pl.Date,
    "BOOLEAN": pl.Boolean,
    "VARCHAR": pl.Utf8,
}


def _query_to_polars(con: duckdb.DuckDBPyConnection, sql: str) -> pl.DataFrame:
    con.execute(sql)
    columns = [d[0] for d in con.description]
    duckdb_types = [str(d[1]) for d in con.description]
    schema = {
        name: _DUCKDB_TO_POLARS.get(dt, pl.Float64)
        for name, dt in zip(columns, duckdb_types, strict=True)
    }
    rows = con.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def build_team_possession_rates(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, team): strictly as-of, shrinkage-blended possession rates.

    Works even when ``possessions`` is empty (e.g. the committed fixture,
    which only loads ``games``/``player_game_stats``): every team-game row
    still exists (derived from ``games``), just with ``off_poss``/``def_poss``
    at 0 and every ``*_prior`` sum at 0, which shrinks fully to the
    documented league-average constants below rather than raising or
    producing nulls.
    """
    df = _query_to_polars(con, _TEAM_POSSESSION_RATES_SQL)

    # DuckDB's window SUM/AVG return NULL (not 0) when there are zero
    # preceding rows (a team's/the league's very first game) -- coalesce to
    # 0 up front so every denominator check below is a plain numeric
    # comparison instead of null-propagating through `pl.when`.
    df = df.with_columns(
        [
            pl.col("off_poss_prior_sum").fill_null(0),
            pl.col("off_pts_prior_sum").fill_null(0),
            pl.col("def_poss_prior_sum").fill_null(0),
            pl.col("pts_allowed_prior_sum").fill_null(0),
            pl.col("league_poss_prior_sum").fill_null(0),
            pl.col("league_pts_prior_sum").fill_null(0),
            pl.col("pace_prior").fill_null(PACE_DEFAULT),
            pl.col("games_played_prior").fill_null(0),
        ]
    )

    league_avg_ppp_asof = (
        pl.col("league_pts_prior_sum") / pl.col("league_poss_prior_sum")
    ).fill_nan(LEAGUE_AVG_PPP_DEFAULT)
    df = df.with_columns(
        league_avg_ppp_asof=pl.when(pl.col("league_poss_prior_sum") > 0)
        .then(league_avg_ppp_asof)
        .otherwise(pl.lit(LEAGUE_AVG_PPP_DEFAULT))
    )

    off_rate_obs = (pl.col("off_pts_prior_sum") / pl.col("off_poss_prior_sum")).fill_nan(
        LEAGUE_AVG_PPP_DEFAULT
    )
    def_rate_obs = (pl.col("pts_allowed_prior_sum") / pl.col("def_poss_prior_sum")).fill_nan(
        LEAGUE_AVG_PPP_DEFAULT
    )
    df = df.with_columns(
        off_rate_obs=pl.when(pl.col("off_poss_prior_sum") > 0)
        .then(off_rate_obs)
        .otherwise(pl.col("league_avg_ppp_asof")),
        def_rate_obs=pl.when(pl.col("def_poss_prior_sum") > 0)
        .then(def_rate_obs)
        .otherwise(pl.col("league_avg_ppp_asof")),
        pace_prior=pl.col("pace_prior").fill_null(PACE_DEFAULT),
        games_played_prior=pl.col("games_played_prior").fill_null(0),
    )

    off_poss_n = df.get_column("off_poss_prior_sum").fill_null(0.0).to_numpy()
    def_poss_n = df.get_column("def_poss_prior_sum").fill_null(0.0).to_numpy()
    league_prior = df.get_column("league_avg_ppp_asof").to_numpy()
    off_shrunk = shrink_rate(
        df.get_column("off_rate_obs").to_numpy(),
        off_poss_n,
        league_prior,
        POSSESSION_RATING_PSEUDO_COUNT,
    )
    def_shrunk = shrink_rate(
        df.get_column("def_rate_obs").to_numpy(),
        def_poss_n,
        league_prior,
        POSSESSION_RATING_PSEUDO_COUNT,
    )
    df = df.with_columns(
        off_rtg_prior=pl.Series(off_shrunk),
        def_rtg_prior=pl.Series(def_shrunk),
    )

    return df.select(
        [
            "game_id",
            "game_date",
            "season",
            "team_id",
            "is_home",
            "off_rtg_prior",
            "def_rtg_prior",
            "pace_prior",
            "league_avg_ppp_asof",
            "games_played_prior",
            "off_poss_prior_sum",
            "def_poss_prior_sum",
        ]
    )


#: Columns rung 3 consumes per side of the matchup -- see
#: ``build_possession_matchup_features``.
POSSESSION_FEATURE_COLUMNS: list[str] = [
    "off_rtg_prior",
    "def_rtg_prior",
    "pace_prior",
    "league_avg_ppp_asof",
    "games_played_prior",
    "off_poss_prior_sum",
]


def build_possession_matchup_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per game: ``home_*``/``away_*`` possession-rate features for rung 3.

    Meant to be left-joined onto ``nba.features.team_features.
    build_matchup_features``'s output by ``game_id`` -- see
    ``research.eval.run.run_experiment``. Carries only ``game_id`` plus the new
    columns (no ``game_date``/``season``/etc.) to avoid clobbering columns
    already present on the matchup frame it's joined into.
    """
    team_feats = build_team_possession_rates(con)
    home = team_feats.filter(pl.col("is_home")).rename(
        {c: f"home_{c}" for c in POSSESSION_FEATURE_COLUMNS}
    )
    away = team_feats.filter(~pl.col("is_home")).rename(
        {c: f"away_{c}" for c in POSSESSION_FEATURE_COLUMNS}
    )
    home = home.select(["game_id", *[f"home_{c}" for c in POSSESSION_FEATURE_COLUMNS]])
    away = away.select(["game_id", *[f"away_{c}" for c in POSSESSION_FEATURE_COLUMNS]])
    merged = home.join(away, on="game_id", how="inner")
    return merged.with_columns(league_avg_ppp_asof=pl.col("home_league_avg_ppp_asof")).drop(
        ["home_league_avg_ppp_asof", "away_league_avg_ppp_asof"]
    )
