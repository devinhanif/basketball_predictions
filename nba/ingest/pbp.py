"""Pull raw play-by-play from nba_api, cached and resumable per game_id.

Raw play-by-play has no dedicated DuckDB table in the schema (see
CLAUDE.md) -- it is cached to parquet under data/pbp/ and consumed by the
possession/stint parser (nba/parse/), a later milestone. This module only
handles the fetch/cache/resume concern.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR, RateLimiter, fetch_cached

SOURCE = "pbp"


def _fetch_pbp_for_game(game_id: str) -> pl.DataFrame:
    from nba_api.stats.endpoints import playbyplayv2  # lazy import

    pbp = playbyplayv2.PlayByPlayV2(game_id=game_id)
    raw = pbp.get_data_frames()[0]
    df = pl.from_pandas(raw)
    df.columns = [c.lower() for c in df.columns]
    return df


def pull_game_pbp(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Fetch (or load cached) raw play-by-play for one game. Caches only; no DB load."""
    return fetch_cached(
        con,
        SOURCE,
        game_id,
        lambda: _fetch_pbp_for_game(game_id),
        data_dir=data_dir,
        rate_limiter=rate_limiter,
    )
