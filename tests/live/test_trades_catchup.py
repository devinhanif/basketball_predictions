"""Complete trade catch-up: bursts larger than a page, carry-over across ticks, restart, dedupe.

The fake server mimics ``GET /markets/trades``: newest first, ``min_ts`` inclusive, opaque cursor
(offset into the newest-first list), page ``limit``. Trades are synthetic (recorded shape from
``fixtures/kalshi_trades_pages.json``): a burst of 12,000 prints in 40 seconds is the 2026-11-09
failure mode (more than the old 5 x 1000 cap between two ticks).
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from nba.live.capture import Budget, Capture, trade_priority
from nba.live.http import Fetcher

DAY = date(2026, 11, 9)
T0 = datetime(2026, 11, 10, 1, 0, 0, tzinfo=UTC)
GAME = "KXNBAGAME-26NOV09NOPGSW-GSW"
PROP = "KXNBAPTS-26NOV09NOPGSW-GSWSCURRY30-25"
SPREAD = "KXNBASPREAD-26NOV09NOPGSW-GSW6"


def mk(ticker: str, i: int) -> dict[str, Any]:
    ts = int(T0.timestamp()) + i // 300  # 300 prints per second
    created = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "trade_id": f"{ticker[-6:]}-{i:06d}",
        "ticker": ticker,
        "count_fp": "1.00",
        "yes_price_dollars": "0.5000",
        "taker_side": "yes",
        "created_time": created,
        "_i": i,
    }


class Server:
    def __init__(self) -> None:
        self.trades: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[httpx.Request] = []
        self.fail: dict[tuple[str, int], int] = {}  # (ticker, call number) -> status
        self.calls: dict[str, int] = {}
        self.keyset = False  # cursor = 'after:<last _i>' instead of an offset
        self.expired: set[str] = set()  # cursors answered 200 empty (expired)

    def add(self, ticker: str, n: int) -> None:
        have = len(self.trades.setdefault(ticker, []))
        self.trades[ticker] += [mk(ticker, i) for i in range(have, have + n)]

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        assert req.method == "GET"
        q = req.url.params
        t = q["ticker"]
        k = self.calls[t] = self.calls.get(t, 0) + 1
        if (t, k) in self.fail:
            return httpx.Response(self.fail[(t, k)], json={})
        lim = int(q.get("limit", 100))
        allv = sorted(self.trades.get(t, []), key=lambda x: -x["_i"])  # newest first
        if "min_ts" in q:
            from nba.live.kalshi_pull import trade_ts

            allv = [x for x in allv if (trade_ts(x) or 0) >= int(q["min_ts"])]
        cur = q.get("cursor")
        if cur in self.expired:
            self.expired.discard(cur)  # a cursor expires once; a fresh walk issues a valid one
            return httpx.Response(200, json={"trades": [], "cursor": ""})
        if self.keyset:
            if cur:
                allv = [x for x in allv if x["_i"] < int(cur.split(":")[1])]
            page = allv[:lim]
            more = len(allv) > lim
            nxt = f"after:{page[-1]['_i']}" if more else ""
            return httpx.Response(200, json={"trades": page, "cursor": nxt})
        off = int(cur) if cur else 0
        if off > len(allv) + 10**6:
            return httpx.Response(400, json={})
        page = allv[off : off + lim]
        nxt = str(off + lim) if off + lim < len(allv) else ""
        return httpx.Response(200, json={"trades": page, "cursor": nxt})


def make_cap(tmp_path: Path, srv: Server, **budget: Any) -> Capture:
    client = httpx.Client(transport=httpx.MockTransport(srv.handler), timeout=10.0)
    fetcher = Fetcher(client=client, rate_per_s=1e6, sleep=lambda s: None)
    b = Budget(**budget) if budget else Budget()
    return Capture(DAY, tmp_path / "raw", tmp_path / "hb.json", fetcher=fetcher, budget=b)


def market(ticker: str, vol: float) -> dict[str, Any]:
    return {"ticker": ticker, "volume_fp": str(vol), "event_ticker": "KXNBAGAME-26NOV09NOPGSW"}


def tick(cap: Capture, vols: dict[str, float], budget: int = 500) -> dict[str, Any]:
    cap.fetcher.begin_tick(budget, 30.0)
    cap.st.last_markets = [market(t, v) for t, v in vols.items()]
    info: dict[str, Any] = {"skipped": {}}
    cap.kalshi_trades(info)
    return info


def stored(tmp_path: Path, ticker: str) -> list[str]:
    ids: list[str] = []
    for f in sorted((tmp_path / "raw" / "kalshi_trades").rglob("*.json.gz")):
        ids += [t["trade_id"] for t in json.loads(gzip.decompress(f.read_bytes())).get(ticker, [])]
    return ids


def test_burst_beyond_old_cap_is_complete_in_one_tick(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 12_000)  # 12 pages; the old cap stored 5
    cap = make_cap(tmp_path, srv)
    info = tick(cap, {GAME: 12_000.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 12_000 == len(set(ids))
    assert info["trades"]["pages"] == 12 and info["trades"]["backlog"] == 0
    assert cap.st.trade_last_ts[GAME] == int(T0.timestamp()) + 11_999 // 300
    assert GAME not in cap.st.trade_open and GAME not in cap.st.trade_cursor


def test_burst_is_carried_across_ticks_and_mark_moves_only_when_complete(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 12_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=5)
    info1 = tick(cap, {GAME: 12_000.0})
    assert info1["trades"]["pages"] == 5 and info1["trades"]["backlog"] == 1
    assert GAME not in cap.st.trade_last_ts  # not advanced: the catch-up is open
    assert cap.st.trade_cursor[GAME] == "5000" and len(cap.st.trade_open[GAME]) == 5000
    info2 = tick(cap, {GAME: 12_000.0})
    assert GAME not in cap.st.trade_last_ts and info2["trades"]["backlog"] == 1
    reqs = [r for r in srv.requests if r.url.path.endswith("/markets/trades")]
    assert reqs[5].url.params["cursor"] == "5000"  # resumed, not restarted
    tick(cap, {GAME: 12_000.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 12_000 == len(set(ids))  # every print once, none skipped
    assert cap.st.trade_last_ts[GAME] == int(T0.timestamp()) + 11_999 // 300
    assert GAME in cap.st.trade_pending  # carried catch-up: one verify pass for newer prints
    tick(cap, {GAME: 12_000.0})
    assert not cap.st.trade_pending
    srv.add(GAME, 10)  # next catch-up starts at the mark, new prints only (+ same-second dedupe)
    tick(cap, {GAME: 12_010.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 12_010 == len(set(ids))


def test_prints_arriving_during_a_carried_catchup_are_not_lost(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 4_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=2)
    tick(cap, {GAME: 4_000.0})  # 2 of 4 pages; mark not moved
    srv.add(GAME, 1_500)  # arrive above the open cursor
    for _ in range(5):
        tick(cap, {GAME: 5_500.0})
    assert not cap.st.trade_pending
    ids = stored(tmp_path, GAME)
    assert len(set(ids)) == 5_500 and len(ids) == 5_500


def test_restart_resumes_from_persisted_state(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 9_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=4)
    tick(cap, {GAME: 9_000.0})
    n_before = len([r for r in srv.requests if r.url.path.endswith("/markets/trades")])
    cap2 = make_cap(tmp_path, srv, trades_page_budget=100)  # process restart, same out dir
    assert cap2.st.trade_cursor[GAME] == "4000" and GAME in cap2.st.trade_pending
    tick(cap2, {GAME: 9_000.0})
    reqs = [r for r in srv.requests if r.url.path.endswith("/markets/trades")][n_before:]
    assert reqs[0].url.params["cursor"] == "4000"  # resumed mid catch-up
    ids = stored(tmp_path, GAME)
    assert len(ids) == 9_000 == len(set(ids))
    tick(cap2, {GAME: 9_000.0})  # the verify pass
    cap3 = make_cap(tmp_path, srv)  # restart after completion: from the high-water mark
    assert cap3.st.trade_last_ts[GAME] == cap2.st.trade_last_ts[GAME] and not cap3.st.trade_pending
    srv.add(GAME, 5)
    tick(cap3, {GAME: 9_005.0})
    last = [r for r in srv.requests if r.url.path.endswith("/markets/trades")][-1]
    assert (
        last.url.params["min_ts"] == str(cap2.st.trade_last_ts[GAME])
        and "cursor" not in last.url.params
    )
    ids = stored(tmp_path, GAME)
    assert len(ids) == 9_005 == len(set(ids))


def test_stale_cursor_restarts_from_newest_and_dedupes(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 3_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=1)
    tick(cap, {GAME: 3_000.0})
    cap.st.trade_cursor[GAME] = str(10**9)  # server rejects it (400)
    cap.budget = Budget(trades_page_budget=50)
    tick(cap, {GAME: 3_000.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 3_000 == len(set(ids))


def test_429_keeps_cursor_stops_the_tick_and_backs_off(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 3_000)
    srv.add(PROP, 10)
    for k in (2, 3, 4):  # the fetcher retries a 429 three times; all fail
        srv.fail[(GAME, k)] = 429
    cap = make_cap(tmp_path, srv)
    info = tick(cap, {GAME: 3_000.0, PROP: 10.0})
    assert info["trades"]["rate_limited"] and GAME not in cap.st.trade_last_ts
    assert cap.st.trade_cursor[GAME] == "1000" and cap.st.trade_skip_ticks == 0
    info = tick(cap, {GAME: 3_000.0, PROP: 10.0})  # first 429: retry next tick, no skip
    ids = stored(tmp_path, GAME)
    assert len(ids) == 3_000 == len(set(ids))
    assert info["trades"]["backlog"] == 1  # the verify pass for the carried catch-up
    assert tick(cap, {GAME: 3_000.0, PROP: 10.0})["trades"]["backlog"] == 0


def test_repeated_429_skips_ticks_then_resumes(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 100)
    for k in range(1, 7):  # two ticks of three failing attempts each
        srv.fail[(GAME, k)] = 429
    cap = make_cap(tmp_path, srv)
    for _ in range(2):
        tick(cap, {GAME: 100.0})
    assert cap.st.trade_skip_ticks == 1
    n = len(srv.requests)
    info = tick(cap, {GAME: 100.0})
    assert len(srv.requests) == n and info["skipped"]["trades_incomplete_n"] == 1  # carried over
    tick(cap, {GAME: 100.0})
    assert len(set(stored(tmp_path, GAME))) == 100


def test_priority_game_then_props_then_rest_under_a_tight_budget(tmp_path: Path) -> None:
    assert [trade_priority(t) for t in (GAME, PROP, SPREAD, "KXNBAPTSPROT-X-Y")] == [0, 1, 2, 1]
    srv = Server()
    for t in (SPREAD, PROP, GAME):
        srv.add(t, 10)
    cap = make_cap(tmp_path, srv, trades_page_budget=2)
    tick(cap, {SPREAD: 10.0, PROP: 10.0, GAME: 10.0})
    asked = [r.url.params["ticker"] for r in srv.requests]
    assert asked[0] == GAME and set(asked[:2]) == {GAME, PROP}
    assert SPREAD in cap.st.trade_pending  # carried, not skipped
    tick(cap, {SPREAD: 10.0, PROP: 10.0, GAME: 10.0})
    assert not cap.st.trade_pending and len(stored(tmp_path, SPREAD)) == 10


def test_per_ticker_page_cap_keeps_a_hot_ticker_from_starving_others(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 20_000)
    srv.add(PROP, 10)
    cap = make_cap(tmp_path, srv, trades_pages_per_ticker=3, trades_page_budget=100)
    tick(cap, {GAME: 20_000.0, PROP: 10.0})
    assert len(stored(tmp_path, PROP)) == 10 and len(stored(tmp_path, GAME)) == 3_000
    assert GAME in cap.st.trade_pending and PROP not in cap.st.trade_pending


def test_dedupe_on_trade_id_across_overlapping_pulls(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 700)  # all inside 3 seconds, so min_ts re-serves old prints
    cap = make_cap(tmp_path, srv)
    tick(cap, {GAME: 700.0})
    srv.add(GAME, 100)
    tick(cap, {GAME: 800.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 800 == len(set(ids))


def test_request_budget_never_exceeds_tick_budget(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 50_000)
    cap = make_cap(tmp_path, srv)
    tick(cap, {GAME: 50_000.0}, budget=30)  # 30 - 12 reserve = 18 pages
    assert len(srv.requests) <= 30 - cap.budget.trades_page_reserve
    assert GAME in cap.st.trade_pending


def test_expired_cursor_answering_200_empty_restarts_offset(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 3_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=1)
    tick(cap, {GAME: 3_000.0})
    assert cap.st.trade_cursor[GAME] == "1000"
    srv.expired.add("1000")
    cap.budget = Budget(trades_page_budget=50)
    tick(cap, {GAME: 3_000.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 3_000 == len(set(ids))


def test_expired_cursor_and_carry_with_keyset_cursor(tmp_path: Path) -> None:
    srv = Server()
    srv.keyset = True
    srv.add(GAME, 6_500)
    cap = make_cap(tmp_path, srv, trades_page_budget=2)
    tick(cap, {GAME: 6_500.0})
    cursor = cap.st.trade_cursor[GAME]
    assert cursor == "after:4500"
    srv.add(GAME, 700)  # newer prints at the head during the carry
    srv.expired.add(cursor)
    cap.budget = Budget(trades_page_budget=50)
    for _ in range(4):
        tick(cap, {GAME: 7_200.0})
    ids = stored(tmp_path, GAME)
    assert len(set(ids)) == 7_200 and not cap.st.trade_pending


def test_keyset_cursor_resume_is_complete(tmp_path: Path) -> None:
    srv = Server()
    srv.keyset = True
    srv.add(GAME, 6_500)
    cap = make_cap(tmp_path, srv, trades_page_budget=2)
    for _ in range(6):
        tick(cap, {GAME: 6_500.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 6_500 == len(set(ids)) and not cap.st.trade_pending


def test_raw_write_failure_absorbs_nothing_then_recovers(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 1_500)
    cap = make_cap(tmp_path, srv)
    real = cap.store.save
    calls = [0]

    def flaky(*a: Any, **k: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            raise OSError("disk full")
        return real(*a, **k)

    cap.store.save = flaky  # type: ignore[method-assign]
    tick(cap, {GAME: 1_500.0})
    assert GAME not in cap.st.trade_last_ts and GAME in cap.st.trade_pending
    assert not stored(tmp_path, GAME)
    tick(cap, {GAME: 1_500.0})
    ids = stored(tmp_path, GAME)
    assert len(ids) == 1_500 == len(set(ids))


def test_walk_without_mark_evidence_is_kept_open(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 500)
    cap = make_cap(tmp_path, srv)
    tick(cap, {GAME: 500.0})
    cap.st.trade_ids[GAME] = {"not-a-real-id"}  # the mark's ids never come back
    cap.st.trade_pending[GAME] = None
    tick(cap, {GAME: 500.0})
    assert GAME in cap.st.trade_pending  # first time: kept open, logged
    assert "without reaching the mark" in (tmp_path / "raw" / "errors.log").read_text()


def test_stale_cursor_cleared_after_budget_refused_restart(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 3_000)
    cap = make_cap(tmp_path, srv, trades_page_budget=1)
    tick(cap, {GAME: 3_000.0})
    cap.st.trade_cursor[GAME] = str(10**9)  # server answers 400
    tick(cap, {GAME: 3_000.0})  # 1 page budget: the 400 uses it, the restart is refused
    assert GAME not in cap.st.trade_cursor


def test_retries_count_against_the_probe_reserve(tmp_path: Path) -> None:
    srv = Server()
    srv.add(GAME, 50_000)
    for k in range(1, 40):
        srv.fail[(GAME, k)] = 500  # every attempt fails: 3 requests per page
    cap = make_cap(tmp_path, srv)
    tick(cap, {GAME: 50_000.0}, budget=30)
    assert cap.fetcher.tick_requests <= 30 - cap.budget.trades_page_reserve + 3


def test_player_double_triple_double_rank_with_props() -> None:
    assert trade_priority("KXNBA2D-26NOV09NOPGSW-GSWBPODZIEMSKI2") == 1
    assert trade_priority("KXNBA3D-26NOV09NOPGSW-GSWBPODZIEMSKI3") == 1
    assert trade_priority("KXNBA2HSPREAD-26NOV09DENHOU-DEN9") == 2


def test_drain_after_the_loop_empties_the_queue_and_reports(tmp_path: Path) -> None:
    srv = Server()
    for t in (GAME, PROP, SPREAD):
        srv.add(t, 1_200)
    cap = make_cap(tmp_path, srv, trades_page_budget=2)
    tick(cap, {GAME: 1_200.0, PROP: 1_200.0, SPREAD: 1_200.0})
    assert cap.st.trade_pending  # live ticks left a backlog
    out = cap.drain_trades()
    assert out["drain_start"] > 0 and out["drain_remaining"] == 0
    for t in (GAME, PROP, SPREAD):
        assert len(set(stored(tmp_path, t))) == 1_200
    lines = (tmp_path / "raw" / "ticks.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["drain_remaining"] == 0
