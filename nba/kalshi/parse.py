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

from datetime import UTC, datetime
from typing import Any

import polars as pl

from nba.kalshi.aliases import UnmatchedKalshiNameError, resolve_kalshi_player_name
from nba.kalshi.thresholds import (
    SERIES_STAT,
    UnparseableTitleError,
    parse_prop_title,
    parse_threshold_title,
)

#: Game-level series -> ``kalshi_markets.stat`` label. ``threshold`` holds the
#: market's ``floor_strike`` (spread margin / total points; NULL for winners)
#: and ``player_id`` is NULL.
GAME_SERIES_STAT: dict[str, str] = {
    "KXNBAGAME": "game_win",
    "KXNBASPREAD": "spread",
    "KXNBATOTAL": "total",
    "KXNBATEAMTOTAL": "team_total",
}

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


def to_float(value: Any) -> float | None:
    """Kalshi sends fixed-point numbers as strings (``"0.5600"``); None passes through."""
    if value is None or value == "":
        return None
    return float(value)


def parse_ts(value: Any) -> datetime | None:
    """ISO string (with or without ``Z``) / epoch seconds -> naive UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, UTC).replace(tzinfo=None)
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


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
        # live tier: "close_dollars"; historical tier: "close"; both are strings
        nested = value.get(nested_key, value.get(f"{nested_key}_dollars"))
        return to_float(nested)
    return to_float(value)


def _count(raw: dict[str, Any], *keys: str) -> int | None:
    for k in keys:
        v = to_float(raw.get(k))
        if v is not None:
            return round(v)
    return None


def _series_of(raw: dict[str, Any]) -> str | None:
    """Series ticker: explicit field if present, else the ticker's first segment
    (the live /markets payload omits ``series_ticker``)."""
    explicit = raw.get("series_ticker")
    if explicit:
        return str(explicit)
    ticker = raw.get("ticker")
    return str(ticker).split("-")[0] if ticker else None


def parse_game_market(raw: dict[str, Any]) -> dict[str, Any]:
    """Parse a game-level market (winner / spread / total) into a ``kalshi_markets`` row."""
    series = _series_of(raw)
    if series not in GAME_SERIES_STAT:
        raise SkippedMarket(f"series {series!r} is not a tracked game series")
    return _row(raw, series, GAME_SERIES_STAT[series], None, to_float(raw.get("floor_strike")))


def _row(
    raw: dict[str, Any],
    series: str | None,
    stat: str | None,
    player_id: int | None,
    threshold: float | None,
) -> dict[str, Any]:
    return {
        "ticker": raw["ticker"],
        "series_ticker": series,
        "event_ticker": raw.get("event_ticker"),
        "title": raw.get("title"),
        "player_id": player_id,
        "stat": stat,
        "threshold": threshold,
        "open_time": parse_ts(raw.get("open_time")),
        "close_time": parse_ts(raw.get("close_time")),
        "settled_ts": parse_ts(raw.get("settlement_ts") or raw.get("settled_ts")),
        "result": raw.get("result") or None,
    }


def parse_market(
    raw: dict[str, Any],
    *,
    aliases: dict[str, int] | None = None,
    unmatched: list[str] | None = None,
) -> dict[str, Any]:
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
    series = _series_of(raw)
    player_name: str
    if series in SERIES_STAT and ":" in title:
        # live-API shape: "Victor Wembanyama: 40+ points", stat from the series
        try:
            player_name, threshold = parse_prop_title(title)
        except UnparseableTitleError:
            raise SkippedMarket(f"title {title!r} is not an 'N+' player-prop market") from None
        stat = SERIES_STAT[series]
    else:
        try:
            player_name, stat, threshold = parse_threshold_title(title)
        except UnparseableTitleError:
            raise SkippedMarket(f"title {title!r} is not an 'N+' player-prop market") from None
    try:
        player_id: int | None = resolve_kalshi_player_name(player_name, aliases)
    except UnmatchedKalshiNameError:
        if unmatched is None:
            raise
        unmatched.append(player_name)
        player_id = None
    return {
        "ticker": raw["ticker"],
        "series_ticker": series,
        "event_ticker": raw.get("event_ticker"),
        "title": title,
        "player_id": player_id,
        "stat": stat,
        "threshold": threshold,
        "open_time": parse_ts(raw.get("open_time")),
        "close_time": parse_ts(raw.get("close_time")),
        "settled_ts": parse_ts(raw.get("settlement_ts") or raw.get("settled_ts")),
        "result": raw.get("result") or None,
    }


def parse_markets_frame(
    raw_markets: list[dict[str, Any]],
    *,
    aliases: dict[str, int] | None = None,
    include_game_markets: bool = False,
    unmatched: list[str] | None = None,
    unparsed: list[str] | None = None,
) -> pl.DataFrame:
    """Parse a list of raw ``Market`` JSON objects into a ``kalshi_markets`` frame.

    Non-player-prop markets (``SkippedMarket``) are silently excluded --
    expected noise in the feed, EXCEPT a market in a player-prop series (``SERIES_STAT``)
    whose title does not parse: that is a format change, not noise, so its ticker is appended
    to ``unparsed`` (when given) for the caller to surface. Unmatched player names are NOT
    caught here (propagate up, or are collected via ``unmatched``) per the "fail loudly"
    requirement.
    """
    rows: list[dict[str, Any]] = []
    for raw in raw_markets:
        try:
            rows.append(parse_market(raw, aliases=aliases, unmatched=unmatched))
            continue
        except SkippedMarket:
            pass
        if include_game_markets:
            try:
                rows.append(parse_game_market(raw))
                continue
            except SkippedMarket:
                pass
        if unparsed is not None and _series_of(raw) in SERIES_STAT:
            unparsed.append(str(raw.get("ticker")))
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(MARKETS_SCHEMA, pl.Null))
    return pl.DataFrame(rows, schema_overrides=_MARKET_DTYPES).select(MARKETS_SCHEMA)


_MARKET_DTYPES: dict[str, Any] = {
    "player_id": pl.Int64,
    "threshold": pl.Float64,
    "open_time": pl.Datetime,
    "close_time": pl.Datetime,
    "settled_ts": pl.Datetime,
}


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
    ts = parse_ts(ts_raw)
    return {
        "ticker": ticker,
        "ts": ts,
        "yes_bid": _price_field(raw, "yes_bid"),
        "yes_ask": _price_field(raw, "yes_ask"),
        "last": _price_field(raw, "price"),
        "volume": _count(raw, "volume_fp", "volume"),
        "open_interest": _count(raw, "open_interest_fp", "open_interest"),
        "source": source,
    }


def parse_market_price_row(raw: dict[str, Any], *, ts: datetime) -> dict[str, Any]:
    """Top-of-book snapshot of one live ``Market`` object -> ``kalshi_prices`` row.

    Uses ``yes_bid_dollars``/``yes_ask_dollars`` (the real touch, so the
    bid/ask spread survives for EV), ``last_price_dollars`` and the
    ``*_fp`` counts. A one-sided/empty book yields ``0.0000`` from Kalshi;
    that is kept verbatim (a 0 bid is a real quote), not turned into NULL.
    """
    return {
        "ticker": raw["ticker"],
        "ts": ts,
        "yes_bid": to_float(raw.get("yes_bid_dollars")),
        "yes_ask": to_float(raw.get("yes_ask_dollars")),
        "last": to_float(raw.get("last_price_dollars")),
        "volume": _count(raw, "volume_fp"),
        "open_interest": _count(raw, "open_interest_fp"),
        "source": "live",
    }


def parse_candlesticks_frame(
    raw_candles: list[dict[str, Any]], *, ticker: str, source: str
) -> pl.DataFrame:
    """Parse a list of raw candlestick JSON objects into a ``kalshi_prices`` frame."""
    rows = [parse_candlestick(c, ticker=ticker, source=source) for c in raw_candles]
    if not rows:
        return pl.DataFrame(schema=dict.fromkeys(PRICES_SCHEMA, pl.Null))
    return pl.DataFrame(rows, schema_overrides=_PRICE_DTYPES).select(PRICES_SCHEMA)


_PRICE_DTYPES: dict[str, Any] = {
    "yes_bid": pl.Float64,
    "yes_ask": pl.Float64,
    "last": pl.Float64,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "ts": pl.Datetime,
}
