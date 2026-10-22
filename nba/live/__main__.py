"""``python -m nba.live capture --date D --out data/raw/live_D`` (public data, write-once raw)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.live.capture import Budget, Capture
from nba.live.http import Fetcher

ROOT = Path(__file__).resolve().parents[2]
ET = ZoneInfo("America/New_York")
HEARTBEAT = ROOT / "data" / "ops" / "heartbeat" / "live_capture.json"
LEGS_FILE = ROOT / "data" / "papertrade" / "open_legs.json"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.live", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="capture one slate date, from 2 h before the first tip")
    c.add_argument(
        "--date", type=date.fromisoformat, default=None, help="ET slate date (default today)"
    )
    c.add_argument("--out", type=Path, default=None, help="default data/raw/live_<date>")
    c.add_argument("--heartbeat", type=Path, default=HEARTBEAT)
    c.add_argument("--legs-file", type=Path, default=LEGS_FILE)
    c.add_argument("--interval-s", type=float, default=60.0)
    c.add_argument("--max-requests-per-tick", type=int, default=300)
    c.add_argument("--once", action="store_true", help="one tick now (smoke test), no waiting")
    c.add_argument("--start-now", action="store_true", help="do not wait for the window start")
    a = ap.parse_args(argv)
    day = a.date or datetime.now(ET).date()
    out = a.out or ROOT / "data" / "raw" / f"live_{day.isoformat()}"
    budget = Budget(max_requests_per_tick=a.max_requests_per_tick, interval_s=a.interval_s)
    cap = Capture(
        day,
        out,
        a.heartbeat,
        fetcher=Fetcher(rate_per_s=budget.rate_per_s),
        budget=budget,
        legs_file=a.legs_file,
    )
    return cap.run(once=a.once, start_now=a.start_now)


if __name__ == "__main__":
    raise SystemExit(main())
