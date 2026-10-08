"""Incremental ingest: refresh the season's games, then box scores not yet loaded."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.boxscores import pull_game_boxscore
from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter
from nba.ingest.games import _fetch_games_for_season, upsert_games

FetchGames = Callable[[str], pl.DataFrame]
PullBox = Callable[[duckdb.DuckDBPyConnection, str], object]


def missing_boxscore_game_ids(
    con: duckdb.DuckDBPyConnection, season: int, *, limit: int | None = None
) -> list[str]:
    """Completed games (scores present) of ``season`` with no player_game_stats rows."""
    sql = """
        SELECT g.game_id FROM games g
        WHERE g.season = ? AND g.home_pts IS NOT NULL AND g.home_pts > 0
          AND NOT EXISTS (SELECT 1 FROM player_game_stats s WHERE s.game_id = g.game_id)
        ORDER BY g.game_date, g.game_id
    """
    ids = [r[0] for r in con.execute(sql, [season]).fetchall()]
    return ids[:limit] if limit is not None else ids


def incremental_ingest(
    con: duckdb.DuckDBPyConnection,
    season_str: str,
    season_int: int,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    max_boxscores: int = 250,
    fetch_games: FetchGames | None = None,
    pull_box: PullBox | None = None,
) -> dict[str, int]:
    """Refresh games (bypassing the season-keyed parquet cache, which would
    otherwise freeze the season at its first pull) and pull missing box scores.

    Box-score pulls reuse ``pull_game_boxscore`` (per-game cache, resumable);
    a failure on one game is counted and skipped, never fatal.
    """
    fetch = fetch_games or _fetch_games_for_season
    if rate_limiter is not None:
        rate_limiter.wait()
    games = fetch(season_str)
    upsert_games(con, games)

    def _default_pull(c: duckdb.DuckDBPyConnection, gid: str) -> object:
        return pull_game_boxscore(c, gid, data_dir=data_dir, rate_limiter=rate_limiter)

    pull = pull_box or _default_pull
    pulled = failed = 0
    for gid in missing_boxscore_game_ids(con, season_int, limit=max_boxscores):
        try:
            pull(con, gid)
            pulled += 1
        except Exception:
            failed += 1
    return {
        "games_rows": games.height,
        "boxscores_pulled": pulled,
        "boxscores_failed": failed,
        "boxscores_still_missing": len(missing_boxscore_game_ids(con, season_int)),
    }
