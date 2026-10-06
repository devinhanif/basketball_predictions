"""MANDATORY no-leakage test for the minutes model (CLAUDE.md risk #3:
"At prediction time use projected minutes ... never the actual box-score
minutes/lineups. A test must prove it.").

Two complementary checks, mirroring ``tests/ml/test_no_leakage.py``'s
pattern:

1. Structural: :func:`build_minutes_features` never returns a raw
   ``minutes`` column at all -- the predictor physically cannot read it.
2. Planted-future-game: snapshot an earlier game's minutes prediction,
   then insert a *future* game for the same player with a wildly
   different (extreme) actual minutes value, and assert the earlier
   prediction is byte-for-byte unchanged.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.props.minutes import MINUTES_FEATURE_COLUMNS, build_minutes_features, predict_minutes


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_player_row(
    con: duckdb.DuckDBPyConnection, game_id: str, player_id: int, minutes: float
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov, starter)
        VALUES (?, ?, 1, ?, 10, 5, 3, 1, 1, 0, 1, true)
        """,
        [game_id, player_id, minutes],
    )


def test_minutes_features_never_include_raw_minutes(con: duckdb.DuckDBPyConnection) -> None:
    """Structural proof: the feature frame physically cannot leak the target
    game's own minutes because the column doesn't exist in it."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, 30.0)

    feats = build_minutes_features(con)
    assert "minutes" not in feats.columns
    for col in MINUTES_FEATURE_COLUMNS:
        assert col in feats.columns


def test_future_extreme_minutes_do_not_change_earlier_prediction(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_player_row(con, "g1", 101, 20.0)
    _insert_game(con, "g2", "2023-10-26")
    _insert_player_row(con, "g2", 101, 22.0)
    _insert_game(con, "g3", "2023-10-28")
    _insert_player_row(con, "g3", 101, 24.0)

    feats_before = build_minutes_features(con).filter(pl.col("game_id") == "g3")
    dist_before = predict_minutes(feats_before)[0]

    # Plant a future game (after g3) with an extreme, wildly different
    # actual minutes value for the same player. If the window frame ever
    # leaked same-or-later rows, g3's prediction would change.
    _insert_game(con, "g4_future", "2023-11-05")
    _insert_player_row(con, "g4_future", 101, 0.0)  # extreme: a DNP

    feats_after = build_minutes_features(con).filter(pl.col("game_id") == "g3")
    dist_after = predict_minutes(feats_after)[0]

    assert dist_after.params() == dist_before.params()
    assert dist_after.mean() == pytest.approx(dist_before.mean())


def test_predict_minutes_is_pure_function_of_features(con: duckdb.DuckDBPyConnection) -> None:
    """predict_minutes() takes only the as-of feature frame -- it has no
    other way to reach into player_game_stats, so this also serves as a
    type-level leakage guard: its signature has nowhere to pass raw
    minutes even if a future refactor wanted to."""
    import inspect

    sig = inspect.signature(predict_minutes)
    assert list(sig.parameters) == ["features", "config"]
