"""Tests for ``nba.coldstart.archetypes``' DB-driven as-of builder
(:func:`build_player_archetypes`, :func:`build_archetype_history`,
:func:`flag_archetype_changes`).

Synthetic in-memory DuckDB, mirroring the conventions in
``tests/ml/test_player_rebound_assist_features.py`` for the no-leakage
proof.
"""

from __future__ import annotations

import duckdb
import pytest

from nba.coldstart.archetypes import (
    build_archetype_history,
    build_player_archetypes,
    encode_position_fractions,
    flag_archetype_changes,
)
from nba.db.connect import connect


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    fga: int = 5,
    fgm: int = 2,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov,
             starter, fgm, fga, oreb, dreb)
        VALUES (?, ?, ?, 20.0, 10, 2, 1, 0, 0, 0, 0, true, ?, ?, 0, 0)
        """,
        [game_id, player_id, team_id, fgm, fga],
    )


def _insert_static(
    con: duckdb.DuckDBPyConnection,
    player_id: int,
    position: str,
    height_in: float,
    weight_lb: float,
    birth_date: str = "1998-01-01",
) -> None:
    con.execute(
        "INSERT INTO players_static (player_id, position, height_in, weight_lb, birth_date) "
        "VALUES (?, ?, ?, ?, ?)",
        [player_id, position, height_in, weight_lb, birth_date],
    )


def test_encode_position_fractions_handles_full_words_and_hybrids() -> None:
    assert encode_position_fractions("Guard") == (1.0, 0.0, 0.0)
    assert encode_position_fractions("Center") == (0.0, 0.0, 1.0)
    g, f, c = encode_position_fractions("Guard-Forward")
    assert g == pytest.approx(0.5)
    assert f == pytest.approx(0.5)
    assert c == 0.0
    assert encode_position_fractions(None) == (1 / 3, 1 / 3, 1 / 3)
    assert encode_position_fractions("") == (1 / 3, 1 / 3, 1 / 3)
    # Abbreviated forms (back-compat with the module's original encoding).
    assert encode_position_fractions("G") == (1.0, 0.0, 0.0)
    assert encode_position_fractions("F-C") == (0.0, 0.5, 0.5)


def test_build_player_archetypes_no_static_rows_degrades_gracefully(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Mirrors the real fixture DB today: no ``players_static`` rows at
    all. Must not crash, must say so in the note."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, 1)
    result = build_player_archetypes(con, as_of_date="2023-10-25", k=2, seed=0)
    assert result.assignments.height == 0
    assert "no players_static" in result.note


def test_build_player_archetypes_as_of_no_leakage(con: duckdb.DuckDBPyConnection) -> None:
    """Planted-future-game proof: a wild future game for a player must not
    change that player's archetype assignment as of an earlier date."""
    _insert_static(con, 101, "Guard", 74.0, 185.0)
    _insert_static(con, 102, "Center", 84.0, 260.0)
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, 1, fga=10, fgm=5)
    _insert_pgs(con, "g1", 102, 2, fga=8, fgm=4)

    before = build_player_archetypes(con, as_of_date="2023-10-25", k=2, seed=0)
    before_rows = {int(r["player_id"]): int(r["archetype"]) for r in before.assignments.to_dicts()}

    # Plant a wild future game (after the as_of date) for player 101.
    _insert_game(con, "g_future", "2024-06-01")
    _insert_pgs(con, "g_future", 101, 1, fga=40, fgm=1)
    _insert_pgs(con, "g_future", 102, 2, fga=0, fgm=0)

    after = build_player_archetypes(con, as_of_date="2023-10-25", k=2, seed=0)
    after_rows = {int(r["player_id"]): int(r["archetype"]) for r in after.assignments.to_dicts()}

    assert before_rows == after_rows


def test_build_player_archetypes_rookie_with_no_games_gets_default_tendencies(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A player with a ``players_static`` row but zero games before
    ``as_of_date`` (a true rookie) must still get an archetype, not be
    dropped, via the documented league-default tendency fallback."""
    _insert_static(con, 201, "Forward", 80.0, 220.0)
    _insert_static(con, 202, "Forward", 81.0, 225.0)
    result = build_player_archetypes(con, as_of_date="2023-10-01", k=1, seed=0)
    pids = {int(r["player_id"]) for r in result.assignments.to_dicts()}
    assert pids == {201, 202}


def test_k_selection_degrades_on_tiny_sample(con: duckdb.DuckDBPyConnection) -> None:
    _insert_static(con, 101, "Guard", 74.0, 185.0)
    _insert_static(con, 102, "Center", 84.0, 260.0)
    result = build_player_archetypes(con, as_of_date="2023-10-01", k=None, seed=0)
    assert "insufficient data" in result.note
    assert result.k_selection_method == "default (insufficient data)"


def test_flag_archetype_changes_matches_identical_snapshots_as_unchanged(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Re-clustering the exact same data at two dates should align labels
    to themselves (even though KMeans label integers are arbitrary) and
    report zero changes."""
    _insert_static(con, 101, "Guard", 74.0, 185.0)
    _insert_static(con, 102, "Center", 84.0, 260.0)
    _insert_static(con, 103, "Guard", 73.0, 180.0)
    _insert_static(con, 104, "Center", 85.0, 265.0)

    snap_a = build_player_archetypes(con, as_of_date="2023-10-01", k=2, seed=0)
    snap_b = build_player_archetypes(con, as_of_date="2023-11-01", k=2, seed=1)
    flags = flag_archetype_changes([snap_a, snap_b])
    assert len(flags) == 4
    assert all(not f.changed for f in flags)


def test_build_archetype_history_stacks_snapshots(con: duckdb.DuckDBPyConnection) -> None:
    _insert_static(con, 101, "Guard", 74.0, 185.0)
    _insert_static(con, 102, "Center", 84.0, 260.0)
    history = build_archetype_history(con, as_of_dates=["2023-10-01", "2023-11-01"], k=2, seed=0)
    assert history.height == 4
    assert set(history.get_column("as_of_date").to_list()) == {"2023-10-01", "2023-11-01"}
