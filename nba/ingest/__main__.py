"""CLI entrypoint: ``python -m nba.ingest <command> ...``.

Commands:
  games   --season 2023-24 [--season 2022-23 ...]
  boxscore --game-id 0022300001 [--game-id ...]
  pbp     --game-id 0022300001 [--game-id ...]

All commands are resumable and idempotent: already-cached (source, key)
pairs are skipped (never refetched). Network calls are rate-limited via
RateLimiter.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from nba.ingest.boxscores import pull_game_boxscore
from nba.ingest.cache import RateLimiter, open_db
from nba.ingest.games import pull_season_games
from nba.ingest.pbp import pull_game_pbp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba.ingest")
    parser.add_argument(
        "--rate-limit-s",
        type=float,
        default=0.6,
        help="minimum seconds between network calls (default 0.6)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    games_p = sub.add_parser("games", help="pull season game lists")
    games_p.add_argument("--season", action="append", required=True, dest="seasons")

    box_p = sub.add_parser("boxscore", help="pull per-game player box scores")
    box_p.add_argument("--game-id", action="append", required=True, dest="game_ids")

    pbp_p = sub.add_parser("pbp", help="pull per-game raw play-by-play")
    pbp_p.add_argument("--game-id", action="append", required=True, dest="game_ids")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    con = open_db()
    limiter = RateLimiter(min_interval_s=args.rate_limit_s)

    if args.command == "games":
        for season in args.seasons:
            df = pull_season_games(con, season, rate_limiter=limiter)
            print(f"games[{season}]: {len(df)} rows")
    elif args.command == "boxscore":
        for game_id in args.game_ids:
            df = pull_game_boxscore(con, game_id, rate_limiter=limiter)
            print(f"boxscore[{game_id}]: {len(df)} rows")
    elif args.command == "pbp":
        for game_id in args.game_ids:
            df = pull_game_pbp(con, game_id, rate_limiter=limiter)
            print(f"pbp[{game_id}]: {len(df)} rows")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
