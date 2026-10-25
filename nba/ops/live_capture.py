"""When must the nightly live capture (``python -m nba.live capture``) be running? (read-only)

The capture (public ESPN + Kalshi data, write-once raw under ``data/raw/live_<ET date>/``) waits for
its own window start (first tip - 2 h) and runs to the last tip + 4 h, writing
``data/ops/heartbeat/live_capture.json`` on every beat. Four frozen forward rules (T510, T510b,
FAV_DIP, the paper trader) and two that need >= 72 % of nights (KALSHI_LONGSHOT_FORWARD,
UNDERS_FORWARD) read what it stores, and an in-game price can never be backfilled.

This module has two jobs, both pure functions of (now, tips, heartbeat):

* ``plan``: what the launcher (``ops/nba_live_capture.sh``, ticking every 10 min) should do now.
  It starts the capture from ``START_LEAD`` (150 min) before the first tip, i.e. 30 min before the
  capture's own window opens, so a missed tick or a slow start still leaves the window intact.
* ``problem``: what the watchdog should say if the capture is not running inside its window.

CLI: ``python -m nba.ops.live_capture plan --date YYYY-MM-DD`` prints ``<decision> <detail>`` and
always exits 0 (an unreadable schedule prints ``unknown`` and the launcher starts the capture
anyway: it exits 0 by itself on a no-game day).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

#: the launcher starts the capture this long before the first tip (the capture then waits for
#: its own window start, first tip - 2 h)
START_LEAD = timedelta(minutes=150)
#: the capture's own window (nba.live.capture.Budget lead_h / tail_h)
WINDOW_LEAD = timedelta(hours=2)
WINDOW_TAIL = timedelta(hours=4)
#: watchdog: a beat is written every 60 s; this long without one inside the window is a problem
STALE_AFTER = timedelta(minutes=5)
#: watchdog: give the capture this long after its window opens before calling it missing
OPEN_GRACE = timedelta(minutes=10)
FINISHED = frozenset({"done", "no_games"})
HEARTBEAT = "live_capture.json"
LAUNCH_HEARTBEAT = "live-capture-launch.json"


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def beat_time(hb_path: Path, hb: Mapping[str, Any] | None) -> datetime | None:
    """Newest sign of life: the file mtime (every beat rewrites the file)."""
    try:
        return datetime.fromtimestamp(hb_path.stat().st_mtime, UTC)
    except OSError:
        return None


def plan(
    now: datetime,
    tips: Sequence[datetime],
    hb: Mapping[str, Any] | None,
    day: date,
) -> tuple[str, str]:
    """``(decision, detail)``; decision is one of no_games | over | done | early | start.

    ``tips``: UTC tip times of the ET date ``day``; ``hb``: the capture heartbeat (or None).
    ``start`` does not mean "not already running": the launcher's lock decides that.
    """
    if not tips:
        return "no_games", f"no regular-season game on {day}"
    first, last = min(tips), max(tips)
    if now > last + WINDOW_TAIL:
        return "over", f"window ended {last + WINDOW_TAIL:%H:%MZ}"
    if hb and hb.get("date") == day.isoformat() and hb.get("status") in FINISHED:
        return "done", f"capture {hb.get('status')} for {day}"
    opens = first - START_LEAD
    if now < opens:
        return (
            "early",
            f"start at {opens:%H:%MZ} (first tip {first:%H:%MZ}), "
            f"{int((opens - now).total_seconds() // 60)} min",
        )
    return "start", f"first tip {first:%H:%MZ}, window to {last + WINDOW_TAIL:%H:%MZ}"


def problem(
    now: datetime,
    tips: Sequence[datetime],
    hb: Mapping[str, Any] | None,
    hb_time: datetime | None,
    day: date,
) -> str | None:
    """Why the capture is not running inside its window, or None (fine, or not yet due)."""
    if not tips:
        return None
    first, last = min(tips), max(tips)
    opens, closes = first - WINDOW_LEAD, last + WINDOW_TAIL
    if not (opens + OPEN_GRACE <= now <= closes):
        return None
    if hb is None or hb.get("date") != day.isoformat():
        seen = "no heartbeat" if hb is None else f"last heartbeat is for {hb.get('date')}"
        return f"{seen} (window opened {opens:%H:%MZ})"
    if hb.get("status") in FINISHED:
        return None
    if hb_time is None or now - hb_time > STALE_AFTER:
        age = "unknown" if hb_time is None else f"{int((now - hb_time).total_seconds() // 60)} min"
        return f"heartbeat {age} old, status {hb.get('status')}"
    if hb.get("status") == "failed":
        return "status failed (schedule unreachable)"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["plan"])
    ap.add_argument("--date", type=date.fromisoformat, required=True, help="ET slate date")
    ap.add_argument("--now", default=None, help="ISO time (tests); default now")
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    a = ap.parse_args(argv)
    now = _ts(a.now) if a.now else datetime.now(UTC)
    assert now is not None
    try:
        from nba.ops.watchdog import todays_tips

        tips = list(todays_tips(a.data_dir, a.date).values())
    except Exception as e:  # unreadable schedule: fail open, the capture decides by itself
        print(f"unknown {type(e).__name__}: {e}")
        return 0
    hb = read_json(a.data_dir / "ops" / "heartbeat" / HEARTBEAT)
    decision, detail = plan(now, tips, hb, a.date)
    print(f"{decision} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
