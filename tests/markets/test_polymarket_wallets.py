"""Polymarket wallet pull: mocked responses only, no network."""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest

from nba.markets import polymarket_wallets as pw

SRC = Path(pw.__file__)


def _event(slug: str, start: str, cid: str) -> dict[str, Any]:
    return {
        "id": 1,
        "slug": slug,
        "title": slug,
        "markets": [
            {
                "id": 9,
                "conditionId": cid,
                "slug": slug + "-ml",
                "question": "Who wins?",
                "sportsMarketType": "moneyline",
                "gameStartTime": start,
                "outcomes": '["A", "B"]',
                "clobTokenIds": '["tokA", "tokB"]',
                "volumeNum": 12.5,
            }
        ],
    }


def _trade(i: int, ts: int) -> dict[str, Any]:
    return {
        "proxyWallet": f"0x{i:040x}",
        "side": "BUY",
        "asset": "tokA",
        "conditionId": "c1",
        "size": 1.0,
        "price": 0.5,
        "timestamp": ts,
        "transactionHash": f"0xh{i}",
        "outcome": "A",
        "outcomeIndex": 0,
    }


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, params: dict[str, Any]) -> Any:
        self.calls.append((url, dict(params)))
        if url.endswith("/events/keyset"):
            if "after_cursor" not in params:
                return {
                    "events": [
                        _event("in-sel", "2024-12-25T17:00:00Z", "c1"),
                        _event("in-rep", "2025-03-01T00:00:00Z", "c2"),
                    ],
                    "next_cursor": "p2",
                }
            return {"events": [_event("future", "2025-11-01T00:00:00Z", "c3")], "next_cursor": None}
        if url.endswith("/trades"):
            rows = [_trade(i, 1_700_000_000 - i) for i in range(2500)]
            o, lim = params["offset"], params["limit"]
            return rows[o : o + lim]
        if url.endswith("/prices-history"):
            return {"history": [{"t": 1, "p": 0.4}, {"t": 2, "p": 0.6}]}
        raise AssertionError(url)


def test_window_refuses_season_2025() -> None:
    with pytest.raises(pw.HoldoutRefused):
        pw.check_window("report", end=date(2025, 10, 30))
    with pytest.raises(pw.HoldoutRefused):
        pw.check_window("selection", start=date(2025, 11, 1), end=date(2025, 11, 2))
    assert pw.check_window("selection")[1] == date(2025, 1, 31)
    with pytest.raises(ValueError):
        pw.check_window("selection", end=date(2025, 2, 1))  # report data not allowed here


def test_cli_refuses_holdout(tmp_path: Path) -> None:
    rc = pw.main(["--window", "report", "--end", "2025-12-01", "--out-root", str(tmp_path)])
    assert rc == 2
    assert not list(tmp_path.iterdir())


def test_markets_filter_and_resume(tmp_path: Path) -> None:
    fake = Fake()
    res = pw.run("selection", "markets", tmp_path, "d", get_json=fake)
    assert res["markets"]["markets"] == 1  # report and 2025-26 events excluded
    assert res["markets"]["by_type"] == {"moneyline": 1}
    out = tmp_path / "polymarket_wallets_d" / "selection"
    assert (out / "README.md").exists() and (out / "raw_events").is_dir()
    n_calls = len(fake.calls)
    again = pw.run("selection", "markets", tmp_path, "d", get_json=fake)
    assert again["markets"]["skipped"] and len(fake.calls) == n_calls  # no refetch


def test_trades_paginate_slide_dedupe_and_resume(tmp_path: Path) -> None:
    fake = Fake()
    pw.run("selection", "markets", tmp_path, "d", get_json=fake)
    fake.calls.clear()
    res = pw.run("selection", "trades", tmp_path, "d", get_json=fake)
    assert res["trades"]["fetched_now"] == 1 and res["trades"]["truncated"] == 0
    assert res["trades"]["requests"] == 3  # 1000, 1000, 500 (short page ends)
    assert all(c[1]["end"] == pw.TRADE_END_TS and c[1]["takerOnly"] == "false" for c in fake.calls)
    t = pq.read_table(tmp_path / "polymarket_wallets_d" / "selection" / "trades" / "c1.parquet")
    assert t.num_rows == 2500 and "proxy_wallet" in t.column_names
    fake.calls.clear()
    res2 = pw.run("selection", "trades", tmp_path, "d", get_json=fake)
    assert res2["trades"]["already_done"] == 1 and not fake.calls


def test_offset_cap_slides_window() -> None:
    all_rows = [_trade(i, 1_700_000_000 - i) for i in range(25)]

    def get(url: str, params: dict[str, Any]) -> Any:
        if params["offset"] > 10:
            raise AssertionError("offset beyond cap requested")
        rows = [r for r in all_rows if r["timestamp"] <= params["end"]]
        return rows[params["offset"] : params["offset"] + params["limit"]]

    orig = pw.OFFSET_CAP
    pw.OFFSET_CAP = 10
    try:
        got, info = pw.fetch_market_trades(get, "c1", taker_only=False, limit=5)
    finally:
        pw.OFFSET_CAP = orig
    assert len(got) == 25 and not info["truncated"]


def test_prices_stage(tmp_path: Path) -> None:
    fake = Fake()
    pw.run("selection", "markets", tmp_path, "d", get_json=fake)
    res = pw.run("selection", "prices", tmp_path, "d", get_json=fake)
    assert res["prices"]["fetched_now"] == 2
    p = pq.read_table(tmp_path / "polymarket_wallets_d" / "selection" / "prices" / "tokA.parquet")
    assert p.num_rows == 2


def test_manifest_written(tmp_path: Path) -> None:
    pw.run("selection", "markets", tmp_path, "d", get_json=Fake())
    m = json.loads((tmp_path / "polymarket_wallets_d" / "selection" / "manifest.json").read_text())
    assert m["runs"][0]["stage"] == "markets"


def test_source_is_get_only_no_credentials() -> None:
    text = SRC.read_text()
    tree = ast.parse(text)
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not attrs & {"post", "put", "delete", "patch", "request"}
    low = text.lower()
    for bad in ("authorization", "api_key", "apikey", "secret", "api.polymarket.us", "/order"):
        assert bad not in low
