"""Resumable/idempotent ingest orchestration -- no network.

A fake client (no ``httpx`` call, just a call counter) stands in for
``KalshiClient`` so these tests exercise the caching/upsert contract
without touching the network, per the Phase-3 instruction that tests use
recorded/synthetic fixtures only.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from nba.db.connect import connect
from nba.kalshi.cutoff import parse_cutoff_response
from nba.kalshi.ingest import pull_candlesticks_for_market, pull_markets_for_series

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class _FakeClient:
    """Counts calls so tests can assert the cache prevents re-fetching."""

    def __init__(self, markets_raw: dict, candles_raw: dict) -> None:
        self.markets_raw = markets_raw
        self.candles_raw = candles_raw
        self.markets_calls = 0
        self.candles_calls = 0

    def fetch_markets_page(
        self, *, tier: str, series_ticker: str | None = None, cursor: str | None = None
    ):
        self.markets_calls += 1
        return self.markets_raw

    def fetch_candlesticks(
        self, ticker, series_ticker, *, tier, start_ts, end_ts, period_interval=60
    ):
        self.candles_calls += 1
        return self.candles_raw


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def cutoff():
    raw = json.loads((FIXTURES_DIR / "cutoff.json").read_text())
    return parse_cutoff_response(raw)


@pytest.fixture
def fake_client() -> _FakeClient:
    markets_raw = json.loads((FIXTURES_DIR / "markets_page.json").read_text())
    # Drop the unmatched-name market for this orchestration test -- name
    # matching is covered in test_parse.py; here we exercise caching/upsert.
    markets_raw = {
        **markets_raw,
        "markets": [m for m in markets_raw["markets"] if "Not A Real Player" not in m["title"]],
    }
    candles_raw = json.loads((FIXTURES_DIR / "candlesticks.json").read_text())
    return _FakeClient(markets_raw, candles_raw)


def test_pull_markets_is_idempotent_and_resumable(con, cutoff, fake_client, tmp_path) -> None:
    as_of = datetime(2026, 10, 8)
    n1 = pull_markets_for_series(
        con, fake_client, "KXNBAPTS", as_of=as_of, cutoff=cutoff, data_dir=tmp_path
    )
    n2 = pull_markets_for_series(
        con, fake_client, "KXNBAPTS", as_of=as_of, cutoff=cutoff, data_dir=tmp_path
    )
    assert n1 == 2
    assert n2 == 2
    # resumable: second call must not hit the (fake) network again
    assert fake_client.markets_calls == 1
    # idempotent: re-ingesting the same series must not duplicate rows
    count = con.execute("SELECT COUNT(*) FROM kalshi_markets").fetchone()[0]
    assert count == 2


def test_pull_candlesticks_is_idempotent_and_resumable(con, cutoff, fake_client, tmp_path) -> None:
    as_of = datetime(2026, 10, 8)
    ticker = "KXNBAPTS-26OCT08LALBOS-LEBJAM-25"
    n1 = pull_candlesticks_for_market(
        con,
        fake_client,
        ticker,
        "KXNBAPTS",
        start_ts=1,
        end_ts=2,
        as_of=as_of,
        cutoff=cutoff,
        data_dir=tmp_path,
    )
    n2 = pull_candlesticks_for_market(
        con,
        fake_client,
        ticker,
        "KXNBAPTS",
        start_ts=1,
        end_ts=2,
        as_of=as_of,
        cutoff=cutoff,
        data_dir=tmp_path,
    )
    assert n1 == 3
    assert n2 == 3
    assert fake_client.candles_calls == 1
    count = con.execute("SELECT COUNT(*) FROM kalshi_prices WHERE ticker = ?", [ticker]).fetchone()[
        0
    ]
    assert count == 3  # not 6: replace-before-insert, not append


def test_pull_markets_before_cutoff_uses_historical_tier(
    con, cutoff, fake_client, tmp_path
) -> None:
    before_cutoff = datetime(2026, 1, 1)
    pull_markets_for_series(
        con, fake_client, "KXNBAPTS", as_of=before_cutoff, cutoff=cutoff, data_dir=tmp_path
    )
    row = con.execute(
        "SELECT cache_path FROM ingest_log WHERE source = 'kalshi-markets'"
    ).fetchone()
    assert row is not None
    assert "historical:KXNBAPTS" in row[0]
