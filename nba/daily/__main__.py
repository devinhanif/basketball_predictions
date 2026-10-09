"""CLI: ``python -m nba.daily {run,settle,report} ...``.

``run`` writes to the real ``nba.duckdb`` (single writer -- do not run while an
eval holds it). Network access only in ``run`` (schedule, games, box scores,
injury PDFs); never any market/execution code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from nba.daily.pipeline import RunSummary, run_daily, utcnow
from nba.daily.report import build_report
from nba.daily.schedule import fetch_schedule_nba_api, schedule_from_db
from nba.daily.settle import settle_pending
from nba.db.connect import DEFAULT_DB_PATH, connect
from nba.features.game_tipoff import SCHEDULE_DIR
from nba.ingest.cache import RateLimiter
from nba.ops.watchdog import health_section

DEFAULT_REPORT = Path("registry_store/reports/forward_report.md")


_NOW_HELP = "override the clock (naive UTC ISO, e.g. 2024-10-22T21:50); rehearsals only"


def _naive_utc(text: str) -> datetime:
    """Parse an ISO timestamp as naive UTC (an explicit offset is converted to UTC)."""
    d = datetime.fromisoformat(text)
    return d.astimezone(UTC).replace(tzinfo=None) if d.tzinfo else d


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m nba.daily")
    p.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="ingest, pull injury report, predict, settle")
    r.add_argument("--date", required=True, type=date.fromisoformat)
    r.add_argument("--skip-ingest", action="store_true")
    r.add_argument("--skip-injury", action="store_true")
    r.add_argument("--no-props", action="store_true")
    r.add_argument("--props-model", choices=["context", "routed", "rolling"], default="context")
    r.add_argument("--n-sims", type=int, default=1000)
    r.add_argument("--rate-limit-s", type=float, default=0.6)
    r.add_argument(
        "--schedule-from-db",
        action="store_true",
        help="read the slate from the games table (19:00 ET tip proxy) instead of nba_api; "
        "for rehearsals/replays on a database copy",
    )
    r.add_argument("--now", type=_naive_utc, default=None, help=_NOW_HELP)
    r.add_argument(
        "--model-cache",
        type=Path,
        default=None,
        help="context-residual model cache root (default data/models/context_residual)",
    )
    r.add_argument(
        "--log-int-variant",
        action="store_true",
        help="also log comparison rows 'props_context_residual_int' (integer-support "
        "quantiles, docs/INTEGER_QUANTILES.md); the primary rows are unchanged",
    )
    r.add_argument(
        "--log-lower-tail-variant",
        action="store_true",
        help="SHADOW: also log pts comparison rows 'props_context_residual_lt' (short-minutes "
        "mixture, integer-support quantiles, docs/LOWER_TAIL.md); never primary, primary rows "
        "unchanged",
    )
    r.add_argument(
        "--roster-source",
        choices=["recent", "official"],
        default="recent",
        help="forward roster: 'recent' = players in each team's last 10 games (default); "
        "'official' = pre-tip CommonTeamRoster (cached per date) plus recent players, with "
        "rookie/new-team handling (docs/OPENING_WEEK_ROSTERS.md)",
    )
    r.add_argument(
        "--roster-dir",
        type=Path,
        default=None,
        help="official-roster cache root (default data/rosters); rehearsals use a scratch dir so "
        "a rehearsal day never seeds the real per-date cache",
    )
    t = sub.add_parser(
        "run-t30",
        help="SHADOW: log props_context_residual_t30 for games at/after T-30 whose confirmed "
        "lineup was collected (docs/LINEUPS_T30_COLLECTOR.md); never primary",
    )
    t.add_argument("--date", required=True, type=date.fromisoformat)
    t.add_argument("--now", type=_naive_utc, default=None, help=_NOW_HELP)
    t.add_argument("--lineups-db", default=None, help="default data/lineups/lineups.duckdb")
    t.add_argument("--roster-source", choices=["recent", "official"], default="recent")
    t.add_argument("--model-cache", type=Path, default=None)
    t.add_argument("--schedule-from-db", action="store_true", help="rehearsals only")
    t.add_argument(
        "--schedule-nba-api",
        action="store_true",
        help="use the nba_api season schedule (1 stats.nba.com call) instead of the collector's "
        "own tip table",
    )
    st = sub.add_parser("settle", help="score completed predictions only")
    st.add_argument("--now", type=_naive_utc, default=None, help=_NOW_HELP)
    rep = sub.add_parser("report", help="rolling forward metrics + rollover flag")
    rep.add_argument("--out", default=str(DEFAULT_REPORT))
    rep.add_argument("--season", type=int, default=2026)
    return p


def _print_summary(s: RunSummary) -> None:
    print(
        f"run {s.run_id} date={s.run_date} slate={s.n_slate} predicted={s.n_predicted_games} "
        f"refused_after_tipoff={s.n_refused_after_tipoff} rows={s.n_rows_written} "
        f"props={s.n_props_rows} injury={s.injury_status} ({s.injury_report_et}) "
        f"out_excluded={s.n_out_excluded}"
    )
    print(f"models: {s.model_status}\ningest: {s.ingest}\nsettle: {s.settle}")
    if s.static_autofill:
        print(f"players_static autofill: {s.static_autofill}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    con = connect(args.db_path)
    if args.command == "run":
        from nba.registry import get_registry

        s = run_daily(
            con,
            args.date,
            schedule_fn=schedule_from_db(con) if args.schedule_from_db else fetch_schedule_nba_api,
            now=args.now,
            model_cache=args.model_cache,
            roster_source=args.roster_source,
            roster_dir=args.roster_dir,
            registry=get_registry(con),
            skip_ingest=args.skip_ingest,
            skip_injury=args.skip_injury,
            with_props=not args.no_props,
            props_model=args.props_model,
            log_int_variant=args.log_int_variant,
            log_lower_tail_variant=args.log_lower_tail_variant,
            n_sims=args.n_sims,
            rate_limiter=RateLimiter(args.rate_limit_s),
            tips_dir=None if args.schedule_from_db else SCHEDULE_DIR,
        )
        _print_summary(s)
        return 2 if s.n_refused_after_tipoff else 0
    if args.command == "run-t30":
        from nba.daily.t30 import run_t30, schedule_from_lineups
        from nba.lineups.store import DEFAULT_LINEUPS_DB, connect_lineups

        lcon = connect_lineups(args.lineups_db or DEFAULT_LINEUPS_DB, read_only=True)
        t30 = run_t30(
            con,
            lcon,
            args.date,
            schedule_fn=schedule_from_db(con)
            if args.schedule_from_db
            else fetch_schedule_nba_api
            if args.schedule_nba_api
            else schedule_from_lineups(lcon),
            now=args.now,
            model_cache=args.model_cache,
            roster_source=args.roster_source,
        )
        print(t30)
        return 0
    if args.command == "settle":
        print(settle_pending(con, args.now or utcnow()))
        return 0
    text = build_report(con, args.season)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    print(health_section())
    return 0


if __name__ == "__main__":
    sys.exit(main())
