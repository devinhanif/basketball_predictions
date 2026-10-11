"""lineup_snapshots_latest: one row per player per snapshot; starter is OR, status is latest."""

from __future__ import annotations

from datetime import date, datetime

import duckdb

from nba.lineups.store import DDL


def _row(pid: int, status: str, starter: bool, pos: str, ts: datetime) -> tuple[object, ...]:
    fetched = datetime(2026, 10, 9, 23, 17)
    return (
        "s1", fetched, date(2026, 10, 9), "0012600037", 1, "8:00 pm ET", 1610612763, "MEM", False,
        pid, f"P{pid}", status, starter, pos, "Active", ts,
    )  # fmt: skip


def test_duplicate_player_rows_collapse_and_keep_the_starter() -> None:
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    exp, conf = datetime(2026, 10, 9, 23, 11, 11), datetime(2026, 10, 9, 23, 12)
    rows = [_row(1, "Expected", True, "C", exp), _row(1, "Confirmed", False, "", conf)]
    rows += [_row(p, "Confirmed", False, "", conf) for p in range(2, 8)]
    con.executemany("INSERT INTO lineup_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    raw = con.execute("SELECT count(*) FROM lineup_snapshots").fetchone()[0]
    out = con.execute("SELECT * FROM lineup_snapshots_latest ORDER BY player_id").fetchall()
    assert raw == 8 and len(out) == 7  # raw rows untouched, view collapses the duplicate
    p1 = con.execute(
        "SELECT lineup_status, announced_starter, position FROM lineup_snapshots_latest "
        "WHERE player_id = 1"
    ).fetchone()
    assert p1 == ("Confirmed", True, "C")
    assert (
        con.execute("SELECT count(DISTINCT lineup_status) FROM lineup_snapshots_latest").fetchone()[
            0
        ]
        == 1
    )
