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

#: NBA game_id prefixes to keep: 002=regular season, 004=playoffs, 005=play-in.
#: Dropped: 001=preseason, 003=all-star (and anything else, e.g. exhibitions).
#: See https://github.com/swar/nba_api game_id conventions; kept as a small,
#: easily-editable module constant rather than hardcoded inline.
KEPT_GAME_TYPE_PREFIXES: frozenset[str] = frozenset({"002", "004", "005"})

#: Valid NBA franchise team_id range; excludes international/exhibition "teams"
#: (e.g. team id 15020) that LeagueGameFinder sometimes includes.
MIN_VALID_TEAM_ID = 1610612737
MAX_VALID_TEAM_ID = 1610612766


def _fetch_games_for_season(season: str) -> pl.DataFrame:
    """Hit nba_api's LeagueGameFinder for one season string, e.g. '2023-24'."""
    from nba_api.stats.endpoints import leaguegamefinder  # lazy import

    finder = leaguegamefinder.LeagueGameFinder(season_nullable=season)
    raw = finder.get_data_frames()[0]
    return _normalize_games_frame(pl.from_pandas(raw), season)


def _filter_competitive_games(df: pl.DataFrame) -> pl.DataFrame:
    """Keep only real competitive games (regular season/playoffs/play-in).

    Drops preseason and all-star games by ``game_id`` prefix (see
    ``KEPT_GAME_TYPE_PREFIXES``) and drops any game involving a team id
    outside the valid NBA franchise range, which removes exhibition games
    against non-NBA (e.g. international) opponents. Expects a frame already
    shaped like the ``games`` table (``game_id``, ``home_team``, ``away_team``).
    """
    prefixes = list(KEPT_GAME_TYPE_PREFIXES)
    return df.filter(
        pl.col("game_id").str.slice(0, 3).is_in(prefixes)
        & pl.col("home_team").is_between(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID)
        & pl.col("away_team").is_between(MIN_VALID_TEAM_ID, MAX_VALID_TEAM_ID)
    )


def _normalize_games_frame(raw: pl.DataFrame, season: str) -> pl.DataFrame:
    """Collapse nba_api's one-row-per-team-per-game format into one row per game.

    Also drops non-competitive rows (preseason, all-star, exhibitions against
    non-NBA teams) via ``_filter_competitive_games``; this is the default
    behavior for every games pull.
    """
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
    return _filter_competitive_games(out)


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


def refresh_season_games(season: str) -> pl.DataFrame:
    """Uncached pull of one season's completed games (e.g. '2026-27').

    ``pull_season_games`` caches by season key forever, which would freeze an
    in-progress season at its first pull. The daily loop calls this instead and
    upserts the result itself (``upsert_games``). Network call; nothing is
    written to disk or to the database here.
    """
    return _fetch_games_for_season(season)


def upsert_games(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Idempotently load a games DataFrame into the ``games`` table."""
    upsert_rows(con, "games", _GAMES_SCHEMA, ["game_id"], df)


def game_ids_for_season(con: duckdb.DuckDBPyConnection, season: str) -> list[str]:
    """Resolve every already-ingested (filtered) game_id for a season string.

    ``season`` is e.g. '2023-24'. Reads from the ``games`` table rather than
    re-deriving game types, so results are already limited to the
    regular-season/playoff/play-in games that passed the ingest filter.
    """
    season_int = season_to_int(season)
    rows = con.execute(
        "SELECT game_id FROM games WHERE season = ? ORDER BY game_id", [season_int]
    ).fetchall()
    return [r[0] for r in rows]
