"""Builders for synthetic lineup snapshots (no network)."""

from __future__ import annotations

from datetime import date, datetime

import duckdb

from nba.lineups.source import CONFIRMED, EXPECTED, LineupRow, RawFetch
from nba.lineups.store import record_snapshot


def team_rows(
    game_id: str,
    team_id: int,
    is_home: bool,
    starters: list[int],
    bench: list[int],
    *,
    status: str = CONFIRMED,
    inactive: tuple[int, ...] = (),
    status_text: str = "7:00 pm ET",
    game_status: int = 1,
    source_ts: datetime | None = None,
) -> list[LineupRow]:
    out: list[LineupRow] = []
    pos = ["PG", "SG", "SF", "PF", "C"]
    for i, pid in enumerate(starters):
        out.append(
            LineupRow(game_id, game_status, status_text, team_id, "T", is_home, pid, f"p{pid}",
                      status, pos[i], True, "Active", source_ts)
        )  # fmt: skip
    if status == CONFIRMED:  # a confirmed block lists the whole roster
        for pid in bench:
            rs = "Inactive" if pid in inactive else "Active"
            out.append(
                LineupRow(game_id, game_status, status_text, team_id, "T", is_home, pid,
                          f"p{pid}", status, "", False, rs, source_ts)
            )  # fmt: skip
    return out


def add_snapshot(
    lcon: duckdb.DuckDBPyConnection,
    fetched_at: datetime,
    game_date: date,
    rows: list[LineupRow],
) -> str:
    body = (
        repr(sorted((r.game_id, r.player_id, r.lineup_status) for r in rows))
        + fetched_at.isoformat()
    ).encode()
    fetch = RawFetch("test://lineups", 200, body, fetched_at)
    return record_snapshot(lcon, fetch, game_date, rows, None)


__all__ = ["CONFIRMED", "EXPECTED", "add_snapshot", "team_rows"]
