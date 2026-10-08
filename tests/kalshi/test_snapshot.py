"""Live-shape parsing, snapshot, and historical backfill -- no network.

Fixtures are trimmed recordings of real public responses (2026-10-08).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.db.connect import connect
from nba.kalshi.client import KalshiClient
from nba.kalshi.cutoff import parse_cutoff_response
from nba.kalshi.parse import (
    parse_candlesticks_frame,
    parse_markets_frame,
)
from nba.kalshi.snapshot import run_backfill_historical, run_snapshot

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 8, 15, 0, 0)


def _load(name: str) -> dict[str, Any]:
    return json.loads((FX / name).read_text())  # type: ignore[no-any-return]


class _Fake:
    """Routes by (tier, series, status); counts calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail_series: set[str] = set()

    def fetch_markets_page(self, *, tier: str, series_ticker: str | None = None, **kw: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        if series_ticker in self.fail_series:
            raise RuntimeError("boom")
        if tier == "historical":
            return _load("hist_prop_page.json") if series_ticker == "KXNBAPTS" else {"markets": []}
        status = kw.get("status")
        if series_ticker == "KXNBAGAME" and status == "open":
            return _load("live_game_open.json")
        if series_ticker == "KXNBASPREAD" and status == "open":
            return {"markets": _load("live_spread_total_open.json")["markets"][:1]}
        if series_ticker == "KXNBAPTS" and status == "settled":
            return _load("live_prop_settled.json")
        return {"markets": [], "cursor": ""}

    def fetch_candlesticks(self, ticker: str, series_ticker: str, **kw: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        return _load("hist_candles.json")


@pytest.fixture
def con() -> Any:
    c = connect(":memory:")
    yield c
    c.close()


def test_cutoff_live_field_and_utc_naive() -> None:
    info = parse_cutoff_response(_load("cutoff_live.json"))
    assert info.settled_markets_cutoff == datetime(2026, 8, 9)
    assert info.settled_markets_cutoff.tzinfo is None


def test_game_spread_total_rows_have_no_player() -> None:
    raw = _load("live_game_open.json")["markets"] + _load("live_spread_total_open.json")["markets"]
    df = parse_markets_frame(raw, include_game_markets=True)
    assert df.height == 4
    assert set(df["stat"].to_list()) == {"game_win", "spread", "total"}
    assert df["player_id"].null_count() == 4
    spread = df.filter(df["stat"] == "spread").row(0, named=True)
    assert spread["threshold"] == pytest.approx(7.5)
    # default (legacy) call still ignores game markets
    assert parse_markets_frame(raw).is_empty()


def test_prop_title_stat_from_series_and_unmatched_collected() -> None:
    raw = _load("live_prop_settled.json")["markets"]
    unmatched: list[str] = []
    df = parse_markets_frame(raw, unmatched=unmatched)
    assert df["stat"].unique().to_list() == ["pts"]
    assert df["player_id"].null_count() == 3  # Jaylen Brown x3 unreviewed; LeBron is seeded
    assert "Jaylen Brown" in unmatched
    assert set(df["threshold"].to_list()) >= {15.0, 20.0}
    assert df["result"].drop_nulls().is_in(["yes", "no", "scalar"]).all()
    with pytest.raises(Exception, match="Jaylen Brown"):  # default stays loud
        parse_markets_frame(raw)
    resolved = parse_markets_frame(raw, aliases={"jaylen brown": 1627759, "lebron james": 2544})
    assert resolved["player_id"].null_count() == 0


def test_candles_both_tier_shapes() -> None:
    hist = parse_candlesticks_frame(
        _load("hist_candles.json")["candlesticks"], ticker="T", source="historical"
    )
    live = parse_candlesticks_frame(
        _load("live_candles.json")["candlesticks"], ticker="T", source="live"
    )
    for df in (hist, live):
        assert df["yes_bid"].drop_nulls().len() > 0
        assert df["yes_bid"].drop_nulls().is_between(0, 1).all()
        assert df["volume"].dtype.is_integer()
    # 1781154000 == 2026-06-11T05:00:00Z, stored as naive UTC (not local time)
    assert hist["ts"][0] == datetime(2026, 6, 11, 5, 0)
    assert hist["yes_ask"][0] == pytest.approx(0.14)
    assert live["yes_ask"][0] == pytest.approx(0.88)


def test_snapshot_writes_markets_prices_raw_and_is_idempotent(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    fake = _Fake()
    kw: dict[str, Any] = {
        "series": ["KXNBAGAME", "KXNBASPREAD", "KXNBAPTS"],
        "now": NOW,
        "data_dir": tmp_path,
    }
    rep = run_snapshot(con, fake, **kw)  # type: ignore[arg-type]
    assert rep.markets_by_series["KXNBAGAME"] == 2
    assert rep.markets_by_series["KXNBASPREAD"] == 1
    assert rep.prices_rows == 3
    assert rep.settled_updated == 4
    assert rep.unmatched_names == {"Jaylen Brown": 3}
    assert not rep.ok  # unmatched name -> non-zero exit, but nothing dropped
    assert (tmp_path / "unmatched_names.json").exists()
    raw = list((tmp_path / "raw" / "2026-10-08").glob("*.json.gz"))
    assert len(raw) == rep.raw_files and raw
    with gzip.open(raw[0], "rt") as fh:
        assert "markets" in json.load(fh)

    n_prices = con.execute("SELECT count(*) FROM kalshi_prices").fetchone()[0]  # type: ignore[index]
    n_mk = con.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0]  # type: ignore[index]
    row = con.execute(
        "SELECT yes_bid, yes_ask, source FROM kalshi_prices WHERE ticker LIKE 'KXNBASPREAD%'"
    ).fetchone()
    assert row is not None and row[2] == "live" and row[0] <= row[1]

    run_snapshot(con, fake, **kw)  # type: ignore[arg-type]  # same ts: replay
    assert con.execute("SELECT count(*) FROM kalshi_prices").fetchone()[0] == n_prices  # type: ignore[index]
    assert con.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0] == n_mk  # type: ignore[index]

    kw["now"] = datetime(2026, 10, 8, 15, 15, 0)  # next snapshot = new observation
    run_snapshot(con, fake, **kw)  # type: ignore[arg-type]
    assert con.execute("SELECT count(*) FROM kalshi_prices").fetchone()[0] == 2 * n_prices  # type: ignore[index]
    assert con.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0] == n_mk  # type: ignore[index]


def test_snapshot_isolates_series_failure(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    fake = _Fake()
    fake.fail_series = {"KXNBAGAME"}
    rep = run_snapshot(con, fake, series=["KXNBAGAME", "KXNBASPREAD"], now=NOW, data_dir=tmp_path)  # type: ignore[arg-type]
    assert "KXNBAGAME" in rep.failures
    assert rep.markets_by_series["KXNBASPREAD"] == 1


def test_backfill_historical_idempotent_and_cached(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    fake = _Fake()
    cutoff = parse_cutoff_response(_load("cutoff_live.json"))
    kw: dict[str, Any] = {
        "series": ["KXNBAPTS"],
        "data_dir": tmp_path,
        "with_candles": True,
        "max_candle_markets": 2,
        "aliases": {"victor wembanyama": 1641705},
    }
    rep = run_backfill_historical(con, fake, cutoff, **kw)  # type: ignore[arg-type]
    assert rep.markets_by_series["KXNBAPTS"] == 3
    assert rep.candle_rows == 2 * 4
    first_calls = fake.calls
    rep2 = run_backfill_historical(con, fake, cutoff, **kw)  # type: ignore[arg-type]
    assert fake.calls == first_calls  # fully served from the raw cache
    assert rep2.candle_rows == rep.candle_rows
    assert con.execute("SELECT count(*) FROM kalshi_markets").fetchone()[0] == 3  # type: ignore[index]
    assert (
        con.execute("SELECT count(*) FROM kalshi_prices WHERE source='historical'").fetchone()[0]
        == 8
    )  # type: ignore[index]


def test_client_urls_and_no_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        seen.append((url, kwargs.get("params") or {}, kwargs))
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    c = KalshiClient()
    c.fetch_candlesticks("T-1", "S", tier="historical", start_ts=1, end_ts=2)
    c.fetch_candlesticks("T-1", "S", tier="live", start_ts=1, end_ts=2)
    c.fetch_markets_page(tier="live", series_ticker="S", status="open", limit=5, cursor="")
    assert seen[0][0].endswith("/historical/markets/T-1/candlesticks")
    assert seen[1][0].endswith("/series/S/markets/T-1/candlesticks")
    assert seen[2][1] == {"series_ticker": "S", "status": "open", "limit": 5}
    for _, _, kwargs in seen:
        assert "headers" not in kwargs and "auth" not in kwargs
