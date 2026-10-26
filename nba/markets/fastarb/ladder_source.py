"""Confirmed within-venue ladder arbitrage for JARVIS to consume later. STABLE INTERFACE, NOT WIRED.

``confirmed_ladder_arbs(cfg, games, slate, now, *, get=None, config=None, clock=None, mono=...)``
returns :class:`ConfirmedLadderArb` records, one per pair that passed every gate:

* both legs on the SAME venue (Kalshi, or Polymarket US) and the same ladder (moneyline and
  "wins by over N" spreads in home-margin coordinates, game totals, team totals);
* the two legs were re-fetched back to back and the reads span at most ``alert.confirm_max_gap_s``;
* depth of at least ``ladder.min_depth_pairs`` contracts at the quoted price on both legs;
* the fee objects are verified; the guaranteed profit per pair is after both fees and is at least
  ``ladder.min_gap``;
* player-stat ladders are never returned (a DNP settles every rung at an unspecified fair price).

Nothing is returned for a game that is not in the future, and nothing on any failure: an empty list
is the normal answer ("keep your money"). This module places no order and has no execution path;
it is not added to ``QUOTE_SOURCES`` or anywhere else. ``get`` is an httpx-style ``get(url, **kw)``
(``status_code``, ``content``); the default is the package's polite rate-limited fetcher.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlencode

from nba.jarvis.models import Game
from nba.live import kalshi_pull as kp
from nba.live.http import Response
from nba.markets.fastarb import venues
from nba.markets.fastarb.config import FastArbConfig, UnverifiedFeeError, load_config
from nba.markets.fastarb.ladder import (
    LadderResult,
    LadderScanner,
    find_ladder_violations,
    is_ladder_alertable,
    kalshi_ladder_quotes,
)
from nba.markets.fastarb.polymarket_us import PolymarketUSClient
from nba.markets.fastarb.props import ID_TO_ABBR, GameKey
from nba.markets.fastarb.venues import PM_US, K

__all__ = ["ConfirmedLadderArb", "LadderLeg", "confirmed_ladder_arbs"]

HttpGet = Callable[..., Any]


@dataclass(frozen=True)
class LadderLeg:
    venue: str  # "kalshi" | "polymarket_us"
    ticker: str  # Kalshi ticker, or the Polymarket US market slug
    side: str  # "YES" | "NO" of the canonical proposition; ``label`` names the contract to BUY
    price: float  # dollars per contract at the top of the quoted levels
    size: float | None  # contracts available at that price (None never occurs on a confirmed leg)
    label: str  # human text of the contract being bought


@dataclass(frozen=True)
class ConfirmedLadderArb:
    """Buy ``yes_leg`` and ``no_leg`` together: the pair pays at least $1.00 whatever happens."""

    game_key: GameKey
    ladder: str  # e.g. "margin>-3.5 vs margin>4.5", "total>225.5 vs >227.5"
    kind: str  # margin | total | team_total:<id>
    yes_leg: LadderLeg  # YES of the LOWER rung
    no_leg: LadderLeg  # NO of the HIGHER rung
    cost_per_pair: float  # both asks, before fees
    net_per_pair: float  # guaranteed profit per pair after both fees, at n_eval pairs
    n_eval: int  # pair count the fee rounding was evaluated at (not a stake)
    max_pairs: int  # pairs whose margin still clears ``ladder.min_gap`` at the visible depth
    leg_gap_s: float  # seconds spanned by the two back-to-back reads
    ts: datetime  # naive UTC when the second read finished
    note: str  # settlement note to show with the quote


def _leg(venue: str, a: Any, side: str) -> LadderLeg:
    return LadderLeg(venue, a.ref, side, a.price, a.size, a.desc)


def _adapt(get: HttpGet) -> Callable[[str], Response | None]:
    def fetch(url: str) -> Response | None:
        try:
            r = get(url, timeout=10.0)
        except Exception:
            return None
        return Response(int(r.status_code), bytes(r.content), url)

    return fetch


class _Getter:
    """``get(url, params=None)`` over a one-argument fetch (what ``kalshi_pull`` expects)."""

    def __init__(self, fetch: Callable[[str], Response | None]) -> None:
        self._fetch = fetch

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
        return self._fetch(f"{url}?{urlencode(params)}" if params else url)


def _default_fetch() -> Callable[[str], Response | None]:
    import httpx

    from nba.markets.fastarb.http import PoliteFetcher
    from nba.markets.fastarb.runner import USER_AGENT

    f = PoliteFetcher(
        client=httpx.Client(timeout=httpx.Timeout(10.0), headers={"User-Agent": USER_AGENT})
    )
    f.begin_tick(400, 120.0)
    return f.get


def _kalshi_markets(
    getter: _Getter, cfg: FastArbConfig, slate: date
) -> list[dict[str, Any]] | None:
    """Open markets dated ``slate`` for every ladder series; None if any series fails to load."""
    series = list(cfg.section("kalshi")["series"]) + list(
        cfg.section("ladder")["kalshi_extra_series"]
    )
    out: list[dict[str, Any]] = []
    for s in dict.fromkeys(series):
        for _n, _resp, body in kp.iter_series_pages(getter, s):  # type: ignore[arg-type]
            if body is None:
                return None
            kept, _dropped = kp.filter_for_date(body.get("markets") or [], slate)
            out.extend(kept)
    return out


def _arb(r: LadderResult, info: dict[str, Any], ts: datetime, note: str) -> ConfirmedLadderArb:
    return ConfirmedLadderArb(
        r.lo.game,
        r.label(),
        r.kind,
        _leg(r.venue, r.yes, "YES"),
        _leg(r.venue, r.no, "NO"),
        r.yes.price + r.no.price,
        r.margin,
        r.n_eval,
        r.max_n,
        float(info["gap_s"]),
        ts,
        note,
    )


def confirmed_ladder_arbs(
    cfg: Any,
    games: list[Game],
    slate: date,
    now: datetime,
    *,
    get: HttpGet | None = None,
    config: FastArbConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    mono: Callable[[], float] = time.monotonic,
) -> list[ConfirmedLadderArb]:
    """Confirmed ladder arbitrage for ``games`` on ``slate`` (see the module docstring).

    ``cfg`` is JARVIS's config (accepted for hook-signature parity; only ``confirmed_max_gap_s``
    is read, when it exists, so a looser window than the consumer's yields nothing)."""
    fa = config or load_config()
    try:
        fa.require_verified_fees()
    except UnverifiedFeeError:
        return []
    if not bool(fa.section("ladder")["enabled"]):
        return []
    max_gap = float(fa.section("alert")["confirm_max_gap_s"])
    jarvis_gap = getattr(cfg, "confirmed_max_gap_s", None)
    if jarvis_gap is not None and max_gap > float(jarvis_gap):
        return []
    fetch = _adapt(get) if get is not None else _default_fetch()
    read_clock = clock or (lambda: datetime.now(UTC).replace(tzinfo=None))
    getter = _Getter(fetch)
    pm = PolymarketUSClient(fetch, str(fa.section("polymarket_us")["base_url"]))
    scanner = LadderScanner(fa, getter, pm, mono=mono)
    markets = _kalshi_markets(getter, fa, slate)
    if markets is None:
        return []
    kq = kalshi_ladder_quotes(markets)
    out: list[ConfirmedLadderArb] = []
    for g in games:
        if g.tipoff is None or g.tipoff <= now or g.away_team not in ID_TO_ABBR:
            continue
        key = GameKey(g.away_team, g.home_team, slate.isoformat())
        pools: list[tuple[str, Any, dict[str, Any] | None]] = []
        game_book = type(kq)()
        game_book.quotes = {p: v for p, v in kq.quotes.items() if p.game == key}
        pools.append((K, game_book, None))
        ev = pm.filtered_event(venues.slug_for(key))
        if ev is not None and not venues.us_is_live(ev):
            books: dict[str, dict[str, Any]] = {}
            for slug in scanner.pm_candidate_slugs(ev):
                b = pm.book(slug)
                if b is not None:
                    books[slug] = b
            pools.append((PM_US, venues.us_quotes(ev, fa.us_fee.coefficient, books), ev))
        for venue, qb, event in pools:
            for r in find_ladder_violations(qb, venue, fa, scanner.fee):
                if is_ladder_alertable(r, fa) != "alert":
                    continue
                new, info = scanner.confirm(r, event)
                if new is None:
                    continue
                note = (
                    "Kalshi: a game voided >48h settles at a fair price"
                    if venue == K
                    else (
                        "Polymarket US push/void rules not verified"
                        if not fa.settlement_rules_verified
                        else "Polymarket US rules verified"
                    )
                )
                out.append(_arb(new, info, read_clock(), note))
    return out
