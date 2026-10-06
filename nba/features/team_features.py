"""Team-level, strictly as-of feature builders (SQL-first, per CLAUDE.md).

Everything here is derived from ``games`` and ``player_game_stats`` only --
no play-by-play / possessions dependency (the parser is not built yet) and
``team_context`` (rest/travel/injuries) is consulted only if it has rows, so
this module works today and gets richer for free once the data engineer
populates ``team_context``.

No-leakage guarantee: every rolling/"prior" column is a window aggregate
with frame ``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`` ordered by
``(game_date, game_id)`` -- i.e. it can only see rows strictly before the
current one for that team. The current game's own box score never
contributes to its own feature row. ``as_of`` on every row equals the
game's own date, by construction the rolling window never reaches into
``as_of`` or later.

There is no true possession count available without the play-by-play
parser, so "efficiency" here is a documented proxy: rolling points scored
(ORtg proxy), rolling points allowed (DRtg proxy), rolling total points
(pace proxy), and rolling team turnovers (ball-control proxy) -- all
derivable from ``games`` + ``player_game_stats`` box scores alone. Replace
with true per-100-possession ratings once the possessions table is
populated (rung 3+).
"""

from __future__ import annotations

import duckdb
import polars as pl

#: League-average fill values used for a team's first game (no prior
#: history at all). Config-driven so walk-forward CV can tune them later;
#: for now a single documented default per column.
FEATURE_DEFAULTS: dict[str, float] = {
    "win_pct_prior": 0.5,
    "avg_pts_for_prior": 110.0,
    "avg_pts_allowed_prior": 110.0,
    "avg_tov_prior": 13.0,
    "pace_proxy_prior": 220.0,
    "rest_days": 7.0,
}

_TEAM_GAME_FEATURES_SQL = """
WITH team_games AS (
    SELECT game_id, game_date, season, home_team AS team_id, away_team AS opp_team_id,
           home_pts AS team_pts, away_pts AS opp_pts, TRUE AS is_home
    FROM games
    UNION ALL
    SELECT game_id, game_date, season, away_team AS team_id, home_team AS opp_team_id,
           away_pts AS team_pts, home_pts AS opp_pts, FALSE AS is_home
    FROM games
),
team_tov AS (
    SELECT game_id, team_id, SUM(tov) AS team_tov
    FROM player_game_stats
    GROUP BY game_id, team_id
),
team_games_full AS (
    SELECT tg.*, COALESCE(tt.team_tov, 0) AS team_tov
    FROM team_games tg
    LEFT JOIN team_tov tt USING (game_id, team_id)
)
SELECT
    game_id,
    team_id,
    opp_team_id,
    game_date AS as_of,
    game_date,
    season,
    is_home,
    team_pts,
    opp_pts,
    team_tov,
    -- strictly-prior sample size: drives shrinkage weight downstream
    COUNT(*) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS games_played_prior,
    AVG(CASE WHEN team_pts > opp_pts THEN 1.0 ELSE 0.0 END) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS win_pct_prior,
    AVG(team_pts) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS avg_pts_for_prior,
    AVG(opp_pts) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS avg_pts_allowed_prior,
    AVG(team_tov) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS avg_tov_prior,
    AVG(team_pts + opp_pts) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS pace_proxy_prior,
    LAG(game_date) OVER (
        PARTITION BY team_id ORDER BY game_date, game_id
    ) AS prev_game_date
FROM team_games_full
ORDER BY game_date, game_id, team_id
"""


def _has_rows(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    if row is None:
        return False
    return bool(row[0])


#: DuckDB type name (``str(description_type_code)``) -> polars dtype. Used
#: to give an explicit schema to ``pl.DataFrame`` so all-null columns (e.g.
#: a team's first game, where every ``*_prior`` column is NULL) don't get
#: inferred as polars' ``Null`` dtype, which breaks later arithmetic.
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
    """Run ``sql`` and materialize the result as a polars DataFrame.

    Deliberately avoids DuckDB's ``.pl()``/``.arrow()`` convenience methods,
    which pull in pyarrow (not a pinned project dependency -- see
    ``nba/ingest/cache.py::insert_rows`` for the same constraint), in favor
    of plain ``fetchall()`` + an explicit schema derived from
    ``cursor.description`` (so all-null columns keep their real dtype
    instead of being inferred as polars ``Null``).
    """
    con.execute(sql)
    columns = [d[0] for d in con.description]
    duckdb_types = [str(d[1]) for d in con.description]
    schema = {
        name: _DUCKDB_TO_POLARS.get(dt, pl.Float64)
        for name, dt in zip(columns, duckdb_types, strict=True)
    }
    rows = con.fetchall()
    return pl.DataFrame(rows, schema=schema, orient="row")


def build_team_game_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per (game, team): strictly as-of rolling features.

    ``as_of`` equals the game's own date (features are "known as of tipoff
    of this game"); every ``*_prior`` column is computed from games with an
    earlier ``(game_date, game_id)`` ordering only -- see module docstring.
    """
    df = _query_to_polars(con, _TEAM_GAME_FEATURES_SQL)

    df = df.with_columns(
        rest_days=(pl.col("game_date") - pl.col("prev_game_date")).dt.total_days().cast(pl.Float64)
    )
    df = df.with_columns(
        [
            pl.col("win_pct_prior").fill_null(FEATURE_DEFAULTS["win_pct_prior"]),
            pl.col("avg_pts_for_prior").fill_null(FEATURE_DEFAULTS["avg_pts_for_prior"]),
            pl.col("avg_pts_allowed_prior").fill_null(FEATURE_DEFAULTS["avg_pts_allowed_prior"]),
            pl.col("avg_tov_prior").fill_null(FEATURE_DEFAULTS["avg_tov_prior"]),
            pl.col("pace_proxy_prior").fill_null(FEATURE_DEFAULTS["pace_proxy_prior"]),
            pl.col("rest_days").fill_null(FEATURE_DEFAULTS["rest_days"]),
        ]
    )
    df = df.with_columns(b2b=(pl.col("rest_days") <= 1.0))

    if _has_rows(con, "team_context"):
        tc = _query_to_polars(
            con,
            "SELECT game_id, team_id, rest_days AS tc_rest_days, b2b AS tc_b2b, "
            "travel_miles, len(injured_out) AS n_injured_out FROM team_context",
        )
        df = df.join(tc, on=["game_id", "team_id"], how="left")
        # team_context is an authoritative as-of source (e.g. real injury
        # reports); prefer it over the box-score-derived proxy when present.
        df = df.with_columns(
            rest_days=pl.coalesce(["tc_rest_days", "rest_days"]),
            b2b=pl.coalesce(["tc_b2b", "b2b"]),
            travel_miles=pl.col("travel_miles").fill_null(0.0),
            n_injured_out=pl.col("n_injured_out").fill_null(0),
        ).drop(["tc_rest_days", "tc_b2b"])
    else:
        df = df.with_columns(
            travel_miles=pl.lit(0.0),
            n_injured_out=pl.lit(0),
        )

    return df.drop("prev_game_date")


def build_matchup_features(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per game: home/away as-of features + target (home win).

    Joins :func:`build_team_game_features` back to itself on ``game_id`` for
    the home and away side of each game, so every feature column becomes a
    ``home_*`` / ``away_*`` / ``*_diff`` triple. ``as_of`` is carried through
    unchanged from the per-team table (== the game's own date).
    """
    team_feats = build_team_game_features(con)
    home = team_feats.filter(pl.col("is_home")).rename(
        {c: f"home_{c}" for c in team_feats.columns if c not in ("game_id",)}
    )
    away = team_feats.filter(~pl.col("is_home")).rename(
        {c: f"away_{c}" for c in team_feats.columns if c not in ("game_id",)}
    )
    merged = home.join(away, on="game_id", how="inner")

    merged = merged.with_columns(
        as_of=pl.col("home_as_of"),
        game_date=pl.col("home_game_date"),
        season=pl.col("home_season"),
        home_team=pl.col("home_team_id"),
        away_team=pl.col("away_team_id"),
        win_pct_diff=pl.col("home_win_pct_prior") - pl.col("away_win_pct_prior"),
        pts_for_diff=pl.col("home_avg_pts_for_prior") - pl.col("away_avg_pts_for_prior"),
        pts_allowed_diff=(
            pl.col("home_avg_pts_allowed_prior") - pl.col("away_avg_pts_allowed_prior")
        ),
        tov_diff=pl.col("home_avg_tov_prior") - pl.col("away_avg_tov_prior"),
        pace_diff=pl.col("home_pace_proxy_prior") - pl.col("away_pace_proxy_prior"),
        rest_diff=pl.col("home_rest_days") - pl.col("away_rest_days"),
        games_played_prior_min=pl.min_horizontal(
            "home_games_played_prior", "away_games_played_prior"
        ),
        y=(pl.col("home_team_pts") > pl.col("away_team_pts")).cast(pl.Int8),
    )
    return merged.sort("game_date", "game_id")


#: Feature columns handed to rungs 1-2 (team-level, as-of, no leakage).
MATCHUP_FEATURE_COLUMNS: list[str] = [
    "win_pct_diff",
    "pts_for_diff",
    "pts_allowed_diff",
    "tov_diff",
    "pace_diff",
    "rest_diff",
    "home_b2b",
    "away_b2b",
]
