"""``python -m nba.live capture --date D --out data/raw/live_D`` (public data, write-once raw)."""

from __future__ import annotations

import argparse
import json
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
    o = sub.add_parser("offer", help="log one observed cash-out offer with the legs' public quotes")
    o.add_argument("--combo", required=True, help="combo market ticker")
    o.add_argument("--offer", type=float, required=True, help="offered dollars")
    o.add_argument("--contracts", type=int, required=True)
    o.add_argument("--leg", action="append", default=[], help="leg ticker if the API lists none")
    o.add_argument("--note", default=None)
    o.add_argument("--date", type=date.fromisoformat, default=None, help="ET date for the folder")
    o.add_argument("--out", type=Path, default=None, help="default data/raw/live_<date>")
    a = ap.parse_args(argv)
    if a.cmd == "offer":
        from nba.live.offer import log_offer

        oday = a.date or datetime.now(ET).date()
        row = log_offer(
            a.out or ROOT / "data" / "raw" / f"live_{oday.isoformat()}",
            combo=a.combo,
            offer=a.offer,
            contracts=a.contracts,
            fetcher=Fetcher(rate_per_s=Budget().rate_per_s),
            legs=a.leg,
            note=a.note,
        )
        print(json.dumps(row, sort_keys=True))
        return 0
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
