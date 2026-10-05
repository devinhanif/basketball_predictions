"""Pull game-level rows (games table) from nba_api, cached and resumable.

nba_api is imported lazily inside ``_fetch_games_for_season`` so importing
this module never requires network access or the dependency to be present.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached, upsert_rows

SOURCE = "games"

_GAMES_SCHEMA = [
    "game_id",
    "game_date",
    "season",
    "home_team",
    "away_team",
    "home_pts",
    "away_pts",
]


def _fetch_games_for_season(season: str) -> pl.DataFrame:
    """Hit nba_api's LeagueGameFinder for one season string, e.g. '2023-24'."""
    from nba_api.stats.endpoints import leaguegamefinder  # lazy import

    finder = leaguegamefinder.LeagueGameFinder(season_nullable=season)
    raw = finder.get_data_frames()[0]
    return _normalize_games_frame(pl.from_pandas(raw), season)


def _normalize_games_frame(raw: pl.DataFrame, season: str) -> pl.DataFrame:
    """Collapse nba_api's one-row-per-team-per-game format into one row per game."""
    home = raw.filter(~pl.col("MATCHUP").str.contains("@"))
    away = raw.filter(pl.col("MATCHUP").str.contains("@"))
    joined = home.join(away, on="GAME_ID", suffix="_away")
    out = joined.select(
        pl.col("GAME_ID").alias("game_id"),
        pl.col("GAME_DATE").str.to_date().alias("game_date"),
        pl.lit(season_to_int(season)).alias("season"),
        pl.col("TEAM_ID").alias("home_team"),
        pl.col("TEAM_ID_away").alias("away_team"),
        pl.col("PTS").alias("home_pts"),
        pl.col("PTS_away").alias("away_pts"),
    )
    return out


def season_to_int(season: str) -> int:
    """'2023-24' -> 2023."""
    return int(season.split("-")[0])


def pull_season_games(
    con: duckdb.DuckDBPyConnection,
    season: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) games for a season and upsert into ``games``."""
    df = fetch_cached(
        con,
        SOURCE,
        season,
        lambda: _fetch_games_for_season(season),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
    )
    upsert_games(con, df)
    return df


def upsert_games(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Idempotently load a games DataFrame into the ``games`` table."""
    upsert_rows(con, "games", _GAMES_SCHEMA, ["game_id"], df)
