"""``--schedule-from-db``: slate from the games table, results ignored, 19:00 ET tip."""

from __future__ import annotations

from datetime import date, datetime

import duckdb

from nba.daily.schedule import schedule_from_db, slate_for_date
from tests.daily.conftest import T1, T2, T3, T4
from tests.fixtures.loader import build_fixture_db


def test_schedule_from_db_slate_and_tip() -> None:
    con = build_fixture_db()
    con.execute(
        "INSERT INTO games VALUES ('0022400001', '2024-10-22', 2024, ?, ?, NULL, NULL, NULL)",
        [T1, T2],
    )
    con.execute(
        "INSERT INTO games VALUES ('0022400002', '2024-10-23', 2024, ?, ?, 100, 90, NULL)", [T3, T4]
    )
    games = schedule_from_db(con)("2024-25")
    assert {g.game_id for g in games} == {"0022400001", "0022400002"}
    day = slate_for_date(games, date(2024, 10, 22))
    assert [g.game_id for g in day] == ["0022400001"]  # NULL-score game is a normal slate game
    assert day[0].tipoff == datetime(2024, 10, 22, 23, 0)  # 19:00 EDT == 23:00 UTC
    assert (day[0].home_team, day[0].away_team) == (T1, T2)
    assert isinstance(con, duckdb.DuckDBPyConnection)
