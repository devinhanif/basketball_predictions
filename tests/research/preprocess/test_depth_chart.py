"""Tests for ``research.preprocess.depth_chart`` (depth-chart rank + minutes-
ceiling hazard).

Synthetic in-memory DuckDB only -- no real DB access, fast (CLAUDE.md "no
multi-minute jobs"). Mirrors ``tests/ml/test_player_rebound_assist_features.
py``'s conventions for the companion as-of feature module.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from research.preprocess.depth_chart import (
    NEW_PLAYER_HAZARD_DEFAULT,
    OUTPUT_COLUMNS,
    build_depth_chart_features,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection, game_id: str, game_date: str, home: int = 1, away: int = 2
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, 2023, ?, ?, 100, 90)",
        [game_id, game_date, home, away],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    minutes: float | None,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, true)
        """,
        [game_id, player_id, team_id, minutes],
    )


def _row(df: pl.DataFrame, game_id: str, player_id: int) -> dict[str, object]:
    sub = df.filter(pl.col("game_id") == game_id)
    sub = sub.filter(pl.col("player_id") == player_id)
    assert sub.height == 1, f"expected exactly 1 row for {game_id}/{player_id}, got {sub.height}"
    return sub.to_dicts()[0]


def test_output_contract_columns(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, 1, 30.0)
    _insert_pgs(con, "g1", 102, 1, 20.0)
    out = build_depth_chart_features(con)
    assert out.columns == OUTPUT_COLUMNS
    assert out.height == 2


def test_debut_players_rank_last_default_hazard(con: duckdb.DuckDBPyConnection) -> None:
    """Every player's first-ever game: no trailing history exists, so
    hazard falls back to the documented NEW_PLAYER_HAZARD_DEFAULT and rank
    ties break on ascending player_id."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 201, 1, 30.0)
    _insert_pgs(con, "g1", 101, 1, 20.0)
    out = build_depth_chart_features(con)
    row_101 = _row(out, "g1", 101)
    row_201 = _row(out, "g1", 201)
    assert row_101["minutes_ceiling_hazard"] == NEW_PLAYER_HAZARD_DEFAULT
    assert row_201["minutes_ceiling_hazard"] == NEW_PLAYER_HAZARD_DEFAULT
    # Both have avg_minutes_prior == 0.0 (no history) -> tie -> ascending
    # player_id breaks the tie: 101 ranks above 201.
    assert row_101["depth_chart_rank"] == 1
    assert row_201["depth_chart_rank"] == 2


def test_rank_orders_by_trailing_minutes(con: duckdb.DuckDBPyConnection) -> None:
    """Build a trailing history where player 301 has clearly higher
    trailing minutes than 302, then check the ranking on a later game."""
    for i in range(5):
        gid = f"hist{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}")
        _insert_pgs(con, gid, 301, 1, 35.0)
        _insert_pgs(con, gid, 302, 1, 10.0)

    _insert_game(con, "g_target", "2023-11-01")
    _insert_pgs(con, "g_target", 301, 1, 999.0)  # own-game minutes must not matter
    _insert_pgs(con, "g_target", 302, 1, 999.0)

    out = build_depth_chart_features(con)
    row_301 = _row(out, "g_target", 301)
    row_302 = _row(out, "g_target", 302)
    assert row_301["depth_chart_rank"] == 1
    assert row_302["depth_chart_rank"] == 2


def test_hazard_in_unit_interval(con: duckdb.DuckDBPyConnection) -> None:
    for i in range(10):
        gid = f"hist{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}")
        _insert_pgs(con, gid, 401, 1, float(5 + 30 * (i % 2)))  # highly volatile
        _insert_pgs(con, gid, 402, 1, 10.0)

    _insert_game(con, "g_target", "2023-11-15")
    _insert_pgs(con, "g_target", 401, 1, 0.0)
    _insert_pgs(con, "g_target", 402, 1, 0.0)

    out = build_depth_chart_features(con)
    for row in out.to_dicts():
        assert 0.0 <= row["minutes_ceiling_hazard"] <= 1.0


def test_dnp_null_minutes_treated_as_zero(con: duckdb.DuckDBPyConnection) -> None:
    """A DNP row (NULL minutes) should not crash and should be treated as
    0 minutes played in the trailing average, same discipline as
    nba.props.minutes.fetch_actual_minutes's DNP-coalesce."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 501, 1, None)
    _insert_pgs(con, "g1", 502, 1, 30.0)
    out = build_depth_chart_features(con)
    assert out.height == 2
    for row in out.to_dicts():
        assert row["minutes_ceiling_hazard"] == row["minutes_ceiling_hazard"]  # not NaN


def test_planted_future_game_no_leakage(con: duckdb.DuckDBPyConnection) -> None:
    """Planted-future-game proof (CLAUDE.md "no leakage"): a wild future
    game's minutes must not change an earlier game's as-of feature row."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 601, 1, 25.0)
    _insert_pgs(con, "g1", 602, 1, 15.0)

    _insert_game(con, "g2", "2023-10-26")
    _insert_pgs(con, "g2", 601, 1, 28.0)
    _insert_pgs(con, "g2", 602, 1, 12.0)

    before = build_depth_chart_features(con)
    snapshot = _row(before, "g1", 601)

    # Plant an extreme future game: player 601's minutes explode, which
    # would (if leaked) massively change their own trailing avg as of g1.
    for i in range(50):
        gid = f"future_{i}"
        _insert_game(con, gid, "2024-01-01")
        _insert_pgs(con, gid, 601, 1, 48.0)
        _insert_pgs(con, gid, 602, 1, 0.0)

    after = build_depth_chart_features(con)
    assert _row(after, "g1", 601) == snapshot


def test_empty_db_returns_empty_frame(con: duckdb.DuckDBPyConnection) -> None:
    out = build_depth_chart_features(con)
    assert out.height == 0
    assert out.columns == OUTPUT_COLUMNS
