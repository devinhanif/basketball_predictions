"""Unit tests for nba.ingest.cache: resumability, caching, idempotency.

No network, no nba_api import -- fetch_fn is a plain in-test stub.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.cache import RateLimiter, fetch_cached, is_cached


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    yield con
    con.close()


def test_fetch_cached_calls_fetch_fn_once_then_reuses_cache(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def fetch_fn() -> pl.DataFrame:
        calls["n"] += 1
        return pl.DataFrame({"a": [1, 2, 3]})

    df1 = fetch_cached(con, "unittest", "key1", fetch_fn, data_dir=tmp_path)
    assert calls["n"] == 1
    assert is_cached(con, "unittest", "key1")

    df2 = fetch_cached(con, "unittest", "key1", fetch_fn, data_dir=tmp_path)
    assert calls["n"] == 1, "cached data must never be refetched"
    assert df1.equals(df2)


def test_fetch_cached_marks_failed_and_does_not_cache_on_error(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def fetch_fn() -> pl.DataFrame:
        raise RuntimeError("simulated network failure")

    with pytest.raises(RuntimeError):
        fetch_cached(con, "unittest", "bad_key", fetch_fn, data_dir=tmp_path)

    assert not is_cached(con, "unittest", "bad_key")
    row = con.execute(
        "SELECT status FROM ingest_log WHERE source='unittest' AND key='bad_key'"
    ).fetchone()
    assert row == ("failed",)


def test_rate_limiter_waits_at_least_min_interval() -> None:
    limiter = RateLimiter(min_interval_s=0.05)
    import time

    t0 = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.05
