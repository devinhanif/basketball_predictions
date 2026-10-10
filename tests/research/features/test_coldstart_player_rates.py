"""No-leakage + cold-start bucket tests for ``research.features.player_features``.

Mirrors the discipline in ``tests/ml/test_no_leakage.py`` (team features)
but for the player-level career-possession proxy and the real cold-start
bucket slice definition that replaces the games-played placeholder.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from research.coldstart.config import CAREER_POSS_COLD_START_THRESHOLD
from research.eval.slices import cold_start_bucket_slice
from research.features.player_features import (
    POSSESSIONS_PER_MINUTE,
    build_game_cold_start_flags,
    build_player_career_poss_proxy,
    log1p_n_poss,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection, game_id: str, game_date: str, home: int, away: int
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",  # noqa: E501
        [game_id, game_date, 2023, home, away, 100, 90],
    )


def _insert_stat(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    minutes: float,
    starter: bool,
) -> None:
    con.execute(
        "INSERT INTO player_game_stats "
        "(game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter) "
        "VALUES (?, ?, ?, ?, 10, 5, 3, 1, 1, 0, 2, ?)",
        [game_id, player_id, team_id, minutes, starter],
    )


def test_career_poss_proxy_is_zero_on_first_career_game(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2)
    _insert_stat(con, "g1", 501, 1, 30.0, starter=True)

    proxy = build_player_career_poss_proxy(con)
    row = proxy.filter(pl.col("game_id") == "g1").row(0, named=True)
    assert row["career_poss_proxy_prior"] == pytest.approx(0.0)


def test_career_poss_proxy_accumulates_strictly_prior_games_only(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2)
    _insert_game(con, "g2", "2023-10-26", 1, 3)
    _insert_stat(con, "g1", 501, 1, 30.0, starter=True)
    _insert_stat(con, "g2", 501, 1, 20.0, starter=True)

    proxy = build_player_career_poss_proxy(con)
    row_g1 = proxy.filter(pl.col("game_id") == "g1").row(0, named=True)
    row_g2 = proxy.filter(pl.col("game_id") == "g2").row(0, named=True)
    assert row_g1["career_poss_proxy_prior"] == pytest.approx(0.0)
    assert row_g2["career_poss_proxy_prior"] == pytest.approx(30.0 * POSSESSIONS_PER_MINUTE)


def test_future_game_never_changes_an_earlier_games_proxy(con: duckdb.DuckDBPyConnection) -> None:
    """Planted-future-game no-leakage check (same style as team_features)."""
    _insert_game(con, "g1", "2023-10-24", 1, 2)
    _insert_stat(con, "g1", 501, 1, 30.0, starter=True)

    before = build_player_career_poss_proxy(con).filter(pl.col("game_id") == "g1")
    row_before = before.row(0, named=True)

    _insert_game(con, "g2_future", "2023-12-01", 1, 9)
    _insert_stat(con, "g2_future", 501, 1, 48.0, starter=True)  # huge future minutes

    after = build_player_career_poss_proxy(con).filter(pl.col("game_id") == "g1")
    row_after = after.row(0, named=True)
    assert row_after["career_poss_proxy_prior"] == pytest.approx(
        row_before["career_poss_proxy_prior"]
    )


def test_cold_start_flag_true_when_a_starter_is_under_threshold(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2)
    # a brand-new starter: 0 prior possessions, well under the threshold
    _insert_stat(con, "g1", 501, 1, 30.0, starter=True)

    flags = build_game_cold_start_flags(con)
    row = flags.filter(pl.col("game_id") == "g1").row(0, named=True)
    assert row["any_starter_cold_start"] is True


def test_cold_start_flag_false_once_starter_crosses_threshold(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # Give player 501 enough prior minutes across many games to clear the
    # 500-possession threshold before the game under test.
    n_warm_games = 20
    for i in range(n_warm_games):
        gid = f"warm{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}", 1, 2)
        _insert_stat(con, gid, 501, 1, 36.0, starter=True)

    _insert_game(con, "g_test", "2023-11-25", 1, 2)
    _insert_stat(con, "g_test", 501, 1, 30.0, starter=True)

    career_poss_before_test_game = n_warm_games * 36.0 * POSSESSIONS_PER_MINUTE
    assert career_poss_before_test_game >= CAREER_POSS_COLD_START_THRESHOLD

    flags = build_game_cold_start_flags(con)
    row = flags.filter(pl.col("game_id") == "g_test").row(0, named=True)
    assert row["any_starter_cold_start"] is False


def test_non_starters_never_affect_the_cold_start_flag(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2)
    n_warm_games = 20
    for i in range(n_warm_games):
        gid = f"warm{i}"
        _insert_game(con, gid, f"2023-09-{i + 1:02d}", 1, 2)
        _insert_stat(con, gid, 601, 1, 36.0, starter=True)
    # the target game's only starter is warmed-up player 601; a cold
    # non-starter (502, 0 history) on the bench must not flip the flag.
    _insert_stat(con, "g1", 601, 1, 30.0, starter=True)
    _insert_stat(con, "g1", 502, 1, 5.0, starter=False)

    flags = build_game_cold_start_flags(con)
    row = flags.filter(pl.col("game_id") == "g1").row(0, named=True)
    assert row["any_starter_cold_start"] is False


def test_cold_start_bucket_slice_uses_the_flag_column() -> None:
    df = pl.DataFrame({"any_starter_cold_start": [True, False, None]})
    slices = cold_start_bucket_slice(df)
    assert slices["cold_start_bucket_low_career_poss"].tolist() == [True, False, True]
    assert slices["cold_start_bucket_warm"].tolist() == [False, True, False]


def test_log1p_n_poss_monotonic_and_zero_at_zero() -> None:
    import numpy as np

    n = np.array([0.0, 10.0, 1000.0])
    out = log1p_n_poss(n)
    assert out[0] == 0.0
    assert out[0] < out[1] < out[2]
