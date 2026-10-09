"""Shared caching, rate-limiting, and resumability helpers for ingest pullers.

No nba_api imports here -- this module is pure IO/bookkeeping so it can be
imported (and tested) with no network access and without the dependency
being installed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
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


class EmptyFetchError(RuntimeError):
    """Raised when ``fetch_fn`` returns an empty frame and ``allow_empty=False``.

    A played game's box score (or similar per-entity fetch) is never
    legitimately empty; an empty frame usually means the endpoint was
    throttled, stale, or hit for a game that hasn't posted data yet. Such
    responses must never be silently cached as 'done'.
    """


def fetch_cached(
    con: duckdb.DuckDBPyConnection,
    source: str,
    key: str,
    fetch_fn: Callable[[], pl.DataFrame],
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    rate_limiter: RateLimiter | None = None,
    allow_empty: bool = True,
) -> pl.DataFrame:
    """Return cached parquet for (source, key) if present; otherwise fetch, cache, log.

    ``fetch_fn`` should perform the actual (rate-limited-by-caller) network
    call and return a polars DataFrame. Never called if the cache already
    has a 'done' row pointing at an existing file -- i.e. we never refetch
    cached data.

    If ``allow_empty`` is False and ``fetch_fn`` returns an empty frame, the
    (source, key) pair is marked 'failed' (not 'done'), no parquet is
    written, and ``EmptyFetchError`` is raised -- callers that know an empty
    response can never be legitimate (e.g. a played game's box score) should
    pass ``allow_empty=False`` so throttled/stale responses are never
    silently cached as success.
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

    if df.is_empty() and not allow_empty:
        mark_failed(con, source, key)
        raise EmptyFetchError(f"empty fetch result for source={source!r} key={key!r}")

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


class CircuitBreaker:
    """Pause after consecutive failures; give up after too many pauses.

    stats.nba.com throttles bursts by timing out every request. After
    ``failures`` consecutive failures the breaker sleeps ``cooldown_s``
    (one "trip"); after ``max_trips`` trips it tells the caller to stop so a
    rerun (everything is resumable) can pick up later. Same policy as
    ``nba.ingest.postgame.fetch_source``.
    """

    def __init__(
        self,
        failures: int = 3,
        cooldown_s: float = 600.0,
        max_trips: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        cooldown_cap_s: float | None = None,
    ) -> None:
        self.failures = failures
        self.cooldown_s = cooldown_s
        self.cooldown_cap_s = cooldown_cap_s if cooldown_cap_s is not None else cooldown_s
        self.max_trips = max_trips
        self._sleep = sleep
        self.streak = 0
        self.trips = 0

    def success(self) -> None:
        self.streak = 0
        self.trips = 0  # recovered: the next block starts the escalation over

    def next_cooldown_s(self) -> float:
        """Escalating cool-down: base * 2**(trips-1), capped (flat when cap == base)."""
        return float(min(self.cooldown_s * 2 ** max(self.trips - 1, 0), self.cooldown_cap_s))

    def failure(self) -> bool:
        """Record a failure. Returns True when the caller must stop early."""
        self.streak += 1
        if self.streak < self.failures:
            return False
        self.trips += 1
        if self.trips > self.max_trips:
            print(f"breaker tripped {self.trips - 1}x; stopping (rerun resumes)", flush=True)
            return True
        wait = self.next_cooldown_s()
        print(
            f"{self.streak} consecutive failures; cooling down {wait:.0f}s "
            f"(trip {self.trips}/{self.max_trips})",
            flush=True,
        )
        self._sleep(wait)
        self.streak = 0
        return False


@dataclass
class LoopSummary:
    total: int = 0
    cached: int = 0
    fetched: int = 0
    failed: list[str] = field(default_factory=list)
    stopped_early: bool = False


def pull_ids_with_breaker(
    con: duckdb.DuckDBPyConnection,
    source: str,
    ids: Sequence[str],
    pull_fn: Callable[[str], object],
    *,
    breaker: CircuitBreaker | None = None,
    label: str = "",
    log_every: int = 100,
) -> LoopSummary:
    """Run ``pull_fn(id)`` for every id with failure isolation and a circuit breaker.

    Already-cached ids are still passed to ``pull_fn`` (it re-upserts from the
    parquet cache without a network call) so the target DB is always filled.
    A failure is logged and skipped (``fetch_cached`` already marked it
    'failed', so a rerun retries it); the breaker pauses/stops on streaks.
    Prints ``PROGRESS`` lines every ``log_every`` ids for the queue runner.
    """
    brk = breaker or CircuitBreaker()
    out = LoopSummary(total=len(ids))
    t0 = time.monotonic()
    for i, gid in enumerate(ids, 1):
        was_cached = is_cached(con, source, gid)
        try:
            pull_fn(gid)
        except Exception as exc:
            out.failed.append(gid)
            print(f"FAILED {source}[{gid}]: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            if brk.failure():
                out.stopped_early = True
                break
            continue
        brk.success()
        if was_cached:
            out.cached += 1
        else:
            out.fetched += 1
        if i % log_every == 0:
            rate = out.fetched / max(time.monotonic() - t0, 1e-9)
            print(
                f"PROGRESS {source} {label} {i}/{len(ids)} cached={out.cached} "
                f"fetched={out.fetched} failed={len(out.failed)} rate={rate:.3f}/s",
                flush=True,
            )
    return out
