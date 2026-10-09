"""DuckDB connection helpers.

Opens a connection to the project's DuckDB file (or an in-memory / tmp path
for tests) and applies ``schema.sql`` idempotently. No network, no nba_api
import here -- this module only talks to DuckDB.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
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


LOCK_WAIT_ENV = "NBA_DB_LOCK_WAIT_S"
DEFAULT_LOCK_WAIT_S = 180.0


def is_lock_error(exc: BaseException) -> bool:
    """True for DuckDB's cross-process 'Could not set lock on file' open failure."""
    msg = str(exc).lower()
    return isinstance(exc, duckdb.IOException) and (
        "could not set lock" in msg or "conflicting lock" in msg
    )


def connect_with_retry(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    read_only: bool = False,
    max_wait_s: float | None = None,
    first_delay_s: float = 2.0,
    max_delay_s: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> duckdb.DuckDBPyConnection:
    """:func:`connect`, retrying on the single-writer file lock with bounded backoff.

    The lock is taken when the file is opened, so this is the retry around the write: the jobs
    that share ``nba.duckdb`` (hourly pretip, 5-minute lineups tick, ingest) race each other and a
    check-then-act ``lsof`` cannot close that window. Total wait is ``max_wait_s`` (default env
    ``NBA_DB_LOCK_WAIT_S`` or 180 s); the last lock error is re-raised afterwards. Errors that are
    not lock errors are never retried."""
    if max_wait_s is None:
        max_wait_s = float(os.environ.get(LOCK_WAIT_ENV, DEFAULT_LOCK_WAIT_S))
    deadline = clock() + max_wait_s
    delay = first_delay_s
    while True:
        try:
            return connect(db_path, read_only=read_only)
        except duckdb.IOException as exc:
            if not is_lock_error(exc) or clock() + delay > deadline:
                raise
        sleep(delay)
        delay = min(delay * 2, max_delay_s)
