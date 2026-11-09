# ruff: noqa: E501
"""``python -m nba.pulls tick | status | requests``. Read-only toward markets (GET via the existing client)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from nba.ops.exitcodes import RC_INFORMATIONAL
from nba.pulls.config import load_pulls_config

ET = ZoneInfo("America/New_York")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nba.pulls", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser(
        "tick", help="serve open pull requests once (the 2-minute orchestrator pass)"
    )
    t.add_argument("--now", help="ISO UTC time override (tests, replays)")
    s = sub.add_parser("status", help="newest Pinnacle row and its age per game of a slate")
    s.add_argument("--date", type=date.fromisoformat, help="ET slate date (default: today)")
    r = sub.add_parser("requests", help="recent pull requests and what became of them")
    r.add_argument("--hours", type=float, default=24.0)
    r.add_argument("--status")
    return ap


def _now(text: str | None) -> datetime:
    if not text:
        return datetime.now(UTC)
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def main(argv: Sequence[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    pc = load_pulls_config()
    if a.cmd == "tick":
        from nba.pulls.orchestrator import tick

        rep = tick(_now(a.now), cfg=pc)
        for line in rep.lines():
            print(line, flush=True)
        return RC_INFORMATIONAL if rep.degraded else 0
    if a.cmd == "status":
        from nba.odds import live_close as lc
        from nba.pulls.freshness import freshness_view

        day = a.date or datetime.now(ET).date()
        lcfg = lc.load_live_config()
        games = lc.read_slate(lcfg.tips_db, day, lcfg.games_db)
        view = freshness_view(lcfg.out_db, [(g.game_id, g.tipoff) for g in games], _now(None))
        if view is None:
            print(f"pulls status {day}: market store unreadable (missing or held by a writer)")
            return 0
        print(
            f"pulls status {day}: {len(view)} game(s); alert limit {pc.pinnacle_alert_max_age_min:g} min"
        )
        for row in view:
            if row.newest is None or row.age_min is None:
                print(f"  {row.game_id}  tip {row.tipoff:%H:%MZ}  no Pinnacle row")
            else:
                flag = "STALE" if row.age_min > pc.pinnacle_alert_max_age_min else "fresh"
                print(
                    f"  {row.game_id}  tip {row.tipoff:%H:%MZ}  newest {row.newest.kind} "
                    f"{row.age_min:.0f} min old  {flag}"
                )
        return 0
    from nba.pulls.store import PullStore

    since = _now(None) - timedelta(hours=a.hours)
    rows = PullStore(pc.requests_db).list_requests(since=since, status=a.status)
    print(f"pull requests, last {a.hours:g} h: {len(rows)}")
    for q in reversed(rows):
        print(
            f"  {q.created_at_utc:%m-%d %H:%MZ} {q.request_id} {q.source}/{q.scope_game_id} "
            f"{q.requester} max_age={q.max_age_min:g} {q.status} {q.detail}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
