"""Players ruled OUT on the injury report, known at serve time (read-only consumer gate).

The production forward store writes no row for a player who is newly ruled OUT after the morning
run (exclusions write no row), so his earlier forward row stays the latest and carries its old
``p_play`` (the 2026-11-02 Brown/George case: p_play 0.91 from a 07:00 row while he was OUT at
14:15 ET). Consumers that price props (``nba.parlay`` and JARVIS) refuse such a player instead of
pricing him as if he plays. This module only READS ``player_availability``; it changes no forecast,
no stored row and no scoring (``docs/FORWARD_PREREG_2026_27.md`` is untouched).

Rules (chosen before any result was seen):
  * status scope is ``out`` only; ``doubtful`` / ``questionable`` are NOT treated as out (the model
    trained on OUT only; doubtful-as-out is a separate, open question).
  * known-at: only report rows with ``as_of <= serve time`` count; a later report is never used for
    an earlier serve time.
  * the NEWEST such row per (game, player) decides, so OUT then upgraded is priced again.
  * a refused leg is refused, not repriced: Kalshi's DNP settlement is unread, so no price (not even
    p_play 0, which would make a No look certain) is offered.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import duckdb

ET = ZoneInfo("America/New_York")
OUT_STATUS = "out"
SKIP_REASON = "player_out_on_report"


@dataclass(frozen=True)
class OutNotice:
    as_of: datetime
    reason: str | None


def utc_to_et_naive(t: datetime) -> datetime:
    """Naive UTC -> naive ET wall clock. ``player_availability.as_of`` is stored as naive ET (the
    report's own clock, nba/daily/injury.py, nba/ingest/availability.py); comparing it with a naive
    UTC serve time would hide a report by 4-5 hours."""
    return t.replace(tzinfo=UTC).astimezone(ET).replace(tzinfo=None)


def table_exists(con: duckdb.DuckDBPyConnection, prefix: str = "") -> bool:
    cat = prefix.rstrip(".")
    sql = "SELECT count(*) FROM information_schema.tables WHERE table_name = 'player_availability'"
    if cat:
        sql += " AND table_catalog = ?"
    row = con.execute(sql, [cat] if cat else []).fetchone()
    return bool(row and row[0])


def load_out_on_report(
    con: duckdb.DuckDBPyConnection,
    game_ids: Iterable[str],
    as_of: datetime,
    prefix: str = "",
) -> dict[tuple[str, int], OutNotice]:
    """(game_id, player_id) -> notice for players whose newest report row known at ``as_of``
    (the serve time, NAIVE UTC) says OUT. The table's ``as_of`` is naive ET, so the serve time is
    converted before binding; ``OutNotice.as_of`` is that stored ET wall clock. Empty when the
    table is absent (the caller says so in its notes)."""
    gids = sorted({str(g) for g in game_ids})
    if not gids or not table_exists(con, prefix):
        return {}
    marks = ",".join("?" for _ in gids)
    cols = {r[0] for r in con.execute(f"DESCRIBE {prefix}player_availability").fetchall()}
    why = "reason" if "reason" in cols else "NULL"
    tie = ", pulled_at DESC" if "pulled_at" in cols else ""
    rows = con.execute(
        f"SELECT game_id, player_id, lower(status), as_of, {why} FROM ("  # noqa: S608
        f"SELECT *, row_number() OVER (PARTITION BY game_id, player_id "
        f"ORDER BY as_of DESC{tie}) rn FROM {prefix}player_availability "
        f"WHERE game_id IN ({marks}) AND player_id IS NOT NULL AND as_of <= ?) WHERE rn = 1",
        [*gids, utc_to_et_naive(as_of)],
    ).fetchall()
    return {
        (str(g), int(p)): OutNotice(a, None if r is None else str(r))
        for g, p, s, a, r in rows
        if s == OUT_STATUS
    }
