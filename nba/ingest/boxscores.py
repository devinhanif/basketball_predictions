"""Pull per-player box score lines (player_game_stats) from nba_api.

Cached and resumable per game_id. nba_api is imported lazily so this module
is safe to import with no network access.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached, insert_rows

SOURCE = "boxscore"

_STATS_SCHEMA = [
    "game_id",
    "player_id",
    "team_id",
    "minutes",
    "pts",
    "reb",
    "ast",
    "fg3m",
    "stl",
    "blk",
    "tov",
    "starter",
]


def _parse_minutes(raw: str | None) -> float | None:
    """nba_api reports minutes as 'MM:SS' or 'MM.SSSSSS'; normalize to float minutes."""
    if raw is None or raw == "":
        return None
    if ":" in raw:
        mins, secs = raw.split(":")
        return float(mins) + float(secs) / 60.0
    return float(raw)


def _fetch_boxscore_for_game(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import boxscoretraditionalv2  # lazy import

    box = boxscoretraditionalv2.BoxScoreTraditionalV2(game_id=game_id)
    raw = pl.from_pandas(box.get_data_frames()[0])
    return _normalize_boxscore_frame(raw, game_id)


def _normalize_boxscore_frame(raw: pl.DataFrame, game_id: str) -> pl.DataFrame:
    minutes = [_parse_minutes(m) for m in raw["MIN"].to_list()]
    starters = [bool(pos) for pos in raw["START_POSITION"].to_list()]
    out = pl.DataFrame(
        {
            "game_id": [game_id] * len(raw),
            "player_id": raw["PLAYER_ID"].to_list(),
            "team_id": raw["TEAM_ID"].to_list(),
            "minutes": minutes,
            "pts": raw["PTS"].to_list(),
            "reb": raw["REB"].to_list(),
            "ast": raw["AST"].to_list(),
            "fg3m": raw["FG3M"].to_list(),
            "stl": raw["STL"].to_list(),
            "blk": raw["BLK"].to_list(),
            "tov": raw["TO"].to_list(),
            "starter": starters,
        }
    )
    return out


def pull_game_boxscore(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) box score for one game and upsert into player_game_stats."""
    df = fetch_cached(
        con,
        SOURCE,
        game_id,
        lambda: _fetch_boxscore_for_game(game_id),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
    )
    upsert_player_game_stats(con, df)
    return df


def upsert_player_game_stats(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    """Idempotently load a box score DataFrame into ``player_game_stats``.

    The table has no primary key in the schema, so we delete-then-insert
    per game_id to stay idempotent across re-runs against the same cache.
    """
    if df.is_empty():
        return
    game_ids = df["game_id"].unique().to_list()
    placeholders = ", ".join(["?"] * len(game_ids))
    con.execute(f"DELETE FROM player_game_stats WHERE game_id IN ({placeholders})", game_ids)
    insert_rows(con, "player_game_stats", _STATS_SCHEMA, df)
