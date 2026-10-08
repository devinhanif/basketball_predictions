"""Tests for ``nba.preprocess.embeddings`` (player co-occurrence embeddings).

Synthetic in-memory DuckDB only -- inserts ``possessions`` rows directly
(the committed fixture's ``build_fixture_db`` does not populate
``possessions``, see ``tests/fixtures/loader.py``), fast, no network.
"""

from __future__ import annotations

import duckdb
import numpy as np
import pytest

from nba.db.connect import connect
from nba.preprocess.embeddings import (
    build_cooccurrence_counts,
    build_player_embeddings,
    k_nearest_players,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection, game_id: str, game_date: str, season: int = 2023
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, ?, 1, 2, 100, 90)",
        [game_id, game_date, season],
    )


def _insert_poss(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    poss_idx: int,
    off_players: list[int] | None,
    def_players: list[int] | None,
) -> None:
    con.execute(
        """
        INSERT INTO possessions
            (game_id, poss_idx, period, off_team, def_team, off_players, def_players, outcome)
        VALUES (?, ?, 1, 1, 2, ?, ?, 'FGA_miss')
        """,
        [game_id, poss_idx, off_players, def_players],
    )


OFF_A = [1, 2, 3, 4, 5]
DEF_A = [11, 12, 13, 14, 15]
OFF_B = [6, 7, 8, 9, 10]


def test_build_cooccurrence_counts_symmetric_no_self_pairs() -> None:
    groups = [[1, 2, 3, 4, 5]]
    vocab, counts, cooc = build_cooccurrence_counts(groups)
    assert vocab == [1, 2, 3, 4, 5]
    assert np.all(counts == 1.0)
    assert np.all(np.diag(cooc) == 0.0)
    assert np.array_equal(cooc, cooc.T)
    # Every pair among the 5 co-occurred exactly once.
    assert cooc[0, 1] == 1.0
    assert cooc[0, 4] == 1.0


def test_empty_possessions_returns_empty_dict(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    out = build_player_embeddings(con, "2023-12-01")
    assert out == {}


def test_embeddings_built_for_as_of_window(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    for i in range(20):
        _insert_poss(con, "g1", i, OFF_A, DEF_A)
        _insert_poss(con, "g1", 1000 + i, OFF_B, DEF_A)
    out = build_player_embeddings(con, "2023-12-01", dim=4)
    for pid in [*OFF_A, *DEF_A, *OFF_B]:
        assert pid in out
        assert out[pid].shape == (4,)
        assert np.all(np.isfinite(out[pid]))


def test_min_count_drops_rare_players(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    for i in range(20):
        _insert_poss(con, "g1", i, OFF_A, DEF_A)
    # One single rare appearance of player 999 in a different lineup.
    _insert_poss(con, "g1", 999, [999, 2, 3, 4, 5], DEF_A)
    out = build_player_embeddings(con, "2023-12-01", dim=4, min_count=5)
    assert 999 not in out
    assert 1 in out  # appears 20+ times, well above the floor


def test_knn_excludes_self_and_sorts_descending() -> None:
    embeddings = {
        1: np.array([1.0, 0.0]),
        2: np.array([0.9, 0.1]),
        3: np.array([-1.0, 0.0]),
    }
    neighbors = k_nearest_players(embeddings, 1, k=2)
    assert len(neighbors) == 2
    assert neighbors[0][0] == 2  # most similar (same direction)
    assert all(pid != 1 for pid, _ in neighbors)
    sims = [s for _, s in neighbors]
    assert sims == sorted(sims, reverse=True)


def test_knn_missing_player_returns_empty() -> None:
    assert k_nearest_players({1: np.array([1.0])}, 404) == []


def test_null_lineups_are_skipped_not_crash(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_poss(con, "g1", 0, None, None)
    for i in range(1, 21):
        _insert_poss(con, "g1", i, OFF_A, DEF_A)
    out = build_player_embeddings(con, "2023-12-01", dim=4)
    assert 1 in out


def test_season_filter_restricts_window(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g_old", "2022-10-24", season=2022)
    for i in range(20):
        _insert_poss(con, "g_old", i, OFF_A, DEF_A)
    _insert_game(con, "g_new", "2023-10-24", season=2023)
    for i in range(20):
        _insert_poss(con, "g_new", 2000 + i, OFF_B, DEF_A)

    out_2023_only = build_player_embeddings(con, "2023-12-01", dim=4, season=2023)
    # Players from the 2022-only lineup never co-occurred in a 2023 game,
    # so they should not appear when restricted to season=2023.
    assert 1 not in out_2023_only
    assert 6 in out_2023_only


def test_planted_future_game_no_leakage(con: duckdb.DuckDBPyConnection) -> None:
    """A future game's wild new co-occurrence pattern must not change an
    embedding fit as-of an earlier date."""
    _insert_game(con, "g1", "2023-10-24")
    for i in range(20):
        _insert_poss(con, "g1", i, OFF_A, DEF_A)

    before = build_player_embeddings(con, "2023-11-01", dim=4, seed=0)
    snapshot = {pid: vec.copy() for pid, vec in before.items()}

    _insert_game(con, "g_future", "2023-12-15")
    for i in range(50):
        _insert_poss(con, "g_future", i, [1, 999, 998, 997, 996], [995, 994, 993, 992, 991])

    after = build_player_embeddings(con, "2023-11-01", dim=4, seed=0)
    assert set(after.keys()) == set(snapshot.keys())
    for pid, vec in snapshot.items():
        assert np.allclose(after[pid], vec)


def test_deterministic_with_fixed_seed(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    for i in range(20):
        _insert_poss(con, "g1", i, OFF_A, DEF_A)
        _insert_poss(con, "g1", 1000 + i, OFF_B, DEF_A)
    out1 = build_player_embeddings(con, "2023-12-01", dim=4, seed=7)
    out2 = build_player_embeddings(con, "2023-12-01", dim=4, seed=7)
    for pid in out1:
        assert np.allclose(out1[pid], out2[pid])
