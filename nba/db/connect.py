"""DuckDB connection helpers.

Opens a connection to the project's DuckDB file (or an in-memory / tmp path
for tests) and applies ``schema.sql`` idempotently. No network, no nba_api
import here -- this module only talks to DuckDB.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "nba.duckdb"


def apply_schema(con: duckdb.DuckDBPyConnection, schema_path: Path = SCHEMA_PATH) -> None:
    """Apply schema.sql to an open connection. Safe to call repeatedly."""
    sql = schema_path.read_text()
    con.execute(sql)


def connect(
    db_path: str | Path = DEFAULT_DB_PATH, *, read_only: bool = False
) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection at ``db_path`` and ensure the schema exists.

    Pass ``:memory:`` for an ephemeral in-process database (used by tests).
    """
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        apply_schema(con)
    return con
