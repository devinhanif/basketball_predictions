"""CLI: ``python -m nba.lineups {poll,status,report}``.

``poll`` is one collector tick (launchd runs it every 5 minutes). It never opens
``nba.duckdb``; it writes only ``data/lineups/lineups.duckdb`` and ``data/lineups/raw/``.
Network: one GET of the public daily-lineups file per fetching tick, plus at most one
season-schedule call per date (marker file) to learn exact tip-offs.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from nba.daily.schedule import ET, ScheduleFn, fetch_schedule_nba_api, slate_for_date
from nba.daily.season import season_str_for_date
from nba.lineups.collector import PollResult, poll_once
from nba.lineups.source import fetch_daily_lineups, utc_now
from nba.lineups.store import (
    DEFAULT_LINEUPS_DB,
    T30_LEAD_MINUTES,
    connect_lineups,
    games_with_tips,
    tips_for_date,
    upsert_tip,
)


def _naive_utc(text: str) -> datetime:
    d = datetime.fromisoformat(text)
    return d.astimezone(UTC).replace(tzinfo=None) if d.tzinfo else d


def today_et(now: datetime) -> date:
    return now.replace(tzinfo=UTC).astimezone(ET).date()


def ensure_schedule_tips(
    lcon: object, game_date: date, now: datetime, schedule_fn: ScheduleFn, marker_dir: Path
) -> int:
    """At most one schedule call per date (marker file, written even on failure so an
    off-day or preseason date does not call every tick). Returns tips recorded."""
    marker = marker_dir / f"schedule_checked_{game_date.isoformat()}"
    if marker.exists():
        return 0
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text(now.isoformat())
    try:
        games = slate_for_date(schedule_fn(season_str_for_date(game_date)), game_date)
    except Exception as exc:  # the feed's own status text is the fallback
        print(f"schedule unavailable ({type(exc).__name__}); using feed status text for tips")
        return 0
    for g in games:
        upsert_tip(lcon, g.game_id, game_date, g.tipoff, "schedule", now)  # type: ignore[arg-type]
    return len(games)


#: HTTP statuses that are not a collector failure. 404 = no file; 302 = the day's file is not
#: published yet (stats.nba.com answers a future date with a redirect to /error/, observed
#: 2026-10-09), which must not raise an alert on every morning/off-day tick.
BENIGN_STATUSES = (None, 200, 302, 404)


def poll_ok(res: PollResult) -> bool:
    return res.reason != "parse_error" and res.http_status in BENIGN_STATUSES


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m nba.lineups")
    p.add_argument("--db-path", default=str(DEFAULT_LINEUPS_DB))
    sub = p.add_subparsers(dest="command", required=True)
    pl = sub.add_parser("poll", help="one collector tick")
    pl.add_argument("--date", type=date.fromisoformat, default=None, help="Eastern game date")
    pl.add_argument("--now", type=_naive_utc, default=None, help="override clock (naive UTC)")
    pl.add_argument("--force", action="store_true", help="fetch regardless of the window")
    pl.add_argument("--no-schedule", action="store_true", help="skip the schedule call")
    sub.add_parser("status", help="print snapshot/tip counts")
    du = sub.add_parser("due", help="exit 0 iff some game is inside [tip-30 min, tip)")
    du.add_argument("--now", type=_naive_utc, default=None, help="override clock (naive UTC)")
    args = p.parse_args(argv)

    if args.command == "due":
        ro = connect_lineups(args.db_path, read_only=True)
        now = args.now or utc_now()
        due = [
            g
            for g, tip, _h, _a in games_with_tips(ro, include_unseen=True)
            if tip - timedelta(minutes=T30_LEAD_MINUTES) <= now < tip
        ]
        print(f"due games: {len(due)}")
        return 0 if due else 1
    lcon = connect_lineups(args.db_path)
    if args.command == "status":
        for t in ("snapshot_log", "lineup_snapshots", "game_tips"):
            n = lcon.execute(f"SELECT count(*) FROM {t}").fetchone()  # noqa: S608
            print(f"{t}: {n[0] if n else 0}")
        return 0
    now = args.now or utc_now()
    gd = args.date or today_et(now)
    if not args.no_schedule and not tips_for_date(lcon, gd):
        ensure_schedule_tips(lcon, gd, now, fetch_schedule_nba_api, Path(args.db_path).parent)
    root = Path(args.db_path).parent
    res = poll_once(lcon, gd, now=now, fetch=fetch_daily_lineups, force=args.force, root=root)
    print(res)
    return 0 if poll_ok(res) else 1


if __name__ == "__main__":
    sys.exit(main())
