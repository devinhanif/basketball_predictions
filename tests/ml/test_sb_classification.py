"""Tests for ``nba.coldstart.sb_classification`` (Syntetos-Boylan ADI/CV^2
volatility-bucket classification)."""

from __future__ import annotations

import duckdb
import pytest

from nba.coldstart.sb_classification import (
    ADI_CUTOFF,
    CV2_CUTOFF,
    INSUFFICIENT_HISTORY_BUCKET,
    build_sb_classification,
    classify_sb,
    compute_adi_cv2,
    summarize_sb_classes,
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


def _insert_pgs(con: duckdb.DuckDBPyConnection, game_id: str, player_id: int, pts: int) -> None:
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, stl, blk, tov, starter) VALUES (?, ?, 1, 20.0, ?, 0, 0, 0, 0, 0, 0, true)",
        [game_id, player_id, pts],
    )


def test_classify_sb_quadrants() -> None:
    assert classify_sb(ADI_CUTOFF - 0.1, CV2_CUTOFF - 0.1) == "smooth"
    assert classify_sb(ADI_CUTOFF - 0.1, CV2_CUTOFF + 0.1) == "erratic"
    assert classify_sb(ADI_CUTOFF + 0.1, CV2_CUTOFF - 0.1) == "intermittent"
    assert classify_sb(ADI_CUTOFF + 0.1, CV2_CUTOFF + 0.1) == "lumpy"
    assert classify_sb(float("nan"), 0.1) == INSUFFICIENT_HISTORY_BUCKET
    assert classify_sb(1.0, float("nan")) == INSUFFICIENT_HISTORY_BUCKET


def test_compute_adi_cv2_insufficient_history() -> None:
    adi, cv2, n = compute_adi_cv2([10.0, 12.0], min_games=10)
    assert adi != adi  # NaN
    assert cv2 != cv2
    assert n == 2


def test_compute_adi_cv2_constant_series_is_smooth() -> None:
    # Every game nonzero, identical value -> ADI=1, CV^2=0 -> smooth.
    values = [20.0] * 20
    adi, cv2, n = compute_adi_cv2(values, min_games=10)
    assert adi == pytest.approx(1.0)
    assert cv2 == pytest.approx(0.0)
    assert classify_sb(adi, cv2) == "smooth"


def test_compute_adi_cv2_sparse_series_is_intermittent_or_lumpy() -> None:
    # Nonzero only 1 in 5 games -> ADI = 20/4 = 5.0 (>> cutoff).
    values = ([20.0] + [0.0] * 4) * 4
    adi, cv2, n = compute_adi_cv2(values, min_games=10)
    assert adi == pytest.approx(5.0)
    assert classify_sb(adi, cv2) in ("intermittent", "lumpy")


def test_build_sb_classification_as_of_no_leakage(con: duckdb.DuckDBPyConnection) -> None:
    """Planted-future-game proof: a wild future scoring run must not
    change an earlier game's as-of ADI/CV^2 row."""
    for i in range(15):
        gid = f"g{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}")
        _insert_pgs(con, gid, 101, pts=10)

    before = build_sb_classification(con, "pts", min_games=5)
    before_row = before.filter(before["game_id"] == "g14").to_dicts()[0]

    for i in range(30):
        gid = f"future_{i}"
        _insert_game(con, gid, "2024-06-01")
        _insert_pgs(con, gid, 101, pts=0 if i % 2 == 0 else 99)

    after = build_sb_classification(con, "pts", min_games=5)
    after_row = after.filter(after["game_id"] == "g14").to_dicts()[0]

    assert before_row["adi"] == after_row["adi"]
    assert before_row["cv2"] == after_row["cv2"]
    assert before_row["sb_class"] == after_row["sb_class"]


def test_build_sb_classification_degrades_on_short_history(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, pts=10)
    df = build_sb_classification(con, "pts", min_games=5)
    row = df.to_dicts()[0]
    assert row["sb_class"] == INSUFFICIENT_HISTORY_BUCKET
    assert row["n_prior"] == 0


def test_build_sb_classification_rejects_unsupported_stat(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="column must be one of"):
        build_sb_classification(con, "fg3m", min_games=5)


def test_summarize_sb_classes(con: duckdb.DuckDBPyConnection) -> None:
    for i in range(12):
        gid = f"g{i}"
        _insert_game(con, gid, f"2023-10-{i + 1:02d}")
        _insert_pgs(con, gid, 101, pts=10)
    df = build_sb_classification(con, "pts", min_games=5)
    summary = summarize_sb_classes(df)
    assert summary.stat == "pts"
    assert summary.n_total == 12
    assert sum(summary.class_counts.values()) == 12
