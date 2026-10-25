"""News events from data we already collect: lineup changes and new official injury reports.

Read-only. The lineups DB is opened ``read_only`` (its writer is the 5-minute lineups job; a busy
lock means "news unavailable this tick", counted, never a crash). An injury report PDF carries every
game of the day, so a new report is an event for every game (the PDF is not parsed here).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class NewsEvent:
    ts: datetime  # naive UTC, when we saw it
    kind: str  # lineup | injury_report
    home: int | None  # None = applies to every game
    away: int | None
    detail: str


_SQL = """
SELECT snapshot_id, max(fetched_at) AS fetched_at, game_id, team_id,
       string_agg(CAST(player_id AS VARCHAR) || ':' || coalesce(lineup_status, '') || ':'
                  || CAST(coalesce(announced_starter, false) AS VARCHAR), ','
                  ORDER BY player_id) AS fp
FROM lineup_snapshots_dedup
WHERE fetched_at >= ?
GROUP BY snapshot_id, game_id, team_id
ORDER BY fetched_at, snapshot_id
"""


def lineup_events(db: Path, baseline_since: datetime) -> list[NewsEvent] | None:
    """Changes of a team's (player, status, starter) fingerprint between successive snapshots.

    ``baseline_since`` should be a few hours back so the first in-window snapshot has a predecessor.
    None when the DB is missing or locked."""
    if not db.exists():
        return None
    try:
        con = duckdb.connect(str(db), read_only=True)
    except duckdb.Error:
        return None
    try:
        rows = con.execute(_SQL, [baseline_since]).fetchall()
        teams = con.execute(
            "SELECT game_id, max(CASE WHEN is_home THEN team_id END), "
            "max(CASE WHEN NOT is_home THEN team_id END) FROM lineup_snapshots GROUP BY game_id"
        ).fetchall()
    except duckdb.Error:
        return None
    finally:
        con.close()
    ha = {str(g): (h, a) for g, h, a in teams}
    prev: dict[tuple[str, int], str] = {}
    out: list[NewsEvent] = []
    for _sid, ts, gid, tid, fp in rows:
        k = (str(gid), int(tid))
        if k in prev and prev[k] != fp:
            h, a = ha.get(str(gid), (None, None))
            out.append(NewsEvent(ts, "lineup", h, a, f"{gid} team {tid} lineup changed"))
        prev[k] = fp
    return out


def injury_baseline(injury_dir: Path) -> set[str]:
    return (
        {p.name for p in injury_dir.glob("Injury-Report_*.pdf")} if injury_dir.is_dir() else set()
    )


def injury_events(injury_dir: Path, known: set[str]) -> list[NewsEvent]:
    """PDFs not in ``known`` (which is updated in place). Event time = file mtime."""
    out: list[NewsEvent] = []
    if not injury_dir.is_dir():
        return out
    for p in sorted(injury_dir.glob("Injury-Report_*.pdf")):
        if p.name in known:
            continue
        known.add(p.name)
        ts = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).replace(tzinfo=None)
        out.append(NewsEvent(ts, "injury_report", None, None, p.name))
    return out


def recent(events: list[NewsEvent], now: datetime, window_min: float) -> list[NewsEvent]:
    return [e for e in events if 0 <= (now - e.ts).total_seconds() <= window_min * 60]
