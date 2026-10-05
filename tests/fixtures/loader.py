"""Load the tiny committed fixture dataset into an ephemeral DuckDB.

No network access, no nba_api import. Used by tests/data/ (schema +
coverage checks) and will be reused by the parser milestone's fixture
based reconciliation tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from nba.db.connect import connect
from nba.ingest.cache import insert_rows

FIXTURES_DIR = Path(__file__).parent


def _load_json(name: str) -> list[dict]:
    path = FIXTURES_DIR / name
    with path.open() as f:
        data: list[dict] = json.load(f)
    return data


def load_games() -> pl.DataFrame:
    return pl.DataFrame(_load_json("games.json")).with_columns(pl.col("game_date").str.to_date())


def load_player_game_stats() -> pl.DataFrame:
    return pl.DataFrame(_load_json("player_game_stats.json"))


def load_pbp() -> dict[str, pl.DataFrame]:
    """One raw play-by-play DataFrame per fixture game_id."""
    pbp_dir = FIXTURES_DIR / "pbp"
    out: dict[str, pl.DataFrame] = {}
    for path in sorted(pbp_dir.glob("*.json")):
        with path.open() as f:
            rows: list[dict] = json.load(f)
        out[path.stem] = pl.DataFrame(rows)
    return out


def build_fixture_db(db_path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    """Apply schema.sql to a fresh DuckDB and load all fixture tables into it."""
    con = connect(db_path)

    games = load_games()
    insert_rows(con, "games", games.columns, games)

    stats = load_player_game_stats()
    insert_rows(con, "player_game_stats", stats.columns, stats)

    return con
