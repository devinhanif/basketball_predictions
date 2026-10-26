"""The poll loop and the single tick. Read-only: every request is a public GET.

One tick = Kalshi game/spread/total markets (3 series), then per active game the Polymarket
US event metadata (cached ``meta_ttl_s``, used to SELECT candidate lines only), the order books
of the lines both venues quote, optionally the international gamma event and Pinnacle core
lines; then the arbitrage and lag checks. An arbitrage candidate is emitted only after both
legs are re-fetched back to back and the gap still clears (``_confirm``); a candidate whose
Polymarket leg has no order book behind it is counted as ``unknown_depth`` and never emitted.
Nothing here can place an order: the only HTTP verb in the package is GET (a test pins that).
"""

from __future__ import annotations

import json
import socket
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from nba.live import kalshi_pull as kp
from nba.live.http import Fetcher, Response
from nba.markets.fastarb import news as newsmod
from nba.markets.fastarb import pinnacle as pin
from nba.markets.fastarb import sinks, venues
from nba.markets.fastarb.arb import (
    ArbResult,
    LagResult,
    evaluate_pair,
    fee_functions,
    find_arbs,
    find_lags,
    is_alertable,
)
from nba.markets.fastarb.config import FastArbConfig
from nba.markets.fastarb.ladder import (
    PM_RULES_UNVERIFIED,
    LadderResult,
    LadderScanner,
    find_ladder_violations,
    is_ladder_alertable,
    kalshi_ladder_quotes,
)
from nba.markets.fastarb.polymarket_us import PolymarketUSClient
from nba.markets.fastarb.props import GameKey, PropKey, QuoteBook, team_id
from nba.markets.fastarb.venues import PM_INTL, PM_US, K
from nba.odds.the_odds_api import AuthError, CreditBudgetExceeded, KeyMissingError

ET = ZoneInfo("America/New_York")
USER_AGENT = "nba-prediction-fastarb/1 (public market data, read-only)"


class KeyLeakError(RuntimeError):
    """A Pinnacle body contained the API key: nothing is written and the client is disabled."""


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
    ticks: int = 0
    books: dict[str, dict[str, Any]] = field(default_factory=dict)  # this tick's Polymarket books


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
    arbs_alertable: int = 0  # confirmed and emitted (or would be, with no sink)
    arbs_thin: int = 0
    arbs_unknown_depth: int = 0
    arbs_unconfirmed: int = (
        0  # cleared the gap on the tick's data, failed the back-to-back re-fetch
    )
    ladders_apparent: int = 0  # asks sum under $1 before fees, on the tick's data (any depth)
    ladders_alertable: int = 0  # confirmed back to back and emitted (or would be, with no sink)
    ladders_unconfirmed: int = 0  # cleared on the tick's data, failed the back-to-back re-fetch
    ladders_thin: int = 0
    ladders_unknown_depth: int = 0
    ladders_excluded: int = 0  # player-stat rungs: never alerted (DNP fair-price settlement)
    lags: int = 0
    news: int = 0
    credits: int = 0
    kalshi_ok: bool = True  # every configured series returned a body this tick
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
        self.pm = PolymarketUSClient(fetcher.get, str(cfg.section("polymarket_us")["base_url"]))
        self.fees = fee_functions(cfg)
        self.ladder = LadderScanner(cfg, fetcher, self.pm, mono=mono)
        self.games: dict[GameKey, GameCtx] = {}
        self.news_events: list[newsmod.NewsEvent] = []
        self.injury_known: set[str] = set()
        self._tips = tips
        self._tips_at = float("-inf")
        self._tips_cache: dict[tuple[int, int], datetime] = {}
        self.resolver: Any = None
        self.pin_events_at = float("-inf")
        self.pin_event_ids: dict[GameKey, str] = {}
        self.pin_disabled = ""  # reason, once a credit / auth / key error turned the client off
        self.news_unavailable = 0
        self.news_baselined = False
        self._k_rules: dict[str, Any] = {}
        self._pm_rules: dict[str, Any] = {}
        self._rules_day: date | None = None

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
        series_list = list(self.cfg.section("kalshi")["series"])
        lad = self.cfg.section("ladder")
        if lad["enabled"]:
            series_list += [s for s in lad["kalshi_extra_series"] if s not in series_list]
        for series in series_list:
            pages: list[dict[str, Any]] = []
            for _n, _resp, body in kp.iter_series_pages(self.f, series):
                if body is None:
                    rep.skipped[f"{K}:{series}:fetch_failed"] += 1
                    rep.kalshi_ok = False
                    break
                raw_markets = body.get("markets") or []
                if series not in self._k_rules:
                    for m in raw_markets:
                        if (
                            m.get("rules_primary")
                            and kp.event_date(str(m.get("event_ticker"))) == day
                        ):
                            self._k_rules[series] = {
                                "ticker": m.get("ticker"),
                                "rules_primary": m.get("rules_primary"),
                                "rules_secondary": m.get("rules_secondary"),
                                "latest_expiration_time": m.get("latest_expiration_time"),
                            }
                            break
                kept, _dropped = kp.filter_for_date(raw_markets, day)
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

    def discover(self, kalshi: list[dict[str, Any]], day: date, rep: TickReport) -> None:
        if self.mono() - self._tips_at > 600:
            try:
                self._tips_cache = self._slate_tips(day)
                self._tips_at = self.mono()
            except duckdb.Error:  # the lineups writer holds the lock: keep the previous tips
                rep.skipped["news:tips_unavailable"] += 1
                self._tips_at = self.mono() - 540  # retry in a minute, not in ten
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
        slug = venues.slug_for(ctx.key)
        raw_ev = self.pm.event(slug)
        if raw_ev is None:
            rep.skipped[f"{PM_US}:event_missing:{slug}"] += 1
            return
        if not self._pm_rules:
            self._pm_rules = venues.us_rules_text(raw_ev)
        ev = venues.us_filter_event(raw_ev)
        ctx.us_event, ctx.us_meta_at = ev, self.mono()
        self._save(now, PM_US, f"{slug}_event", ev)
        if ctx.tip is None and ev.get("startTime"):
            ctx.tip = datetime.fromisoformat(str(ev["startTime"]).replace("Z", "+00:00")).replace(
                tzinfo=None
            )
            ctx.tip_source = "polymarket_us.startTime"

    def _archive_rules(self, now: datetime, day: date) -> None:
        """Once per day: both venues' contract rules, as they read today (settlement-risk audit)."""
        if self.raw is None or self._rules_day == day or not (self._k_rules or self._pm_rules):
            return
        series: dict[str, Any] = {}
        for s in self.cfg.section("kalshi")["series"]:
            series[s] = _json(self.f.get(f"{kp.BASE}/series/{s}"))
        self.raw.write(
            now,
            "rules",
            "daily",
            {
                "day": day.isoformat(),
                "kalshi_series": series,
                "kalshi_market_rules": self._k_rules,
                "polymarket_us": self._pm_rules,
            },
        )
        self._rules_day = day

    # ------------------------------------------------------------------ one game
    def _game_books(
        self, ctx: GameCtx, kbook: QuoteBook, now: datetime, rep: TickReport, stat: GameStat
    ) -> QuoteBook:
        full = QuoteBook()
        full.merge(kbook)
        if ctx.us_event is not None:
            fc = self.cfg.us_fee.coefficient
            meta = venues.us_quotes(ctx.us_event, fc)  # event quotes: candidate selection only
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
            books: dict[str, dict[str, Any]] = {}
            for s in slugs:
                b = self.pm.book(s)
                if b is not None:
                    books[s] = b
                else:
                    rep.skipped[f"{PM_US}:book_failed"] += 1
            stat.lines_booked = len(books)
            ctx.books = books
            self._save(now, PM_US, f"{venues.slug_for(ctx.key)}_books", books)
            full.merge(venues.us_quotes(ctx.us_event, fc, books))
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

    # ------------------------------------------------------------------ confirm (back to back)
    def _confirm(
        self, r: ArbResult, ctx: GameCtx, rep: TickReport
    ) -> tuple[ArbResult | None, dict[str, Any]]:
        """Re-fetch both legs back to back; None when the gap is gone, the fetches are more than
        ``confirm_max_gap_s`` apart, or either leg cannot be re-read."""
        info: dict[str, Any] = {"result": "refused"}
        k_is_yes = r.yes_ask.venue == K
        k_ask, p_ask = (r.yes_ask, r.no_ask) if k_is_yes else (r.no_ask, r.yes_ask)
        if p_ask.venue != PM_US or ctx.us_event is None:
            info["reason"] = "not_tradable_pair"
            return None, info
        t0 = self.mono()
        km = kp.get_market(self.f, k_ask.ref)
        pb = self.pm.book(p_ask.ref)
        gap = self.mono() - t0  # both reads fall inside this window
        info["fetch_window_s"] = round(gap, 2)
        if km is None or pb is None:
            info["reason"] = "refetch_failed"
            return None, info
        if gap > float(self.cfg.section("alert")["confirm_max_gap_s"]):
            info["reason"] = "legs_too_far_apart"
            return None, info
        info["kalshi_updated_time"] = km.get("updated_time")
        info["pm_transact_time"] = (pb.get("marketData") or {}).get("transactTime")
        fresh_k = venues.kalshi_quotes([km]).quotes.get(r.prop, {}).get(K, {})
        one = dict(ctx.us_event)
        one["markets"] = [m for m in ctx.us_event.get("markets", []) if m.get("slug") == p_ask.ref]
        fresh_p = (
            venues.us_quotes(one, self.cfg.us_fee.coefficient, {p_ask.ref: pb})
            .quotes.get(r.prop, {})
            .get(PM_US, {})
        )
        side_k, side_p = ("yes", "no") if k_is_yes else ("no", "yes")
        ak, ap = fresh_k.get(side_k), fresh_p.get(side_p)
        if ak is None or ap is None or not (ak.depth_known() and ap.depth_known()):
            info["reason"] = "side_missing_after_refetch"
            return None, info
        yes, no = (ak, ap) if k_is_yes else (ap, ak)
        new = evaluate_pair(r.prop, yes, no, self.cfg, self.fees)
        info["confirmed_margin"] = round(new.margin, 4)
        if is_alertable(new, self.cfg) != "alert":
            info["reason"] = "gap_gone"
            return None, info
        info["result"] = "confirmed"
        info["gap_s"] = gap
        return new, info

    # ------------------------------------------------------------------ ladders (within venue)
    def _ladder_tick(
        self, ctx: GameCtx, book: QuoteBook, now: datetime, stamp: str, rep: TickReport
    ) -> None:
        pools: list[tuple[str, QuoteBook]] = [(K, book)]
        if ctx.us_event is not None:
            books = dict(ctx.books)
            extra: dict[str, dict[str, Any]] = {}
            for slug in self.ladder.pm_candidate_slugs(ctx.us_event):
                if slug in books:
                    continue
                b = self.pm.book(slug)
                if b is None:
                    rep.skipped[f"{PM_US}:ladder_book_failed"] += 1
                    continue
                books[slug], extra[slug] = b, b
            if extra:
                self._save(now, PM_US, f"{venues.slug_for(ctx.key)}_ladder_books", extra)
            pools.append(
                (PM_US, venues.us_quotes(ctx.us_event, self.cfg.us_fee.coefficient, books))
            )
        rules_ok = self.cfg.settlement_rules_verified
        for venue, qb in pools:
            for r in find_ladder_violations(qb, venue, self.cfg, self.ladder.fee):
                rep.ladders_apparent += 1
                verdict = is_ladder_alertable(r, self.cfg)
                if verdict == "excluded":
                    rep.ladders_excluded += 1
                if verdict == "thin":
                    rep.ladders_thin += 1
                if verdict == "unknown_depth":
                    rep.ladders_unknown_depth += 1
                    self._log_ladder(ctx, r, verdict, {"result": "not_confirmable"}, now)
                if verdict != "alert":
                    continue
                new, info = self.ladder.confirm(r, ctx.us_event)
                self._log_ladder(ctx, r, verdict, info, now)
                if new is None:
                    if info.get("reason") == PM_RULES_UNVERIFIED:
                        rep.skipped["ladder:pm_us_rules_unverified"] += 1
                        continue
                    rep.ladders_unconfirmed += 1
                    continue
                rep.ladders_alertable += 1
                text = sinks.ladder_text(new, gap_s=float(info["gap_s"]), rules_verified=rules_ok)
                rep.alerts.append(text)
                if self.sink is not None:
                    self.sink.emit(sinks.ladder_key(new), text, now, stamp)

    def _log_ladder(
        self, ctx: GameCtx, r: LadderResult, verdict: str, info: dict[str, Any], now: datetime
    ) -> None:
        if self.raw is None:
            return
        sinks.append_jsonl(
            self.raw.dir / "candidates.jsonl",
            {
                "type": "ladder",
                "ts": now.isoformat(),
                "game": ctx.key.label(),
                "venue": r.venue,
                "ladder": r.label(),
                "yes": [r.yes.ref, r.yes.price, r.yes.depth_known()],
                "no": [r.no.ref, r.no.price, r.no.depth_known()],
                "margin": round(r.margin, 4),
                "gross": round(r.gross_gap, 4),
                "verdict": verdict,
                **info,
            },
        )

    def _log_candidate(
        self, ctx: GameCtx, r: ArbResult, verdict: str, info: dict[str, Any], now: datetime
    ) -> None:
        if self.raw is None:
            return
        sinks.append_jsonl(
            self.raw.dir / "candidates.jsonl",
            {
                "ts": now.isoformat(),
                "game": ctx.key.label(),
                "prop": r.prop.label(),
                "yes": [r.yes_ask.venue, r.yes_ask.ref, r.yes_ask.price, r.yes_ask.depth_known()],
                "no": [r.no_ask.venue, r.no_ask.ref, r.no_ask.price, r.no_ask.depth_known()],
                "margin": round(r.margin, 4),
                "gross": round(r.gross_gap, 4),
                "verdict": verdict,
                **info,
            },
        )

    # ------------------------------------------------------------------ Pinnacle
    def _disable_pinnacle(self, reason: str) -> None:
        self.pc, self.pin_disabled = None, reason

    def _pinnacle(
        self, ctx: GameCtx, now: datetime, rep: TickReport, stat: GameStat
    ) -> tuple[dict[PropKey, float], dict[PropKey, str]]:
        empty: tuple[dict[PropKey, float], dict[PropKey, str]] = ({}, {})
        if self.pc is None:
            stat.pinnacle = f"disabled:{self.pin_disabled}" if self.pin_disabled else "off"
            return empty
        eid = self.pin_event_ids.get(ctx.key)
        if eid is None:
            stat.pinnacle = "no_event"
            return empty
        try:
            body, cost = self.pc.core_odds(eid, self.cfg.raw)
            rep.credits += cost
            key = self.pc.key_text()
            if key and key in json.dumps(body):
                raise KeyLeakError("pinnacle body contained the API key; not written")
        except (CreditBudgetExceeded, AuthError, KeyMissingError, KeyLeakError) as exc:
            self._disable_pinnacle(type(exc).__name__)
            stat.pinnacle = f"error:{type(exc).__name__}"
            rep.skipped["pinnacle:error"] += 1
            return empty
        except Exception as exc:  # one game's bad response must not touch the others
            stat.pinnacle = f"error:{type(exc).__name__}"
            rep.skipped["pinnacle:error"] += 1
            return empty
        self._save(now, "pinnacle", f"{venues.slug_for(ctx.key)}_core", body)
        probs, upd, skipped = pin.pinnacle_probs(body, ctx.key, self.resolver)
        for k, v in skipped.items():
            rep.skipped[f"pinnacle:{k}"] += v
        stat.pinnacle = f"{len(probs)} props"
        return probs, upd

    def _match_pinnacle_events(self, games: list[GameCtx], rep: TickReport) -> None:
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
        try:
            body, _ = self.pc.live_events(lo, hi)
        except (CreditBudgetExceeded, AuthError, KeyMissingError) as exc:
            self._disable_pinnacle(type(exc).__name__)
            rep.skipped["pinnacle:error"] += 1
            return
        except Exception:
            rep.skipped["pinnacle:error"] += 1
            self.pin_events_at = self.mono() - 540
            return
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
    def _game_tick(
        self,
        ctx: GameCtx,
        kb: QuoteBook,
        now: datetime,
        stamp: str,
        rep: TickReport,
    ) -> None:
        stat = GameStat(ctx.key.label(), ctx.tip)
        rep.games[ctx.key.label()] = stat
        ctx.ticks += 1
        self._us_meta(ctx, now, rep)
        one = QuoteBook()
        one.quotes = {p: v for p, v in kb.quotes.items() if p.game == ctx.key}
        book = self._game_books(ctx, one, now, rep, stat)
        rep.skipped.update(book.skipped)
        stat.n_props_both_venues = sum(
            1 for per in book.quotes.values() if K in per and (PM_US in per or PM_INTL in per)
        )
        rules_ok = self.cfg.settlement_rules_verified
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
                if verdict == "unknown_depth":
                    rep.arbs_unknown_depth += 1
                    self._log_candidate(ctx, r, verdict, {"result": "not_confirmable"}, now)
                if verdict != "alert":
                    continue
                if not tradable:
                    rep.alerts.append(sinks.arb_text(r, rules_verified=rules_ok))
                    continue
                new, info = self._confirm(r, ctx, rep)
                self._log_candidate(ctx, r, verdict, info, now)
                if new is None:
                    rep.arbs_unconfirmed += 1
                    continue
                rep.arbs_alertable += 1
                text = sinks.arb_text(new, gap_s=float(info["gap_s"]), rules_verified=rules_ok)
                rep.alerts.append(text)
                if self.sink is not None:
                    self.sink.emit(sinks.arb_key(new), text, now, stamp)
        if self.cfg.section("ladder")["enabled"]:
            try:
                self._ladder_tick(ctx, book, now, stamp, rep)
            except Exception as exc:  # a ladder failure never blocks the lag check or other games
                rep.skipped["ladder:error"] += 1
                rep.errors.append(f"ladder {ctx.key.label()}: {type(exc).__name__}")
        # lag vs Pinnacle: only on news inside the window, or a baseline call every N ticks
        window = float(self.cfg.section("alert")["lag_window_min"])
        recent = [
            e
            for e in newsmod.recent(self.news_events, now, window)
            if (e.home is None or e.home == ctx.key.home)
            and (e.away is None or e.away == ctx.key.away)
        ]
        rep.news += len(recent)
        every = int(self.cfg.section("pinnacle")["every_n_ticks"])
        if recent or (every > 0 and (ctx.ticks - 1) % every == 0):
            probs, upd = self._pinnacle(ctx, now, rep, stat)
        else:
            probs, upd = {}, {}
            stat.pinnacle = "idle(no news)"
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

    def tick(self, day: date, *, force_active: bool = False) -> TickReport:
        now = self.clock()
        rep = TickReport(now)
        req0 = self.f.requests
        self.f.begin_tick(1500, float(self.poll["tick_deadline_s"]))
        kal = self.kalshi_markets(now, day, rep)
        self.discover(kal, day, rep)
        kb = kalshi_ladder_quotes(kal)  # game, spread, total (+ team totals when ladders are on)
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
        self._match_pinnacle_events(active, rep)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for ctx in sorted(active, key=lambda c: c.tip or now):
            try:
                self._game_tick(ctx, kb, now, stamp, rep)
            except Exception as exc:  # one game's failure never cancels the others
                rep.skipped[f"game:error:{ctx.key.label()}"] += 1
                rep.errors.append(f"{ctx.key.label()}: {type(exc).__name__}: {str(exc)[:100]}")
        try:
            self._archive_rules(now, day)
        except Exception as exc:
            rep.errors.append(f"rules archive: {type(exc).__name__}")
        rep.requests = self.f.requests - req0
        return rep

    @staticmethod
    def _pinnacle_fresh(lag: LagResult, ev: newsmod.NewsEvent) -> bool:
        """Pinnacle must have updated at or after the news, or its price is not evidence of lag."""
        t = pin.parse_update(lag.pinnacle_updated)
        return t is not None and t >= ev.ts

    # ------------------------------------------------------------------ the loop
    def _utc_iso(self) -> str:
        """Offset-aware UTC (``+00:00``): the watchdog compares heartbeats with aware datetimes."""
        return self.clock().replace(tzinfo=UTC).isoformat()

    def _beat(self, start: str, rc: int | None, **state: Any) -> None:
        sinks.beat(
            self.cfg.path("heartbeat"),
            {
                "mode": "poll",
                "host": socket.gethostname().split(".")[0],
                "start_ts": start,
                "end_ts": None if rc is None else self._utc_iso(),
                "rc": rc,
                "last_tick_ts": self._utc_iso(),
                "raw_files": self.raw.files if self.raw else 0,
                **state,
            },
        )

    def run(self, day: date, sleep: Callable[[float], None] = time.sleep) -> int:
        """Poll until the last tip. Exit codes: 0 done / a clean no-game day, 1 gave up on errors.

        "No games" is declared only after a tick in which every Kalshi series returned a body; a
        failed tick (Kalshi 5xx, a locked lineups DB) is an ``error`` and polling continues until
        ``poll.give_up_et`` (no game ever found) or ``poll.hard_stop_et`` the next morning."""
        interval = float(self.poll["interval_s"])
        lead = timedelta(minutes=float(self.poll["lead_min"]))
        give_up = _et_naive_utc(day, str(self.poll["give_up_et"]))
        hard_stop = _et_naive_utc(day + timedelta(days=1), str(self.poll["hard_stop_et"]))
        ticks = errors = 0
        start = self._utc_iso()
        while True:
            now = self.clock()
            tips = [c.tip for c in self.games.values() if c.tip is not None]
            if now >= hard_stop:
                self._beat(start, 0 if self.games else 1, status="hard_stop", ticks=ticks)
                return 0 if self.games else 1
            if self.games and tips and now >= max(tips):
                self._beat(start, 0, status="done", ticks=ticks, errors=errors)
                return 0
            t0 = self.mono()
            rep: TickReport | None = None
            try:
                rep = self.tick(day)
                ticks += 1
                detail = f"ok requests={rep.requests} alerts={len(rep.alerts)}"
                if not rep.kalshi_ok:
                    detail += " kalshi_partial"
            except Exception as exc:  # a bad tick must not end the night
                errors += 1
                detail = f"{type(exc).__name__}: {str(exc)[:120]}"
            if not self.games:
                if rep is not None and rep.kalshi_ok:
                    self._beat(start, 0, status="no_games", detail=str(day), ticks=ticks)
                    return 0
                if now >= give_up:
                    self._beat(start, 1, status="error", detail=detail, ticks=ticks, errors=errors)
                    return 1
                self._beat(start, None, status="error", detail=detail, ticks=ticks, errors=errors)
                sleep(interval)
                continue
            first = min(tips) - lead if tips else now
            self._beat(
                start,
                None,
                ticks=ticks,
                errors=errors,
                status="waiting" if now < first else "running",
                detail=detail,
            )
            wait = interval - (self.mono() - t0)
            if now < first and tips:
                wait = max(wait, min((first - now).total_seconds() - 30, 600))
            sleep(max(wait, 1.0))


def _et_naive_utc(day: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    local = datetime.combine(day, dtime(int(h), int(m)), tzinfo=ET)
    return local.astimezone(UTC).replace(tzinfo=None)
