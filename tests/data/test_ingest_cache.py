"""Unit tests for nba.ingest.cache: resumability, caching, idempotency.

No network, no nba_api import -- fetch_fn is a plain in-test stub.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.cache import EmptyFetchError, RateLimiter, fetch_cached, is_cached


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


def test_fetch_cached_treats_empty_frame_as_failure_when_disallowed(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def fetch_fn() -> pl.DataFrame:
        return pl.DataFrame({"a": []})

    with pytest.raises(EmptyFetchError):
        fetch_cached(con, "unittest", "empty_key", fetch_fn, data_dir=tmp_path, allow_empty=False)

    assert not is_cached(con, "unittest", "empty_key")
    row = con.execute(
        "SELECT status FROM ingest_log WHERE source='unittest' AND key='empty_key'"
    ).fetchone()
    assert row == ("failed",)
    assert not (tmp_path / "unittest" / "empty_key.parquet").exists()


def test_fetch_cached_allows_empty_frame_by_default(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def fetch_fn() -> pl.DataFrame:
        return pl.DataFrame({"a": []})

    df = fetch_cached(con, "unittest", "empty_ok_key", fetch_fn, data_dir=tmp_path)
    assert df.is_empty()
    assert is_cached(con, "unittest", "empty_ok_key")


def test_fetch_cached_retry_succeeds_after_transient_empties(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def flaky_fetch_fn() -> pl.DataFrame:
        calls["n"] += 1
        if calls["n"] < 3:
            return pl.DataFrame({"a": []})
        return pl.DataFrame({"a": [1, 2, 3]})

    def fetch_with_retry() -> pl.DataFrame:
        last = pl.DataFrame({"a": []})
        for _ in range(3):
            last = flaky_fetch_fn()
            if not last.is_empty():
                return last
        return last

    df = fetch_cached(
        con, "unittest", "retry_key", fetch_with_retry, data_dir=tmp_path, allow_empty=False
    )
    assert calls["n"] == 3
    assert not df.is_empty()
    row = con.execute(
        "SELECT status FROM ingest_log WHERE source='unittest' AND key='retry_key'"
    ).fetchone()
    assert row == ("done",)


def test_rate_limiter_waits_at_least_min_interval() -> None:
    limiter = RateLimiter(min_interval_s=0.05)
    import time

    t0 = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.05
