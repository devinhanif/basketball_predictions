"""Load the committed fixture dataset and assert basic coverage.

Tiny fixture so CI never needs network access (see tests/fixtures/).
"""

from __future__ import annotations

import duckdb
import pytest

from tests.fixtures.loader import build_fixture_db, load_pbp


@pytest.fixture
def fixture_con() -> duckdb.DuckDBPyConnection:
    con = build_fixture_db(":memory:")
    yield con
    con.close()


def test_games_table_has_two_teams_and_no_nulls(fixture_con: duckdb.DuckDBPyConnection) -> None:
    rows = fixture_con.execute("SELECT game_id, home_team, away_team FROM games").fetchall()
    assert len(rows) >= 3, "fixture must have at least 3 games"
    for game_id, home_team, away_team in rows:
        assert game_id is not None
        assert home_team is not None
        assert away_team is not None
        assert home_team != away_team


def test_box_score_rows_present_for_every_game(fixture_con: duckdb.DuckDBPyConnection) -> None:
    rows = fixture_con.execute(
        """
        SELECT g.game_id, COUNT(DISTINCT s.team_id) AS n_teams, COUNT(*) AS n_rows
        FROM games g
        LEFT JOIN player_game_stats s USING (game_id)
        GROUP BY g.game_id
        """
    ).fetchall()
    assert len(rows) >= 3
    for game_id, n_teams, n_rows in rows:
        assert n_rows > 0, f"{game_id} has no box score rows"
        assert n_teams == 2, f"{game_id} box score should cover both teams, got {n_teams}"


def test_no_null_primary_keys(fixture_con: duckdb.DuckDBPyConnection) -> None:
    null_games = fixture_con.execute("SELECT COUNT(*) FROM games WHERE game_id IS NULL").fetchone()[
        0
    ]
    null_stats = fixture_con.execute(
        "SELECT COUNT(*) FROM player_game_stats WHERE game_id IS NULL OR player_id IS NULL"
    ).fetchone()[0]
    assert null_games == 0
    assert null_stats == 0


def test_player_points_sum_to_team_total(fixture_con: duckdb.DuckDBPyConnection) -> None:
    """Internal consistency check on the hand-authored fixture."""
    rows = fixture_con.execute(
        """
        SELECT g.game_id, g.home_team, g.home_pts, g.away_team, g.away_pts,
               (SELECT SUM(pts) FROM player_game_stats s
                WHERE s.game_id = g.game_id AND s.team_id = g.home_team) AS home_sum,
               (SELECT SUM(pts) FROM player_game_stats s
                WHERE s.game_id = g.game_id AND s.team_id = g.away_team) AS away_sum
        FROM games g
        """
    ).fetchall()
    for _game_id, _home_team, home_pts, _away_team, away_pts, home_sum, away_sum in rows:
        assert home_sum == home_pts
        assert away_sum == away_pts


def test_pbp_fixture_loads_and_covers_every_game(fixture_con: duckdb.DuckDBPyConnection) -> None:
    pbp = load_pbp()
    game_ids = {row[0] for row in fixture_con.execute("SELECT game_id FROM games").fetchall()}
    assert set(pbp.keys()) == game_ids
    for game_id, df in pbp.items():
        assert len(df) > 0, f"{game_id} pbp fixture is empty"
        assert "action_type" in df.columns
