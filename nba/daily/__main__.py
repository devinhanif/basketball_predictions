"""CLI: ``python -m nba.daily {run,ingest,settle,report} ...``.

``run`` writes to the real ``nba.duckdb`` (single writer -- do not run while an
eval holds it). Network access only in ``run`` (schedule, games, box scores,
injury PDFs); never any market/execution code.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from nba.daily.ingest_step import (
    FetchGames,
    PullBox,
    incremental_ingest,
    insert_scheduled_games,
)
from nba.daily.pipeline import RunSummary, run_daily, utcnow
from nba.daily.report import build_report
from nba.daily.schedule import (
    DEFAULT_SCHEDULE_CACHE_DIR,
    ET,
    MAX_SCHEDULE_CACHE_AGE,
    ScheduleFn,
    fetch_schedule_nba_api,
    load_schedule_cache,
    schedule_from_db,
    slate_for_date,
)
from nba.daily.season import season_int_for_date, season_str_for_date
from nba.daily.settle import settle_pending
from nba.db.connect import DEFAULT_DB_PATH, connect_with_retry, is_lock_error
from nba.features.game_tipoff import SCHEDULE_DIR
from nba.ingest.cache import RateLimiter
from nba.ops.exitcodes import RC_DB_BUSY, RC_DEGRADED, RC_INFORMATIONAL
from nba.ops.watchdog import health_section

DEFAULT_REPORT = Path("registry_store/reports/forward_report.md")
SCHEDULE_CACHE_DIR = DEFAULT_SCHEDULE_CACHE_DIR  # one definition: nba.daily.schedule


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
    r.add_argument("--props-model", choices=["context", "rolling"], default="context")
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
        "--log-pplay-shadow",
        action="store_true",
        help="SHADOW: log the recent-role P(play) model R next to production's p_play for every "
        "rostered not-OUT player (table forward_pplay_shadow, docs/DAILY_PIPELINE.md); "
        "forward_predictions and the props outputs are unchanged",
    )
    r.add_argument(
        "--log-full-shadow",
        action="store_true",
        help="SHADOW: log the RECENT_ROLE_FULL conditional grids (props heads + lower-tail pi "
        "refit with the five recent-role columns) next to production's (table forward_full_shadow, "
        "docs/DAILY_PIPELINE.md); forward_predictions and the props outputs are unchanged",
    )
    r.add_argument(
        "--log-avg-shadow",
        action="store_true",
        help="SHADOW (AVG_SHADOW_LIVE_2): CPU inference of the E2 sequence transformer in a "
        "child process and AVG = 0.5 pmf(SEQc) + 0.5 pmf(FULL), regular-season rows only "
        "(tables forward_avg_*, heartbeat data/ops/heartbeat/avg_shadow.json); no weights "
        "installed -> status skipped; default OFF",
    )
    r.add_argument(
        "--log-combo-shadow",
        action="store_true",
        help="SHADOW (COMBO_STOCKS_SHADOW): blk / stl NB heads and the six-stat copula joint for "
        "PRA, PR, PA, RA, blk+stl with RECENT and INDEP comparators, regular-season rows only "
        "(tables forward_combo_*, heartbeat data/ops/heartbeat/combo_shadow.json); "
        "forward_predictions and the props outputs are unchanged; default OFF",
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
    for cmd, helptext in (
        (
            "run-t30",
            "SHADOW: log props_context_residual_t30 for games at/after T-30 whose confirmed "
            "lineup was collected (docs/LINEUPS_T30_COLLECTOR.md); never primary",
        ),
        (
            "run-t15",
            "SHADOW: log props_context_residual_t15 for games at/after T-15 whose confirmed "
            "lineup was collected (docs/prereg/FORWARD_T15_2026_27.md); never primary",
        ),
    ):
        t = sub.add_parser(cmd, help=helptext)
        t.add_argument("--date", required=True, type=date.fromisoformat)
        t.add_argument("--now", type=_naive_utc, default=None, help=_NOW_HELP)
        t.add_argument("--lineups-db", default=None, help="default data/lineups/lineups.duckdb")
        t.add_argument("--roster-source", choices=["recent", "official"], default="recent")
        t.add_argument("--model-cache", type=Path, default=None)
        t.add_argument(
            "--rate-limit-s",
            type=float,
            default=2.5,
            help="min gap between metered roster/pedigree calls (floored at 2.5 s)",
        )
        t.add_argument("--roster-dir", type=Path, default=None, help="official-roster cache root")
        t.add_argument("--schedule-from-db", action="store_true", help="rehearsals only")
        t.add_argument(
            "--schedule-nba-api",
            action="store_true",
            help="use the nba_api season schedule (1 stats.nba.com call) instead of the "
            "collector's own tip table",
        )
    ig = sub.add_parser(
        "ingest",
        help="refresh the season's games and box scores (the morning job, BEFORE settle)",
    )
    ig.add_argument("--date", type=date.fromisoformat, default=None, help="default: today (ET)")
    ig.add_argument("--rate-limit-s", type=float, default=0.6)
    st = sub.add_parser("settle", help="score completed predictions only")
    st.add_argument("--now", type=_naive_utc, default=None, help=_NOW_HELP)
    ck = sub.add_parser(
        "checkpoint",
        help="write the immutable pre-registered checkpoint snapshot "
        "(docs/FORWARD_PREREG_2026_27.md section 8); read-only on the database",
    )
    ck.add_argument("--look", required=True, choices=["L14", "L30", "L60", "L120", "END", "auto"])
    ck.add_argument(
        "--descriptive",
        action="store_true",
        help="bypass the min-n gate; writes under <out-dir>/descriptive, never a decision input",
    )
    ck.add_argument("--out-dir", type=Path, default=Path("data/checkpoints"))
    ck.add_argument("--lineups-db", type=Path, default=None)
    rep = sub.add_parser("report", help="rolling forward metrics + rollover flag")
    rep.add_argument("--out", default=str(DEFAULT_REPORT))
    rep.add_argument("--season", type=int, default=2026)
    return p


def _print_summary(s: RunSummary) -> None:
    print(
        f"run {s.run_id} date={s.run_date} slate={s.n_slate} predicted={s.n_predicted_games} "
        f"refused_after_tipoff={s.n_refused_after_tipoff} rows={s.n_rows_written} "
        f"deduped={s.n_rows_deduped} "
        f"props={s.n_props_rows} injury={s.injury_status} ({s.injury_report_et}) "
        f"out_excluded={s.n_out_excluded}"
    )
    dirty = "unknown" if s.git_dirty is None else str(s.git_dirty).lower()
    print(f"code: git={s.git_head[:12]} dirty={dirty} (tracked files modified: {s.git_n_dirty})")
    print(f"model stamps: {s.stamp_status}")
    print(f"prop grid199: {s.grid_status}")
    if s.n_slate == 0 and s.n_preseason_skipped:
        print(f"no regular-season games: {s.n_preseason_skipped} preseason skipped")
    print(f"models: {s.model_status}\ningest: {s.ingest}\nsettle: {s.settle}")
    if s.static_autofill:
        print(f"players_static autofill: {s.static_autofill}")
    for e in s.errors:
        print(f"DEGRADED: {e}")
    for e in s.shadow_errors:
        print(f"shadow error (primary unaffected): {e}")


OPS_DIR = DEFAULT_DB_PATH.parent / "data" / "ops"


def alert_shadow_errors(s: RunSummary, ops_dir: Path = OPS_DIR) -> bool:
    """One ``ALERTS.md`` line when any shadow arm / non-primary step failed. The run keeps rc 0
    (primary rows are unaffected) but a daily-broken shadow arm must not stay silent."""
    if not s.shadow_errors:
        return False
    ops_dir.mkdir(parents=True, exist_ok=True)
    first = s.shadow_errors[0].replace("\n", " ")[:160]
    line = (
        f"- {datetime.now():%Y-%m-%d %H:%M} [daily-run] {len(s.shadow_errors)} shadow error(s) "
        f"in run {s.run_id} (primary rows unaffected): {first}\n"
    )
    with (ops_dir / "ALERTS.md").open("a") as fh:
        fh.write(line)
    return True


def run_ingest(
    con: duckdb.DuckDBPyConnection,
    run_date: date,
    *,
    rate_limiter: RateLimiter,
    fetch_games: FetchGames | None = None,
    pull_box: PullBox | None = None,
    schedule_fn: ScheduleFn | None = None,
) -> int:
    """Morning ingest: games (final scores) and the box scores not yet loaded, for the season of
    ``run_date``. 2026-10-20: the morning settle ran before any ingest and would have graded a
    half-time score. Exit 0, or ``RC_DEGRADED`` when the fetch fails (settle still runs: its own
    guards refuse a game without a box score)."""
    out: dict[str, int] = {}
    rc = 0
    try:
        out = incremental_ingest(
            con,
            season_str_for_date(run_date),
            season_int_for_date(run_date),
            rate_limiter=rate_limiter,
            fetch_games=fetch_games,
            pull_box=pull_box,
            run_date=run_date,
        )
    except Exception as exc:  # the slate rows below are still written (the pretip run needs them)
        print(f"DEGRADED: ingest failed ({type(exc).__name__}: {exc})"[:300])
        rc = RC_DEGRADED
    if schedule_fn is not None:  # today's slate rows (the finder holds only started games)
        try:
            season_s = season_str_for_date(run_date)
            try:
                sched = schedule_fn(season_s)
            except Exception:  # fall back to the last good schedule copy
                cached = load_schedule_cache(
                    season_s, SCHEDULE_CACHE_DIR, max_age=MAX_SCHEDULE_CACHE_AGE
                )
                if cached is None:
                    raise
                sched = cached
            out["scheduled_rows_inserted"] = insert_scheduled_games(
                con, slate_for_date(sched, run_date), run_date, season_int_for_date(run_date)
            )
        except Exception as exc:
            print(f"DEGRADED: scheduled games not written ({type(exc).__name__}: {exc})"[:300])
            rc = RC_DEGRADED
    print(f"ingest: {out}")
    return rc


def _checkpoint(args: argparse.Namespace) -> int:
    from nba.daily.checkpoint import GateNotMet, due_looks, evaluate_checkpoint

    con = connect_with_retry(args.db_path, read_only=True)
    try:
        has_elig = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'forward_scores_elig'"
        ).fetchone()
        if not has_elig or not has_elig[0]:
            print("checkpoint: forward_scores_elig does not exist yet; run `settle` first")
            return 0 if args.look == "auto" else 2
        looks = due_looks(con, args.out_dir) if args.look == "auto" else [args.look]
        if not looks:
            print("checkpoint: no look is due")
            return 0
        for look in looks:
            try:
                snap = evaluate_checkpoint(
                    con,
                    look,
                    out_dir=args.out_dir,
                    lineups_db=args.lineups_db,
                    descriptive=args.descriptive,
                )
            except GateNotMet as exc:
                print(f"checkpoint {look}: REFUSED: {exc}")
                return 2
            print(f"checkpoint {look}: wrote {snap['paths']['json']}")
            print({k: r["outcome"] for k, r in snap["results"].items()})
    finally:
        con.close()
    return 0


#: (label, module, function, table): shadows settled after the primary settle, each in its own try
SHADOW_SETTLERS = (
    ("pplay", "nba.daily.pplay_shadow.settle", "settle_pplay_shadow", "forward_pplay_shadow"),
    ("full", "nba.daily.full_shadow.settle", "settle_full_shadow", "forward_full_shadow"),
    ("avg", "nba.daily.avg_shadow.settle", "settle_avg_shadow", "forward_avg_shadow"),
    ("combo", "nba.daily.combo_shadow.settle", "settle_combo_shadow", "forward_combo_shadow"),
)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "checkpoint":
        return _checkpoint(args)
    # the file lock is taken on open: wait (bounded) for a concurrent writer instead of failing
    try:
        con = connect_with_retry(args.db_path)
    except duckdb.IOException as exc:
        if not is_lock_error(exc):
            raise
        print(f"nba.duckdb is write-locked by another process (bounded wait exhausted): {exc}")
        return RC_DB_BUSY
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
            with_pplay_shadow=args.log_pplay_shadow,
            with_full_shadow=args.log_full_shadow,
            with_avg_shadow=args.log_avg_shadow,
            with_combo_shadow=args.log_combo_shadow,
            tip_source="proxy" if args.schedule_from_db else "real",
            rate_limiter=RateLimiter(args.rate_limit_s),
            tips_dir=None if args.schedule_from_db else SCHEDULE_DIR,
            schedule_cache_dir=None if args.schedule_from_db else SCHEDULE_CACHE_DIR,
        )
        _print_summary(s)
        alert_shadow_errors(s)
        if s.errors:
            return RC_DEGRADED
        return RC_INFORMATIONAL if s.n_refused_after_tipoff else 0
    if args.command in ("run-t30", "run-t15"):
        from nba.daily.t30 import T30_MIN_INTERVAL_S, run_t15, run_t30, schedule_from_lineups
        from nba.lineups.store import DEFAULT_LINEUPS_DB, connect_lineups

        lcon = connect_lineups(args.lineups_db or DEFAULT_LINEUPS_DB, read_only=True)
        run_arm = run_t15 if args.command == "run-t15" else run_t30
        t30 = run_arm(
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
            rate_limiter=RateLimiter(max(args.rate_limit_s, T30_MIN_INTERVAL_S)),
            roster_dir=args.roster_dir,
        )
        print(t30)
        return 0
    if args.command == "ingest":
        return run_ingest(
            con,
            args.date or datetime.now(ET).date(),
            rate_limiter=RateLimiter(args.rate_limit_s),
            schedule_fn=fetch_schedule_nba_api,
        )
    if args.command == "settle":
        now = args.now or utcnow()
        print(settle_pending(con, now))
        for label, module, fn, table in SHADOW_SETTLERS:
            try:  # shadow scoring never blocks the primary settle; the import is inside the try
                settle = getattr(importlib.import_module(module), fn)
                if con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
                ).fetchone()[0]:  # type: ignore[index]
                    print(settle(con, now))
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                print(f"{label} shadow settle failed (primary unaffected): {msg}")
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
