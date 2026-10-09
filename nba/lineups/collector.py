"""Poll logic for the lineup collector (injectable fetch/schedule; no network in tests).

Design (docs/LINEUPS_T30_COLLECTOR.md): launchd fires ``python -m nba.lineups poll`` every
5 minutes. A tick fetches the day's lineup file only when some game is inside
``[tip - 60 min, tip + 5 min)``; before any tip is known it does a cheap bootstrap fetch at
most every 30 minutes (which also teaches the store the tips from the feed's status text).
Every 200 response is stored in full (``lineup_snapshots``) with ``fetched_at`` and the
feed's own timestamps, and the raw JSON is kept. Snapshots fetched at/after tip-off are
still stored (they show how the status evolved) but the T-30 reader never sees them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from nba.lineups.source import (
    RawFetch,
    parse_daily_lineups,
    tip_from_status_text,
)
from nba.lineups.store import (
    LINEUPS_DIR,
    last_fetch_time,
    record_snapshot,
    save_raw,
    tips_for_date,
    upsert_tip,
)

FetchFn = Callable[[date], RawFetch]
WINDOW_MINUTES = 60
GRACE_MINUTES = 5
MIN_INTERVAL_S = 240.0  # a double-fired tick inside this gap is a no-op
BOOTSTRAP_INTERVAL_S = 1800.0


@dataclass(frozen=True)
class PollResult:
    action: str  # 'fetched' | 'skipped'
    reason: str
    snapshot_id: str | None = None
    http_status: int | None = None
    n_games: int = 0
    n_rows: int = 0


def should_fetch(
    tips: dict[str, datetime], last_fetch: datetime | None, now: datetime
) -> tuple[bool, str]:
    if last_fetch is not None and (now - last_fetch).total_seconds() < MIN_INTERVAL_S:
        return False, "min_interval"
    if not tips:
        if last_fetch is not None and (now - last_fetch).total_seconds() < BOOTSTRAP_INTERVAL_S:
            return False, "no_tips_known_bootstrap_backoff"
        return True, "bootstrap_no_tips_known"
    for t in tips.values():
        if t - timedelta(minutes=WINDOW_MINUTES) <= now < t + timedelta(minutes=GRACE_MINUTES):
            return True, "game_in_window"
    return False, "no_game_in_window"


def poll_once(
    lcon: duckdb.DuckDBPyConnection,
    game_date: date,
    *,
    now: datetime,
    fetch: FetchFn,
    force: bool = False,
    root: Path = LINEUPS_DIR,
) -> PollResult:
    """One collector tick for the Eastern ``game_date``. ``now`` is naive UTC."""
    tips = tips_for_date(lcon, game_date)
    go, reason = (
        (True, "forced") if force else should_fetch(tips, last_fetch_time(lcon, game_date), now)
    )
    if not go:
        return PollResult("skipped", reason)
    raw = fetch(game_date)
    if raw.status != 200:
        sid = record_snapshot(lcon, raw, game_date, None, None, note=f"http_{raw.status}")
        return PollResult("fetched", f"http_{raw.status}", sid, raw.status)
    path = save_raw(raw, game_date, root)
    try:
        rows = parse_daily_lineups(raw.body)
    except ValueError as exc:
        sid = record_snapshot(lcon, raw, game_date, None, path, note=f"parse_error: {exc}"[:200])
        return PollResult("fetched", "parse_error", sid, raw.status)
    sid = record_snapshot(lcon, raw, game_date, rows, path)
    seen: set[str] = set()
    for r in rows:
        if r.game_id in seen or r.game_id in tips:
            continue
        seen.add(r.game_id)
        tip = tip_from_status_text(r.game_status_text, game_date) if r.game_status == 1 else None
        if tip is not None:
            upsert_tip(lcon, r.game_id, game_date, tip, "status_text", raw.fetched_at)
    return PollResult("fetched", reason, sid, raw.status, len({r.game_id for r in rows}), len(rows))
