"""Shared caching, rate-limiting, and resumability helpers for ingest pullers.

No nba_api imports here -- this module is pure IO/bookkeeping so it can be
imported (and tested) with no network access and without the dependency
being installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

import duckdb
import polars as pl

from nba.db.connect import connect

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

T = TypeVar("T")


class RateLimiter:
    """Sleeps between calls so we stay polite to the nba_api endpoints."""

    def __init__(self, min_interval_s: float = 0.6) -> None:
        self.min_interval_s = min_interval_s
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            elapsed = time.monotonic() - self._last_call
            remaining = self.min_interval_s - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call = time.monotonic()


def cache_path_for(source: str, key: str, data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """Parquet cache path for a given source/key pair, e.g. ('pbp', '0022300001')."""
    safe_key = key.replace("/", "_")
    return data_dir / source / f"{safe_key}.parquet"


def is_cached(con: duckdb.DuckDBPyConnection, source: str, key: str) -> bool:
    """Resumability check: has this (source, key) already been pulled successfully?"""
    row = con.execute(
        "SELECT cache_path FROM ingest_log WHERE source = ? AND key = ? AND status = 'done'",
        [source, key],
    ).fetchone()
    if row is None:
        return False
    return Path(row[0]).exists()


def mark_done(con: duckdb.DuckDBPyConnection, source: str, key: str, cache_path: Path) -> None:
    con.execute(
        """
        INSERT INTO ingest_log (source, key, status, cache_path, fetched_at)
        VALUES (?, ?, 'done', ?, now())
        ON CONFLICT (source, key) DO UPDATE SET
            status = 'done', cache_path = excluded.cache_path, fetched_at = now()
        """,
        [source, key, str(cache_path)],
    )


def mark_failed(con: duckdb.DuckDBPyConnection, source: str, key: str) -> None:
    con.execute(
        """
        INSERT INTO ingest_log (source, key, status, cache_path, fetched_at)
        VALUES (?, ?, 'failed', NULL, now())
        ON CONFLICT (source, key) DO UPDATE SET
            status = 'failed', fetched_at = now()
        """,
        [source, key],
    )


def fetch_cached(
    con: duckdb.DuckDBPyConnection,
    source: str,
    key: str,
    fetch_fn: Callable[[], pl.DataFrame],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
) -> pl.DataFrame:
    """Return cached parquet for (source, key) if present; otherwise fetch, cache, log.

    ``fetch_fn`` should perform the actual (rate-limited-by-caller) network
    call and return a polars DataFrame. Never called if the cache already
    has a 'done' row pointing at an existing file -- i.e. we never refetch
    cached data.
    """
    path = cache_path_for(source, key, data_dir)
    if is_cached(con, source, key):
        return pl.read_parquet(path)

    if rate_limiter is not None:
        rate_limiter.wait()

    try:
        df = fetch_fn()
    except Exception:
        mark_failed(con, source, key)
        raise

    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    mark_done(con, source, key, path)
    return df


def open_db(db_path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """Open (and schema-initialize) the ingest bookkeeping / target database."""
    if db_path is None:
        return connect()
    return connect(db_path)


def insert_rows(
    con: duckdb.DuckDBPyConnection, table: str, columns: list[str], df: pl.DataFrame
) -> None:
    """Row-wise insert of a polars DataFrame's ``columns`` into ``table``.

    Avoids polars<->DuckDB zero-copy bridging (which needs pyarrow, not a
    pinned dependency here) in favor of plain ``executemany``. Fine at the
    row counts this project's ingest pullers deal with.
    """
    if df.is_empty():
        return
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    rows = df.select(columns).rows()
    con.executemany(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", rows)


def upsert_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[str],
    conflict_key: list[str],
    df: pl.DataFrame,
) -> None:
    """Row-wise upsert (INSERT ... ON CONFLICT DO UPDATE) of a polars DataFrame."""
    if df.is_empty():
        return
    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(columns)
    update_cols = [c for c in columns if c not in conflict_key]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(conflict_key)}) DO UPDATE SET {set_clause}"
    )
    rows = df.select(columns).rows()
    con.executemany(sql, rows)


def iter_skip_cached(
    con: duckdb.DuckDBPyConnection, source: str, keys: Iterable[str]
) -> Iterable[str]:
    """Yield only the keys not already cached/done for ``source``."""
    for key in keys:
        if not is_cached(con, source, key):
            yield key
