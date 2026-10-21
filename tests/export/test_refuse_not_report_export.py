"""Plan item A (2026-10-20): the export takes teams from the schedule cache when `games` lacks the
game (OKC@SAS, which the started-games finder never returned) and never shows a withdrawn player."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from nba.daily.schedule import ScheduledGame, save_schedule_cache
from nba.daily.store import ensure_tables
from nba.export import Window, forward_predictions
from tests.fixtures.loader import build_fixture_db

DAY = date(2026, 10, 28)
TIP = datetime(2026, 10, 28, 23, 30)
GID = "0022600003"
HOME, AWAY = 1610612760, 1610612759  # OKC, SAS
Q = [float(i) for i in range(2, 21)]
PRED = json.dumps(
    {"mean": 12.0, "std": 4.0, "p_play": 0.9, "proj_minutes": 30.0, "q10": 4.0, "q50": 12.0,
     "q90": 14.0, "q_grid": Q, "p_ge": {"10": 0.4}, "p_ge_full": {"c": [198, 150, 0], "n": 199}}
)  # fmt: skip


def _db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    con = build_fixture_db(":memory:")
    ensure_tables(con)
    made = TIP - timedelta(minutes=90)
    for pid in (1000, 1001):  # the game has no `games` row at all
        con.execute(
            "INSERT INTO forward_predictions VALUES ('r1', ?, ?, ?, 'props_context_residual', "
            "'v1', 'pts', ?, ?)",
            [made, GID, TIP, pid, PRED],
        )
    cache = tmp_path / "sched"
    save_schedule_cache([ScheduledGame(GID, TIP, HOME, AWAY)], "2026-27", cache)
    return con


def test_export_teams_come_from_the_schedule_cache_when_games_lacks_the_game(
    tmp_path: Path,
) -> None:
    con = _db(tmp_path)
    fwd = forward_predictions(con, Window(DAY), schedule_cache_dir=tmp_path / "sched")
    (g,) = [x for x in fwd.games if x["game_id"] == GID]
    assert (g["home_team"], g["away_team"]) == (HOME, AWAY)


def test_export_without_cache_or_win_row_keeps_teams_unknown(tmp_path: Path) -> None:
    con = _db(tmp_path)
    fwd = forward_predictions(con, Window(DAY), schedule_cache_dir=tmp_path / "nowhere")
    (g,) = [x for x in fwd.games if x["game_id"] == GID]
    assert (g["home_team"], g["away_team"]) == (None, None)


def test_export_omits_a_withdrawn_player(tmp_path: Path) -> None:
    con = _db(tmp_path)
    con.execute(
        "INSERT INTO forward_withdrawals VALUES (?, 'r2', ?, 'props_context_residual', 'pts', "
        "1001, ?, 'official_roster_team_not_in_game')",
        [TIP - timedelta(minutes=60), GID, 1610612738],
    )
    fwd = forward_predictions(con, Window(DAY), schedule_cache_dir=tmp_path / "sched")
    assert {p["player_id"] for p in fwd.prop_grids} == {1000}
    n = con.execute("SELECT count(*) FROM forward_predictions WHERE player_id = 1001").fetchone()
    assert n == (1,)  # append-only: the forecast row is still in the log
