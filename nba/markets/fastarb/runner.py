"""The poll loop and the single tick. Read-only: every request is a public GET.

One tick = Kalshi game/spread/total markets (3 series), then per active game the Polymarket US event
metadata (cached ``meta_ttl_s``), the order books of the lines both venues quote, optionally the
international gamma event and Pinnacle core lines; then the arbitrage and lag checks and the alerts.
Nothing here can place an order: the only HTTP verb in the package is GET (a test pins that).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher, Response
from nba.markets.fastarb import news as newsmod
from nba.markets.fastarb import pinnacle as pin
from nba.markets.fastarb import sinks, venues
from nba.markets.fastarb.arb import (
    ArbResult,
    LagResult,
    fee_functions,
    find_arbs,
    find_lags,
    is_alertable,
)
from nba.markets.fastarb.config import FastArbConfig
from nba.markets.fastarb.props import GameKey, PropKey, QuoteBook, team_id
from nba.markets.fastarb.venues import PM_INTL, PM_US, K

ET = ZoneInfo("America/New_York")
USER_AGENT = "nba-prediction-fastarb/1 (public market data, read-only)"


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json(resp: Response | None) -> Any | None:
    if resp is None or resp.status != 200:
        return None
    try:
        return json.loads(resp.body)
    except ValueError:
        return None


@dataclass
class GameCtx:
    key: GameKey
    tip: datetime | None = None
    tip_source: str = ""
    us_event: dict[str, Any] | None = None
    us_meta_at: float = float("-inf")
    pin_event_id: str | None = None


@dataclass
class GameStat:
    label: str
    tip: datetime | None
    n_props_both_venues: int = 0
    best: ArbResult | None = None  # highest net margin seen, any pair
    best_intl: ArbResult | None = None
    gross_best: float = float("-inf")
    pinnacle: str = "off"
    lines_booked: int = 0


@dataclass
class TickReport:
    now: datetime
    requests: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    games: dict[str, GameStat] = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    arbs_alertable: int = 0
    arbs_thin: int = 0
    lags: int = 0
    news: int = 0
    credits: int = 0
    errors: list[str] = field(default_factory=list)


class Runner:
    def __init__(
        self,
        cfg: FastArbConfig,
        fetcher: Fetcher,
        *,
        raw: sinks.RawWriter | None = None,
        sink: sinks.AlertSink | None = None,
        pinnacle_client: pin.FastPinnacleClient | None = None,
        use_intl: bool = False,
        clock: Callable[[], datetime] = now_utc,
        mono: Callable[[], float] = time.monotonic,
        tips: Callable[[date], dict[tuple[int, int], datetime]] | None = None,
    ) -> None:
        self.cfg, self.f, self.raw, self.sink = cfg, fetcher, raw, sink
        self.pc, self.use_intl, self.clock, self.mono = pinnacle_client, use_intl, clock, mono
        self.fees = fee_functions(cfg)
        self.games: dict[GameKey, GameCtx] = {}
        self.news_events: list[newsmod.NewsEvent] = []
        self.injury_known: set[str] = set()
        self._tips = tips
        self._tips_at = float("-inf")
        self._tips_cache: dict[tuple[int, int], datetime] = {}
        self.resolver: Any = None
        self.pin_events_at = float("-inf")
        self.pin_event_ids: dict[GameKey, str] = {}
        self.news_unavailable = 0
        self.news_baselined = False

    # ------------------------------------------------------------------ config shortcuts
    @property
    def poll(self) -> dict[str, Any]:
        return self.cfg.section("poll")

    def _save(self, now: datetime, venue: str, label: str, payload: Any) -> None:
        if self.raw is not None:
            self.raw.write(now, venue, label, payload)

    # ------------------------------------------------------------------ Kalshi
    def kalshi_markets(self, now: datetime, day: date, rep: TickReport) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for series in self.cfg.section("kalshi")["series"]:
            pages: list[dict[str, Any]] = []
            for _n, _resp, body in kp.iter_series_pages(self.f, series):
                if body is None:
                    rep.skipped[f"{K}:{series}:fetch_failed"] += 1
                    break
                kept, _dropped = kp.filter_for_date(body.get("markets") or [], day)
                pages.append({"markets": kept})
                out.extend(kept)
            self._save(now, K, series, pages)
        return out

    # ------------------------------------------------------------------ games and tips
    def _slate_tips(self, day: date) -> dict[tuple[int, int], datetime]:
        if self._tips is not None:
            return self._tips(day)
        from nba.odds.live_close import read_slate

        db = self.cfg.resolve(str(self.cfg.section("news")["lineups_db"]))
        return {(g.away_team, g.home_team): g.tipoff for g in read_slate(db, day) if g.home_team}

    def discover(self, kalshi: list[dict[str, Any]], day: date) -> None:
        if self.mono() - self._tips_at > 600:
            self._tips_cache, self._tips_at = self._slate_tips(day), self.mono()
        for m in kalshi:
            if not str(m.get("ticker", "")).startswith("KXNBAGAME"):
                continue
            ev = str(m.get("event_ticker", ""))
            teams, d = kp.event_teams(ev), kp.event_date(ev)
            if teams is None or d is None:
                continue
            a, h = team_id(teams[0]), team_id(teams[1])
            if a is None or h is None:
                continue
            key = GameKey(a, h, d.isoformat())
            ctx = self.games.setdefault(key, GameCtx(key))
            tip = self._tips_cache.get((a, h))
            if tip is not None:
                ctx.tip, ctx.tip_source = tip, "lineups.game_tips"

    def _us_meta(self, ctx: GameCtx, now: datetime, rep: TickReport) -> None:
        if ctx.us_event is not None and self.mono() - ctx.us_meta_at < float(
            self.poll["meta_ttl_s"]
        ):
            return
        base = self.cfg.section("polymarket_us")["base_url"]
        slug = venues.slug_for(ctx.key)
        resp = self.f.get(f"{base}/v1/events/slug/{slug}")
        body = _json(resp)
        if not isinstance(body, dict) or not isinstance(body.get("event"), dict):
            rep.skipped[f"{PM_US}:event_missing:{slug}"] += 1
            return
        ev = venues.us_filter_event(body["event"])
        ctx.us_event, ctx.us_meta_at = ev, self.mono()
        self._save(now, PM_US, f"{slug}_event", ev)
        if ctx.tip is None and ev.get("startTime"):
            ctx.tip = datetime.fromisoformat(str(ev["startTime"]).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
            ctx.tip_source = "polymarket_us.startTime"

    # ------------------------------------------------------------------ one game
    def _game_books(
        self, ctx: GameCtx, kbook: QuoteBook, now: datetime, rep: TickReport, stat: GameStat
    ) -> QuoteBook:
        full = QuoteBook()
        full.merge(kbook)
        if ctx.us_event is not None:
            fc = self.cfg.us_fee.coefficient
            meta = venues.us_quotes(ctx.us_event, fc)
            band = self.cfg.section("alert")["price_band"]
            cand: dict[str, float] = {}  # market slug -> distance of Kalshi YES ask from 0.5
            for prop, per in meta.quotes.items():
                k = kbook.quotes.get(prop, {}).get(K)
                if not k or "yes" not in k:
                    continue
                if not band[0] <= k["yes"].price <= band[1]:
                    continue
                for sides in per.get(PM_US, {}).values():
                    cand[sides.ref] = min(cand.get(sides.ref, 1.0), abs(k["yes"].price - 0.5))
            # the most balanced lines first: that is where depth is and where the cap bites last
            slugs = sorted(cand, key=lambda s: cand[s])[: int(self.poll["max_books_per_game"])]
            base = self.cfg.section("polymarket_us")["base_url"]
            books: dict[str, dict[str, Any]] = {}
            for s in slugs:
                b = _json(self.f.get(f"{base}/v1/markets/{s}/book"))
                if isinstance(b, dict) and (b.get("marketData") or {}).get("marketSlug") == s:
                    books[s] = b
                else:
                    rep.skipped[f"{PM_US}:book_failed"] += 1
            stat.lines_booked = len(books)
            self._save(now, PM_US, f"{venues.slug_for(ctx.key)}_books", books)
            usb = venues.us_quotes(ctx.us_event, fc, books)
            full.merge(usb)
        if self.use_intl:
            g = self.cfg.section("polymarket_intl")["gamma_url"]
            body = _json(self.f.get(f"{g}/events", {"slug": venues.slug_for(ctx.key)}))
            if isinstance(body, list) and body:
                ev = body[0]
                keep = [
                    m
                    for m in ev.get("markets") or []
                    if m.get("sportsMarketType") in ("moneyline", "spreads", "totals")
                ]
                filt = {"slug": ev.get("slug"), "teams": ev.get("teams"), "markets": keep}
                self._save(now, PM_INTL, f"{venues.slug_for(ctx.key)}_event", filt)
                full.merge(venues.intl_quotes(filt))
            else:
                rep.skipped[f"{PM_INTL}:event_missing"] += 1
        return full

    def _pinnacle(
        self, ctx: GameCtx, now: datetime, rep: TickReport, stat: GameStat
    ) -> tuple[dict[PropKey, float], dict[PropKey, str]]:
        empty: tuple[dict[PropKey, float], dict[PropKey, str]] = ({}, {})
        if self.pc is None:
            return empty
        eid = self.pin_event_ids.get(ctx.key)
        if eid is None:
            stat.pinnacle = "no_event"
            return empty
        body, cost = self.pc.core_odds(eid, self.cfg.raw)
        rep.credits += cost
        self._save(now, "pinnacle", f"{venues.slug_for(ctx.key)}_core", body)
        probs, upd, skipped = pin.pinnacle_probs(body, ctx.key, self.resolver)
        for k, v in skipped.items():
            rep.skipped[f"pinnacle:{k}"] += v
        stat.pinnacle = f"{len(probs)} props"
        return probs, upd

    def _match_pinnacle_events(self, games: list[GameCtx]) -> None:
        if self.pc is None or self.mono() - self.pin_events_at < 600:
            return
        from nba.odds.theoddsapi import NameResolver

        self.resolver = self.resolver or NameResolver.default()
        tips = [(g.key, g.tip) for g in games if g.tip is not None]
        if not tips:
            return
        lo, hi = (
            min(t for _, t in tips) - timedelta(minutes=30),
            max(t for _, t in tips) + timedelta(minutes=30),
        )
        body, _ = self.pc.live_events(lo, hi)
        self.pin_event_ids, _unm = pin.match_events(body, tips, self.resolver, 30.0)
        self.pin_events_at = self.mono()

    # ------------------------------------------------------------------ news
    def refresh_news(self, now: datetime, rep: TickReport) -> None:
        n = self.cfg.section("news")
        lineups = self.cfg.resolve(str(n["lineups_db"]))
        injury = self.cfg.resolve(str(n["injury_dir"]))
        if not self.news_baselined:
            self.injury_known = newsmod.injury_baseline(injury)
            self.news_baselined = True
        else:
            self.news_events += newsmod.injury_events(injury, self.injury_known)
        ev = newsmod.lineup_events(lineups, now - timedelta(hours=6))
        if ev is None:
            self.news_unavailable += 1
            rep.skipped["news:lineups_unavailable"] += 1
        else:
            seen = {(e.ts, e.kind, e.detail) for e in self.news_events}
            self.news_events += [e for e in ev if (e.ts, e.kind, e.detail) not in seen]
        self.news_events = [e for e in self.news_events if now - e.ts < timedelta(hours=2)]

    # ------------------------------------------------------------------ the tick
    def tick(self, day: date, *, force_active: bool = False) -> TickReport:
        now = self.clock()
        rep = TickReport(now)
        req0 = self.f.requests
        self.f.begin_tick(1500, float(self.poll["tick_deadline_s"]))
        kal = self.kalshi_markets(now, day, rep)
        self.discover(kal, day)
        kb = venues.kalshi_quotes(kal)
        rep.skipped.update(kb.skipped)
        lead = timedelta(minutes=float(self.poll["lead_min"]))
        for ctx in self.games.values():
            if ctx.tip is None:
                self._us_meta(ctx, now, rep)
        active = [
            c
            for c in self.games.values()
            if c.tip is not None and (force_active or c.tip - lead <= now < c.tip)
        ]
        self.refresh_news(now, rep)
        self._match_pinnacle_events(active)
        alert_cfg = self.cfg.section("alert")
        window = float(alert_cfg["lag_window_min"])
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for ctx in sorted(active, key=lambda c: c.tip or now):
            stat = GameStat(ctx.key.label(), ctx.tip)
            rep.games[ctx.key.label()] = stat
            self._us_meta(ctx, now, rep)
            one = QuoteBook()
            one.quotes = {p: v for p, v in kb.quotes.items() if p.game == ctx.key}
            book = self._game_books(ctx, one, now, rep, stat)
            rep.skipped.update(book.skipped)
            stat.n_props_both_venues = sum(
                1 for per in book.quotes.values() if K in per and (PM_US in per or PM_INTL in per)
            )
            for pm, tradable in ((PM_US, True), (PM_INTL, False)):
                if pm == PM_INTL and not self.use_intl:
                    continue
                arbs = find_arbs(book, self.cfg, self.fees, (K, pm))
                for r in arbs:
                    stat.gross_best = max(stat.gross_best, r.gross_gap)
                if arbs:
                    if tradable and (stat.best is None or arbs[0].margin > stat.best.margin):
                        stat.best = arbs[0]
                    if not tradable and (
                        stat.best_intl is None or arbs[0].margin > stat.best_intl.margin
                    ):
                        stat.best_intl = arbs[0]
                for r in arbs:
                    verdict = is_alertable(r, self.cfg)
                    if verdict == "thin":
                        rep.arbs_thin += 1
                    if verdict != "alert":
                        continue
                    if tradable:
                        rep.arbs_alertable += 1
                    text = sinks.arb_text(r)
                    rep.alerts.append(text)
                    if self.sink is not None and tradable:
                        self.sink.emit(sinks.arb_key(r), text, now, stamp)
            # lag vs Pinnacle, only inside the news window
            probs, upd = self._pinnacle(ctx, now, rep, stat)
            recent = [
                e
                for e in newsmod.recent(self.news_events, now, window)
                if (e.home is None or e.home == ctx.key.home)
                and (e.away is None or e.away == ctx.key.away)
            ]
            rep.news += len(recent)
            if probs and recent:
                news_ev = max(recent, key=lambda e: e.ts)
                for lag in find_lags(book, probs, self.cfg, self.fees, (K, PM_US), upd):
                    if not self._pinnacle_fresh(lag, news_ev):
                        rep.skipped["lag:pinnacle_older_than_news"] += 1
                        continue
                    rep.lags += 1
                    mins = (now - news_ev.ts).total_seconds() / 60
                    text = sinks.lag_text(lag, f"{news_ev.kind} {news_ev.detail}", mins)
                    rep.alerts.append(text)
                    if self.sink is not None:
                        self.sink.emit(sinks.lag_key(lag, news_ev.detail), text, now, stamp)
        rep.requests = self.f.requests - req0
        return rep

    @staticmethod
    def _pinnacle_fresh(lag: LagResult, ev: newsmod.NewsEvent) -> bool:
        """Pinnacle must have updated at or after the news, or its price is not evidence of lag."""
        t = pin.parse_update(lag.pinnacle_updated)
        return t is not None and t >= ev.ts

    # ------------------------------------------------------------------ the loop
    def run(self, day: date, sleep: Callable[[float], None] = time.sleep) -> int:
        interval = float(self.poll["interval_s"])
        ticks = errors = 0
        hb = self.cfg.path("heartbeat")
        start = self.clock().isoformat()
        while True:
            now = self.clock()
            tips = [c.tip for c in self.games.values() if c.tip is not None]
            lead = timedelta(minutes=float(self.poll["lead_min"]))
            if self.games and tips and now >= max(tips):
                break
            t0 = self.mono()
            try:
                rep = self.tick(day)
                ticks += 1
                detail = f"ok requests={rep.requests} alerts={len(rep.alerts)}"
            except Exception as exc:  # a bad tick must not end the night
                errors += 1
                detail = f"{type(exc).__name__}: {str(exc)[:120]}"
            if ticks + errors >= 1 and not self.games:
                sinks.beat(hb, {"start_ts": start, "status": "no_games", "detail": str(day)})
                return 0
            first = min(tips) - lead if tips else now
            sinks.beat(
                hb,
                {
                    "start_ts": start,
                    "last_tick_ts": self.clock().isoformat(),
                    "ticks": ticks,
                    "errors": errors,
                    "status": "waiting" if now < first else "running",
                    "detail": detail,
                    "raw_files": self.raw.files if self.raw else 0,
                },
            )
            wait = interval - (self.mono() - t0)
            if now < first and tips:
                wait = max(wait, min((first - now).total_seconds() - 30, 600))
            sleep(max(wait, 1.0))
        return 0
