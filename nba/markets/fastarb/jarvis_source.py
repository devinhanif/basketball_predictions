"""Confirmed live quotes for JARVIS's ``QUOTE_SOURCES`` hook. STABLE INTERFACE.

``confirmed_quotes(cfg, games, slate, now, *, get=None, config=None, clock=None, mono=...)`` has
the hook signature ``(cfg, games, slate, now) -> list[nba.jarvis.models.Quote]``; the keyword
arguments are optional. It is NOT wired into ``QUOTE_SOURCES`` on this branch (a follow-up).

What it returns: moneyline (``game_win``) quotes for both teams on Kalshi and Polymarket US, for
every game with a known future tip, and ONLY for a game whose legs all passed the confirm step:

* both venues' markets were re-fetched back to back (Kalshi ``GET /markets/{ticker}`` for both
  teams, then the Polymarket US order book) and the reads span at most
  ``alert.confirm_max_gap_s`` (5 s);
* depth on every leg: Kalshi bid and ask sizes, and Polymarket book levels on both sides;
* the fee objects are verified (``FastArbConfig.require_verified_fees``).

Any failure (a missing read, a thin side, an unverified fee, a slow window, a 404) yields no quote
for that game, never an unconfirmed one: this function never returns ``confirmed=False`` and never
builds a ``confirmed=True`` quote from an event-only ask (one with no order-book levels). A game
is also unconfirmed unless the Polymarket market is open and not closed, each side is tradable,
its ``feeCoefficient`` equals the verified fee, the book state is open and both Kalshi markets are
active. Each quote's ``ts`` is the time ITS OWN read finished (two Kalshi reads, then the book),
so JARVIS's pair-gap check is real; nothing is returned when ``alert.confirm_max_gap_s`` exceeds
the consumer's ``confirmed_max_gap_s``. Kalshi tickers are the venue's own; Polymarket
tickers follow
JARVIS's loader (``POLYUS:{market slug}:{side index}``). Public GETs only; no order endpoint.

``get`` is an httpx-style ``get(url, **kw)`` (for example ``httpx.get`` or ``client.get``)
returning an object with ``status_code`` and ``content``; the default is the package's polite,
rate-limited fetcher.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from nba.jarvis.config import JarvisConfig
from nba.jarvis.models import Game, Quote
from nba.live import kalshi_pull as kp
from nba.live.http import Response
from nba.markets.fastarb import venues
from nba.markets.fastarb.config import FastArbConfig, UnverifiedFeeError, load_config
from nba.markets.fastarb.polymarket_us import PolymarketUSClient
from nba.markets.fastarb.props import ID_TO_ABBR, GameKey, team_id

__all__ = ["confirmed_quotes"]

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
HttpGet = Callable[..., Any]


def _adapt(get: HttpGet) -> Callable[[str], Response | None]:
    """httpx-style ``get(url, **kw)`` -> ``(url) -> Response | None``; a transport error is None."""

    def fetch(url: str) -> Response | None:
        try:
            r = get(url, timeout=10.0)
        except Exception:
            return None
        return Response(int(r.status_code), bytes(r.content), url)

    return fetch


def _default_get() -> Callable[[str], Response | None]:
    import httpx

    from nba.markets.fastarb.http import PoliteFetcher
    from nba.markets.fastarb.runner import USER_AGENT

    f = PoliteFetcher(
        client=httpx.Client(timeout=httpx.Timeout(10.0), headers={"User-Agent": USER_AGENT})
    )
    f.begin_tick(200, 40.0)
    return f.get


def _kalshi_event(game: Game, slate: date) -> str:
    return (
        f"KXNBAGAME-{slate:%y}{_MONTHS[slate.month - 1]}{slate:%d}{game.away_abbr}{game.home_abbr}"
    )


def _kalshi_quote(
    game: Game, tid: int, ab: str, other: str, m: dict[str, Any], ts: datetime
) -> Quote | None:
    bid, ask = kp.to_f(m.get("yes_bid_dollars")), kp.to_f(m.get("yes_ask_dollars"))
    bsz, asz = kp.to_f(m.get("yes_bid_size_fp")), kp.to_f(m.get("yes_ask_size_fp"))
    if str(m.get("status", "")) not in ("active", "open"):  # paused / closed / settled: no quote
        return None
    if not (bid and ask and bsz and asz and 0 < bid < ask < 1):  # depth on both sides, sane book
        return None
    return Quote(
        "kalshi", str(m["ticker"]), game.game_id, "game_win", ts, bid, ask, team_id=tid,
        series="KXNBAGAME", label=f"{ab} to win", label_no=f"{other} to win", confirmed=True,
    )  # fmt: skip


def _poly_quotes(
    game: Game,
    ev: dict[str, Any],
    slug: str,
    book: dict[str, Any],
    ts: datetime,
    fee_coefficient: float,
) -> list[Quote] | None:
    market = next((m for m in ev.get("markets", []) if m.get("slug") == slug), None)
    if market is None:
        return None
    # the same gates as fastarb.venues.us_quotes and JARVIS's polymarket loader
    if market.get("status") != "MARKET_STATUS_OPEN" or market.get("closed"):
        return None
    fc = kp.to_f(market.get("feeCoefficient"))
    if fc is None or abs(fc - fee_coefficient) > 1e-9:
        return None
    if (book.get("marketData") or {}).get("state") != "MARKET_STATE_OPEN":
        return None
    id_to_team = {
        int(t["id"]): tid
        for t in ev.get("teams", [])
        if t.get("id") is not None and (tid := team_id(str(t.get("abbreviation", "")))) is not None
    }
    out: list[Quote] = []
    for i, side in enumerate(market.get("marketSides", [])):
        if not side.get("tradable", False):  # an untradable side unconfirms the game
            return None
        tid = id_to_team.get(int(side["teamId"])) if side.get("teamId") is not None else None
        asks = venues.us_levels(book, bool(side.get("long")))  # cheapest-first asks for this side
        opp = venues.us_levels(book, not bool(side.get("long")))  # the other side's asks
        if tid not in (game.home_team, game.away_team) or not asks or not opp:
            return None  # a thin or unmapped side: the whole game is unconfirmed
        ask = asks[0][0]
        bid = 1.0 - opp[0][0]  # selling this side = buying the other at its ask
        if not 0 < bid < ask < 1:
            return None
        ab = game.home_abbr if tid == game.home_team else game.away_abbr
        other = game.away_abbr if tid == game.home_team else game.home_abbr
        out.append(
            Quote(
                "polymarket", f"POLYUS:{slug}:{i}", game.game_id, "game_win", ts, bid, ask,
                team_id=tid, series="polymarket_us_winner", label=f"{ab} to win",
                label_no=f"{other} to win", confirmed=True,
            )
        )  # fmt: skip
    return out if len(out) == 2 else None


def confirmed_quotes(
    cfg: JarvisConfig,
    games: list[Game],
    slate: date,
    now: datetime,
    *,
    get: HttpGet | None = None,
    config: FastArbConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    mono: Callable[[], float] = time.monotonic,
) -> list[Quote]:
    """Confirmed moneyline quotes (see the module docstring). ``cfg`` and ``now`` are the hook's
    arguments; games whose tip is unknown or already past ``now`` are skipped."""
    fa = config or load_config()
    try:
        fa.require_verified_fees()
    except UnverifiedFeeError:
        return []
    fetch = _adapt(get) if get is not None else _default_get()
    read_clock = clock or (lambda: datetime.now(UTC).replace(tzinfo=None))
    pm = PolymarketUSClient(fetch, str(fa.section("polymarket_us")["base_url"]))
    max_gap = float(fa.section("alert")["confirm_max_gap_s"])
    jarvis_gap = getattr(cfg, "confirmed_max_gap_s", None)
    if jarvis_gap is None:
        return []  # unknown consumer window: fail closed (ladder review, minor)
    if max_gap > float(jarvis_gap):
        return []  # our window is looser than the consumer's: its pair check would be vacuous

    class _F:  # kp.get_market only needs ``.get``
        def get(self, url: str, params: dict[str, Any] | None = None) -> Response | None:
            return fetch(url)

    out: list[Quote] = []
    for g in games:
        if g.tipoff is None or g.tipoff <= now or g.away_team not in ID_TO_ABBR:
            continue
        key = GameKey(g.away_team, g.home_team, slate.isoformat())
        ev = pm.filtered_event(venues.slug_for(key))  # metadata: market slug and team ids only
        ml = None if ev is None else next(
            (m for m in ev["markets"] if m.get("sportsMarketType") == venues.US_WINNER), None
        )  # fmt: skip
        if ev is None or ml is None or venues.us_is_live(ev):
            continue
        slug = str(ml["slug"])
        evt = _kalshi_event(g, slate)
        t0 = mono()  # the confirm window: both Kalshi markets, then the Polymarket book
        kms: list[dict[str, Any] | None] = []
        k_ts: list[datetime] = []
        for ab in (g.away_abbr, g.home_abbr):
            kms.append(kp.get_market(_F(), f"{evt}-{ab}"))  # type: ignore[arg-type]
            k_ts.append(read_clock())  # each leg carries its own read-completion time
        book = pm.book(slug)
        p_ts = read_clock()
        gap = mono() - t0
        if book is None or any(m is None for m in kms) or gap > max_gap:
            continue
        legs = ((g.away_team, g.away_abbr, g.home_abbr), (g.home_team, g.home_abbr, g.away_abbr))
        k_quotes = [
            _kalshi_quote(g, tid, ab, other, m, t)  # type: ignore[arg-type]
            for (tid, ab, other), m, t in zip(legs, kms, k_ts, strict=True)
        ]
        p_quotes = _poly_quotes(g, ev, slug, book, p_ts, fa.us_fee.coefficient)
        if p_quotes is None or any(q is None for q in k_quotes):
            continue  # one unconfirmed leg unconfirms the game
        out += [q for q in k_quotes if q is not None] + p_quotes
    return out
