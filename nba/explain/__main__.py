"""Two subcommands, both read-only.

``python -m nba.explain game --game-id <id> --db <db> --odds-db <odds db> --out <html>``
``python -m nba.explain slate --date <YYYY-MM-DD ET> --db <db> --out <md> --edition early|report``
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.explain.data import open_ro
from nba.explain.known import DEFAULT_FACTS_DB
from nba.explain.page import build_page
from nba.explain.render import render_page
from nba.explain.slate import EDITIONS, write_slate

CDT = ZoneInfo("America/Chicago")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nba.explain", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("game", help="write one self-contained HTML page for one game")
    g.add_argument("--game-id", "--id", dest="game_id", required=True)
    g.add_argument("--db", required=True, help="database with forward_predictions (read-only)")
    g.add_argument("--odds-db", default=None, help="odds_history database (read-only; optional)")
    g.add_argument(
        "--facts-db",
        default=DEFAULT_FACTS_DB,
        help="as-of fact store (read-only); default data/facts/facts.duckdb",
    )
    g.add_argument("--out", default=None, help="default reports/explain/<game_id>.html")
    s = sub.add_parser(
        "slate", help="write one plain-language Markdown briefing for an ET slate date"
    )
    s.add_argument("--date", required=True, type=date.fromisoformat, help="ET slate date")
    s.add_argument("--db", required=True, help="database with forward_predictions (read-only)")
    s.add_argument("--out", required=True, help="Markdown page to write")
    s.add_argument("--edition", required=True, choices=EDITIONS)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "slate":
        return write_slate(args.db, args.date, args.edition, args.out, datetime.now(tz=CDT))
    out = Path(args.out) if args.out else Path("reports/explain") / f"{args.game_id}.html"
    con = open_ro(args.db)
    odds = open_ro(args.odds_db) if args.odds_db else None
    try:
        page = build_page(
            con,
            odds,
            args.game_id,
            source_name=Path(args.db).name,
            facts_db=args.facts_db,
            with_known=True,
        )
    finally:
        con.close()
        if odds is not None:
            odds.close()
    if page is None:
        print(f"game {args.game_id} not found in {args.db}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(page), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
