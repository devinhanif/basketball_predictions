"""``python -m nba.export build --date <d> --out <dir>``  (read-only against nba.duckdb)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

from nba.db.connect import DEFAULT_DB_PATH, is_lock_error
from nba.export import guard
from nba.export.build import REPO_ROOT, build_export

DEFAULT_ODDS_DB = REPO_ROOT / "data" / "markets" / "market_asof.duckdb"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.export", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write export.duckdb, parlay_of_day.json, manifest.json")
    b.add_argument("--date", required=True, type=date.fromisoformat, help="Eastern slate date")
    b.add_argument("--out", required=True, type=Path, help="output directory")
    b.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="source nba.duckdb (read-only)")
    b.add_argument("--lookback-days", type=int, default=7)
    b.add_argument("--box-games", type=int, default=82, help="own last N games per slate player")
    b.add_argument("--box-days", type=int, default=365, help="and every row this many days back")
    b.add_argument("--season", type=int, action="append", help="default 2026; 2025 is refused")
    b.add_argument(
        "--odds-db",
        type=Path,
        default=DEFAULT_ODDS_DB,
        help="live odds capture store (read-only); absent file = no lines",
    )
    b.add_argument(
        "--roster-dir",
        type=Path,
        default=REPO_ROOT / "data" / "rosters",
        help="official pre-tip roster cache (<dir>/<date>/<team>.parquet); places players with "
        "no earlier 2026 row",
    )
    b.add_argument("--parlay-dir", type=Path, default=REPO_ROOT / "reports" / "parlay")
    a = ap.parse_args(argv)
    try:
        m = build_export(
            a.db, a.out, a.date, lookback_days=a.lookback_days,
            box_games=a.box_games, box_days=a.box_days, seasons=a.season,
            parlay_dir=a.parlay_dir, odds_db=a.odds_db, roster_dir=a.roster_dir,
        )  # fmt: skip
    except guard.ExportRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3
    except duckdb.IOException as exc:
        print(f"{'LOCKED' if is_lock_error(exc) else 'IO error'}: {exc}", file=sys.stderr)
        return 2
    print(
        f"export {m['export_date']}: {m['row_counts']} parlay={m['parlay']['verdict']} "
        f"lines={m['lines']['status']}"
    )
    for n, f in m["files"].items():
        print(f"  {n} sha256={f['sha256'][:12]} bytes={f['bytes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
