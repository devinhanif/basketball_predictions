"""``python -m nba.facts build --source nba.duckdb --out data/facts/facts.duckdb``."""

from __future__ import annotations

import argparse
import sys

import duckdb

from nba.facts.build import build_from_paths


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nba.facts")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser(
        "build", help="derive the fact store from existing tables (drop and recreate)"
    )
    b.add_argument("--source", default="nba.duckdb")
    b.add_argument("--out", default="data/facts/facts.duckdb")
    b.add_argument("--lineups", default="data/lineups/lineups.duckdb")
    b.add_argument("--since", default=None, help="only games on/after YYYY-MM-DD")
    b.add_argument("--max-season", type=int, default=2024, help="holdout guard (default 2024)")
    args = p.parse_args(argv)
    try:
        counts = build_from_paths(
            args.source, args.out, args.lineups, since=args.since, max_season=args.max_season
        )
    except duckdb.IOException as e:
        print(f"LOCKED or unreadable: {e}", file=sys.stderr)
        return 2
    print("facts per predicate:")
    for k, v in counts["predicate"].items():
        print(f"  {k:<20} {v:>9,}")
    print("facts per source:")
    for k, v in counts["source"].items():
        print(f"  {k:<20} {v:>9,}")
    print(f"entities: {counts['entities']:,}")
    for note in counts["notes"]:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
