# ruff: noqa: E501
"""INJURY_TIMING pilot probe (network, Devin's shell): which intraday official-report slots exist on the CDN for sample game dates?

    .venv/bin/python -W ignore -m research.eval.injury_timing_pilot_probe --dates 2024-01-17 2024-02-22 2023-12-12 2024-03-05 2023-11-08 [--dry-run]

Probes every 15-minute slot from 12:00 to 21:45 ET (both filename forms for top-of-hour slots) with the ranged 4-byte GET of
``probe_report_status``; downloads nothing, writes nothing but ``reports/injury_timing/pilot_probe.json``. Count per date:
40 slots plus 10 hour-only filename forms at the top of the hour = 50 requests; 5 dates = 250 requests, about 3 minutes at 0.6 s.
``--dry-run`` prints the URL count and exits without any request. Read-only with respect to everything else.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from nba.ingest.availability import official_report_url_candidates, probe_report_status
from nba.ingest.cache import RateLimiter

FIRST, LAST = (12, 0), (21, 45)


def slots(d: date) -> list[datetime]:
    t = datetime(d.year, d.month, d.day, *FIRST)
    end = datetime(d.year, d.month, d.day, *LAST)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(minutes=15)
    return out


def urls_for(ts: datetime) -> list[str]:
    c = official_report_url_candidates(ts)
    return c if ts.minute == 0 else c[:1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dates", nargs="+", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("reports/injury_timing/pilot_probe.json"))
    a = ap.parse_args(argv)
    days = [date.fromisoformat(x) for x in a.dates]
    n = sum(len(urls_for(t)) for d in days for t in slots(d))
    print(f"{len(days)} dates, {n} requests")
    if a.dry_run:
        return 0
    rl = RateLimiter(0.6)
    found: dict[str, list[str]] = {}
    for d in days:
        hits = []
        for t in slots(d):
            for u in urls_for(t):
                rl.wait()
                if probe_report_status(u) == "present":
                    hits.append(t.strftime("%H:%M"))
                    break
        found[d.isoformat()] = hits
        print(d, hits, flush=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(found, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
