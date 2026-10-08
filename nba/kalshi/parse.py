"""Raw Kalshi market/candlestick JSON -> ``kalshi_markets``/``kalshi_prices`` rows.

Field names below follow Kalshi's documented ``Market`` and
``MarketCandlestick`` objects (docs.kalshi.com/api-reference/market/...).
Price fields on the real API are fixed-point dollar strings (e.g.
``yes_bid_dollars``) and candlesticks nest OHLC under ``yes_bid``/
``yes_ask``/``price``; this parser accepts either that nested shape or a
flat ``{"yes_bid": 0.55, ...}`` shape (used by the synthetic fixtures in
``tests/kalshi/fixtures/``) so unit tests don't have to hand-roll the full
nested structure to exercise parsing logic. Either way the *target* schema
(``kalshi_prices``: one flat float per field) matches CLAUDE.md's table.

Pure functions -- no network, no DB connection required (callers own the
DuckDB write).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import polars as pl

from nba.kalshi.aliases import resolve_kalshi_player_name
from nba.kalshi.thresholds import UnparseableTitleError, parse_threshold_title

MARKETS_SCHEMA = [
    "ticker",
    "series_ticker",
    "event_ticker",
    "title",
    "player_id",
    "stat",
    "threshold",
    "open_time",
    "close_time",
    "settled_ts",
    "result",
]

PRICES_SCHEMA = [
    "ticker",
    "ts",
    "yes_bid",
    "yes_ask",
    "last",
    "volume",
    "open_interest",
    "source",
]


class SkippedMarket(Exception):
    """Internal signal: this market isn't a parseable player-prop "N+" market.

    Not every Kalshi NBA market is a player prop (game winner/spread/total
    markets share the same ``/markets`` feed); these are expected and
    skipped, not an error. Distinguished from ``UnparseableTitleError``
    (also caught) only so callers can log skip reasons if desired.
    """


def _price_field(raw: dict[str, Any], flat_key: str, nested_key: str = "close") -> float | None:
    """Read a price field that may be a flat float or a nested OHLC dict.

    Real candlesticks nest OHLC under e.g. ``yes_bid: {"close": ...}``;
    fixtures may pass ``yes_bid: 0.55`` directly. ``None`` passes through
    (a field genuinely absent on a given candle, e.g. no trades that
    period) rather than being coerced to 0, which would misrepresent a
    missing quote as a real zero price.
    """
    value = raw.get(flat_key)
    if value is None:
        return None
    if isinstance(value, dict):
        nested = value.get(nested_key)
        return float(nested) if nested is not None else None
    return float(value)


def parse_market(raw: dict[str, Any], *, aliases: dict[str, int] | None = None) -> dict[str, Any]:
    """Parse one raw ``Market`` JSON object into a ``kalshi_markets`` row dict.

    Raises ``SkippedMarket`` (caught by ``parse_markets_frame``, not
    propagated) for markets whose title isn't a recognized "N+" player-prop
    shape -- expected for game-level markets. Raises
    ``nba.kalshi.aliases.UnmatchedKalshiNameError`` loudly (NOT caught) if
    the title *is* a player-prop shape but the player name has no reviewed
    alias -- CLAUDE.md requires failing loudly here, not skipping.
    """
    title = raw.get("title")
    if not title or not isinstance(title, str):
        raise SkippedMarket(f"market {raw.get('ticker')!r} has no title")
    try:
        player_name, stat, threshold = parse_threshold_title(title)
    except UnparseableTitleError:
        raise SkippedMarket(f"title {title!r} is not an 'N+' player-prop market") from None
    player_id = resolve_kalshi_player_name(player_name, aliases)
    return {
        "ticker": raw["ticker"],
        "series_ticker": raw.get("series_ticker"),
        "event_ticker": raw.get("event_ticker"),
        "title": title,
        "player_id": player_id,
        "stat": stat,
        "threshold": threshold,
        "open_time": raw.get("open_time"),
        "close_time": raw.get("close_time"),
        "settled_ts": raw.get("settlement_ts") or raw.get("settled_ts"),
        "result": raw.get("result"),
    }


def parse_markets_frame(
    raw_markets: list[dict[str, Any]], *, aliases: dict[str, int] | None = None
) -> pl.DataFrame:
    """Parse a list of raw ``Market`` JSON objects into a ``kalshi_markets`` frame.

    Non-player-prop markets (``SkippedMarket``) are silently excluded --
    expected noise in the feed. Unmatched player names are NOT caught here
    (propagate up) per the "fail loudly" requirement.
    """
    rows: list[dict[str, Any]] = []
    for raw in raw_markets:
        try:
            rows.append(parse_market(raw, aliases=aliases))
        except SkippedMarket:
            continue
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(MARKETS_SCHEMA, pl.Null))
    return pl.DataFrame(rows).select(MARKETS_SCHEMA)


def parse_candlestick(raw: dict[str, Any], *, ticker: str, source: str) -> dict[str, Any]:
    """Parse one raw ``MarketCandlestick`` JSON object into a ``kalshi_prices`` row.

    ``source`` is caller-supplied ('live' | 'historical') -- the candlestick
    payload itself doesn't say which tier served it, and CLAUDE.md requires
    that provenance be recorded per row.
    """
    if source not in {"live", "historical"}:
        raise ValueError(f"source must be 'live' or 'historical', got {source!r}")
    ts_raw = raw.get("end_period_ts", raw.get("ts"))
    if ts_raw is None:
        raise ValueError(f"candlestick missing 'end_period_ts'/'ts': {raw!r}")
    ts = datetime.fromtimestamp(ts_raw) if isinstance(ts_raw, int | float) else ts_raw
    return {
        "ticker": ticker,
        "ts": ts,
        "yes_bid": _price_field(raw, "yes_bid"),
        "yes_ask": _price_field(raw, "yes_ask"),
        "last": _price_field(raw, "price"),
        "volume": raw.get("volume_fp", raw.get("volume")),
        "open_interest": raw.get("open_interest_fp", raw.get("open_interest")),
        "source": source,
    }


def parse_candlesticks_frame(
    raw_candles: list[dict[str, Any]], *, ticker: str, source: str
) -> pl.DataFrame:
    """Parse a list of raw candlestick JSON objects into a ``kalshi_prices`` frame."""
    rows = [parse_candlestick(c, ticker=ticker, source=source) for c in raw_candles]
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(PRICES_SCHEMA, pl.Null))
    return pl.DataFrame(rows).select(PRICES_SCHEMA)
