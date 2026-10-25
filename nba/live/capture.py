"""Game-night capture loop: ESPN scoreboard and summaries, Kalshi markets and order books.

Window: 2 h before the first tip to 4 h after the last tip of the slate date (ET), one tick every
``interval_s`` seconds. Every request has a 10 s timeout and bounded retries; a tick that raises is
logged and the loop goes on. Raw bodies are stored write-once (``nba.live.store``).
Public data only.
"""

from __future__ import annotations

import json
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
    trades_max_pages: int = 5


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
    trade_pending: dict[str, None] = field(default_factory=dict)  # FIFO backlog of tickers to pull


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
    ) -> None:
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
        the last tick (first sight: any volume, largest jump first within a tick), oldest first."""
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
        for _, t in jumps:  # new work joins the back of the queue; the oldest entries go first
            self.st.trade_pending.setdefault(t, None)
        return list(self.st.trade_pending)

    def pull_trades(self, ticker: str) -> tuple[list[dict[str, Any]], bool]:
        try:
            return self._pull_trades(ticker)
        except Exception as exc:  # one bad ticker must not cost the rest of the tick
            self.log_error(f"trades {ticker} {type(exc).__name__}: {exc}"[:300])
            return [], False

    def _pull_trades(self, ticker: str) -> tuple[list[dict[str, Any]], bool]:
        """New public trades for one ticker (min_ts = last seen, deduplicated by trade id).
        Returns (trades, complete); incomplete when a page was skipped for budget or error."""
        min_ts = self.st.trade_last_ts.get(ticker)
        seen_ids = self.st.trade_ids.get(ticker, set())
        out: list[dict[str, Any]] = []
        complete = True
        for page, resp, body in kp.iter_trade_pages(
            self.fetcher, ticker, min_ts, max_pages=self.budget.trades_max_pages
        ):
            if body is None:
                complete = False
                if resp is not None:
                    self.log_error(f"trades {ticker} p{page} http {resp.status}")
                break
            for tr in body.get("trades") or []:
                if isinstance(tr, dict) and str(tr.get("trade_id", "")) not in seen_ids:
                    out.append(tr)
            if page == self.budget.trades_max_pages - 1 and body.get("cursor"):
                complete = False  # more pages than the cap
        return out, complete

    def kalshi_trades(self, info: dict[str, Any]) -> None:
        """Public trades for every slate market that traded since the last tick, after the books.
        One write-once raw file per tick; counts and skips go to ``ticks.jsonl``."""
        backlog_before = len(self.st.trade_pending)
        targets = self.trade_targets()
        cap = self.budget.trades_max_tickers
        skipped = list(targets[cap:])  # stay queued; pulled on the next ticks, oldest first
        targets = targets[:cap]
        got: dict[str, list[dict[str, Any]]] = {}
        incomplete: list[str] = []
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=self.budget.orderbook_workers) as pool:
            results = list(pool.map(self.pull_trades, targets))
        for t, (trades, complete) in zip(targets, results, strict=True):
            if not complete:
                incomplete.append(t)
            else:
                self.st.trade_pending.pop(t, None)
            if trades:
                got[t] = trades
                stamps = [ts for tr in trades if (ts := kp.trade_ts(tr)) is not None]
                if stamps:
                    top = max(stamps)
                    prev = self.st.trade_last_ts.get(t, 0)
                    ids = self.st.trade_ids.get(t, set()) if top == prev else set()
                    ids |= {str(tr.get("trade_id", "")) for tr in trades if kp.trade_ts(tr) == top}
                    self.st.trade_last_ts[t] = max(top, prev)
                    self.st.trade_ids[t] = ids
        n = sum(len(v) for v in got.values())
        if got:
            self.store.save(
                "kalshi_trades",
                self.day.isoformat(),
                json.dumps(got, sort_keys=True).encode(),
                http_status=200,
                note={"n_trades": n, "n_tickers": len(got), "n_incomplete": len(incomplete)},
            )
        info["trades"] = {
            "n": n,
            "tickers": len(got),
            "targets": len(targets),
            "backlog": len(self.st.trade_pending),
        }
        if backlog_before > 0 and not self.st.trade_pending:
            info["trades"]["backlog_cleared"] = True
        if skipped:
            info["skipped"]["trades"] = skipped[:50]
            info["skipped"]["trades_n"] = len(skipped)
        if incomplete:
            info["skipped"]["trades_incomplete"] = incomplete[:50]
            info["skipped"]["trades_incomplete_n"] = len(incomplete)

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
        if not once:
            try:
                self.sweep("end")
            except Exception:
                self.log_error("sweep end " + traceback.format_exc().replace("\n", " | ")[:1500])
        self.beat("done", rc=0, end_ts=self.now().isoformat())
        return 0
