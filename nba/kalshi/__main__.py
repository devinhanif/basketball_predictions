"""CLI entrypoint: ``uv run python -m nba.kalshi <command> ...``.

Read-only by construction: every command ends in a call into
``nba.kalshi.client.KalshiClient``, which only exposes public market-data
GETs (no auth header; no order/portfolio path exists in this package).

The default target is a SEPARATE DuckDB file (``data/kalshi/kalshi.duckdb``,
gitignored) so snapshots never contend for ``nba.duckdb``'s single writer.
Override with ``--db``. Merge path: see docs/KALSHI_LIVE_2026-10-08.md.

Commands:
  discover                list NBA-related series tickers from /series
  cutoff                  fetch GET /historical/cutoff
  snapshot                open NBA markets -> kalshi_markets + kalshi_prices(live) + raw gz
  backfill-historical     settled markets (+ optional candles) from /historical/*
  alias-candidates        write review-only name->player_id candidates for unmatched names
  markets / candles       single-series / single-market pulls (original scaffold commands)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from nba.db.connect import connect
from nba.ingest.cache import RateLimiter
from nba.kalshi.aliases import load_reviewed_aliases, propose_alias_candidates
from nba.kalshi.client import DEFAULT_KALSHI_DB, KALSHI_DATA_DIR, KalshiClient
from nba.kalshi.ingest import get_cutoff, pull_candlesticks_for_market, pull_markets_for_series
from nba.kalshi.snapshot import run_backfill_historical, run_snapshot

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "kalshi.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba.kalshi")
    parser.add_argument("--db", default=str(DEFAULT_KALSHI_DB), help="target DuckDB file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--rate-limit-s", type=float, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="list NBA series tickers")
    sub.add_parser("cutoff", help="fetch the live/historical tier boundary")
    sub.add_parser("snapshot", help="snapshot open NBA markets + top-of-book prices")

    bf = sub.add_parser("backfill-historical", help="settled markets via /historical/*")
    bf.add_argument("--series", action="append", help="repeatable; default = config series")
    bf.add_argument("--max-pages", type=int, default=1)
    bf.add_argument("--page-limit", type=int, default=200)
    bf.add_argument("--candles", action="store_true", help="also pull 60-min candlesticks")
    bf.add_argument("--max-candle-markets", type=int, default=20)

    sub.add_parser("alias-candidates", help="review-only candidates for unmatched names")

    markets_p = sub.add_parser("markets", help="pull one series' markets")
    markets_p.add_argument("--series", required=True, dest="series_ticker")

    candles_p = sub.add_parser("candles", help="pull one market's candlesticks")
    candles_p.add_argument("--ticker", required=True)
    candles_p.add_argument("--series", required=True, dest="series_ticker")
    candles_p.add_argument("--start-ts", required=True, type=int)
    candles_p.add_argument("--end-ts", required=True, type=int)
    candles_p.add_argument("--period-interval", type=int, default=60)
    return parser


def _load_config(path: str) -> dict[str, Any]:
    loaded = yaml.safe_load(Path(path).read_text())
    assert isinstance(loaded, dict)
    return loaded


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _load_config(args.config)
    series: list[str] = list(cfg["game_series"]) + list(cfg["prop_series"])
    limiter = RateLimiter(min_interval_s=args.rate_limit_s or float(cfg["rate_limit_s"]))
    client = KalshiClient(rate_limiter=limiter)
    data_dir = Path(args.db).parent if args.db != ":memory:" else KALSHI_DATA_DIR
    if args.db != ":memory:":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    if args.command == "discover":
        listing = client.fetch_series_list()
        for s in sorted(listing.get("series", []), key=lambda x: x["ticker"]):
            if s["ticker"].startswith(("KXNBA", "KXNBL")) or "NBA" in s.get("title", "").upper():
                print(f"{s['ticker']}\t{s['title']}\t{s.get('frequency')}")
        return 0

    con = connect(args.db)
    aliases = load_reviewed_aliases()
    rc = 0
    now = datetime.now(UTC).replace(tzinfo=None)

    if args.command == "cutoff":
        cutoff = get_cutoff(con, client, data_dir=data_dir)
        print(f"market_settled_ts={cutoff.settled_markets_cutoff.isoformat()}")
    elif args.command == "snapshot":
        rep = run_snapshot(
            con,
            client,
            series=series,
            now=now,
            data_dir=data_dir,
            aliases=aliases,
            settle_lookback_days=int(cfg["settle_lookback_days"]),
            page_limit=int(cfg["page_limit"]),
        )
        print(
            f"snapshot {rep.snapshot_ts.isoformat()}Z "
            f"markets={sum(rep.markets_by_series.values())} "
            f"price_rows={rep.prices_rows} settled_updated={rep.settled_updated} "
            f"raw_files={rep.raw_files}"
        )
        print("per_series " + json.dumps({k: v for k, v in rep.markets_by_series.items() if v}))
        if rep.unmatched_names:
            unmatched_csv = ", ".join(sorted(rep.unmatched_names))
            print(
                f"{rep.snapshot_ts.isoformat()}Z UNMATCHED player names "
                f"({len(rep.unmatched_names)}): {unmatched_csv}; stored with player_id NULL; "
                "review via `alias-candidates` / `python -m nba.kalshi.alias_review`",
                file=sys.stderr,
            )
        if rep.unparsed_props:
            print(
                f"{rep.snapshot_ts.isoformat()}Z UNPARSED prop-series markets "
                f"({len(rep.unparsed_props)}, title format drift?): "
                f"{', '.join(rep.unparsed_props[:10])}",
                file=sys.stderr,
            )
        for s, err in rep.failures.items():
            print(f"{rep.snapshot_ts.isoformat()}Z FAILED {s}: {err}", file=sys.stderr)
        rc = 0 if rep.ok else 1
    elif args.command == "backfill-historical":
        cutoff = get_cutoff(con, client, data_dir=data_dir)
        brep = run_backfill_historical(
            con,
            client,
            cutoff,
            series=args.series or series,
            data_dir=data_dir,
            aliases=aliases,
            max_pages=args.max_pages,
            page_limit=args.page_limit,
            with_candles=args.candles,
            max_candle_markets=args.max_candle_markets,
        )
        print(
            f"backfill markets={sum(brep.markets_by_series.values())} "
            f"candle_rows={brep.candle_rows} unmatched_names={len(brep.unmatched_names)}"
        )
        print("per_series " + json.dumps({k: v for k, v in brep.markets_by_series.items() if v}))
        for s, err in brep.failures.items():
            print(f"FAILED {s}: {err}", file=sys.stderr)
        if brep.unmatched_names:
            path = data_dir / "unmatched_names.json"
            prior = json.loads(path.read_text()) if path.exists() else {}
            for name, n in brep.unmatched_names.items():
                prior[name] = max(prior.get(name, 0), n)
            path.write_text(json.dumps(prior, indent=1, sort_keys=True))
        rc = 1 if brep.failures else 0
    elif args.command == "alias-candidates":
        path = data_dir / "unmatched_names.json"
        names = sorted(json.loads(path.read_text())) if path.exists() else []
        cands = propose_alias_candidates(names)
        out = data_dir / "alias_candidates.json"
        out.write_text(json.dumps(cands, indent=1))
        unique = sum(1 for v in cands.values() if len(v) == 1)
        print(f"{len(names)} unmatched names; {unique} have exactly one candidate -> {out}")
    elif args.command == "markets":
        cutoff = get_cutoff(con, client, data_dir=data_dir)
        n = pull_markets_for_series(
            con, client, args.series_ticker, as_of=now, cutoff=cutoff, data_dir=data_dir
        )
        print(f"markets[{args.series_ticker}]: {n} player-prop rows parsed")
    elif args.command == "candles":
        cutoff = get_cutoff(con, client, data_dir=data_dir)
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
            data_dir=data_dir,
        )
        print(f"candles[{args.ticker}]: {n} rows parsed")

    con.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
