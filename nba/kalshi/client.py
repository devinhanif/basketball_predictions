"""Read-only Kalshi public-market-data client.

Hard guarantees (CLAUDE.md Phase 3 + the no-auth/no-orders rule that
overrides everything else in this repo):
  - Every request in this module is a plain ``httpx.get`` with no
    ``Authorization``/API-key header, ever. No function in this module
    constructs a request to any trading/order/portfolio path.
  - ``_LIVE_PATHS`` / ``_HISTORICAL_PATHS`` below are the only paths this
    client knows how to call, and all of them are public market-data reads
    (markets, candlesticks, series, the cutoff endpoint itself). There is
    no ``place_order``/``cancel_order``/``portfolio`` method and there
    never will be in this module.
  - ``httpx`` is imported lazily inside functions (not at module import
    time) so this module -- and everything that imports it for parsing/
    testing -- stays importable with zero network access, mirroring
    ``nba.ingest.*``'s lazy ``nba_api`` imports.

Base URLs: Kalshi's documented production base is
``https://api.elections.kalshi.com/trade-api/v2`` (an alias,
``external-api.kalshi.com``, is also documented as supported). Despite the
"elections" subdomain this serves all Kalshi markets, not just elections
ones. Most REST market-data reads (series/events/markets/candlesticks) are
public and require no authentication.

Caching/resumability mirrors ``nba.ingest.cache.fetch_cached`` exactly (the
exemplar CLAUDE.md names explicitly): raw JSON is written once per
``(source, key)`` under ``data/kalshi/raw/`` and logged 'done' in the
shared ``ingest_log`` table, so a crashed/interrupted pull picks up where
it left off and never re-hits the network for a key it already has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from nba.ingest.cache import (
    DEFAULT_DATA_DIR,
    RateLimiter,
    is_cached,
    mark_done,
    mark_failed,
)

LIVE_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
HISTORICAL_BASE_URL = f"{LIVE_BASE_URL}/historical"

SOURCE_CUTOFF = "kalshi-cutoff"
SOURCE_MARKETS = "kalshi-markets"
SOURCE_CANDLES = "kalshi-candles"

_RAW_DIR_NAME = "raw"
_MAX_ATTEMPTS = 4

#: Kalshi-specific data root (gitignored via /data/): raw JSON + the dedicated DuckDB file.
KALSHI_DATA_DIR = DEFAULT_DATA_DIR / "kalshi"
DEFAULT_KALSHI_DB = KALSHI_DATA_DIR / "kalshi.duckdb"


def _raw_path(source: str, key: str, data_dir: Path) -> Path:
    safe_key = key.replace("/", "_")
    return data_dir / _RAW_DIR_NAME / source / f"{safe_key}.json"


class KalshiClient:
    """Thin, read-only wrapper over Kalshi's public market-data REST API.

    No constructor argument accepts an API key/credential -- this client
    cannot authenticate even if a caller wanted it to.
    """

    def __init__(
        self,
        *,
        live_base_url: str = LIVE_BASE_URL,
        historical_base_url: str = HISTORICAL_BASE_URL,
        rate_limiter: RateLimiter | None = None,
        timeout_s: float = 10.0,
        retry_backoff_s: float = 1.0,
    ) -> None:
        self.live_base_url = live_base_url
        self.historical_base_url = historical_base_url
        self.rate_limiter = rate_limiter or RateLimiter(min_interval_s=0.2)
        self.timeout_s = timeout_s
        self.retry_backoff_s = retry_backoff_s

    @staticmethod
    def _sleep(seconds: float) -> None:
        import time

        time.sleep(seconds)

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plain unauthenticated GET. No headers besides httpx's defaults."""
        import httpx  # lazy: keep this module network-import-free otherwise

        response = None
        for attempt in range(_MAX_ATTEMPTS):  # retry 429/5xx and transient transport errors
            self.rate_limiter.wait()
            try:
                response = httpx.get(url, params=params, timeout=self.timeout_s)
            except httpx.TransportError:
                # ConnectError / RemoteProtocolError / timeouts: a dropped connection is not a
                # reason to lose a series for 15 minutes. Re-raise after the last attempt.
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
                self._sleep(self.retry_backoff_s * (attempt + 1))
                continue
            if response.status_code != 429 and response.status_code < 500:
                break
            self._sleep(2.0 * (attempt + 1))
        assert response is not None
        response.raise_for_status()
        result = response.json()
        assert isinstance(result, dict)
        return result

    def fetch_cutoff(self) -> dict[str, Any]:
        """``GET /historical/cutoff`` -- the tier-boundary endpoint, no auth."""
        return self._get(f"{self.historical_base_url}/cutoff")

    def fetch_markets_page(
        self,
        *,
        tier: str,
        series_ticker: str | None = None,
        cursor: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        min_settled_ts: int | None = None,
    ) -> dict[str, Any]:
        """One page of ``GET /markets`` (live) or ``GET /historical/markets``.

        ``tier`` must be ``'live'`` or ``'historical'`` -- callers decide
        the tier via ``nba.kalshi.cutoff.select_tier``, this method just
        routes to the matching base URL. Pagination follows Kalshi's
        ``cursor`` convention (opaque token in the response, echoed back on
        the next call); this method fetches one page only, callers loop.
        """
        base = self.live_base_url if tier == "live" else self.historical_base_url
        params: dict[str, Any] = {}
        if series_ticker is not None:
            params["series_ticker"] = series_ticker
        if cursor:
            params["cursor"] = cursor
        if status is not None:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if min_settled_ts is not None:
            params["min_settled_ts"] = min_settled_ts
        return self._get(f"{base}/markets", params=params)

    def fetch_series_list(self, category: str = "Sports") -> dict[str, Any]:
        """``GET /series?category=...`` (public) -- used for ticker discovery."""
        return self._get(f"{self.live_base_url}/series", params={"category": category})

    def fetch_candlesticks(
        self,
        ticker: str,
        series_ticker: str,
        *,
        tier: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> dict[str, Any]:
        """``GET /markets/{ticker}/candlesticks`` (live) or the historical
        equivalent. ``period_interval`` is minutes per candle (1, 60, or
        1440 per Kalshi's documented valid values)."""
        # Verified live 2026-10-08: the live tier nests under /series/{s}/markets/{t};
        # the historical tier is /historical/markets/{t}/candlesticks (no series).
        if tier == "live":
            url = f"{self.live_base_url}/series/{series_ticker}/markets/{ticker}/candlesticks"
        else:
            url = f"{self.historical_base_url}/markets/{ticker}/candlesticks"
        params = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "period_interval": period_interval,
        }
        return self._get(url, params=params)


def fetch_cached_json(
    con: duckdb.DuckDBPyConnection,
    source: str,
    key: str,
    fetch_fn: Any,
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
) -> dict[str, Any]:
    """Resumable/idempotent raw-JSON cache for Kalshi responses.

    Same contract as ``nba.ingest.cache.fetch_cached`` but for a raw JSON
    dict instead of a parquet-backed DataFrame (Kalshi responses are
    nested/paginated JSON, not a flat row table, so the parquet-oriented
    helper doesn't fit directly -- this is the JSON-shaped sibling of the
    same pattern: check ``ingest_log``, skip the network call if already
    'done', write the raw snapshot before marking done, mark 'failed' (not
    'done') on any exception so a retry doesn't skip a response that never
    actually landed).
    """
    path = _raw_path(source, key, data_dir)
    if is_cached(con, source, key):
        result = json.loads(path.read_text())
        assert isinstance(result, dict)
        return result

    try:
        result = fetch_fn()
    except Exception:
        mark_failed(con, source, key)
        raise
    assert isinstance(result, dict)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    mark_done(con, source, key, path)
    return result
