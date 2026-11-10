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
        rows.sort(key=lambda p: -p["_rank"])
        if cursor:
            rows = [p for p in rows if p["_rank"] < int(cursor)]
        chunk = rows[: self.page]
        more = len(rows) > len(chunk)
        out = [{k: v for k, v in p.items() if k != "_rank"} for p in chunk]
        return {"trades": out, "cursor": str(chunk[-1]["_rank"]) if more and chunk else ""}


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
    assert p.st.n429 == 1 and p.st.cursor[TICKER] == cursor and p.st.cooldown_until is not None
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


def test_a_state_file_for_another_day_or_a_corrupt_one_falls_back_to_the_stored_prints(
    tmp_path: Path,
) -> None:
    feed = Feed(120)
    clock, client = Clock(), FakeClient(feed)
    p = _poller(tmp_path, clock, client)
    _tick(p, clock, 3)
    assert len(_stored(p)) == 120 and TICKER not in p.st.cursor
    p.trade_state_path.write_text("{ not json")
    p2 = _poller(tmp_path, clock, client)
    p2.restore()
    assert p2.st.last_trade_ts[TICKER] == BASE_TS + 119 // 5  # rebuilt from the newest stored print
    assert "trade_state unreadable" in (p2.store.day_dir / "errors.log").read_text()
    p.trade_state_path.write_text(
        json.dumps({"day": "2026-01-01", "tickers": {TICKER: {"hwm": 1}}})
    )
    p3 = _poller(tmp_path, clock, client)
    p3.restore()
    assert p3.st.last_trade_ts[TICKER] == BASE_TS + 119 // 5  # another day's marks are ignored
