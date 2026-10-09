"""CLI: ``python -m nba.daily {run,settle,report} ...``.

``run`` writes to the real ``nba.duckdb`` (single writer -- do not run while an
eval holds it). Network access only in ``run`` (schedule, games, box scores,
injury PDFs); never any market/execution code.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from nba.daily.pipeline import RunSummary, run_daily, utcnow
from nba.daily.report import build_report
from nba.daily.schedule import fetch_schedule_nba_api
from nba.daily.settle import settle_pending
from nba.db.connect import DEFAULT_DB_PATH, connect
from nba.ingest.cache import RateLimiter

DEFAULT_REPORT = Path("registry_store/reports/forward_report.md")


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
    sub.add_parser("settle", help="score completed predictions only")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    con = connect(args.db_path)
    if args.command == "run":
        from nba.registry import get_registry

        s = run_daily(
            con,
            args.date,
            schedule_fn=fetch_schedule_nba_api,
            registry=get_registry(con),
            skip_ingest=args.skip_ingest,
            skip_injury=args.skip_injury,
            with_props=not args.no_props,
            props_model=args.props_model,
            n_sims=args.n_sims,
            rate_limiter=RateLimiter(args.rate_limit_s),
        )
        _print_summary(s)
        return 2 if s.n_refused_after_tipoff else 0
    if args.command == "settle":
        print(settle_pending(con, utcnow()))
        return 0
    text = build_report(con, args.season)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
