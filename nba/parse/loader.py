"""Idempotent loader: parsed ``possessions``/``stints`` rows -> DuckDB.

Thin wrapper around ``nba.ingest.cache.upsert_rows`` keyed on the table's
primary key (``game_id``, ``poss_idx``), so re-running the parser for a
game (e.g. after a parser fix) safely overwrites that game's rows instead
of duplicating them. Not run against ``nba.duckdb`` by this agent -- see
``nba/parse/possessions.py`` module docstring; the maintainer wires this
into the real ingest pipeline once the box-score backfill lands. The
lineup/stint functions below (``nba/parse/lineups.py``) are likewise
provided but not executed against the real DB by this agent.
"""

from __future__ import annotations

import duckdb
import polars as pl

from nba.ingest.cache import upsert_rows
from nba.parse.lineups import STINTS_COLUMNS, with_stint_idx
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


def load_possession_lineups(con: duckdb.DuckDBPyConnection, possessions: pl.DataFrame) -> None:
    """Write ``off_players``/``def_players`` onto already-loaded ``possessions`` rows.

    ``possessions`` must carry ``game_id``, ``poss_idx``, ``off_players``,
    ``def_players`` (the output of
    ``nba.parse.lineups.attach_lineups_to_possessions``). Plain ``UPDATE``
    rather than ``upsert_rows`` -- these rows already exist (inserted by
    ``load_possessions``); this only fills in the two lineup columns.
    Safe to call repeatedly for the same game_id(s).
    """
    if possessions.is_empty():
        return
    rows = possessions.select(["off_players", "def_players", "game_id", "poss_idx"]).rows()
    con.executemany(
        "UPDATE possessions SET off_players = ?, def_players = ? "
        "WHERE game_id = ? AND poss_idx = ?",
        rows,
    )


def load_stints(con: duckdb.DuckDBPyConnection, stints: pl.DataFrame) -> None:
    """Replace a game's ``stints`` rows.

    ``stints`` table has no primary key (see ``nba/db/schema.sql``), so
    idempotency is achieved by deleting any existing rows for the
    game_id(s) present in ``stints`` before inserting -- safe to call
    repeatedly (e.g. after a lineup-tracker fix) without duplicating
    rows.
    """
    if stints.is_empty():
        return
    stints = with_stint_idx(stints)
    game_ids = [(g,) for g in stints["game_id"].unique().to_list()]
    con.executemany("DELETE FROM stints WHERE game_id = ?", game_ids)
    con.executemany(
        f"INSERT INTO stints ({', '.join(STINTS_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(STINTS_COLUMNS))})",
        stints.select(STINTS_COLUMNS).rows(),
    )
