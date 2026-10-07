"""Idempotent loader: parsed ``possessions`` rows -> DuckDB.

Thin wrapper around ``nba.ingest.cache.upsert_rows`` keyed on the table's
primary key (``game_id``, ``poss_idx``), so re-running the parser for a
game (e.g. after a parser fix) safely overwrites that game's rows instead
of duplicating them. Not run against ``nba.duckdb`` by this agent -- see
``nba/parse/possessions.py`` module docstring; the maintainer wires this
into the real ingest pipeline once the box-score backfill lands.
"""

from __future__ import annotations

import duckdb
import polars as pl

from nba.ingest.cache import upsert_rows
from nba.parse.possessions import POSSESSIONS_COLUMNS

_CONFLICT_KEY = ["game_id", "poss_idx"]


def load_possessions(con: duckdb.DuckDBPyConnection, possessions: pl.DataFrame) -> None:
    """Upsert parsed possession rows into the ``possessions`` table.

    ``possessions`` must have (at least) the columns produced by
    ``nba.parse.possessions.parse_possessions``. Safe to call repeatedly
    for the same game_id(s) -- rows are keyed on (game_id, poss_idx).
    """
    if possessions.is_empty():
        return
    upsert_rows(con, "possessions", POSSESSIONS_COLUMNS, _CONFLICT_KEY, possessions)
