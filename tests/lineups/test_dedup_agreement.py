"""SQL view and Python twin of the frozen lineup dedup rule must agree."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from nba.lineups.store import (
    DDL,
    DEDUP_SELECT,
    DEFAULT_LINEUPS_DB,
    dedup_lineup_snapshots,
    dedup_snapshot_rows,
)

_COLS = "team_id, lineup_status, announced_starter, roster_status, player_id, source_ts"


def _py_result(con: duckdb.DuckDBPyConnection) -> set[tuple[object, ...]]:
    out: set[tuple[object, ...]] = set()
    blocks = con.execute("SELECT DISTINCT snapshot_id, game_id FROM lineup_snapshots").fetchall()
    for sid, gid in blocks:
        rows = con.execute(
            f"SELECT {_COLS} FROM lineup_snapshots WHERE snapshot_id=? AND game_id=? "  # noqa: S608
            "ORDER BY rowid",
            [sid, gid],
        ).fetchall()
        kept, _ = dedup_snapshot_rows(rows)
        out |= {(sid, gid, t, p, st, bool(s), ts) for t, st, s, _rs, p, ts in kept}
    return out


def _sql_result(con: duckdb.DuckDBPyConnection) -> set[tuple[object, ...]]:
    rows = con.execute(
        "SELECT snapshot_id, game_id, team_id, player_id, lineup_status, announced_starter, "
        f"source_ts FROM ({DEDUP_SELECT})"  # noqa: S608
    ).fetchall()
    return {(a, b, c, d, e, bool(f), g) for a, b, c, d, e, f, g in rows}


def _synthetic(con: duckdb.DuckDBPyConnection, seed: int = 7) -> None:
    rng = random.Random(seed)
    base = datetime(2026, 10, 11, 21, 0)
    rows = []
    for sid in ("s1", "s2"):
        for team in (1, 2):
            for pid in range(team * 100, team * 100 + 8):
                for _ in range(rng.choice([1, 1, 2, 3])):
                    rows.append((
                        sid, base, date(2026, 10, 11), "G1", 1, "t", team, "T", team == 1, pid,
                        f"p{pid}", rng.choice(["Expected", "Confirmed"]), rng.random() < 0.5,
                        "", "Active", base + timedelta(minutes=rng.randrange(0, 5)),
                    ))  # fmt: skip
    con.executemany("INSERT INTO lineup_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def test_sql_and_python_agree_on_synthetic_fixture() -> None:
    con = duckdb.connect(":memory:")
    con.execute(DDL)
    _synthetic(con)
    assert _sql_result(con) == _py_result(con)
    # the view and the helper return the same thing as the inline SELECT
    n_view = con.execute("SELECT count(*) FROM lineup_snapshots_dedup").fetchone()[0]  # type: ignore[index]
    assert n_view == len(_sql_result(con)) == dedup_lineup_snapshots(con).count("*").fetchone()[0]  # type: ignore[index]


@pytest.mark.skipif(not DEFAULT_LINEUPS_DB.exists(), reason="no local lineups store (CI)")
def test_sql_and_python_agree_on_the_real_store_read_only() -> None:
    try:
        con = duckdb.connect(str(DEFAULT_LINEUPS_DB), read_only=True)
    except duckdb.IOException:
        pytest.skip("lineups store locked by the collector")
    try:
        sql, py = _sql_result(con), _py_result(con)
    finally:
        con.close()
    assert sql == py
    assert len(sql) > 0 or Path(DEFAULT_LINEUPS_DB).stat().st_size > 0
