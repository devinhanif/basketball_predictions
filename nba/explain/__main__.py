"""``python -m nba.explain game --game-id <id> --db <db> --odds-db <odds db> --out <html>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from nba.explain.data import open_ro
from nba.explain.page import build_page
from nba.explain.render import render_page


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nba.explain", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("game", help="write one self-contained HTML page for one game")
    g.add_argument("--game-id", "--id", dest="game_id", required=True)
    g.add_argument("--db", required=True, help="database with forward_predictions (read-only)")
    g.add_argument("--odds-db", default=None, help="odds_history database (read-only; optional)")
    g.add_argument("--out", default=None, help="default reports/explain/<game_id>.html")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    out = Path(args.out) if args.out else Path("reports/explain") / f"{args.game_id}.html"
    con = open_ro(args.db)
    odds = open_ro(args.odds_db) if args.odds_db else None
    try:
        page = build_page(con, odds, args.game_id, source_name=Path(args.db).name)
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
