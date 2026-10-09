"""Game-window gate: keep background ingest off the stats.nba.com quota and nba.duckdb on game days.

A window runs from (first tip of an Eastern-time day - ``pre_h``) to (last tip of that day +
``post_h``), built from the cached schedules in ``data/schedule`` (``live_tips_*.parquet`` written
by the daily job, plus the ``raw_*.parquet`` season schedules; live tips win for a game id).
Days with no games have no window. Outside windows nothing is gated. The gate is ON by default;
disable with ``NBA_INGEST_GAME_WINDOW=0`` (or the queue's ``--no-game-window``).

Pure IO/bookkeeping (polars read only); clock and sleep are injectable for tests.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl

from nba.ingest.cache import DEFAULT_DATA_DIR

ET = ZoneInfo("America/New_York")
#: Env switches (default on). Hours before the first tip / after the last tip of an ET day.
WINDOW_ENV = "NBA_INGEST_GAME_WINDOW"
PRE_H_ENV = "NBA_INGEST_WINDOW_PRE_H"
POST_H_ENV = "NBA_INGEST_WINDOW_POST_H"
DEFAULT_PRE_H = 2.5
DEFAULT_POST_H = 0.5
SCHEDULE_DIR = DEFAULT_DATA_DIR / "schedule"

Window = tuple[datetime, datetime]


def enabled(env: dict[str, str] | None = None) -> bool:
    raw = (env if env is not None else os.environ).get(WINDOW_ENV, "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def _hours(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def load_tips(schedule_dir: Path = SCHEDULE_DIR) -> list[datetime]:
    """All known tip times (tz-aware UTC), deduplicated by game id (live tips preferred)."""
    by_game: dict[str, datetime] = {}
    files = sorted(schedule_dir.glob("raw_*.parquet")) + sorted(
        schedule_dir.glob("live_tips_*.parquet")
    )  # later files overwrite earlier ones
    for f in files:
        try:
            df = pl.read_parquet(f, columns=["gameId", "gameDateTimeUTC"])
        except Exception:
            continue  # unreadable/partially written cache: ignore, fail open
        for gid, raw in df.iter_rows():
            if not raw:
                continue
            try:
                by_game[str(gid)] = datetime.fromisoformat(
                    str(raw).replace("Z", "+00:00")
                ).astimezone(UTC)
            except ValueError:
                continue
    return sorted(by_game.values())


def build_windows(
    tips: list[datetime], pre_h: float = DEFAULT_PRE_H, post_h: float = DEFAULT_POST_H
) -> list[Window]:
    """One window per Eastern-time calendar day that has tips."""
    days: dict[object, list[datetime]] = {}
    for t in tips:
        days.setdefault(t.astimezone(ET).date(), []).append(t)
    return [
        (min(v) - timedelta(hours=pre_h), max(v) + timedelta(hours=post_h))
        for _, v in sorted(days.items(), key=lambda kv: str(kv[0]))
    ]


@dataclass
class GameWindowGate:
    """``wait()`` blocks while ``now`` is inside a game window; no-op when disabled/outside."""

    schedule_dir: Path = SCHEDULE_DIR
    pre_h: float = DEFAULT_PRE_H
    post_h: float = DEFAULT_POST_H
    poll_s: float = 60.0
    on: bool = True
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sleep: Callable[[float], None] = time.sleep
    reload_s: float = 300.0  # re-read the schedule at most this often (daily job may rewrite it)

    def __post_init__(self) -> None:
        self._windows: list[Window] = []
        self._loaded_at: float | None = None

    def _refresh(self) -> list[Window]:
        mono = time.monotonic()
        if self._loaded_at is None or mono - self._loaded_at >= self.reload_s:
            self._windows = build_windows(load_tips(self.schedule_dir), self.pre_h, self.post_h)
            self._loaded_at = mono
        return self._windows

    def current_window(self, now: datetime | None = None) -> Window | None:
        if not self.on:
            return None
        t = now or self.clock()
        for w in self._refresh():
            if w[0] <= t < w[1]:
                return w
        return None

    def in_window(self, now: datetime | None = None) -> bool:
        return self.current_window(now) is not None

    def wait(self) -> float:
        """Block until outside every window; returns seconds slept."""
        slept = 0.0
        announced = False
        while (w := self.current_window()) is not None:
            if not announced:
                print(
                    f"game-window: paused until {w[1].astimezone(ET):%Y-%m-%d %H:%M %Z}", flush=True
                )
                announced = True
            self.sleep(self.poll_s)
            slept += self.poll_s
        if announced:
            print(f"game-window: resuming after {slept:.0f}s", flush=True)
        return slept


_ENV_GATES: dict[tuple[float, float, bool], GameWindowGate] = {}


def gate_from_env() -> GameWindowGate:
    """Process-wide gate configured from env (cached so the schedule is not re-read per fetch)."""
    key = (_hours(PRE_H_ENV, DEFAULT_PRE_H), _hours(POST_H_ENV, DEFAULT_POST_H), enabled())
    gate = _ENV_GATES.get(key)
    if gate is None:
        gate = _ENV_GATES[key] = GameWindowGate(pre_h=key[0], post_h=key[1], on=key[2])
    return gate
