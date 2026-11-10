"""Trade catch-up with a carried cursor (poll15): a burst bigger than one poll's page budget is
captured completely across polls, the mark moves only after a complete catch-up, quote polls are
never starved, and a restart resumes mid catch-up. Recorded fixtures and a fake feed; no network."""

from __future__ import annotations

import copy
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from nba.kalshi.poll15 import (
    TRADE_SCHEMA,
    PollConfig,
    Poller,
    RawStore,
    RequestBudget,
)

FIX = Path(__file__).parent / "fixtures"
DAY = date(2026, 11, 9)
T0 = datetime(2026, 11, 10, 0, 5, 0, tzinfo=UTC)
TICKER = "KXNBAGAME-26NOV09SASPHX-SAS"
TIPS = [datetime(2026, 11, 10, 0, 30, tzinfo=UTC), datetime(2026, 11, 10, 3, 0, tzinfo=UTC)]
BASE_TS = int(datetime(2026, 11, 10, 0, 1, 0, tzinfo=UTC).timestamp())  # after the window start


class _HTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"http {code}")
        self.response = type("R", (), {"status_code": code})()


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += timedelta(seconds=s)


class Feed:
    """A ticker's prints served newest first in pages of ``page``, cursor = the last print's rank
    (stable when newer prints arrive, like a real cursor), honouring ``min_ts``."""

    def __init__(self, n: int, page: int = 100) -> None:
        self.page = page
        self.offset = False  # cursor = offset into the current list (shifts when prints arrive)
        self.expire_empty = False  # a resumed cursor is unknown: 200 with no trades and no cursor
        self.drop_mark_second = False  # the server omits the prints of the min_ts second
        self.prints: list[dict[str, Any]] = []
        self.add(n)

    def add(self, n: int) -> None:
        start = len(self.prints)
        for i in range(start, start + n):
            self.prints.append(
                {
                    "trade_id": f"t{i:06d}",
                    "ticker": TICKER,
                    "created_time": (
                        datetime.fromtimestamp(BASE_TS + i // 5, UTC)
                        .isoformat()
                        .replace("+00:00", "Z")
                    ),  # five prints share each second
                    "yes_price_dollars": "0.5000",
                    "no_price_dollars": "0.5000",
                    "count_fp": "3.00",
                    "taker_side": "yes",
                    "_rank": i,
                }
            )

    def serve(self, min_ts: int | None, cursor: str | None) -> dict[str, Any]:
        rows = [p for p in self.prints if min_ts is None or BASE_TS + p["_rank"] // 5 >= min_ts]
        if self.drop_mark_second and min_ts is not None:
            rows = [p for p in rows if BASE_TS + p["_rank"] // 5 != min_ts]
        rows.sort(key=lambda p: -p["_rank"])
        if cursor and self.expire_empty:
            self.expire_empty = False  # one-shot: the restarted walk gets fresh cursors
            return {"trades": [], "cursor": ""}
        if self.offset:
            start = int(cursor) if cursor else 0
            chunk = rows[start : start + self.page]
            more = start + len(chunk) < len(rows)
            nxt = str(start + len(chunk)) if more else ""
        else:
            if cursor:
                rows = [p for p in rows if p["_rank"] < int(cursor)]
            chunk = rows[: self.page]
            more = len(rows) > len(chunk)
            nxt = str(chunk[-1]["_rank"]) if more and chunk else ""
        out = [{k: v for k, v in p.items() if k != "_rank"} for p in chunk]
        return {"trades": out, "cursor": nxt}


class FakeClient:
    def __init__(self, feed: Feed) -> None:
        self.feed = feed
        self.requests = 0
        self.last_server_date: str | None = None
        self.calls: list[str] = []
        self.volume = 1000.0
        self.fail_trade_with: int | None = None
        self.fail_once_cursor_with: int | None = None

    def fetch_markets_page(self, **kw: Any) -> dict[str, Any]:
        self.requests += 1
        self.calls.append("markets")
        page: dict[str, Any] = copy.deepcopy(json.loads((FIX / "live_game_poll.json").read_text()))
        page["markets"] = (
            [m for m in page["markets"] if m["ticker"] == TICKER]
            if kw["series_ticker"] == "KXNBAGAME"
            else []
        )
        self.volume += 50.0
        for m in page["markets"]:
            m["volume_fp"] = f"{self.volume:.2f}"
        return page

    def fetch_trades_page(
        self, ticker: str, *, min_ts: int | None = None, cursor: str | None = None, **kw: Any
    ) -> dict[str, Any]:
        self.requests += 1
        self.calls.append("trades")
        if self.fail_trade_with is not None:
            raise _HTTPError(self.fail_trade_with)
        if cursor and self.fail_once_cursor_with is not None:
            code, self.fail_once_cursor_with = self.fail_once_cursor_with, None
            raise _HTTPError(code)
        out: dict[str, Any] = self.feed.serve(min_ts, cursor)
        return out


def _cfg(**over: Any) -> PollConfig:
    base: dict[str, Any] = {
        "series": {"KXNBAGAME": 15},
        "jitter_s": 0.0,
        "min_interval_s": 0.0,
        "export_every_polls": 0,
        "max_requests_per_minute": 60,
        "trades_pages_per_poll": 4,
        "trades_pages_per_ticker": 3,
        "trades_budget_reserve": 10,
        "backoff_base_s": 15.0,
    }
    base.update(over)
    return PollConfig(**base)


def _poller(tmp: Path, clock: Clock, client: FakeClient, cfg: PollConfig | None = None) -> Poller:
    return Poller(
        cfg or _cfg(),
        client,  # type: ignore[arg-type]
        DAY,
        TIPS,
        RawStore(tmp / "store", DAY),
        tmp / "hb.json",
        capture_root=tmp / "capture",
        now_fn=clock.now,
        sleep_fn=clock.sleep,
        rng=random.Random(1),
    )


def _tick(p: Poller, clock: Clock, n: int = 1) -> None:
    for _ in range(n):
        p.tick()
        clock.sleep(15)


def _stored(p: Poller) -> pl.DataFrame:
    return p.store.load("trades", TRADE_SCHEMA)


def test_a_burst_larger_than_a_polls_page_budget_is_captured_completely_across_polls(
    tmp_path: Path,
) -> None:
    feed = Feed(2_050)  # 21 pages of 100; a poll reads at most 3 pages of one ticker
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock)  # first sight: volume baseline only
    assert not client.calls.count("trades")
    _tick(p, clock)  # the burst starts: 3 pages, then the cursor is carried
    assert len(_stored(p)) == 300
    assert TICKER in p.st.cursor and TICKER not in p.st.last_trade_ts  # the mark has NOT moved
    assert p.st.open_max[TICKER] == BASE_TS + 2_049 // 5  # newest print seen: the next mark
    for _ in range(12):
        _tick(p, clock)
        if TICKER not in p.st.cursor:
            break
    assert TICKER not in p.st.cursor
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 2_050  # every print, exactly once
    assert p.st.last_trade_ts[TICKER] == BASE_TS + 2_049 // 5  # the mark moved on completion
    # a late arrival after the carried catch-up is found by the verify pass and later polls
    feed.add(7)
    _tick(p, clock, 4)
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 2_057
    assert TICKER not in p.st.verify and not p.st.cursor


def test_the_verify_pass_follows_a_carried_catch_up_and_clears_the_queue(tmp_path: Path) -> None:
    feed = Feed(450)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)  # 3 pages carried
    assert TICKER in p.st.cursor
    _tick(p, clock)  # completes (4th and 5th page): carried, so it stays queued for verification
    assert TICKER not in p.st.cursor and TICKER in p.st.verify and TICKER in p.st.pending
    feed.add(3)  # arrives after the first page was read
    _tick(p, clock)  # the verify pass starts at the new mark and takes it
    assert len(_stored(p)) == 453
    assert TICKER not in p.st.verify and TICKER not in p.st.pending


def test_trade_pages_never_starve_the_quote_polls(tmp_path: Path) -> None:
    feed = Feed(5_000)  # far more than the budget can read
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    p.budget = RequestBudget(20, clock=lambda: 0.0)  # a frozen window: nothing ever refills
    for _ in range(10):
        p.tick()
        clock.sleep(15)
    # 10 quote polls went out although the trade pages were refused once 10 slots had to stay free
    assert client.calls.count("markets") == 10
    assert 0 < client.calls.count("trades") <= 9
    assert p.st.skipped_budget >= 1
    assert p.st.cursor.get(TICKER)  # the unread remainder is carried, not dropped
    assert p.st.last_trade_ts.get(TICKER) is None  # and the mark has not moved


def test_a_restart_mid_catch_up_resumes_from_the_persisted_cursor(tmp_path: Path) -> None:
    feed = Feed(1_000)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)  # 600 prints stored, cursor open
    assert TICKER in p.st.cursor
    stored_before = len(_stored(p))
    cursor_before, open_max_before = p.st.cursor[TICKER], p.st.open_max[TICKER]
    # a new process on the same store
    p2 = _poller(tmp_path, clock, client)
    p2.restore()
    assert p2.st.cursor == {TICKER: cursor_before} and p2.st.open_max == {TICKER: open_max_before}
    assert TICKER not in p2.st.last_trade_ts and TICKER in p2.st.pending
    assert len(p2.st.seen_ids[TICKER]) == stored_before
    calls_before = len(client.calls)
    for _ in range(12):
        _tick(p2, clock)
        if TICKER not in p2.st.cursor and TICKER not in p2.st.verify:
            break
    ids = _stored(p2)["trade_id"]
    assert ids.n_unique() == ids.len() == 1_000  # nothing missed, nothing stored twice
    assert p2.st.last_trade_ts[TICKER] == BASE_TS + 999 // 5
    assert len(client.calls) > calls_before


def test_a_429_mid_catch_up_keeps_the_cursor_and_the_next_poll_resumes(tmp_path: Path) -> None:
    feed = Feed(700)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)
    cursor = p.st.cursor[TICKER]
    client.fail_once_cursor_with = 429
    _tick(p, clock)  # the first resumed page is rate-limited
    assert p.st.n429 == 1 and p.st.cursor[TICKER] == cursor
    # C5: only the trade pulls cool down; the quote polls and the watchdog counters are untouched
    assert p.st.trades_cooldown_until is not None and p.st.cooldown_until is None
    assert p.st.consecutive_failures == 0
    markets_before = client.calls.count("markets")
    _tick(p, clock)  # still inside the trade cool-down: a quote poll goes out, no trade page
    assert client.calls.count("markets") == markets_before + 1
    assert p.st.cursor[TICKER] == cursor
    clock.sleep(300)  # past the cool-down
    for _ in range(10):
        _tick(p, clock)
        if TICKER not in p.st.cursor and TICKER not in p.st.verify:
            break
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 700


def test_a_stale_cursor_restarts_the_walk_without_duplicates(tmp_path: Path) -> None:
    feed = Feed(900)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)
    assert TICKER in p.st.cursor
    client.fail_once_cursor_with = 400  # the server no longer knows the cursor
    for _ in range(12):
        _tick(p, clock)
        if TICKER not in p.st.cursor and TICKER not in p.st.verify:
            break
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 900
    assert "stale cursor" in (p.store.day_dir / "errors.log").read_text()


def test_a_corrupt_or_other_day_state_triggers_the_rewalk_fallback(tmp_path: Path) -> None:
    feed = Feed(120)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)
    assert len(_stored(p)) == 120 and TICKER not in p.st.cursor
    p.trade_state_path.write_text("{ not json")
    p2 = _poller(tmp_path, clock, client)
    p2.restore()
    assert p2.trade_state_fallback and not p2.st.last_trade_ts and TICKER in p2.st.pending
    assert "trade_state missing or unreadable" in (p2.store.day_dir / "errors.log").read_text()
    p.trade_state_path.write_text(
        json.dumps({"day": "2026-01-01", "tickers": {TICKER: {"hwm": 1}}})
    )
    p3 = _poller(tmp_path, clock, client)
    p3.restore()
    assert p3.trade_state_fallback and not p3.st.last_trade_ts  # another day's marks are ignored
    _run_to_done(p3, clock)
    ids = _stored(p3)["trade_id"]
    assert ids.n_unique() == ids.len() == 120 and p3.st.last_trade_ts[TICKER] == BASE_TS + 119 // 5


# ------------------------------------------------------------------ adversary C1 to C5
def _run_to_done(p: Poller, clock: Clock, n: int = 25) -> None:
    for _ in range(n):
        _tick(p, clock)
        if TICKER not in p.st.cursor and TICKER not in p.st.verify and TICKER not in p.st.pending:
            return


def test_c1_an_expired_cursor_answering_200_empty_is_not_completion(tmp_path: Path) -> None:
    feed = Feed(900)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)  # 300 stored, a cursor open
    assert TICKER in p.st.cursor and len(_stored(p)) == 300
    feed.expire_empty = True  # the server no longer knows the cursor and says "nothing, no more"
    _tick(p, clock)
    assert TICKER not in p.st.last_trade_ts  # the mark did NOT move on an empty answer
    assert TICKER in p.st.pending
    assert "empty page after a resume" in (p.store.day_dir / "errors.log").read_text()
    _run_to_done(p, clock)
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 900  # the restarted walk got everything
    assert p.st.last_trade_ts[TICKER] == BASE_TS + 899 // 5


def test_c1_a_walk_that_ends_without_the_marks_second_keeps_the_ticker_open(
    tmp_path: Path,
) -> None:
    feed = Feed(60)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)
    mark = p.st.last_trade_ts[TICKER]
    assert mark == BASE_TS + 59 // 5 and TICKER not in p.st.pending
    feed.add(40)
    feed.drop_mark_second = True  # no stored print of the mark's second comes back
    _tick(p, clock)  # volume rose, so a walk runs; it ends without proof it reached the mark
    assert p.st.last_trade_ts[TICKER] == mark  # not advanced
    assert TICKER in p.st.pending
    assert "walk ended without reaching the mark" in (p.store.day_dir / "errors.log").read_text()
    feed.drop_mark_second = False
    _run_to_done(p, clock)
    assert p.st.last_trade_ts[TICKER] == BASE_TS + 99 // 5 and TICKER not in p.st.pending
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 100


def test_c2_a_lost_state_file_mid_catch_up_still_captures_every_print(tmp_path: Path) -> None:
    feed = Feed(1_000)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)  # 600 stored, cursor open
    assert TICKER in p.st.cursor and len(_stored(p)) == 600
    p.trade_state_path.unlink()  # the state file is lost
    p2 = _poller(tmp_path, clock, client)
    p2.restore()
    assert p2.trade_state_fallback is True
    assert TICKER not in p2.st.last_trade_ts and TICKER in p2.st.pending  # no unsafe mark
    log = (p2.store.day_dir / "errors.log").read_text()
    assert "trade_state missing or unreadable: re-walking 1 tickers" in log  # a missing file logs
    _run_to_done(p2, clock, 40)
    ids = _stored(p2)["trade_id"]
    assert ids.n_unique() == ids.len() == 1_000  # 1000/1000: pages were spent, no print was lost
    assert p2.st.last_trade_ts[TICKER] == BASE_TS + 999 // 5
    p2.beat("running")
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["trade_state_fallback"] is True


def test_c2_the_fallback_is_named_in_the_reconciliation(tmp_path: Path) -> None:
    feed = Feed(300)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)
    p.trade_state_path.write_text("{ broken")
    p2 = _poller(tmp_path, clock, client)
    p2.restore()
    candles = json.loads((FIX / "live_candles_1m.json").read_text())

    def fetch_candlesticks(ticker: str, series: str, **kw: Any) -> dict[str, Any]:
        client.requests += 1
        out: dict[str, Any] = candles
        return out

    client.fetch_candlesticks = fetch_candlesticks  # type: ignore[attr-defined]
    p2.finalize("test")
    rec = json.loads((p2.store.day_dir / "derived" / "reconciliation.json").read_text())
    assert rec["trade_state_fallback"] is True
    # and a clean restart is not marked
    clean = _poller(tmp_path / "c", clock, FakeClient(Feed(10)))
    clean.restore()
    assert clean.trade_state_fallback is False


def test_c3_a_failed_raw_write_applies_nothing_and_the_next_poll_recovers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    feed = Feed(250)  # three pages; the walk completes within one poll
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock)  # baseline
    real_write = p.store.write
    calls: list[str] = []

    def flaky(kind: str, *a: Any, **kw: Any) -> Any:
        if kind == "trades" and not calls:
            calls.append(kind)
            raise OSError("disk full")
        return real_write(kind, *a, **kw)

    monkeypatch.setattr(p.store, "write", flaky)
    _tick(p, clock)  # the walk finished (3 pages) but the write fails
    assert calls == ["trades"]
    assert TICKER not in p.st.last_trade_ts  # the mark was NOT moved ahead of the write
    assert TICKER in p.st.pending and not p.st.seen_ids.get(TICKER)
    assert len(_stored(p)) == 0
    _run_to_done(p, clock)
    ids = _stored(p)["trade_id"]
    assert ids.n_unique() == ids.len() == 250
    assert p.st.last_trade_ts[TICKER] == BASE_TS + 249 // 5


def test_c4_an_offset_cursor_that_repeats_prints_cannot_inflate_the_raw_rows(
    tmp_path: Path,
) -> None:
    feed = Feed(500)
    feed.offset = True  # new prints shift every later page: the same print can come back
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)  # 300 stored, cursor open
    feed.add(100)  # arrives mid catch-up: later pages repeat prints the walk already read
    _run_to_done(p, clock)
    raw = _stored(p)
    assert raw["trade_id"].n_unique() == raw.height  # no duplicate rows reached the raw files
    assert p.st.trade_rows == raw.height  # and the counter is not inflated
    assert raw.height == 600


def test_c5_a_trade_page_429_does_not_silence_the_quote_polls(tmp_path: Path) -> None:
    feed = Feed(900)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 2)
    client.fail_trade_with = 429
    _tick(p, clock)
    assert p.st.trades_cooldown_until is not None and p.st.cooldown_until is None
    n = client.calls.count("markets")
    _tick(p, clock, 3)  # inside the trade cool-down (15 to 19 s, then doubling): quotes keep going
    assert client.calls.count("markets") >= n + 2
    assert p.st.consecutive_failures == 0
