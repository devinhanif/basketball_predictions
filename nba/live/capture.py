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
    max_requests_per_tick: int = 300
    tick_deadline_s: float = 50.0
    rate_per_s: float = 8.0
    lead_h: float = 2.0
    tail_h: float = 4.0
    interval_s: float = 60.0
    markets_every_after_empty: int = (
        5  # poll Kalshi every N ticks once no open dated market is left
    )
    orderbook_depth_limit: int = 200


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
    undated: set[str] = field(default_factory=set)
    rules_seen: set[str] = field(default_factory=set)
    orderbook_blocked: bool = False
    empty_ticks: int = 0
    last_markets: list[dict[str, Any]] = field(default_factory=list)
    series_refreshed: float = 0.0


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
        self.fetcher.begin_tick(b.max_requests_per_tick, b.tick_deadline_s)
        info: dict[str, Any] = {"at": self.now().isoformat(), "skipped": {}}
        games = self.scoreboard() or []
        info["games"] = {g.event_id: g.state for g in games}
        now_s = time.monotonic()
        for g in games:
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
        if self.st.ticks % b.markets_every_after_empty == 0 or self.st.empty_ticks == 0:
            self.kalshi_markets(info)
        self.kalshi_orderbooks([g for g in games if g.state == "in"], info)
        info["requests"] = self.fetcher.tick_requests
        return info

    def refresh_series(self) -> None:
        if self.st.series and time.monotonic() - self.st.series_refreshed < 7200:
            return
        series, resp = kp.list_nba_series(self.fetcher)
        self.st.series, self.st.series_refreshed = series, time.monotonic()
        if resp is not None and resp.status == 200:
            self.store.save("kalshi_series", "sports", resp.body, http_status=resp.status)

    def kalshi_markets(self, info: dict[str, Any]) -> None:
        self.refresh_series()
        all_kept: list[dict[str, Any]] = []
        for series in self.st.series:
            if series in self.st.undated:
                continue
            for page, resp, body in kp.iter_series_pages(self.fetcher, series):
                if resp is None:
                    info["skipped"].setdefault("kalshi_pages", []).append(f"{series}_p{page}")
                    break
                if body is None:
                    self.log_error(f"kalshi {series} p{page} http {resp.status}")
                    break
                markets = [m for m in body.get("markets", []) if isinstance(m, dict)]
                kept, dropped = kp.filter_for_date(markets, self.day)
                if (
                    page == 0
                    and markets
                    and not any(kp.event_date(str(m.get("event_ticker", ""))) for m in markets)
                ):
                    self.st.undated.add(series)  # futures-style series: nothing to capture by date
                    break
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
                all_kept += kept
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
        self.st.last_markets = all_kept
        self.st.empty_ticks = 0 if all_kept else self.st.empty_ticks + 1
        info["markets_kept"] = len(all_kept)

    def leg_tickers(self) -> set[str]:
        if self.legs_file is None or not self.legs_file.exists():
            return set()
        try:
            data = json.loads(self.legs_file.read_text())
            return {str(t) for t in data.get("tickers", [])}
        except (OSError, ValueError):
            return set()

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
        for i, t in enumerate(targets):
            resp = kp.get_orderbook(self.fetcher, t)
            if resp is None:
                skipped += targets[i:]  # budget or deadline spent (or a final network failure)
                break
            if resp.status in (401, 403):
                denied += 1
                if denied >= 2 and not got:
                    self.st.orderbook_blocked = True
                    self.log_error(
                        "orderbook endpoint answered 401/403 without credentials; disabled for "
                        "this run (no credential will be added)"
                    )
                    break
            body = kp.parse_body(resp)
            if body is None:
                self.log_error(f"orderbook {t} http {resp.status}")
                continue
            got[t] = {"status": resp.status, "orderbook": body}
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
        self.beat("done", rc=0, end_ts=self.now().isoformat())
        return 0
