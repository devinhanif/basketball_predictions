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
    b.add_argument(
        "--seasons",
        default="",
        help="start years whose FULL facts load, e.g. 2022,2023,2024,2026; replaces --max-season",
    )
    b.add_argument(
        "--schedule-seasons",
        default="",
        help="start years, e.g. 2026: build ONLY tips_at/home_team/away_team for them",
    )
    b.add_argument(
        "--reporter-store",
        default="data/facts/reporter_facts.duckdb",
        help="write-once reporter facts, read in (never written); '' = leave them out",
    )
    args = p.parse_args(argv)
    sched = tuple(int(x) for x in args.schedule_seasons.split(",") if x.strip())
    seasons = tuple(int(x) for x in args.seasons.split(",") if x.strip())
    try:
        counts = build_from_paths(
            args.source,
            args.out,
            args.lineups,
            since=args.since,
            max_season=args.max_season,
            schedule_seasons=sched,
            seasons=seasons,
            reporter_store=args.reporter_store or None,
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
