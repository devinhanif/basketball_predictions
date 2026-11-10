"""15-second poller, candles, export and reconciliation: recorded fixtures, no network, no clock."""

from __future__ import annotations

import copy
import gzip
import json
import random
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from nba.kalshi import candles15, poll15
from nba.kalshi.client import KalshiClient
from nba.kalshi.poll15 import (
    QUOTE_SCHEMA,
    TRADE_SCHEMA,
    PollConfig,
    Poller,
    RawStore,
    RequestBudget,
    event_date,
    load_config,
    plan,
    problem,
    quote_rows,
    trade_rows,
)

FIX = Path(__file__).parent / "fixtures"
DAY = date(2026, 11, 9)
T0 = datetime(2026, 11, 10, 0, 0, 7, 250000, tzinfo=UTC)


def _json(name: str) -> dict[str, Any]:
    data = json.loads((FIX / name).read_text())
    assert isinstance(data, dict)
    return data


class _HTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"http {code}")
        self.response = type("R", (), {"status_code": code})()


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += timedelta(seconds=s)


class FakeClient:
    """Stands in for KalshiClient: replays the recorded pages; counts calls; can fail on demand."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.requests = 0
        self.last_server_date: str | None = "Tue, 10 Nov 2026 00:00:07 GMT"
        self.calls: list[tuple[str, str]] = []
        self.volume_step = 0.0
        self.fail_with: int | None = None
        self.open_markets = True
        self._vol = 0.0

    def _maybe_fail(self) -> None:
        self.requests += 1
        if self.fail_with is not None:
            raise _HTTPError(self.fail_with)

    def fetch_markets_page(self, **kw: Any) -> dict[str, Any]:
        self._maybe_fail()
        self.calls.append(("markets", kw["series_ticker"]))
        page = copy.deepcopy(_json("live_game_poll.json"))
        if kw["series_ticker"] != "KXNBAGAME" or not self.open_markets:
            page["markets"] = []
        else:
            self._vol += self.volume_step
            for m in page["markets"]:
                m["volume_fp"] = f"{1000.0 + self._vol:.2f}"
        return page

    def fetch_trades_page(self, ticker: str, **kw: Any) -> dict[str, Any]:
        self._maybe_fail()
        self.calls.append(("trades", ticker))
        page = copy.deepcopy(_json("live_trades.json"))
        page["cursor"] = ""  # one page: the recorded cursor would make every pull look capped
        for t in page["trades"]:
            t["ticker"] = ticker
            t["trade_id"] = f"{t['trade_id']}-{ticker}"  # ids are unique across tickers
        return page


def _cfg(**over: Any) -> PollConfig:
    base: dict[str, Any] = {
        "series": {"KXNBAGAME": 15},
        "jitter_s": 0.0,
        "min_interval_s": 0.0,
        "backoff_base_s": 15.0,
        "backoff_max_s": 60.0,
        "export_every_polls": 0,
    }
    base.update(over)
    return PollConfig(**base)


def _poller(
    tmp_path: Path, clock: FakeClock, client: FakeClient, cfg: PollConfig | None = None
) -> Poller:
    tips = [datetime(2026, 11, 10, 0, 30, tzinfo=UTC), datetime(2026, 11, 10, 3, 0, tzinfo=UTC)]
    return Poller(
        cfg or _cfg(),
        client,  # type: ignore[arg-type]
        DAY,
        tips,
        RawStore(tmp_path / "store", DAY),
        tmp_path / "hb.json",
        export_dir=tmp_path / "export",
        now_fn=clock.now,
        sleep_fn=clock.sleep,
        rng=random.Random(1),
    )


# ------------------------------------------------------------------ parsing
def test_event_date_reads_the_slate_date_not_the_expiry() -> None:
    assert event_date("KXNBAGAME-26NOV09SASPHX") == DAY
    assert event_date("KXNBAGAME-26OCT21CHABKN") == date(2026, 10, 21)
    assert event_date("KXNBAFUTURES-27") is None
    assert event_date("KXNBAGAME-26FOO09SASPHX") is None


def test_quote_rows_keep_only_the_slate_date_and_parse_dollars() -> None:
    page = _json("live_game_poll.json")
    other = copy.deepcopy(page["markets"][0])
    other["event_ticker"] = "KXNBAGAME-26NOV12SASPHX"
    rows = quote_rows(
        [*page["markets"], other],
        series="KXNBAGAME",
        day=DAY,
        fetched_at=T0,
        request_at=T0 - timedelta(seconds=1),
        server_date="Tue, 10 Nov 2026 00:00:07 GMT",
    )
    assert len(rows) == len(page["markets"])  # the 12 Nov market is dropped
    r = rows[0]
    m = page["markets"][0]
    assert r["yes_bid"] == float(m["yes_bid_dollars"]) and r["yes_ask"] == float(
        m["yes_ask_dollars"]
    )
    assert r["last"] == float(m["last_price_dollars"]) and r["volume"] == float(m["volume_fp"])
    assert r["open_interest"] == float(m["open_interest_fp"])
    assert r["fetched_at"] == T0 and r["server_date"].endswith("GMT")
    pl.DataFrame(rows, schema=QUOTE_SCHEMA)  # fits the raw schema


def test_trade_rows_parse_the_recorded_prints() -> None:
    page = _json("live_trades.json")
    rows = trade_rows(page["trades"], T0)
    assert len(rows) == len(page["trades"])
    assert all(0.0 <= r["yes_price"] <= 1.0 and r["count"] > 0 for r in rows)
    assert all(r["created_time"].tzinfo is not None for r in rows)
    assert not trade_rows([{"trade_id": "x", "created_time": "garbage"}, {"ticker": "t"}], T0)


# ------------------------------------------------------------------ raw store
def test_raw_store_is_write_once_and_reloads(tmp_path: Path) -> None:
    store = RawStore(tmp_path, DAY)
    rows = trade_rows(_json("live_trades.json")["trades"], T0)
    p1 = store.write("trades", T0, "poll", rows, TRADE_SCHEMA)
    p2 = store.write(
        "trades", T0, "poll", rows, TRADE_SCHEMA
    )  # same stamp: a second file, not a replace
    assert p1 is not None and p2 is not None and p1 != p2 and p1.exists() and p2.exists()
    assert store.write("trades", T0, "poll", [], TRADE_SCHEMA) is None
    assert store.load("trades", TRADE_SCHEMA).height == len(rows)  # same prints, one copy
    assert not list((tmp_path / DAY.isoformat()).rglob("*.tmp"))
    assert store.load("quotes", QUOTE_SCHEMA).is_empty()


# ------------------------------------------------------------------ candles
def _t(sec: float, price: float, n: float, tid: str, ticker: str = "T") -> dict[str, Any]:
    base = datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC)
    return {
        "fetched_at": base,
        "ticker": ticker,
        "trade_id": tid,
        "created_time": base + timedelta(seconds=sec),
        "yes_price": price,
        "no_price": round(1 - price, 4),
        "count": n,
        "taker_side": "yes",
    }


def test_trade_candles_are_true_ohlc_per_bucket_and_dedupe() -> None:
    rows = [
        _t(1.0, 0.50, 10, "a"),
        _t(5.0, 0.55, 5, "b"),
        _t(14.9, 0.52, 1, "c"),
        _t(15.0, 0.60, 2, "d"),  # next bucket: boundary belongs to the bucket it starts
        _t(16.0, 0.58, 3, "e"),
        _t(16.0, 0.58, 3, "e"),  # duplicate id (seen on two pulls) counts once
        _t(50.0, 0.40, 7, "f"),  # a bucket with a gap before it: no filler candles
    ]
    df = candles15.trade_candles(candles15.trades_frame(rows), 15)
    base = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp())
    got = {r["t"] - base: r for r in df.iter_rows(named=True)}
    assert sorted(got) == [0, 15, 45]
    assert (got[0]["o"], got[0]["h"], got[0]["l"], got[0]["c"]) == (0.50, 0.55, 0.50, 0.52)
    assert got[0]["volume"] == 16 and got[0]["n_trades"] == 3
    assert (got[15]["o"], got[15]["c"], got[15]["n_trades"]) == (0.60, 0.58, 2)
    assert (got[45]["o"], got[45]["h"], got[45]["l"], got[45]["c"]) == (0.40, 0.40, 0.40, 0.40)


def _q(sec: float, bid: float, ask: float, vol: float, ticker: str = "T") -> dict[str, Any]:
    at = datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=sec)
    return {
        "fetched_at": at, "request_at": at, "server_date": None, "series": "KXNBAGAME",
        "ticker": ticker, "event_ticker": "KXNBAGAME-26NOV09AAABBB", "title": "x",
        "status": "active",
        "yes_bid": bid, "yes_ask": ask, "yes_bid_size": 1.0, "yes_ask_size": 1.0, "last": ask,
        "volume": vol, "open_interest": 5.0, "market_updated": None,
    }  # fmt: skip


def test_quote_points_take_the_last_poll_and_null_mid_on_one_sided_book() -> None:
    q = candles15.quotes_frame(
        [
            _q(1, 0.40, 0.44, 10),
            _q(9, 0.41, 0.45, 12),
            _q(16, 0.0, 0.46, 13),
            _q(31, 0.42, 0.46, 14),
        ]
    )
    pts = candles15.quote_points(q, 15)
    rows = list(pts.iter_rows(named=True))
    assert [r["n_polls"] for r in rows] == [2, 1, 1]
    assert (
        rows[0]["bid"] == 0.41 and rows[0]["volume"] == 12 and rows[0]["mid"] == pytest.approx(0.43)
    )
    assert rows[1]["mid"] is None and rows[1]["bid"] == 0.0  # empty bid side: mid refused, bid kept
    assert candles15.quote_points(candles15.quotes_frame([]), 15).is_empty()


def test_build_export_shape_is_display_only_and_columnar() -> None:
    q = candles15.quotes_frame([_q(1, 0.4, 0.44, 10), _q(31, 0.42, 0.46, 14)])
    t = candles15.trades_frame([_t(2.0, 0.43, 4, "a")])
    out = candles15.build_export(q, t, day=DAY, bucket_s=15, built_at=T0, status="live")
    assert out["schema"] == 1 and out["kind"] == "kalshi_candles15" and out["display_only"] is True
    assert out["built_at"].endswith("Z") and out["slate_date"] == "2026-11-09"
    (m,) = out["markets"]
    assert m["ticker"] == "T" and m["n_polls"] == 2 and m["max_gap_s"] == 30
    assert len(m["quotes"][0]) == len(out["quote_fields"]) and len(m["candles"][0]) == len(
        out["candle_fields"]
    )
    assert out["reconciliation"] is None
    json.dumps(out)  # serialisable as is


# ------------------------------------------------------------------ official 1-minute + reconcile
def test_parse_official_reads_live_and_historical_field_names() -> None:
    df = candles15.parse_official("TK", _json("live_candles_1m.json"))
    assert df.height == len(_json("live_candles_1m.json")["candlesticks"])
    assert df["c"].null_count() < df.height and df["end_ts"].dtype == pl.Int64
    hist = {"candlesticks": [{"end_period_ts": 60, "price": {"open": "0.1", "high": "0.3",
            "low": "0.1", "close": "0.2"}, "volume": 4, "open_interest": 9,
            "yes_bid": {"close": "0.1"}, "yes_ask": {"close": "0.3"}}]}  # fmt: skip
    h = candles15.parse_official("TK", hist).row(0, named=True)
    assert (h["o"], h["h"], h["l"], h["c"], h["volume"]) == (0.1, 0.3, 0.1, 0.2, 4.0)


def test_minute_rollup_uses_inclusive_period_end_and_reconcile_counts() -> None:
    base = int(datetime(2026, 11, 10, tzinfo=UTC).timestamp())
    rows = [_t(30, 0.50, 2, "a"), _t(60, 0.52, 3, "b"), _t(61, 0.49, 1, "c"), _t(130, 0.7, 5, "d")]
    mine = candles15.minute_rollup(candles15.trades_frame(rows))
    by_end = {r["end_ts"] - base: r for r in mine.iter_rows(named=True)}
    assert sorted(by_end) == [60, 120, 180]  # second 60 closes the first minute (inclusive end)
    assert by_end[60]["h"] == 0.52 and by_end[60]["volume"] == 5
    official = pl.DataFrame(
        [
            {"ticker": "T", "end_ts": base + 60, "o": 0.5, "h": 0.52, "l": 0.5, "c": 0.52,
             "volume": 5.0, "open_interest": 1.0, "yes_bid_close": 0.5, "yes_ask_close": 0.53},
            {"ticker": "T", "end_ts": base + 120, "o": 0.49, "h": 0.49, "l": 0.48, "c": 0.48,
             "volume": 1.0, "open_interest": 1.0, "yes_bid_close": 0.5, "yes_ask_close": 0.53},
            {"ticker": "T", "end_ts": base + 240, "o": 0.6, "h": 0.6, "l": 0.6, "c": 0.6,
             "volume": 9.0, "open_interest": 1.0, "yes_bid_close": 0.5, "yes_ask_close": 0.53},
        ],
        schema=candles15.OFFICIAL_SCHEMA,
    )  # fmt: skip
    rec = candles15.reconcile(official, mine, since_ts=base)
    assert rec["official_traded_minutes"] == 3 and rec["compared_minutes"] == 2
    assert rec["official_minutes_missing_ours"] == 1 and rec["ours_minutes_missing_official"] == 1
    assert (rec["agree_open"], rec["agree_high"], rec["agree_low"], rec["agree_close"]) == (
        2,
        2,
        1,
        1,
    )
    assert rec["volume_official"] == 6.0 and rec["volume_ours"] == 6.0


# ------------------------------------------------------------------ poller (fake client)
def test_one_poll_writes_raw_quotes_and_a_heartbeat(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    assert p.run(once=True) == 0
    q = p.store.load("quotes", QUOTE_SCHEMA)
    assert q.height == 4 and set(q["series"]) == {"KXNBAGAME"}
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["job"] == "kalshi_candles15" and hb["date"] == "2026-11-09"
    assert hb["polls"] == 1 and hb["quote_rows"] == 4 and hb["requests"] == 1
    assert hb["window_start"].startswith(
        "2026-11-10T00:00:00"
    )  # first tip 00:30Z - 30 min = 00:00Z
    assert client.calls == [("markets", "KXNBAGAME")]  # no volume jump yet: no trade pull


def test_trades_are_pulled_only_for_tickers_whose_volume_rose_and_deduped(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    client.volume_step = 25.0
    p = _poller(tmp_path, clock, client, _cfg(trades_max_tickers=2))
    p.run(once=True)  # baseline volumes, no jump
    clock.sleep(15)
    p.run(once=True)  # volumes rose on all 4 tickers; the budget of 2 tickers per poll applies
    pulled = [t for k, t in client.calls if k == "trades"]
    assert len(pulled) == 2 and len(set(pulled)) == 2
    n_rows = p.st.trade_rows
    assert n_rows > 0 and p.store.load("trades", TRADE_SCHEMA).height == n_rows
    clock.sleep(15)
    p.run(once=True)  # the other two tickers; the first two were fully pulled and left the queue
    pulled2 = [t for k, t in client.calls if k == "trades"][2:]
    assert len(pulled2) <= 2  # never more than the per-poll ticker budget
    # the same recorded page came back for every pull: a print is stored once per ticker
    stored = p.store.load("trades", TRADE_SCHEMA)
    assert stored.select(pl.struct("ticker", "trade_id").n_unique()).item() == stored.height
    assert p.st.trade_rows == stored.height >= n_rows


def test_a_restart_reloads_the_night_and_does_not_repull_trades(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    client.volume_step = 10.0
    p = _poller(tmp_path, clock, client)
    p.run(once=True)
    clock.sleep(15)
    p.run(once=True)
    rows_before = p.store.load("trades", TRADE_SCHEMA).height
    assert rows_before > 0
    clock.sleep(15)
    p2 = _poller(tmp_path, clock, client)  # a new process on the same store
    p2.restore()
    assert p2.st.quote_rows == p.st.quote_rows and p2.st.trade_rows == rows_before
    assert set(p2.st.seen_ids) == set(p.st.seen_ids)
    p2.run(once=True)  # same recorded prints come back: all already seen
    assert p2.store.load("trades", TRADE_SCHEMA).height == rows_before


def test_429_opens_a_cooldown_with_no_requests_inside_it(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    client.fail_with = 429
    p.run(once=True)
    assert p.st.n429 == 1 and p.st.consecutive_failures == 1 and client.requests == 1
    cool = p.st.cooldown_until
    assert cool is not None and 15.0 <= (cool - clock.now()).total_seconds() <= 15.0 * 1.25 + 0.1
    # the loop sleeps through the cool-down: not one request until it ends
    client.fail_with = None
    n = client.requests
    p.run(start_now=True, max_polls=2)
    assert client.requests > n
    assert clock.now() >= cool  # the loop slept through the whole cool-down before asking again
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["n429"] == 1 and hb["consecutive_failures"] == 0
    assert "429" in (tmp_path / "store" / DAY.isoformat() / "errors.log").read_text()


def test_backoff_doubles_and_is_capped(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    client.fail_with = 503
    p = _poller(tmp_path, clock, client, _cfg(backoff_base_s=10.0, backoff_max_s=40.0))
    waits = []
    for _ in range(5):
        p.poll_series("KXNBAGAME")
        assert p.st.cooldown_until is not None
        waits.append((p.st.cooldown_until - clock.now()).total_seconds())
    assert [round(w / 1.25) <= c for w, c in zip(waits, [10, 20, 40, 40, 40], strict=True)] == [
        True
    ] * 5
    assert waits[0] >= 10.0 and waits[2] >= 40.0 and max(waits) <= 40.0 * 1.25 + 0.01


def test_request_budget_slides_and_a_poll_is_skipped_not_queued(tmp_path: Path) -> None:
    now = [0.0]
    b = RequestBudget(3, clock=lambda: now[0])
    assert [b.take() for _ in range(4)] == [True, True, True, False]
    now[0] = 61.0
    assert b.take()
    clock = FakeClock(T0)
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    p.budget = RequestBudget(0)
    p.run(once=True)
    assert client.requests == 0 and p.st.skipped_budget == 1 and p.st.errors == 0


def test_the_poller_finishes_when_no_game_market_is_open_after_the_last_tip(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 11, 10, 3, 5, tzinfo=UTC))  # after the last tip (03:00Z)
    client = FakeClient(clock)
    client.open_markets = False
    p = _poller(tmp_path, clock, client)
    seen: list[str] = []
    orig = p.finalize

    def spy(why: str) -> None:
        seen.append(why)
        orig(why)

    p.finalize = spy  # type: ignore[method-assign]
    assert p.run(start_now=True) == 0
    assert seen == ["no open game market left"] and p.st.polls == 3
    hb = json.loads((tmp_path / "hb.json").read_text())
    assert hb["status"] == "done"
    assert (tmp_path / "export" / "latest.json").exists()  # the final export is written


def test_the_window_cap_ends_the_night(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 11, 10, 7, 30, tzinfo=UTC))  # last tip + 4 h = 07:00Z
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    assert p.run(start_now=True) == 0
    assert client.requests == 0 and p.status == "done"


def test_a_failing_export_never_stops_the_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(candles15, "write_export", boom)
    clock = FakeClock(T0)
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client, _cfg(export_every_polls=1))
    p.run(once=True)
    assert p.st.quote_rows == 4 and p.st.errors == 1 and "export failed" in p.st.last_error


def test_a_poll_before_the_window_waits_and_beats(tmp_path: Path) -> None:
    clock = FakeClock(datetime(2026, 11, 9, 22, 0, tzinfo=UTC))  # 2 h before the window opens
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    p.run(max_polls=1)
    assert p.st.polls == 1 and clock.now() >= p.window_start and sum(clock.sleeps) >= 7200 - 1
    assert 30.0 in clock.sleeps  # slept in 30 s steps, beating between them


# ------------------------------------------------------------------ plan / watchdog / config
def test_plan_decisions() -> None:
    cfg = _cfg()
    tips = [datetime(2026, 11, 10, 0, 30, tzinfo=UTC), datetime(2026, 11, 10, 3, 0, tzinfo=UTC)]
    d = DAY
    assert plan(T0, [], None, d, cfg)[0] == "no_games"
    assert plan(datetime(2026, 11, 9, 20, 0, tzinfo=UTC), tips, None, d, cfg)[0] == "early"
    assert plan(datetime(2026, 11, 9, 23, 55, tzinfo=UTC), tips, None, d, cfg)[0] == "start"
    assert plan(datetime(2026, 11, 10, 8, 0, tzinfo=UTC), tips, None, d, cfg)[0] == "over"
    done = {"date": "2026-11-09", "status": "done"}
    assert plan(datetime(2026, 11, 10, 4, 0, tzinfo=UTC), tips, done, d, cfg)[0] == "done"
    stale = {"date": "2026-11-08", "status": "done"}  # yesterday's finish never blocks tonight
    assert plan(datetime(2026, 11, 10, 4, 0, tzinfo=UTC), tips, stale, d, cfg)[0] == "start"


def test_watchdog_problem_names_each_failure() -> None:
    cfg = _cfg(heartbeat_stale_s=180.0)
    tips = [datetime(2026, 11, 10, 0, 30, tzinfo=UTC), datetime(2026, 11, 10, 3, 0, tzinfo=UTC)]
    inside = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    fresh = {"date": "2026-11-09", "status": "running"}
    assert problem(inside, [], None, None, DAY, cfg) is None
    assert problem(datetime(2026, 11, 9, 20, 0, tzinfo=UTC), tips, None, None, DAY, cfg) is None
    assert "no heartbeat" in (problem(inside, tips, None, None, DAY, cfg) or "")
    assert "2026-11-08" in (problem(inside, tips, {"date": "2026-11-08"}, inside, DAY, cfg) or "")
    assert problem(inside, tips, fresh, inside - timedelta(seconds=60), DAY, cfg) is None
    assert "stale" in (
        problem(inside, tips, fresh, inside - timedelta(seconds=400), DAY, cfg) or ""
    )
    assert problem(inside, tips, {"date": "2026-11-09", "status": "done"}, None, DAY, cfg) is None


def test_the_shipped_config_loads_and_stays_inside_its_own_limits() -> None:
    cfg = load_config()
    assert cfg.bucket_s == 15 and cfg.fast_s == 15 and "KXNBAGAME" in cfg.series
    assert cfg.max_requests_per_minute <= 60 and cfg.min_interval_s >= 0.25
    assert cfg.backoff_max_s >= cfg.backoff_base_s >= 1
    assert set(cfg.series).isdisjoint({"KXNBAPTS", "KXNBAREB"})  # props are opt-in


# ------------------------------------------------------------------ read-only guarantees
def test_client_trades_and_candle_urls_carry_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    class _Resp:
        status_code = 200
        headers = {"date": "Tue, 10 Nov 2026 00:00:07 GMT"}

        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, Any]:
            return {}

    def fake_get(url: str, **kwargs: Any) -> _Resp:
        seen.append((url, kwargs.get("params") or {}, kwargs))
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)
    c = KalshiClient(max_attempts=2)
    c.fetch_trades_page("T-1", min_ts=5, limit=10)
    c.fetch_candlesticks("T-1", "S", tier="live", start_ts=1, end_ts=2, period_interval=1)
    assert seen[0][0].endswith("/markets/trades") and seen[0][1] == {
        "ticker": "T-1", "limit": 10, "min_ts": 5,
    }  # fmt: skip
    assert seen[1][1]["period_interval"] == 1
    assert c.requests == 2 and c.last_server_date == "Tue, 10 Nov 2026 00:00:07 GMT"
    for _, _, kwargs in seen:
        assert "headers" not in kwargs and "auth" not in kwargs


def test_client_does_not_sleep_after_the_last_failed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    class _R429:
        status_code = 429
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            raise _HTTPError(429)

        def json(self) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _R429())
    slept: list[float] = []
    c = KalshiClient(max_attempts=2)
    c._sleep = lambda seconds: slept.append(seconds)  # type: ignore[method-assign]
    c.rate_limiter.min_interval_s = 0.0
    with pytest.raises(_HTTPError):
        c.fetch_trades_page("T")
    assert c.requests == 2 and slept == [2.0]  # one pause between the two attempts, none after


def test_no_order_credential_or_websocket_code_in_the_new_modules() -> None:
    """Non-negotiable 5, kept true by a grep: read-only paths, no auth headers, no websockets."""
    root = Path(poll15.__file__).parent
    forbidden = re.compile(
        r"portfolio|/orders|create_order|place_order|cancel_order|Authorization|KALSHI-ACCESS|"
        r"api_key|private_key|websocket|wss://|os\.environ|getenv",
        re.IGNORECASE,
    )
    for name in ("poll15.py", "candles15.py", "client.py"):
        text = (root / name).read_text()
        # docstrings and comments may say "no order, portfolio or credential path": code is judged
        code = re.sub(r'"""[\s\S]*?"""', "", text)
        code = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
        assert not forbidden.search(code), (name, forbidden.search(code))


def test_finalize_pulls_each_ticker_once_and_a_rerun_makes_no_request(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    p = _poller(tmp_path, clock, client)
    p.run(once=True)
    candles = _json("live_candles_1m.json")
    seen: list[str] = []

    def fetch_candlesticks(ticker: str, series: str, **kw: Any) -> dict[str, Any]:
        seen.append(ticker)
        assert kw["tier"] == "live" and kw["period_interval"] == 1
        return candles

    client.fetch_candlesticks = fetch_candlesticks  # type: ignore[attr-defined]
    budget = RequestBudget(100)
    args = (client, p.store, DAY, p.cfg, p.window_start, clock.now(), budget)
    rec = candles15.finalize_day(*args, export_dir=tmp_path / "export")  # type: ignore[arg-type]
    assert sorted(seen) == sorted(set(p.store.load("quotes", QUOTE_SCHEMA)["ticker"]))
    assert rec["tickers"] == 4 and rec["errors"] == [] and rec["compared_minutes"] == 0
    n = len(seen)
    candles15.finalize_day(*args, export_dir=tmp_path / "export")  # type: ignore[arg-type]
    assert len(seen) == n  # official raw is write-once and re-read, not re-fetched
    raw = next((p.store.day_dir / "official_1m").glob("*.json.gz"))
    assert json.loads(gzip.decompress(raw.read_bytes())) == candles
    out = json.loads((tmp_path / "export" / "latest.json").read_text())
    assert out["status"] == "final" and out["reconciliation"]["tickers"] == 4
    assert (p.store.day_dir / "derived" / "reconciliation.json").exists()


def test_compact_is_verified_lossless_and_prune_only_after_the_check(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    client = FakeClient(clock)
    client.volume_step = 10.0
    p = _poller(tmp_path, clock, client)
    for _ in range(3):
        p.run(once=True)
        clock.sleep(15)
    before_q = p.store.load("quotes", QUOTE_SCHEMA)
    before_t = p.store.load("trades", TRADE_SCHEMA)
    assert before_q.height == 12 and before_t.height > 0
    rep = p.store.compact("quotes", QUOTE_SCHEMA)
    assert rep["verified"] and rep["rows"] == 12 and rep["files"] == 3 and rep["pruned_files"] == 0
    assert p.store.load("quotes", QUOTE_SCHEMA).height == 12  # inputs kept: still no double count
    rep_t = p.store.compact("trades", TRADE_SCHEMA, prune=True)
    assert rep_t["pruned_files"] == rep_t["files"] and rep_t["verified"]
    assert not list((p.store.day_dir / "trades").glob("*.parquet"))
    after_t = p.store.load("trades", TRADE_SCHEMA)
    assert after_t.sort("trade_id").equals(before_t.sort("trade_id"))
    assert poll15.main(["compact", "--date", DAY.isoformat(), "--store", str(tmp_path / "store"),
                        "--tip", "2026-11-10T00:30:00Z"]) == 0  # fmt: skip
