"""Market-as-a-feature plumbing (read-only): strict as-of Kalshi state and forward capture.

No orders, no credentials, no authenticated endpoints. Everything here reads the local
``kalshi.duckdb`` (public market data already ingested by ``nba.kalshi``) and writes only to its
own file, ``data/markets/market_asof.duckdb``. Design and pre-registration: docs/MARKET_FEATURE.md.
"""
