"""Polymarket US public market-data client. STABLE INTERFACE (other branches import this).

Public, unauthenticated GETs only against ``https://gateway.polymarket.us`` (docs.polymarket.us:
"No API key needed"). There is no order, portfolio or account call here and none will be added.

Public names (``__all__``), pinned by ``test_polymarket_us_interface`` in
``tests/markets/fastarb/test_fastarb.py``:

* ``PolymarketUSClient(get, base_url=DEFAULT_BASE)``: ``get`` is any ``(url) -> Response | None``
  (``nba.live.http.Fetcher.get`` or ``nba.markets.fastarb.http.PoliteFetcher.get``).
    - ``event(slug) -> dict | None``: the raw event body (``/v1/events/slug/{slug}``); slugs
      look like ``nba-det-phi-2026-10-25`` (away first, ET date).
    - ``filtered_event(slug) -> dict | None``: the same reduced to winner / spread / total.
    - ``book(market_slug) -> dict | None``: ``/v1/markets/{slug}/book``; returned only when its
      ``marketData.marketSlug`` equals the slug asked for (a mismatched payload is dropped).
    - ``bbo(market_slug) -> dict | None``: ``/v1/markets/{slug}/bbo``.
* ``ask_levels(book, long_side) -> [(price, contracts)]`` cheapest first (the long side lifts
  offers, the short side hits bids at 1 - bid); payload sort order is never trusted.
* ``quotes(event, fee_coefficient, books=None) -> QuoteBook``: canonical propositions (see
  props.py) with an ``Ask`` per side; asks carry book levels when a book is supplied, else the
  event quote and ``levels == []`` (a price for candidate selection only; depth unknown).
* ``is_live(event) -> bool``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nba.live.http import Response
from nba.markets.fastarb import venues

DEFAULT_BASE = "https://gateway.polymarket.us"
ask_levels = venues.us_levels
quotes = venues.us_quotes
is_live = venues.us_is_live

__all__ = [
    "DEFAULT_BASE",
    "PolymarketUSClient",
    "ask_levels",
    "is_live",
    "quotes",
]


def _body(resp: Response | None) -> dict[str, Any] | None:
    if resp is None or resp.status != 200:
        return None
    try:
        out = json.loads(resp.body)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


class PolymarketUSClient:
    def __init__(self, get: Callable[[str], Response | None], base_url: str = DEFAULT_BASE) -> None:
        self._get = get
        self.base_url = base_url.rstrip("/")

    def event(self, slug: str) -> dict[str, Any] | None:
        body = _body(self._get(f"{self.base_url}/v1/events/slug/{slug}"))
        ev = body.get("event") if body else None
        return ev if isinstance(ev, dict) else None

    def filtered_event(self, slug: str) -> dict[str, Any] | None:
        ev = self.event(slug)
        return venues.us_filter_event(ev) if ev is not None else None

    def book(self, market_slug: str) -> dict[str, Any] | None:
        body = _body(self._get(f"{self.base_url}/v1/markets/{market_slug}/book"))
        if body is None or (body.get("marketData") or {}).get("marketSlug") != market_slug:
            return None
        return body

    def bbo(self, market_slug: str) -> dict[str, Any] | None:
        return _body(self._get(f"{self.base_url}/v1/markets/{market_slug}/bbo"))
