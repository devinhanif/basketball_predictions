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
  national-tv    --date YYYY-MM-DD [--date ...]
                 --season 2023-24 [--season ...]   (resolves to every
                                                     distinct game_date for
                                                     the season already in
                                                     the games table)
                 Both forms may be combined; one nba_api call per date
                 covers every game played that day.
  availability   --game-id 0022300001 [--game-id ...]
                 --season 2023-24 [--season ...]   (bulk: every game_id for
                                                     the season already in
                                                     the games table)
                 Pulls nba_api's per-game InactivePlayers list into
                 player_availability (source='nba_inactive_list'). NOT a
                 forward-looking injury feed -- see
                 docs/INJURY_FEED_2026-10-08.md and the leakage-contract
                 comment on player_availability in nba/db/schema.sql.
  official-injury-report
                 --date YYYY-MM-DD --time HH:MM{AM,PM}  (e.g. --time 09:45AM)
                 Downloads/parses one official NBA injury-report PDF
                 snapshot into player_availability
                 (source='nba_official_report') -- the genuine
                 forward-looking source. Name resolution uses a merged
                 index: nba_api's static active-player list (precedence)
                 UNION whatever play-by-play has already been pulled (see
                 build_merged_name_index); fails loudly on any unmatched
                 player name. game_id is resolved from the report's own
                 team abbreviations against the games table (best-effort;
                 None if the game isn't loaded yet).

  postgame-fetch --source {tracking,hustle,officials,shots,matchups,coaches}
                 [--limit N]   Backfill post-game nba_api data for exactly the
                 games already in the games table into the parquet cache
                 (data/<source>/); progress is kept in a per-source sidecar
                 DuckDB so nba.duckdb is never write-locked during the pull.
  postgame-load  --source ... (repeatable; default all)  Load cached parquet
                 into nba.duckdb in short batched transactions, then print
                 coverage and the shot-vs-box-score FGA reconciliation.

History mode (older seasons, kept OUT of nba.duckdb so production refits are
unaffected): global options --db-path / --data-dir (or the shorthand --history,
= data/history/nba_history.duckdb + data/history/ caches) retarget every command;
defaults are unchanged. Per-game season loops have a circuit breaker
(3 consecutive failures -> 600 s cool-down, stop after 6 trips; rerun resumes).

All commands are resumable and idempotent: already-cached (source, key)
pairs are skipped (never refetched). Network calls are rate-limited via
RateLimiter.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from functools import partial
from pathlib import Path

from nba.ingest.availability import (
    build_merged_name_index,
    pull_game_availability,
    pull_official_injury_report,
)
from nba.ingest.boxscores import pull_game_boxscore
from nba.ingest.cache import (
    DEFAULT_DATA_DIR,
    CircuitBreaker,
    RateLimiter,
    is_cached,
    open_db,
    pull_ids_with_breaker,
)
from nba.ingest.games import game_ids_for_season, pull_season_games
from nba.ingest.national_tv import game_dates_for_season, pull_national_tv_for_date
from nba.ingest.pbp import pull_game_pbp
from nba.ingest.postgame import (
    SOURCES,
    coverage,
    fetch_source,
    load_source,
    shots_fga_reconciliation,
)
from nba.ingest.team_advanced import pull_game_team_advanced

HISTORY_DIR = DEFAULT_DATA_DIR / "history"
HISTORY_DB = HISTORY_DIR / "nba_history.duckdb"


def resolve_paths(args: argparse.Namespace) -> tuple[Path | None, Path]:
    """(db_path or None for the default nba.duckdb, data_dir). Defaults unchanged."""
    db_path: Path | None = args.db_path
    data_dir: Path = args.data_dir or DEFAULT_DATA_DIR
    if args.history:
        db_path = db_path or HISTORY_DB
        data_dir = args.data_dir or HISTORY_DIR
    return db_path, data_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nba.ingest")
    parser.add_argument(
        "--rate-limit-s",
        type=float,
        default=0.6,
        help="minimum seconds between network calls (default 0.6)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="target DuckDB (default: nba.duckdb). Use with --data-dir for older seasons.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="parquet cache root (default: data/). Keeps history caches apart.",
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=f"shorthand for --db-path {HISTORY_DB} --data-dir {HISTORY_DIR}",
    )
    parser.add_argument("--breaker-cooldown-s", type=float, default=600.0)
    parser.add_argument(
        "--breaker-cooldown-cap-s",
        type=float,
        default=3600.0,
        help="cool-downs escalate 600 -> 1200 -> 2400 -> cap (default 3600) per consecutive trip",
    )
    parser.add_argument("--breaker-max-trips", type=int, default=6)
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

    tv_p = sub.add_parser("national-tv", help="pull per-date national-TV broadcaster info")
    tv_p.add_argument("--date", action="append", dest="dates", help="YYYY-MM-DD")
    tv_p.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="pull every distinct game_date for this season from the games table",
    )

    avail_p = sub.add_parser(
        "availability", help="pull per-game inactive-player list (NOT forward-looking)"
    )
    avail_p.add_argument("--game-id", action="append", dest="game_ids")
    avail_p.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="pull every game_id for this season from the games table",
    )

    official_p = sub.add_parser(
        "official-injury-report",
        help="download/parse one official NBA injury-report PDF (forward-looking)",
    )
    official_p.add_argument("--date", required=True, help="report date, YYYY-MM-DD")
    official_p.add_argument("--time", required=True, help="report time, HH:MMAM/PM e.g. 09:45AM")

    pf_p = sub.add_parser("postgame-fetch", help="fetch post-game sources into the parquet cache")
    pf_p.add_argument("--source", required=True, choices=SOURCES)
    pf_p.add_argument("--limit", type=int, default=None, help="only the first N keys (sampling)")
    pf_p.add_argument(
        "--seasons",
        type=int,
        action="append",
        default=None,
        help="restrict game-keyed sources to these season ints (e.g. 2022); default all",
    )
    pf_p.add_argument(
        "--newest-first",
        action="store_true",
        help="fetch the newest season's games first (history backfills)",
    )

    pl_p = sub.add_parser("postgame-load", help="load cached post-game parquet into nba.duckdb")
    pl_p.add_argument("--source", action="append", dest="sources", choices=SOURCES)

    return parser


def _parse_report_dt(date_str: str, time_str: str) -> datetime:
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %I:%M%p")


def _postgame_main(args: argparse.Namespace) -> int:
    from nba.db.connect import DEFAULT_DB_PATH

    db_path, data_dir = resolve_paths(args)
    if args.command == "postgame-fetch":
        # Fetch phase never opens the scope DB for writing (read-only scope query only).
        summary = fetch_source(
            args.source,
            db_path or DEFAULT_DB_PATH,
            data_dir=data_dir,
            rate_limiter=RateLimiter(min_interval_s=args.rate_limit_s),
            limit=args.limit,
            newest_first=args.newest_first,
            seasons=args.seasons,
            breaker_cooldown_s=args.breaker_cooldown_s,
            breaker_cooldown_cap_s=args.breaker_cooldown_cap_s,
            breaker_max_trips=args.breaker_max_trips,
        )
        print(
            f"postgame-fetch[{summary.source}]: {summary.total} keys, {summary.cached} cached, "
            f"{summary.fetched} fetched, {len(summary.failed)} failed"
        )
        return 1 if summary.failed else 0
    con = open_db(db_path)  # short write window: load cached parquet only
    try:
        for source in args.sources or SOURCES:
            print(f"postgame-load[{source}]: {load_source(con, source, data_dir=data_dir)} rows")
        for table, season, games, rows in coverage(con):
            print(f"coverage {table} season={season} games={games} rows={rows}")
        print("shots_fga_reconciliation", shots_fga_reconciliation(con))
    finally:
        con.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("postgame-fetch", "postgame-load"):
        return _postgame_main(args)
    db_path, data_dir = resolve_paths(args)
    con = open_db() if db_path is None else open_db(db_path)
    limiter = RateLimiter(min_interval_s=args.rate_limit_s)
    failed_any = False

    if args.command == "games":
        for season in args.seasons:
            df = pull_season_games(con, season, data_dir=data_dir, rate_limiter=limiter)
            print(f"games[{season}]: {len(df)} rows")
    elif args.command in ("boxscore", "pbp", "team-advanced", "availability"):
        game_ids = getattr(args, "game_ids", None) or []
        seasons = getattr(args, "seasons", None) or []
        if not game_ids and not seasons:
            build_parser().error(f"{args.command}: provide --game-id and/or --season")
        pull_fns = {
            "boxscore": pull_game_boxscore,
            "pbp": pull_game_pbp,
            "team-advanced": pull_game_team_advanced,
            "availability": pull_game_availability,
        }
        pull_fn = pull_fns[args.command]

        for game_id in game_ids:
            df = pull_fn(con, game_id, data_dir=data_dir, rate_limiter=limiter)
            print(f"{args.command}[{game_id}]: {len(df)} rows")

        for season in seasons:
            season_ids = game_ids_for_season(con, season)
            loop = pull_ids_with_breaker(
                con,
                args.command,
                season_ids,
                partial(pull_fn, con, data_dir=data_dir, rate_limiter=limiter),
                breaker=CircuitBreaker(
                    cooldown_s=args.breaker_cooldown_s,
                    cooldown_cap_s=args.breaker_cooldown_cap_s,
                    max_trips=args.breaker_max_trips,
                ),
                label=f"season={season}",
            )
            print(
                f"{args.command}[season {season}]: {loop.total} games, "
                f"{loop.cached} cached, {loop.fetched} fetched, {len(loop.failed)} failed"
                f"{' (stopped early: breaker)' if loop.stopped_early else ''}"
            )
            failed_any = failed_any or bool(loop.failed)
    elif args.command == "official-injury-report":
        report_dt = _parse_report_dt(args.date, args.time)
        name_index = build_merged_name_index()
        df = pull_official_injury_report(
            con, report_dt, name_index=name_index, rate_limiter=limiter
        )
        print(f"official-injury-report[{report_dt.isoformat()}]: {len(df)} rows")
    elif args.command == "national-tv":
        dates = getattr(args, "dates", None) or []
        seasons = getattr(args, "seasons", None) or []
        if not dates and not seasons:
            build_parser().error("national-tv: provide --date and/or --season")

        for date in dates:
            df = pull_national_tv_for_date(con, date, data_dir=data_dir, rate_limiter=limiter)
            print(f"national-tv[{date}]: {len(df)} rows")

        for season in seasons:
            season_dates = game_dates_for_season(con, season)
            cached_count = sum(1 for d in season_dates if is_cached(con, "national-tv", d))
            fetched_count = 0
            for d in season_dates:
                was_cached = is_cached(con, "national-tv", d)
                pull_national_tv_for_date(con, d, data_dir=data_dir, rate_limiter=limiter)
                if not was_cached:
                    fetched_count += 1
            print(
                f"national-tv[season {season}]: {len(season_dates)} dates, "
                f"{cached_count} cached, {fetched_count} fetched"
            )

    con.close()
    return 1 if failed_any else 0


if __name__ == "__main__":
    sys.exit(main())
