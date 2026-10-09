"""Live snapshot + historical backfill for NBA Kalshi markets (read-only).

Public GETs only. Two entry points:

* ``run_snapshot`` -- list OPEN markets for every configured NBA series
  (game winner / spread / total + player props), upsert ``kalshi_markets``,
  append one top-of-book row per market to ``kalshi_prices`` (``source='live'``),
  and sweep recently SETTLED markets so ``result`` / ``settled_ts`` get filled.
  Every raw page is also written, gzipped, to
  ``<data_dir>/raw/<YYYY-MM-DD>/<HHMMSS>_<series>_<status>_<n>.json.gz``
  so any snapshot can be replayed byte-for-byte.
* ``run_backfill_historical`` -- page ``/historical/markets`` for settled
  markets, optionally their ``/historical/markets/{t}/candlesticks``.
  Resumable: each page / candle pull is cached via ``fetch_cached_json``
  (a re-run makes zero network calls for keys already ``done``).

Failure isolation: one series failing (HTTP error) is recorded in the
report and the run continues; the CLI exits non-zero if anything failed or
any player name was unmatched. Unmatched names are NOT dropped: the market
and its prices are stored with ``player_id`` NULL (so history is not lost
while the alias table catches up) and the names are written to
``<data_dir>/unmatched_names.json`` and printed to stderr.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.ingest.cache import insert_rows, upsert_rows
from nba.kalshi.client import (
    KALSHI_DATA_DIR,
    SOURCE_MARKETS,
    KalshiClient,
    fetch_cached_json,
)
from nba.kalshi.cutoff import CutoffInfo
from nba.kalshi.ingest import pull_candlesticks_for_market
from nba.kalshi.parse import (
    _PRICE_DTYPES,
    MARKETS_SCHEMA,
    PRICES_SCHEMA,
    parse_market_price_row,
    parse_markets_frame,
)

MAX_PAGES = 50  # hard stop against a cursor that never terminates


@dataclass
class SnapshotReport:
    snapshot_ts: datetime
    markets_by_series: dict[str, int] = field(default_factory=dict)
    prices_rows: int = 0
    settled_updated: int = 0
    unmatched_names: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    raw_files: int = 0
    #: prop-series tickers whose title did not parse (format drift); never dropped silently
    unparsed_props: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.unmatched_names and not self.unparsed_props


def write_raw(raw_root: Path, now: datetime, name: str, payload: dict[str, Any]) -> Path:
    """Gzip one raw response under ``raw/<date>/``; returns the path."""
    day_dir = raw_root / "raw" / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{now.strftime('%H%M%S')}_{name}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def _iter_pages(
    client: KalshiClient, *, tier: str, series: str, status: str | None, page_limit: int, **kw: Any
) -> Any:
    cursor: str | None = None
    for n in range(MAX_PAGES):
        page = client.fetch_markets_page(
            tier=tier, series_ticker=series, cursor=cursor, status=status, limit=page_limit, **kw
        )
        yield n, page
        cursor = page.get("cursor") or None
        if not cursor:
            return


def _replace_prices(con: duckdb.DuckDBPyConnection, ts: datetime, df: pl.DataFrame) -> None:
    """Idempotent per (ticker, ts, source): re-ingesting the same snapshot replaces."""
    if df.is_empty():
        return
    con.executemany(
        "DELETE FROM kalshi_prices WHERE ticker = ? AND ts = ? AND source = 'live'",
        [(t, ts) for t in df["ticker"].to_list()],
    )
    insert_rows(con, "kalshi_prices", PRICES_SCHEMA, df)


def _upsert_markets(con: duckdb.DuckDBPyConnection, df: pl.DataFrame) -> None:
    upsert_rows(con, "kalshi_markets", MARKETS_SCHEMA, ["ticker"], df)


def run_snapshot(
    con: duckdb.DuckDBPyConnection,
    client: KalshiClient,
    *,
    series: list[str],
    now: datetime | None = None,
    data_dir: Path = KALSHI_DATA_DIR,
    aliases: dict[str, int] | None = None,
    settle_lookback_days: int = 3,
    page_limit: int = 1000,
) -> SnapshotReport:
    now = (now or datetime.now(UTC).replace(tzinfo=None)).replace(microsecond=0)
    report = SnapshotReport(snapshot_ts=now)
    unmatched: list[str] = []
    unparsed: list[str] = []
    min_settled = int((now - timedelta(days=settle_lookback_days)).replace(tzinfo=UTC).timestamp())

    for s in series:
        n_markets = 0
        try:
            for page_no, page in _iter_pages(
                client, tier="live", series=s, status="open", page_limit=page_limit
            ):
                write_raw(data_dir, now, f"{s}_open_{page_no}", page)
                report.raw_files += 1
                raw_markets = page.get("markets", [])
                mdf = parse_markets_frame(
                    raw_markets,
                    aliases=aliases,
                    include_game_markets=True,
                    unmatched=unmatched,
                    unparsed=unparsed,
                )
                if mdf.is_empty():
                    continue
                kept = set(mdf["ticker"].to_list())
                _upsert_markets(con, mdf)
                price_rows = [
                    parse_market_price_row(m, ts=now) for m in raw_markets if m["ticker"] in kept
                ]
                pdf = pl.DataFrame(price_rows, schema_overrides=_PRICE_DTYPES).select(PRICES_SCHEMA)
                _replace_prices(con, now, pdf)
                n_markets += len(mdf)
                report.prices_rows += len(pdf)
            # results sweep: markets that settled recently on the live tier
            for page_no, page in _iter_pages(
                client,
                tier="live",
                series=s,
                status="settled",
                page_limit=page_limit,
                min_settled_ts=min_settled,
            ):
                write_raw(data_dir, now, f"{s}_settled_{page_no}", page)
                report.raw_files += 1
                sdf = parse_markets_frame(
                    page.get("markets", []),
                    aliases=aliases,
                    include_game_markets=True,
                    unmatched=unmatched,
                    unparsed=unparsed,
                )
                if not sdf.is_empty():
                    _upsert_markets(con, sdf)
                    report.settled_updated += len(sdf)
        except Exception as exc:  # isolate per-series failure; surfaced in report/exit code
            report.failures[s] = f"{type(exc).__name__}: {exc}"
        report.markets_by_series[s] = n_markets

    report.unparsed_props = sorted(set(unparsed))
    for name in unmatched:
        report.unmatched_names[name] = report.unmatched_names.get(name, 0) + 1
    if report.unmatched_names:
        path = data_dir / "unmatched_names.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        prior = json.loads(path.read_text()) if path.exists() else {}
        for name in report.unmatched_names:
            prior[name] = prior.get(name, 0) + 1
        path.write_text(json.dumps(prior, indent=1, sort_keys=True))
    return report


@dataclass
class BackfillReport:
    markets_by_series: dict[str, int] = field(default_factory=dict)
    candle_rows: int = 0
    unmatched_names: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


def run_backfill_historical(
    con: duckdb.DuckDBPyConnection,
    client: KalshiClient,
    cutoff: CutoffInfo,
    *,
    series: list[str],
    data_dir: Path = KALSHI_DATA_DIR,
    aliases: dict[str, int] | None = None,
    max_pages: int = 1,
    page_limit: int = 200,
    with_candles: bool = False,
    max_candle_markets: int = 20,
    period_interval: int = 60,
) -> BackfillReport:
    """Backfill settled markets (and optionally 60-min candles) from ``/historical/*``.

    ``max_pages`` bounds the work per series (default 1 page = a small sample);
    raise it for a full backfill. Idempotent and resumable: pages and candle
    pulls are cached by key, markets upsert on ``ticker``, and candle rows are
    replaced per ``(ticker, 'historical')``.
    """
    report = BackfillReport()
    unmatched: list[str] = []
    candle_budget = max_candle_markets
    for s in series:
        cursor: str | None = None
        n = 0
        try:
            for _ in range(min(max_pages, MAX_PAGES)):
                key = f"hist:{s}:{cursor or 'first'}:{page_limit}"
                page = fetch_cached_json(
                    con,
                    SOURCE_MARKETS,
                    key,
                    lambda c=cursor, ser=s: client.fetch_markets_page(
                        tier="historical", series_ticker=ser, cursor=c, limit=page_limit
                    ),
                    data_dir=data_dir,
                )
                raw_markets = page.get("markets", [])
                mdf = parse_markets_frame(
                    raw_markets, aliases=aliases, include_game_markets=True, unmatched=unmatched
                )
                if not mdf.is_empty():
                    _upsert_markets(con, mdf)
                    n += len(mdf)
                if with_candles:
                    for m in raw_markets:
                        if candle_budget <= 0:
                            break
                        o, c = m.get("open_time"), m.get("close_time")
                        if not (o and c):
                            continue
                        t0 = int(datetime.fromisoformat(o).timestamp())
                        t1 = int(datetime.fromisoformat(c).timestamp())
                        report.candle_rows += pull_candlesticks_for_market(
                            con,
                            client,
                            m["ticker"],
                            s,
                            start_ts=t0,
                            end_ts=t1,
                            as_of=datetime(1970, 1, 1),
                            cutoff=cutoff,
                            period_interval=period_interval,
                            data_dir=data_dir,
                            tier="historical",
                        )
                        candle_budget -= 1
                cursor = page.get("cursor") or None
                if not cursor:
                    break
        except Exception as exc:
            report.failures[s] = f"{type(exc).__name__}: {exc}"
        report.markets_by_series[s] = n
    for name in unmatched:
        report.unmatched_names[name] = report.unmatched_names.get(name, 0) + 1
    return report


__all__ = [
    "BackfillReport",
    "SnapshotReport",
    "run_backfill_historical",
    "run_snapshot",
    "write_raw",
]
