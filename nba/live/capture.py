"""Game-night capture loop: ESPN scoreboard and summaries, Kalshi markets and order books.

Window: 2 h before the first tip to 4 h after the last tip of the slate date (ET), one tick every
``interval_s`` seconds. Every request has a 10 s timeout and bounded retries; a tick that raises is
logged and the loop goes on. Raw bodies are stored write-once (``nba.live.store``).
Public data only.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher
from nba.live.store import RawStore, write_json_atomic

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
#: ESPN abbreviation -> Kalshi abbreviation where they differ.
ESPN_TO_KALSHI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}
JOB = "live_capture"
FINAL_KEEP_S = 15 * 60


@dataclass(frozen=True)
class Budget:
    max_requests_per_tick: int = 600
    tick_deadline_s: float = 55.0
    sweep_budget: int = 900
    sweep_deadline_s: float = 420.0
    probe_per_tick: int = (
        10  # non-slate series re-probed per tick (round robin) for new slate markets
    )
    orderbook_workers: int = 4
    rate_per_s: float = 8.0
    lead_h: float = 2.0
    tail_h: float = 4.0
    interval_s: float = 60.0
    markets_every_after_empty: int = (
        5  # poll Kalshi every N ticks once no open dated market is left
    )
    orderbook_depth_limit: int = 600
    trades_max_tickers: int = (
        150  # markets whose trades are pulled per tick (largest volume jump first)
    )
    trades_page_budget: int = 200  # trade pages per tick, all tickers (carry-over beyond it)
    trades_pages_per_ticker: int = 40  # one ticker cannot take the whole tick (fairness)
    trades_page_reserve: int = 12  # requests left for probe_other after the trade pages
    trades_page_limit: int = 1000
    trades_state_open_cap: int = 20_000  # open ids persisted per ticker (more: cursor only)
    drain_max_rounds: int = 400


@dataclass(frozen=True)
class Game:
    event_id: str
    tip: datetime  # UTC
    state: str  # pre | in | post
    away: str  # Kalshi abbreviation
    home: str

    @property
    def key(self) -> tuple[str, str]:
        return self.away, self.home


def parse_scoreboard(body: bytes) -> list[Game]:
    out: list[Game] = []
    for ev in json.loads(body).get("events", []):
        try:
            comp = ev["competitions"][0]
            teams = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
            tip = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            if tip.tzinfo is None:
                tip = tip.replace(tzinfo=UTC)
            state = ev["status"]["type"]["state"]
            out.append(
                Game(
                    str(ev["id"]),
                    tip.astimezone(UTC),
                    str(state),
                    ESPN_TO_KALSHI.get(teams["away"], teams["away"]),
                    ESPN_TO_KALSHI.get(teams["home"], teams["home"]),
                )
            )
        except (KeyError, IndexError, ValueError):
            continue
    return out


def window(games: list[Game], b: Budget) -> tuple[datetime, datetime] | None:
    if not games:
        return None
    return (
        min(g.tip for g in games) - timedelta(hours=b.lead_h),
        max(g.tip for g in games) + timedelta(hours=b.tail_h),
    )


def orderbook_targets(
    markets: list[dict[str, Any]],
    live_games: list[Game],
    leg_tickers: set[str],
    limit: int,
) -> tuple[list[str], list[str]]:
    """(tickers to request in priority order, tickers skipped for the limit).

    Priority: paper-position legs; then game-winner markets of in-progress games; then spread and
    total markets nearest a 50/50 price first (the ones that move the most information).
    """
    live_keys = {g.key for g in live_games}
    by_ticker = {str(m["ticker"]): m for m in markets}
    ordered: list[str] = sorted(leg_tickers)
    seen = set(ordered)
    rest: list[tuple[int, float, str]] = []
    for t, m in by_ticker.items():
        if t in seen:
            continue
        series = t.split("-")[0]
        if series not in kp.GAME_SERIES:
            continue
        if kp.event_teams(str(m.get("event_ticker", ""))) not in live_keys:
            continue
        bid, ask = kp.to_f(m.get("yes_bid_dollars")), kp.to_f(m.get("yes_ask_dollars"))
        mid = 0.5 if bid is None or ask is None else (bid + ask) / 2
        rest.append((0 if series == "KXNBAGAME" else 1, abs(mid - 0.5), t))
    rest.sort()
    ordered += [t for _, _, t in rest]
    return ordered[:limit], ordered[limit:]


@dataclass
class State:
    ticks: int = 0
    errors: int = 0
    first_post: dict[str, float] = field(default_factory=dict)
    series: list[str] = field(default_factory=list)
    slate_series: list[str] = field(default_factory=list)
    other_series: list[str] = field(default_factory=list)
    probe_idx: int = 0
    swept_start: bool = False
    rules_seen: set[str] = field(default_factory=set)
    orderbook_blocked: bool = False
    empty_ticks: int = 0
    last_markets: list[dict[str, Any]] = field(default_factory=list)
    series_refreshed: float = 0.0
    volumes: dict[str, float] = field(default_factory=dict)  # ticker -> volume_fp at last tick
    trade_last_ts: dict[str, int] = field(default_factory=dict)
    trade_ids: dict[str, set[str]] = field(default_factory=dict)  # ids seen at last_ts
    trade_pending: dict[str, None] = field(default_factory=dict)  # backlog of tickers to pull
    # Catch-up in progress per ticker. Pages come newest first, so the high-water mark
    # (trade_last_ts / trade_ids) moves only when a catch-up reaches it; until then these hold what
    # was stored, where to resume, and the newest print seen (the next mark).
    trade_open: dict[str, dict[str, int | None]] = field(default_factory=dict)  # id -> ts
    trade_cursor: dict[str, str] = field(default_factory=dict)
    trade_open_max: dict[str, int] = field(default_factory=dict)
    trade_429_streak: int = 0
    trade_skip_ticks: int = 0
    trade_unproven: dict[str, int] = field(default_factory=dict)  # walks that never saw the mark
    trade_state_written: str = ""


@dataclass
class TradePull:
    trades: list[dict[str, Any]]
    complete: bool
    cursor: str | None = None
    pages: int = 0
    rate_limited: bool = False


PROP_PREFIXES = (
    "KXNBAPTS",
    "KXNBAREB",
    "KXNBAAST",
    "KXNBA3PT",
    "KXNBAPRA",
    "KXNBASTL",
    "KXNBABLK",
    "KXNBA2D",
    "KXNBA3D",
)


def trade_priority(ticker: str) -> int:
    """0 game winners, 1 player props, 2 everything else (spreads, totals, halves, doubles)."""
    series = ticker.split("-")[0]
    if series == "KXNBAGAME":
        return 0
    return 1 if series.startswith(PROP_PREFIXES) else 2


class Capture:
    def __init__(
        self,
        day: date,
        out: Path,
        heartbeat: Path,
        *,
        fetcher: Fetcher,
        budget: Budget | None = None,
        legs_file: Path | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep_fn: Callable[[float], None] = time.sleep,
        fresh: Any = None,
    ) -> None:
        self.fresh = fresh  # optional nba.live.espn_fresh.FreshPoller (own thread, own client)
        self.day = day
        self.store = RawStore(out)
        self.heartbeat = heartbeat
        self.fetcher = fetcher
        self.budget = budget or Budget()
        self.legs_file = legs_file
        self.now = now_fn
        self.sleep = sleep_fn
        self.st = State()
        self.start_ts = self.now()
        self.out = out
        self._page_lock = threading.Lock()
        self._pages_left = 0
        self._trade_stop = False  # a 429 this tick: no new trade pages until the next tick
        self._load_trade_state()

    # ------------------------------------------------------------------ logging
    def log_error(self, what: str) -> None:
        self.st.errors += 1
        with (self.out / "errors.log").open("a") as f:
            f.write(f"{self.now().isoformat()} {what}\n")

    def beat(self, status: str, **extra: Any) -> None:
        payload = {
            "job": JOB,
            "date": self.day.isoformat(),
            "status": status,
            "start_ts": self.start_ts.isoformat(),
            "last_tick_ts": self.now().isoformat(),
            "ticks": self.st.ticks,
            "files": self.store.files,
            "errors": self.st.errors,
            "gz_bytes": self.store.bytes_gz,
            "requests": self.fetcher.requests,
            **(self.fresh.heartbeat_fields() if self.fresh is not None else {}),
            **extra,
        }
        write_json_atomic(self.heartbeat, payload)
        write_json_atomic(self.out / "heartbeat.json", payload)

    # ------------------------------------------------------------------ one tick
    def scoreboard(self) -> list[Game] | None:
        resp = self.fetcher.get(f"{ESPN}/scoreboard", {"dates": self.day.strftime("%Y%m%d")})
        if resp is None:
            self.log_error(f"espn scoreboard unreachable: {self.fetcher.last_error}")
            return None
        self.store.save("espn_scoreboard", self.day.isoformat(), resp.body, http_status=resp.status)
        try:
            return parse_scoreboard(resp.body)
        except ValueError as exc:
            self.log_error(f"espn scoreboard parse {exc}")
            return None

    def tick(self) -> dict[str, Any]:
        b = self.budget
        self.fetcher.begin_tick(
            self.fetcher.budget_for(b.tick_deadline_s, b.max_requests_per_tick), b.tick_deadline_s
        )
        info: dict[str, Any] = {"at": self.now().isoformat(), "skipped": {}}
        games = self.scoreboard() or []
        info["games"] = {g.event_id: g.state for g in games}
        now_s = time.monotonic()
        order = {"in": 0, "pre": 1, "post": 2}
        for g in sorted(games, key=lambda x: order.get(x.state, 3)):  # live games first
            if g.state == "post":
                self.st.first_post.setdefault(g.event_id, now_s)
            want = g.state in ("pre", "in") or (
                g.state == "post" and now_s - self.st.first_post[g.event_id] <= FINAL_KEEP_S
            )
            if not want:
                continue
            resp = self.fetcher.get(f"{ESPN}/summary", {"event": g.event_id})
            if resp is None:
                info["skipped"].setdefault("espn_summary", []).append(g.event_id)
                continue
            self.store.save("espn_summary", g.event_id, resp.body, http_status=resp.status)
        self.kalshi_markets(info)
        self.kalshi_orderbooks([g for g in games if g.state == "in"], info)
        self.kalshi_trades(info)
        self.probe_other(info)
        info["requests"] = self.fetcher.tick_requests
        info["avg_latency_s"] = round(self.fetcher.avg_latency, 3)
        return info

    def refresh_series(self) -> None:
        if self.st.series and time.monotonic() - self.st.series_refreshed < 7200:
            return
        series, resp = kp.list_nba_series(self.fetcher)
        self.st.series, self.st.series_refreshed = series, time.monotonic()
        if resp is not None and resp.status == 200:
            self.store.save("kalshi_series", "sports", resp.body, http_status=resp.status)
        if not self.st.slate_series:  # before the first sweep: the known game-level series
            self.st.slate_series = list(kp.DEFAULT_SERIES)
            self.st.other_series = [x for x in series if x not in self.st.slate_series]

    @staticmethod
    def series_rank(series: str) -> tuple[int, str]:
        """Game-level series first, then player props, then the rest of the slate's series."""
        game = ("KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL", "KXNBATEAMTOTAL")
        props = ("KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBAPRA")
        if series in game:
            return 0, f"{game.index(series)}"
        if series in props:
            return 1, f"{props.index(series)}"
        return 2, series

    def capture_series(self, series: str, info: dict[str, Any], label: str = "") -> bool:
        """Fetch and store one series. True if it holds markets dated for the slate. A series with
        none is stored unfiltered under ``kalshi_seasonal`` (sweeps only); None-yield pages of a
        slate series are skipped and named in ``info``."""
        slate = False
        for page, resp, body in kp.iter_series_pages(self.fetcher, series):
            if resp is None:
                info["skipped"].setdefault("kalshi_pages", []).append(f"{series}_p{page}")
                break
            if body is None:
                self.log_error(f"kalshi {series} p{page} http {resp.status}")
                break
            markets = [m for m in body.get("markets", []) if isinstance(m, dict)]
            kept, dropped = kp.filter_for_date(markets, self.day)
            if not kept:
                if label:
                    self.store.save(
                        "kalshi_seasonal",
                        f"{series}_p{page}",
                        resp.body,
                        http_status=resp.status,
                        note={"sweep": label, "n_total": len(markets)},
                    )
                continue
            slate = True
            new_rules = {
                str(m["ticker"]): {f: m.get(f) for f in kp.RULE_FIELDS}
                for m in markets
                if kp.event_date(str(m.get("event_ticker", ""))) == self.day
                and str(m["ticker"]) not in self.st.rules_seen
            }
            if new_rules:
                self.st.rules_seen.update(new_rules)
                self.store.save(
                    "kalshi_rules", series, json.dumps(new_rules).encode(), http_status=200
                )
            self.st.last_markets += kept
            out = {"markets": kept, "cursor": body.get("cursor", "")}
            self.store.save(
                "kalshi",
                f"{series}_p{page}",
                json.dumps(out, sort_keys=True).encode(),
                http_status=resp.status,
                note={
                    "transform": "slate-date filter; rules text saved once in kalshi_rules",
                    "n_total": len(markets),
                    "n_kept": len(kept),
                    "dropped_other_dates": dropped,
                },
            )
        return slate

    def sweep(self, label: str) -> dict[str, Any]:
        """Once-a-day pass over EVERY KXNBA* series (start and end of the window): slate series
        join the per-tick list, season-long and other-date series are stored unfiltered here and
        not polled per tick. Has its own request budget."""
        b = self.budget
        self.fetcher.begin_tick(b.sweep_budget, b.sweep_deadline_s)
        info: dict[str, Any] = {"at": self.now().isoformat(), "sweep": label, "skipped": {}}
        self.st.series_refreshed = 0.0
        self.refresh_series()
        self.st.last_markets = []
        slate: list[str] = []
        other: list[str] = []
        for series in sorted(self.st.series, key=self.series_rank):
            (slate if self.capture_series(series, info, label) else other).append(series)
        if label == "start" or not self.st.slate_series:
            self.st.slate_series = sorted(slate, key=self.series_rank) or list(kp.DEFAULT_SERIES)
            self.st.other_series = other
        info["slate_series"] = len(slate)
        info["other_series"] = len(other)
        info["requests"] = self.fetcher.tick_requests
        with (self.out / "ticks.jsonl").open("a") as f:
            f.write(json.dumps(info, sort_keys=True) + "\n")
        return info

    def kalshi_markets(self, info: dict[str, Any]) -> None:
        self.refresh_series()
        self.st.last_markets = []
        for series in sorted(self.st.slate_series, key=self.series_rank):
            self.capture_series(series, info)
        info["markets_kept"] = len(self.st.last_markets)

    def probe_other(self, info: dict[str, Any]) -> None:
        """Round-robin look at a few non-slate series per tick: if one grows slate-dated markets
        (e.g. first-half or quarter markets that open late) it joins the per-tick list."""
        others = self.st.other_series
        if not others:
            return
        n = min(self.budget.probe_per_tick, len(others))
        picked = [others[(self.st.probe_idx + i) % len(others)] for i in range(n)]
        self.st.probe_idx = (self.st.probe_idx + n) % len(others)
        for series in picked:
            if self.fetcher.exhausted():
                break
            if self.capture_series(series, info):
                self.st.other_series.remove(series)
                self.st.slate_series.append(series)
                info.setdefault("promoted_series", []).append(series)

    def leg_tickers(self) -> set[str]:
        """Open paper-position legs: the legs file plus its siblings (``open_legs_<arm>.json``)."""
        if self.legs_file is None:
            return set()
        out: set[str] = set()
        paths = [
            self.legs_file,
            *sorted(self.legs_file.parent.glob(f"{self.legs_file.stem}_*.json")),
        ]
        for path in paths:
            try:
                data = json.loads(path.read_text())
                out |= {str(t) for t in data.get("tickers", [])}
            except (OSError, ValueError):
                continue
        return out

    def kalshi_orderbooks(self, live: list[Game], info: dict[str, Any]) -> None:
        legs = self.leg_tickers()
        if self.st.orderbook_blocked or not (live or legs):
            return
        targets, over = orderbook_targets(
            self.st.last_markets, live, legs, self.budget.orderbook_depth_limit
        )
        got: dict[str, Any] = {}
        skipped: list[str] = list(over)
        denied = 0
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self.budget.orderbook_workers) as pool:
            results = list(pool.map(lambda t: kp.get_orderbook(self.fetcher, t), targets))
        for t, resp in zip(targets, results, strict=True):
            if resp is None:
                skipped.append(t)  # budget or deadline spent (or a final network failure)
                continue
            if resp.status in (401, 403):
                denied += 1
                continue
            body = kp.parse_body(resp)
            if body is None:
                self.log_error(f"orderbook {t} http {resp.status}")
                continue
            got[t] = {"status": resp.status, "orderbook": body}
        if denied and not got:
            self.st.orderbook_blocked = True
            self.log_error(
                "orderbook endpoint answered 401/403 without credentials; disabled for "
                "this run (no credential will be added)"
            )
        if got:
            self.store.save(
                "kalshi_orderbook",
                self.day.isoformat(),
                json.dumps(got, sort_keys=True).encode(),
                http_status=200,
                note={"n_books": len(got), "n_skipped": len(skipped)},
            )
        if skipped:
            info["skipped"]["orderbook"] = skipped[:50]
            info["skipped"]["orderbook_n"] = len(skipped)

    def trade_targets(self) -> list[str]:
        """Backlog queue: slate markets whose cumulative volume (public ``volume_fp``) rose since
        the last tick (first sight: any volume, largest jump first within a tick). Order: game
        winners, then player props, then the rest; first-in first-out inside a class, so a catch-up
        that was carried over resumes before new work."""
        jumps: list[tuple[float, str]] = []
        for m in self.st.last_markets:
            t = str(m.get("ticker", ""))
            vol = kp.to_f(m.get("volume_fp"))
            if not t or vol is None:
                continue
            prev = self.st.volumes.get(t, 0.0)
            if vol > prev:
                jumps.append((vol - prev, t))
            self.st.volumes[t] = max(vol, prev)
        jumps.sort(key=lambda x: (-x[0], x[1]))
        for _, t in jumps:  # new work joins the back of its class; the oldest entries go first
            self.st.trade_pending.setdefault(t, None)
        queue = list(self.st.trade_pending)
        return sorted(queue, key=lambda t: trade_priority(t))  # stable: FIFO within a class

    # -- trade-state persistence (a restart resumes from the high-water marks) ----------------
    @property
    def trade_state_path(self) -> Path:
        return self.out / "trade_state.json"

    def _save_trade_state(self) -> None:
        st = self.st
        tickers = set(st.trade_last_ts) | set(st.trade_open) | set(st.trade_cursor)
        body = {
            "day": self.day.isoformat(),
            "pending": list(st.trade_pending),
            "tickers": {
                t: {
                    "hwm": st.trade_last_ts.get(t),
                    "hwm_ids": sorted(st.trade_ids.get(t, set())),
                    "open": (
                        st.trade_open.get(t, {})
                        if len(st.trade_open.get(t, {})) <= self.budget.trades_state_open_cap
                        else {}
                    ),
                    "open_max": st.trade_open_max.get(t),
                    "cursor": st.trade_cursor.get(t),
                }
                for t in sorted(tickers)
            },
        }
        text = json.dumps(body, sort_keys=True)
        if text != st.trade_state_written:  # only when changed
            write_json_atomic(self.trade_state_path, body)
            st.trade_state_written = text

    def _load_trade_state(self) -> None:
        path = self.trade_state_path
        if not path.exists():
            return
        try:
            body = json.loads(path.read_text())
            if body.get("day") != self.day.isoformat():
                return
            st = self.st
            for t, d in (body.get("tickers") or {}).items():
                if d.get("hwm") is not None:
                    st.trade_last_ts[t] = int(d["hwm"])
                st.trade_ids[t] = set(d.get("hwm_ids") or [])
                if d.get("open"):
                    st.trade_open[t] = {str(k): v for k, v in d["open"].items()}
                if d.get("open_max") is not None:
                    st.trade_open_max[t] = int(d["open_max"])
                if d.get("cursor"):
                    st.trade_cursor[t] = str(d["cursor"])
            st.trade_pending = {str(t): None for t in body.get("pending") or []}
        except (ValueError, OSError, AttributeError, TypeError):
            self.log_error("trade_state unreadable; starting from first sight")
            self.st.trade_last_ts.clear()
            self.st.trade_ids.clear()
            self.st.trade_open.clear()
            self.st.trade_cursor.clear()
            self.st.trade_open_max.clear()

    # -- one ticker's catch-up --------------------------------------------------------------
    def _take_page(self) -> bool:
        with self._page_lock:
            f = self.fetcher
            floor = f.tick_budget - self.budget.trades_page_reserve  # retries count: live check
            if self._trade_stop or self._pages_left <= 0 or f.tick_requests >= floor:
                return False
            if f.exhausted():
                return False
            self._pages_left -= 1
            return True

    def pull_trades(self, ticker: str) -> TradePull:
        try:
            return self._pull_trades(ticker)
        except Exception as exc:  # one bad ticker must not cost the rest of the tick
            self.log_error(f"trades {ticker} {type(exc).__name__}: {exc}"[:300])
            return TradePull([], False, self.st.trade_cursor.get(ticker))

    def _pull_trades(self, ticker: str) -> TradePull:
        """Newest-first pages from ``min_ts`` = the high-water mark until the cursor ends (the
        server stops at the mark) or this tick's page budget is spent. Complete only when the
        walk reached the mark; otherwise the cursor is returned and the next tick resumes there.
        Deduplicated by trade id against the mark's second and this catch-up's earlier pages."""
        st = self.st
        hwm = st.trade_last_ts.get(ticker)
        seen = set(st.trade_ids.get(ticker, set())) | set(st.trade_open.get(ticker, {}))
        cursor = st.trade_cursor.get(ticker)
        resumed = cursor is not None
        mark_ids = st.trade_ids.get(ticker, set())
        reached = hwm is None  # evidence the walk got down to the mark
        out: list[dict[str, Any]] = []
        pages = 0
        limit = self.budget.trades_page_limit
        while True:
            if pages >= self.budget.trades_pages_per_ticker or not self._take_page():
                return TradePull(out, False, cursor, pages)
            resp, body = kp.fetch_trades_page(self.fetcher, ticker, hwm, cursor, limit)
            pages += 1
            if body is None:
                status = resp.status if resp is not None else None
                if status is not None:
                    self.log_error(f"trades {ticker} p{pages - 1} http {status}")
                if resumed and status in (400, 404):  # stale cursor: restart from the newest page
                    cursor, resumed = None, False
                    continue
                limited = status == 429
                if limited:
                    self._trade_stop = True
                return TradePull(out, False, cursor, pages, limited)
            was_resumed, resumed = resumed, False
            page = [tr for tr in body.get("trades") or [] if isinstance(tr, dict)]
            if was_resumed and not page and not body.get("cursor"):
                cursor = None  # an expired cursor can answer 200 empty: restart from the newest
                self.log_error(f"trades {ticker} resumed cursor returned an empty end; restarting")
                continue
            if any(str(tr.get("trade_id", "")) in mark_ids for tr in page):
                reached = True  # min_ts is inclusive: the mark second's prints come back
            for tr in page:
                tid = str(tr.get("trade_id", ""))
                if tid not in seen:
                    seen.add(tid)
                    out.append(tr)
            nxt = str(body.get("cursor") or "")
            stamps = [ts for tr in page if (ts := kp.trade_ts(tr)) is not None]
            if hwm is not None and stamps and min(stamps) < hwm:
                reached = True
                nxt = ""  # the page is below the mark: caught up
            if not nxt:
                if reached or st.trade_unproven.get(ticker, 0) >= 1:
                    st.trade_unproven.pop(ticker, None)
                    return TradePull(out, True, None, pages)
                st.trade_unproven[ticker] = st.trade_unproven.get(ticker, 0) + 1
                self.log_error(f"trades {ticker} walk ended without reaching the mark; kept open")
                return TradePull(out, False, None, pages)
            cursor = nxt

    def _absorb_trades(self, t: str, pull: TradePull) -> None:
        """Fold one ticker's pull into the catch-up state; move the mark only when complete."""
        st = self.st
        open_ids = st.trade_open.setdefault(t, {}) if pull.trades else st.trade_open.get(t, {})
        for tr in pull.trades:
            ts = kp.trade_ts(tr)
            open_ids[str(tr.get("trade_id", ""))] = ts
            if ts is not None:
                st.trade_open_max[t] = max(st.trade_open_max.get(t, 0), ts)
        if not pull.complete:
            if pull.cursor:
                st.trade_cursor[t] = pull.cursor
            else:
                st.trade_cursor.pop(t, None)  # no valid cursor: the next pull restarts at newest
            return
        top = st.trade_open_max.get(t)
        if top is not None:
            prev = st.trade_last_ts.get(t, 0)
            at_top = {i for i, ts in open_ids.items() if ts == top}
            st.trade_ids[t] = (st.trade_ids.get(t, set()) | at_top) if top == prev else at_top
            st.trade_last_ts[t] = max(top, prev)
        st.trade_open.pop(t, None)
        st.trade_cursor.pop(t, None)
        st.trade_open_max.pop(t, None)

    def kalshi_trades(self, info: dict[str, Any]) -> None:
        """Public trades for every slate market that traded since the last tick, after the books.
        Complete catch-up per ticker, bounded by a per-tick page budget with carry-over. One
        write-once raw file per tick; counts and skips go to ``ticks.jsonl``."""
        st = self.st
        backlog_before = len(st.trade_pending)
        targets = self.trade_targets()
        cap = self.budget.trades_max_tickers
        skipped = list(targets[cap:])  # stay queued; pulled on the next ticks
        targets = targets[:cap]
        got: dict[str, list[dict[str, Any]]] = {}
        incomplete: list[str] = []
        pages = 0
        limited = False
        if st.trade_skip_ticks > 0:  # backing off after repeated 429s: carry everything over
            st.trade_skip_ticks -= 1
            incomplete, targets = list(targets), []
            results: list[TradePull] = []
        else:
            fetch = self.fetcher
            room = max(0, fetch.tick_budget - fetch.tick_requests - self.budget.trades_page_reserve)
            self._pages_left = min(self.budget.trades_page_budget, room)
            self._trade_stop = False
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=self.budget.orderbook_workers) as pool:
                results = list(pool.map(self.pull_trades, targets))
        got = {t: p.trades for t, p in zip(targets, results, strict=True) if p.trades}
        n = sum(len(v) for v in got.values())
        saved = True
        if got:  # raw first: state moves only after the prints are on disk
            try:
                self.store.save(
                    "kalshi_trades",
                    self.day.isoformat(),
                    json.dumps(got, sort_keys=True).encode(),
                    http_status=200,
                    note={"n_trades": n, "n_tickers": len(got)},
                )
            except OSError as exc:
                saved = False
                self.log_error(f"trades raw write failed, nothing absorbed: {exc}"[:300])
        cleared = 0
        for t, pull in zip(targets, results, strict=True):
            pages += pull.pages
            limited = limited or pull.rate_limited
            if not saved:  # re-read next tick from the old cursor / mark
                incomplete.append(t)
                continue
            carried = t in st.trade_open  # catch-up spanned ticks: newer prints may have arrived
            self._absorb_trades(t, pull)  # so one verify pass follows
            if pull.complete and not carried:
                st.trade_pending.pop(t, None)
                cleared += 1
            elif not pull.complete:
                incomplete.append(t)
        if limited:
            st.trade_429_streak += 1
            st.trade_skip_ticks = min(2 ** (st.trade_429_streak - 1) - 1, 3)
        elif pages > 0:
            st.trade_429_streak = 0
        if not saved:
            n = 0
            got = {}
        self._save_trade_state()
        info["trades"] = {
            "n": n,
            "tickers": len(got),
            "targets": len(targets),
            "backlog": len(st.trade_pending),
        }
        if pages:
            info["trades"]["pages"] = pages
            info["trades"]["cleared"] = cleared  # tickers fully caught up this tick
        if st.trade_cursor:
            info["trades"]["carried_cursors"] = len(st.trade_cursor)
        if limited:
            info["trades"]["rate_limited"] = True
        if backlog_before > 0 and not st.trade_pending:
            info["trades"]["backlog_cleared"] = True
        if skipped:
            info["skipped"]["trades"] = skipped[:50]
            info["skipped"]["trades_n"] = len(skipped)
        if incomplete:
            info["skipped"]["trades_incomplete"] = incomplete[:50]
            info["skipped"]["trades_incomplete_n"] = len(incomplete)

    def drain_trades(self) -> dict[str, Any]:
        """After the main loop: pull the queued trade backlog under the sweep budget (books and
        markets no longer compete), until empty, out of budget, or no progress. One line per round
        goes to ``ticks.jsonl`` with ``drain: true``; the remainder is reported, never hidden."""
        b = self.budget
        st = self.st
        start = len(st.trade_pending)
        self.fetcher.begin_tick(b.sweep_budget, b.sweep_deadline_s)
        rounds = 0
        while st.trade_pending and rounds < b.drain_max_rounds and not self.fetcher.exhausted():
            rounds += 1
            st.trade_skip_ticks = 0  # no tick clock here: a 429 sleeps instead of skipping ticks
            info: dict[str, Any] = {"at": self.now().isoformat(), "drain": True, "skipped": {}}
            self.kalshi_trades(info)
            with (self.out / "ticks.jsonl").open("a") as f:
                f.write(json.dumps(info, sort_keys=True) + "\n")
            tr = info["trades"]
            if tr.get("rate_limited"):
                self.sleep(15.0 * min(st.trade_429_streak, 4))
                st.trade_skip_ticks = 0
            elif not tr.get("pages"):
                break
        summary = {
            "drain_start": start,
            "drain_rounds": rounds,
            "drain_remaining": len(st.trade_pending),
        }
        with (self.out / "ticks.jsonl").open("a") as f:
            f.write(json.dumps({"at": self.now().isoformat(), **summary}, sort_keys=True) + "\n")
        return summary

    # ------------------------------------------------------------------ schedule + loop
    def schedule(self) -> list[Game] | None:
        self.fetcher.begin_tick(50, 40.0)
        return self.scoreboard()

    def run(self, *, once: bool = False, start_now: bool = False) -> int:
        """0 on a normal end (including no games); 2 when the schedule never became readable."""
        games: list[Game] | None = None
        for _ in range(12):
            games = self.schedule()
            if games is not None:
                break
            self.beat("schedule_unreachable")
            self.sleep(300)
        if games is None:
            self.beat("failed", rc=2, end_ts=self.now().isoformat())
            return 2
        win = window(games, self.budget)
        if win is None:
            self.beat("no_games", rc=0, end_ts=self.now().isoformat())
            return 0
        start, end = win
        self.beat("waiting", window_start=start.isoformat(), window_end=end.isoformat())
        while not (start_now or once) and self.now() < start:
            self.sleep(min(60.0, max(1.0, (start - self.now()).total_seconds())))
            self.beat("waiting", window_start=start.isoformat(), window_end=end.isoformat())
        if not self.st.swept_start:
            self.st.swept_start = True
            try:
                self.sweep("start")
            except Exception:
                self.log_error("sweep start " + traceback.format_exc().replace("\n", " | ")[:1500])
        if self.fresh is not None and not once:
            self.fresh.start()
        while self.now() < end or once:
            t0 = time.monotonic()
            info: dict[str, Any] = {}
            try:
                info = self.tick()
            except Exception:
                self.log_error("tick " + traceback.format_exc().replace("\n", " | ")[:1500])
            self.st.ticks += 1
            with (self.out / "ticks.jsonl").open("a") as f:
                f.write(json.dumps(info, sort_keys=True) + "\n")
            self.beat("running", window_start=start.isoformat(), window_end=end.isoformat())
            if once:
                break
            self.sleep(max(1.0, self.budget.interval_s - (time.monotonic() - t0)))
        if self.fresh is not None:
            self.fresh.close()
        if not once:
            try:
                self.drain_trades()
            except Exception:
                self.log_error("drain trades " + traceback.format_exc().replace("\n", " | ")[:1500])
            try:
                self.sweep("end")
            except Exception:
                self.log_error("sweep end " + traceback.format_exc().replace("\n", " | ")[:1500])
        self.beat("done", rc=0, end_ts=self.now().isoformat())
        return 0
