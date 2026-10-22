"""Public trades capture (min_ts, pagination, dedup) and the cash-out offer log, on fixtures."""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from nba.live.capture import Capture
from nba.live.http import Fetcher
from nba.live.offer import log_offer

FIX = Path(__file__).parent / "fixtures"
MARKETS = json.loads((FIX / "kalshi_markets_pages.json").read_text())["markets"]
TRADES = json.loads((FIX / "kalshi_trades_pages.json").read_text())
DAY = date(2026, 10, 21)
SCOREBOARD = (FIX / "espn_scoreboard.json").read_bytes()


def fetcher_for(handler: Any) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=10.0)
    return Fetcher(client=client, rate_per_s=1e6, sleep=lambda s: None)


def make_handler(seen: list[httpx.Request], until: list[int] | None = None) -> Any:
    cutoff = until if until is not None else [10**12]

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        p = req.url.path
        if p.endswith("/scoreboard"):
            return httpx.Response(200, content=SCOREBOARD)
        if p.endswith("/summary"):
            return httpx.Response(200, json={})
        if p.endswith("/series"):
            return httpx.Response(200, json={"series": [{"ticker": "KXNBAGAME"}]})
        if p.endswith("/orderbook"):
            return httpx.Response(200, json={"orderbook_fp": {}})
        if p.endswith("/markets/trades"):
            q = req.url.params
            page = TRADES["pages"][q.get("cursor", "")]
            ts = q.get("min_ts")
            trades = [
                t
                for t in page["trades"]
                if (ts is None or t["ts"] >= int(ts)) and t["ts"] <= cutoff[0]
            ]  # server-side min_ts; cutoff = what exists "so far"
            return httpx.Response(200, json={"trades": trades, "cursor": page["cursor"]})
        if p.endswith("/markets"):
            series = req.url.params.get("series_ticker", "")
            ms = [m for m in MARKETS if m["ticker"].startswith(series)]
            return httpx.Response(200, json={"markets": ms, "cursor": ""})
        t = p.rsplit("/", 1)[-1]
        if t == "KXMVE-COMBO":
            legs = [{"market_ticker": MARKETS[0]["ticker"], "side": "yes"}]
            return httpx.Response(200, json={"market": {"ticker": t, "mve_selected_legs": legs}})
        m = next((x for x in MARKETS if x["ticker"] == t), None)
        return httpx.Response(200, json={"market": m}) if m else httpx.Response(404)

    return handler


def read_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()))


def test_trades_paginate_use_min_ts_and_dedupe(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    until = [1792000040]  # t1..t5 exist at the first tick
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=fetcher_for(make_handler(seen, until))
    )
    target = TRADES["ticker"]
    cap.fetcher.begin_tick(100, 30.0)
    cap.st.last_markets = [m for m in MARKETS if m["ticker"] == target]
    info: dict[str, Any] = {"skipped": {}}
    cap.kalshi_trades(info)
    reqs = [r for r in seen if r.url.path.endswith("/markets/trades")]
    assert all(r.method == "GET" for r in reqs)
    assert "min_ts" not in reqs[0].url.params  # first sight: everything
    assert reqs[1].url.params["cursor"] == "c1"  # paginated
    saved = read_gz(next((tmp_path / "raw" / "kalshi_trades").rglob("*.json.gz")))
    assert [t["trade_id"] for t in saved[target]] == ["t1", "t2", "t3", "t4", "t5"]
    assert info["trades"]["n"] == 5 and cap.st.trade_last_ts[target] == 1792000040
    # second tick: volume grew; min_ts = last seen; t4 and t5 (same second) are not stored twice
    until[0] = 1792000050
    cap.st.volumes[target] = 0.0  # "traded since the last tick"
    cap.fetcher.begin_tick(100, 30.0)
    info2: dict[str, Any] = {"skipped": {}}
    cap.kalshi_trades(info2)
    again = [r for r in seen if r.url.path.endswith("/markets/trades")][len(reqs) :]
    assert again[0].url.params["min_ts"] == "1792000040"
    assert info2["trades"]["n"] == 1
    files = sorted((tmp_path / "raw" / "kalshi_trades").rglob("*.json.gz"))
    assert len(files) == 2 and [t["trade_id"] for t in read_gz(files[1])[target]] == ["t6"]
    assert "kalshi_trades" in (tmp_path / "raw" / "manifest.jsonl").read_text()


def test_no_trade_when_volume_unchanged(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=fetcher_for(make_handler(seen))
    )
    cap.fetcher.begin_tick(100, 30.0)
    cap.st.last_markets = list(MARKETS)
    cap.st.volumes = {str(m["ticker"]): float(m["volume_fp"]) for m in MARKETS}
    info: dict[str, Any] = {"skipped": {}}
    cap.kalshi_trades(info)
    assert info["trades"] == {"n": 0, "tickers": 0, "targets": 0} and not seen


def test_trades_cap_is_logged_as_skips(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    cap = Capture(
        DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=fetcher_for(make_handler(seen))
    )
    object.__setattr__(cap.budget, "trades_max_tickers", 1)
    cap.fetcher.begin_tick(100, 30.0)
    cap.st.last_markets = list(MARKETS)
    info: dict[str, Any] = {"skipped": {}}
    cap.kalshi_trades(info)
    traded = [m for m in MARKETS if float(m["volume_fp"]) > 0]
    assert info["trades"]["targets"] == 1 and info["skipped"]["trades_n"] == len(traded) - 1


def test_offer_log_line(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    now = datetime(2026, 10, 21, 23, 0, tzinfo=UTC)
    row = log_offer(
        tmp_path, combo="KXMVE-COMBO", offer=12.5, contracts=3, note="app",
        fetcher=fetcher_for(make_handler(seen)), legs=["NOPE-1"], now_fn=lambda: now,
    )  # fmt: skip
    lines = (tmp_path / "cashout_offers.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == row
    assert row["received_at"] == now.isoformat() and row["offer"] == 12.5 and row["contracts"] == 3
    assert row["combo"] == "KXMVE-COMBO" and row["note"] == "app" and row["legs_listed_by_api"]
    ok, missing = row["legs"]
    assert ok["fetched"] and ok["side"] == "yes" and ok["quote"]["yes_bid"] is not None
    assert missing["ticker"] == "NOPE-1" and not missing["fetched"] and missing["quote"] is None
    assert all(r.method == "GET" for r in seen)
    log_offer(  # appends, never rewrites
        tmp_path, combo="KXMVE-COMBO", offer=1.0, contracts=1,
        fetcher=fetcher_for(make_handler(seen)), now_fn=lambda: now,
    )  # fmt: skip
    assert len((tmp_path / "cashout_offers.jsonl").read_text().splitlines()) == 2
