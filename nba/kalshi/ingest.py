"""Orchestration: client + cutoff tiering + parsing -> ``kalshi_markets``/``kalshi_prices``.

This is the only module in ``nba/kalshi`` that touches both the network
client and the DuckDB connection; everything else (parsing, tiering,
sampling) is pure and independently testable. Mirrors the
fetch-cache-parse-load shape of ``nba.ingest.availability.pull_game_availability``.

No order/trading path is reachable from here -- ``KalshiClient`` only knows
how to call public market-data GETs (see its module docstring).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb

from nba.ingest.cache import DEFAULT_DATA_DIR, insert_rows, upsert_rows
from nba.kalshi.client import (
    SOURCE_CANDLES,
    SOURCE_CUTOFF,
    SOURCE_MARKETS,
    KalshiClient,
    fetch_cached_json,
)
from nba.kalshi.cutoff import CutoffInfo, parse_cutoff_response, select_tier
from nba.kalshi.parse import (
    MARKETS_SCHEMA,
    PRICES_SCHEMA,
    parse_candlesticks_frame,
    parse_markets_frame,
)


def get_cutoff(
    con: duckdb.DuckDBPyConnection, client: KalshiClient, *, data_dir: Path = DEFAULT_DATA_DIR
) -> CutoffInfo:
    """Fetch (or load cached) the live/historical boundary, cached per-day.

    Cached under today's date so a backtest re-run within the same day
    doesn't re-hit the network, but a new day gets a fresh cutoff (the
    boundary advances daily as more markets settle).
    """
    key = datetime.now().strftime("%Y-%m-%d")
    raw = fetch_cached_json(con, SOURCE_CUTOFF, key, client.fetch_cutoff, data_dir=data_dir)
    return parse_cutoff_response(raw)


def pull_markets_for_series(
    con: duckdb.DuckDBPyConnection,
    client: KalshiClient,
    series_ticker: str,
    *,
    as_of: datetime,
    cutoff: CutoffInfo,
    aliases: dict[str, int] | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> int:
    """Pull one series' markets page (tier chosen via ``as_of``/``cutoff``),
    parse "N+" player-prop markets, and idempotently upsert into
    ``kalshi_markets``. Returns the number of player-prop rows parsed.

    Idempotent: ``kalshi_markets.ticker`` is a primary key, so re-ingesting
    an already-seen ticker updates the row in place rather than
    duplicating it (``upsert_rows``) -- running this twice for the same
    series is always safe, matching CLAUDE.md's "idempotent" requirement.
    Non-player-prop markets in the same series (game winner/spread/total)
    are silently skipped by ``parse_markets_frame`` -- expected, not an
    error.
    """
    tier = select_tier(as_of, cutoff)
    key = f"{tier}:{series_ticker}"
    raw = fetch_cached_json(
        con,
        SOURCE_MARKETS,
        key,
        lambda: client.fetch_markets_page(tier=tier, series_ticker=series_ticker),
        data_dir=data_dir,
    )
    raw_markets = raw.get("markets", [])
    df = parse_markets_frame(raw_markets, aliases=aliases)
    if df.is_empty():
        return 0
    upsert_rows(con, "kalshi_markets", MARKETS_SCHEMA, ["ticker"], df)
    return len(df)


def pull_candlesticks_for_market(
    con: duckdb.DuckDBPyConnection,
    client: KalshiClient,
    ticker: str,
    series_ticker: str,
    *,
    start_ts: int,
    end_ts: int,
    as_of: datetime,
    cutoff: CutoffInfo,
    period_interval: int = 60,
    data_dir: Path = DEFAULT_DATA_DIR,
    tier: str | None = None,
) -> int:
    """Pull one market's candlesticks (tier chosen via ``as_of``/``cutoff``),
    parse into ``kalshi_prices`` rows tagged with the tier used, and
    idempotently reload them.

    ``kalshi_prices`` has no primary key (same convention as ``stints``/
    ``player_availability``): idempotency is enforced by deleting this
    ``(ticker, source)`` pair's rows before inserting the fresh pull,
    exactly like ``nba.ingest.availability._replace_rows``.
    """
    tier = tier or select_tier(as_of, cutoff)
    key = f"{tier}:{ticker}:{start_ts}:{end_ts}:{period_interval}"
    raw = fetch_cached_json(
        con,
        SOURCE_CANDLES,
        key,
        lambda: client.fetch_candlesticks(
            ticker,
            series_ticker,
            tier=tier,
            start_ts=start_ts,
            end_ts=end_ts,
            period_interval=period_interval,
        ),
        data_dir=data_dir,
    )
    raw_candles = raw.get("candlesticks", [])
    df = parse_candlesticks_frame(raw_candles, ticker=ticker, source=tier)
    con.execute("DELETE FROM kalshi_prices WHERE ticker = ? AND source = ?", [ticker, tier])
    if df.is_empty():
        return 0
    insert_rows(con, "kalshi_prices", PRICES_SCHEMA, df)
    return len(df)
