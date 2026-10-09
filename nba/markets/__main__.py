"""CLI: ``python -m nba.markets {capture,backfill} ...`` (read-only inputs; own output file)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from nba.markets.asof import open_read_only
from nba.markets.capture import (
    DEFAULT_KALSHI,
    DEFAULT_OUT,
    backfill_games,
    capture_forward,
    open_out,
)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.markets", description=__doc__)
    ap.add_argument("--nba-db", default="nba.duckdb")
    ap.add_argument("--kalshi-db", default=str(DEFAULT_KALSHI))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="market state at made_at for forward_predictions")
    c.add_argument(
        "--date", type=date.fromisoformat, default=None, help="ET slate date (default: all)"
    )
    b = sub.add_parser("backfill", help="game-market state for past games (19:00 ET tip proxy)")
    b.add_argument("--since", type=date.fromisoformat, default=date(2026, 10, 8))
    b.add_argument("--until", type=date.fromisoformat, default=None)
    a = ap.parse_args(argv)

    if not Path(a.kalshi_db).exists():
        print(f"kalshi db not found: {a.kalshi_db}", file=sys.stderr)
        return 1
    nba_con = open_read_only(
        a.nba_db, retries=40, wait_s=15.0
    )  # ~10 min: outlasts a pretip/T-30 write
    kal = open_read_only(a.kalshi_db, copy_fallback=True)
    out = open_out(a.out)
    try:
        if a.cmd == "capture":
            s = capture_forward(nba_con, kal, out, a.date)
        else:
            s = backfill_games(nba_con, kal, out, a.since, a.until)
    finally:
        out.close()
        kal.close()
        nba_con.close()
    print(s.line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
