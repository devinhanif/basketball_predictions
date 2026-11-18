"""Pure parsers of the 15-second Kalshi poller (moved verbatim from ``nba.kalshi.poll15``, which
re-exports every name here). Ticker dates, quote rows and trade rows; no I/O, no network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}
_EVENT_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")


# ------------------------------------------------------------------ parsing (pure)
def event_date(event_ticker: str) -> date | None:
    """``KXNBAGAME-26NOV09SASPHX`` -> 2026-11-09 (the ET game date; None for undated tickers)."""
    m = _EVENT_DATE.search(event_ticker)
    if m is None or m[2] not in _MONTHS:
        return None
    try:
        return date(2000 + int(m[1]), _MONTHS[m[2]], int(m[3]))
    except ValueError:
        return None


def _f(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def quote_rows(
    markets: Sequence[Mapping[str, Any]],
    *,
    series: str,
    day: date,
    fetched_at: datetime,
    request_at: datetime,
    server_date: str | None,
) -> list[dict[str, Any]]:
    """One row per open market of the slate date. Prices are dollars (0-1); a one-sided book is
    reported by Kalshi as 0.0000 and kept as is (the spread stays visible)."""
    out: list[dict[str, Any]] = []
    for m in markets:
        if event_date(str(m.get("event_ticker", ""))) != day:
            continue
        out.append(
            {
                "fetched_at": fetched_at,
                "request_at": request_at,
                "server_date": server_date,
                "series": series,
                "ticker": str(m.get("ticker", "")),
                "event_ticker": str(m.get("event_ticker", "")),
                "title": str(m.get("title", "")),
                "status": str(m.get("status", "")),
                "yes_bid": _f(m.get("yes_bid_dollars")),
                "yes_ask": _f(m.get("yes_ask_dollars")),
                "yes_bid_size": _f(m.get("yes_bid_size_fp")),
                "yes_ask_size": _f(m.get("yes_ask_size_fp")),
                "last": _f(m.get("last_price_dollars")),
                "volume": _f(m.get("volume_fp")),
                "open_interest": _f(m.get("open_interest_fp")),
                "market_updated": str(m.get("updated_time", "")) or None,
            }
        )
    return out


def trade_rows(trades: Sequence[Mapping[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        created = _ts(t.get("created_time"))
        if created is None or not t.get("trade_id"):
            continue
        out.append(
            {
                "fetched_at": fetched_at,
                "ticker": str(t.get("ticker", "")),
                "trade_id": str(t["trade_id"]),
                "created_time": created,
                "yes_price": _f(t.get("yes_price_dollars")),
                "no_price": _f(t.get("no_price_dollars")),
                "count": _f(t.get("count_fp")),
                "taker_side": str(t.get("taker_outcome_side") or t.get("taker_side") or "") or None,
            }
        )
    return out
