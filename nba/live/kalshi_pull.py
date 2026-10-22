"""Kalshi public market-data reads used by the capture and the paper trader.

The only paths this module can build are in ``ALLOWED_PATHS`` (market data: series list, markets,
one market, one book). A test pins that list and greps the new packages for anything else.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date
from typing import Any

from nba.live.http import Fetcher, Response

BASE = "https://api.elections.kalshi.com/trade-api/v2"
ALLOWED_PATHS = ("/series", "/markets", "/markets/{ticker}", "/markets/{ticker}/orderbook")
DEFAULT_SERIES = (
    "KXNBAGAME",
    "KXNBASPREAD",
    "KXNBATOTAL",
    "KXNBATEAMTOTAL",
    "KXNBAPTS",
    "KXNBAREB",
    "KXNBAAST",
    "KXNBA3PT",
    "KXNBAPRA",
    "KXNBASTL",
    "KXNBABLK",
)
GAME_SERIES = ("KXNBAGAME", "KXNBASPREAD", "KXNBATOTAL")
RULE_FIELDS = ("rules_primary", "rules_secondary")
MAX_PAGES = 10
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}
_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")


def event_date(event_ticker: str) -> date | None:
    """``KXNBAGAME-26OCT21CHABKN`` -> 2026-10-21 (None for undated tickers, e.g. futures)."""
    m = _EVENT_DATE.search(event_ticker)
    if m is None or m[2] not in _MONTHS:
        return None
    try:
        return date(2000 + int(m[1]), _MONTHS[m[2]], int(m[3]))
    except ValueError:
        return None


def event_teams(event_ticker: str) -> tuple[str, str] | None:
    """(away, home) Kalshi abbreviations from a dated event ticker."""
    m = _EVENT_DATE.search(event_ticker)
    return (m[4], m[5]) if m else None


def parse_body(resp: Response | None) -> dict[str, Any] | None:
    if resp is None or resp.status != 200:
        return None
    try:
        out = json.loads(resp.body)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def list_nba_series(fetcher: Fetcher) -> tuple[list[str], Response | None]:
    """KXNBA* series from the public series list; the default list when the call fails."""
    resp = fetcher.get(f"{BASE}/series", {"category": "Sports"})
    body = parse_body(resp)
    if body is None:
        return list(DEFAULT_SERIES), resp
    found = sorted(
        str(s["ticker"])
        for s in body.get("series") or []
        if isinstance(s, dict) and str(s.get("ticker", "")).startswith("KXNBA")
    )
    merged = sorted(set(found) | set(DEFAULT_SERIES))
    return merged, resp


def filter_for_date(markets: list[dict[str, Any]], day: date) -> tuple[list[dict[str, Any]], int]:
    """Markets whose event is dated ``day`` (rules text removed: static per ticker, saved once)."""
    kept: list[dict[str, Any]] = []
    for m in markets:
        if event_date(str(m.get("event_ticker", ""))) == day:
            kept.append({k: v for k, v in m.items() if k not in RULE_FIELDS})
    return kept, len(markets) - len(kept)


def iter_series_pages(
    fetcher: Fetcher, series: str, status: str = "open"
) -> Iterator[tuple[int, Response | None, dict[str, Any] | None]]:
    """Paginated ``GET /markets`` for one series (page index, response, parsed body)."""
    cursor: str | None = None
    for page in range(MAX_PAGES):
        params: dict[str, Any] = {"series_ticker": series, "status": status, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = fetcher.get(f"{BASE}/markets", params)
        body = parse_body(resp)
        yield page, resp, body
        cursor = str(body.get("cursor") or "") if body else ""
        if not cursor:
            return


def get_market(fetcher: Fetcher, ticker: str) -> dict[str, Any] | None:
    body = parse_body(fetcher.get(f"{BASE}/markets/{ticker}"))
    market = body.get("market") if body else None
    return market if isinstance(market, dict) else None


def get_orderbook(fetcher: Fetcher, ticker: str) -> Response | None:
    return fetcher.get(f"{BASE}/markets/{ticker}/orderbook")


def to_f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def quote(m: dict[str, Any]) -> dict[str, float | None]:
    """Top of book for the YES side and the NO side (NO ask = 1 - YES bid), with sizes."""
    yb, ya = to_f(m.get("yes_bid_dollars")), to_f(m.get("yes_ask_dollars"))
    nb, na = to_f(m.get("no_bid_dollars")), to_f(m.get("no_ask_dollars"))
    return {
        "yes_bid": yb,
        "yes_ask": ya,
        "no_bid": nb if nb is not None else (None if ya is None else 1.0 - ya),
        "no_ask": na if na is not None else (None if yb is None else 1.0 - yb),
        "yes_bid_size": to_f(m.get("yes_bid_size_fp")),
        "yes_ask_size": to_f(m.get("yes_ask_size_fp")),
    }
