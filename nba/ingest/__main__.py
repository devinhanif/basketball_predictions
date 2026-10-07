"""CLI entrypoint: ``python -m nba.ingest <command> ...``.

Commands:
  games          --season 2023-24 [--season 2022-23 ...]
  boxscore       --game-id 0022300001 [--game-id ...]
                 --season 2023-24 [--season ...]   (bulk: every game_id for
                                                     the season already in
                                                     the games table)
                 Both forms may be combined; duplicates are de-duplicated.
  pbp            same two forms as boxscore.
  team-advanced  same two forms as boxscore (per-team advanced box score:
                 offensive/defensive rating, pace, four factors).

All commands are resumable and idempotent: already-cached (source, key)
pairs are skipped (never refetched). Network calls are rate-limited via
RateLimiter.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from nba.ingest.boxscores import pull_game_boxscore
from nba.ingest.cache import RateLimiter, is_cached, open_db
from nba.ingest.games import game_ids_for_season, pull_season_games
from nba.ingest.pbp import pull_game_pbp
from nba.ingest.team_advanced import pull_game_team_advanced


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
    box_p.add_argument("--game-id", action="append", dest="game_ids")
    box_p.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="pull every game_id for this season from the games table",
    )

    pbp_p = sub.add_parser("pbp", help="pull per-game raw play-by-play")
    pbp_p.add_argument("--game-id", action="append", dest="game_ids")
    pbp_p.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="pull every game_id for this season from the games table",
    )

    team_adv_p = sub.add_parser(
        "team-advanced", help="pull per-team-per-game advanced box score stats"
    )
    team_adv_p.add_argument("--game-id", action="append", dest="game_ids")
    team_adv_p.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="pull every game_id for this season from the games table",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    con = open_db()
    limiter = RateLimiter(min_interval_s=args.rate_limit_s)

    if args.command == "games":
        for season in args.seasons:
            df = pull_season_games(con, season, rate_limiter=limiter)
            print(f"games[{season}]: {len(df)} rows")
    elif args.command in ("boxscore", "pbp", "team-advanced"):
        game_ids = getattr(args, "game_ids", None) or []
        seasons = getattr(args, "seasons", None) or []
        if not game_ids and not seasons:
            build_parser().error(f"{args.command}: provide --game-id and/or --season")
        pull_fns = {
            "boxscore": pull_game_boxscore,
            "pbp": pull_game_pbp,
            "team-advanced": pull_game_team_advanced,
        }
        pull_fn = pull_fns[args.command]

        for game_id in game_ids:
            df = pull_fn(con, game_id, rate_limiter=limiter)
            print(f"{args.command}[{game_id}]: {len(df)} rows")

        for season in seasons:
            season_ids = game_ids_for_season(con, season)
            cached_count = sum(1 for gid in season_ids if is_cached(con, args.command, gid))
            fetched_count = 0
            for gid in season_ids:
                was_cached = is_cached(con, args.command, gid)
                pull_fn(con, gid, rate_limiter=limiter)
                if not was_cached:
                    fetched_count += 1
            print(
                f"{args.command}[season {season}]: {len(season_ids)} games, "
                f"{cached_count} cached, {fetched_count} fetched"
            )

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
