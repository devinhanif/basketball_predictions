"""The day runner: scan before each tip window, monitor every tick, settle once at the end.

``run_day`` is a loop over injected callables (clock, sleep, scan, monitor, settle) so the schedule
is tested without a network. It never dies on one failed step: the error is recorded and the next
tick goes on.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from nba.live.store import write_json_atomic

SCAN_LEAD_FIRST = timedelta(minutes=75)
SCAN_LEAD_LATER = timedelta(minutes=45)
TAIL = timedelta(hours=4)
HEARTBEAT = Path(__file__).resolve().parents[2] / "data" / "ops" / "heartbeat" / "papertrade.json"


def scan_times(tips: list[datetime]) -> list[datetime]:
    """One scan 75 min before the first tip, then one 45 min before each later tip window."""
    uniq = sorted(set(tips))
    if not uniq:
        return []
    return [uniq[0] - SCAN_LEAD_FIRST, *[t - SCAN_LEAD_LATER for t in uniq[1:]]]


@dataclass
class DayRunner:
    day: date
    tips: list[datetime]  # naive UTC tip-offs of the slate
    scan: Callable[[datetime], Any]
    monitor: Callable[[datetime], dict[str, int]]
    settle: Callable[[datetime], Any]
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC).replace(tzinfo=None)
    sleep_fn: Callable[[float], None] = time.sleep
    interval_s: float = 60.0
    heartbeat: Path = HEARTBEAT

    def run(self) -> int:
        times = scan_times(self.tips)
        if not times:
            self._beat("no_games", rc=0)
            return 0
        end = max(self.tips) + TAIL
        pending = list(times)
        errors = ticks = 0
        start = self.now_fn()
        while True:
            now = self.now_fn()
            if now >= end:
                break
            if now < times[0]:
                self._beat("waiting", ticks=ticks, errors=errors, start=start, next_scan=times[0])
                self.sleep_fn(min(60.0, max(1.0, (times[0] - now).total_seconds())))
                continue
            try:
                while pending and now >= pending[0]:
                    pending.pop(0)
                    self.scan(now)
                counts = self.monitor(now)
            except Exception:
                errors += 1
                self._log(traceback.format_exc().replace("\n", " | ")[:1500])
                counts = {"positions": 1}
            ticks += 1
            self._beat("running", ticks=ticks, errors=errors, start=start, last=counts)
            if not pending and counts.get("positions", 1) == 0 and now > max(self.tips):
                break
            self.sleep_fn(self.interval_s)
        try:
            self.settle(self.now_fn())
        except Exception:
            errors += 1
            self._log("settle " + traceback.format_exc().replace("\n", " | ")[:1500])
        self._beat("done", ticks=ticks, errors=errors, start=start, rc=0, end_ts=self.now_fn())
        return 0

    def _log(self, what: str) -> None:
        log = self.heartbeat.with_suffix(".errors.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as f:
            f.write(f"{datetime.now(UTC).isoformat()} {what}\n")

    def _beat(self, status: str, **extra: Any) -> None:
        payload: dict[str, Any] = {
            "job": "papertrade",
            "date": self.day.isoformat(),
            "status": status,
            "last_tick_ts": datetime.now(UTC).isoformat(),
        }
        payload.update(
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in extra.items()}
        )
        if "start" in payload:
            payload["start_ts"] = payload.pop("start")
        write_json_atomic(self.heartbeat, payload)
