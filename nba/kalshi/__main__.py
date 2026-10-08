"""CLI entrypoint: ``python -m nba.kalshi <command> ...``.

Read-only by construction: every command below ends in a call into
``nba.kalshi.client.KalshiClient``, which only exposes public
market-data GETs (no auth header, no order/portfolio path exists in this
package -- see ``client.py``'s module docstring). There is deliberately no
``--live`` flag that this scaffold wires up to an automatic run; commands
only execute network calls when a human explicitly invokes them.

Commands:
  cutoff                         fetch (or load cached) GET /historical/cutoff
  markets   --series TICKER      pull one series' markets into kalshi_markets
  candles   --ticker T --series S --start-ts N --end-ts N
                                  pull one market's candlesticks into kalshi_prices

All commands are resumable/idempotent (raw JSON cached under
data/kalshi_raw/, DuckDB rows upserted/replaced -- see ingest.py).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from nba.ingest.cache import RateLimiter, open_db
from nba.kalshi.client import KalshiClient
from nba.kalshi.ingest import get_cutoff, pull_candlesticks_for_market, pull_markets_for_series


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba.kalshi")
    parser.add_argument("--rate-limit-s", type=float, default=0.2)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("cutoff", help="fetch the live/historical tier boundary")

    markets_p = sub.add_parser("markets", help="pull one series' markets")
    markets_p.add_argument("--series", required=True, dest="series_ticker")

    candles_p = sub.add_parser("candles", help="pull one market's candlesticks")
    candles_p.add_argument("--ticker", required=True)
    candles_p.add_argument("--series", required=True, dest="series_ticker")
    candles_p.add_argument("--start-ts", required=True, type=int)
    candles_p.add_argument("--end-ts", required=True, type=int)
    candles_p.add_argument("--period-interval", type=int, default=60)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    con = open_db()
    limiter = RateLimiter(min_interval_s=args.rate_limit_s)
    client = KalshiClient(rate_limiter=limiter)
    now = datetime.now(UTC).replace(tzinfo=None)

    if args.command == "cutoff":
        cutoff = get_cutoff(con, client)
        print(f"settled_markets_cutoff={cutoff.settled_markets_cutoff.isoformat()}")
    elif args.command == "markets":
        cutoff = get_cutoff(con, client)
        n = pull_markets_for_series(con, client, args.series_ticker, as_of=now, cutoff=cutoff)
        print(f"markets[{args.series_ticker}]: {n} player-prop rows parsed")
    elif args.command == "candles":
        cutoff = get_cutoff(con, client)
        n = pull_candlesticks_for_market(
            con,
            client,
            args.ticker,
            args.series_ticker,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
            as_of=now,
            cutoff=cutoff,
            period_interval=args.period_interval,
        )
        print(f"candles[{args.ticker}]: {n} rows parsed")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
