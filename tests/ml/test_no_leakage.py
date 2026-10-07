"""MANDATORY no-leakage test (CLAUDE.md milestone 4 + "Hard constraints").

Two complementary checks:

1. Every feature row's ``as_of`` never exceeds its own ``game_date`` (here
   they're equal by construction, but we assert the invariant directly
   rather than trusting the implementation).
2. A planted-future-game test: build a small multi-game-per-team DB,
   snapshot a target game's as-of features, then insert a *future* game
   (dated after the target) with deliberately extreme stats (e.g. a
   blowout win) for one of the target game's teams. Recompute features
   for the target game and assert they are byte-for-byte identical. If
   the rolling window ever included rows at or after the target date,
   this extreme future game would change ``avg_pts_for_prior`` /
   ``win_pct_prior`` / ``games_played_prior`` for the target row -- so
   this test would fail the moment someone introduces a leak (e.g.
   switches the window frame to ``UNBOUNDED PRECEDING AND CURRENT ROW``,
   or orders by an unstable key that lets a same/later-dated game sort
   earlier).
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.team_features import build_matchup_features, build_team_game_features


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    game_date: str,
    home: int,
    away: int,
    home_pts: int,
    away_pts: int,
    season: int = 2023,
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [game_id, game_date, season, home, away, home_pts, away_pts],
    )


def test_as_of_never_exceeds_game_date(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2, 100, 90)
    _insert_game(con, "g2", "2023-10-26", 2, 1, 95, 105)
    _insert_game(con, "g3", "2023-10-28", 1, 3, 110, 100)

    team_feats = build_team_game_features(con)
    for as_of, game_date in team_feats.select(["as_of", "game_date"]).iter_rows():
        assert as_of <= game_date

    matchup_feats = build_matchup_features(con)
    for as_of, game_date in matchup_feats.select(["as_of", "game_date"]).iter_rows():
        assert as_of <= game_date


def test_rolling_features_use_only_strictly_earlier_games(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", 1, 2, 100, 90)
    _insert_game(con, "g2", "2023-10-26", 2, 1, 95, 105)
    _insert_game(con, "g3", "2023-10-28", 1, 3, 110, 100)

    before = build_team_game_features(con).filter(
        (pl.col("game_id") == "g3") & (pl.col("team_id") == 1)
    )
    assert before.height == 1
    row_before = before.row(0, named=True)
    assert row_before["games_played_prior"] == 2
    assert row_before["win_pct_prior"] == pytest.approx(1.0)  # team 1 won g1 and g2
    assert row_before["avg_pts_for_prior"] == pytest.approx((100 + 105) / 2)

    # Plant a future blowout for team 1, dated strictly after g3. If the
    # window frame ever leaked same-or-later rows, team 1's g3 features
    # would change when this is added.
    _insert_game(con, "g4_future", "2023-11-01", 1, 9, 200, 10)

    after = build_team_game_features(con).filter(
        (pl.col("game_id") == "g3") & (pl.col("team_id") == 1)
    )
    row_after = after.row(0, named=True)

    assert row_after["games_played_prior"] == row_before["games_played_prior"]
    assert row_after["win_pct_prior"] == pytest.approx(row_before["win_pct_prior"])
    assert row_after["avg_pts_for_prior"] == pytest.approx(row_before["avg_pts_for_prior"])
    assert row_after["avg_pts_allowed_prior"] == pytest.approx(row_before["avg_pts_allowed_prior"])
    assert row_after["pace_proxy_prior"] == pytest.approx(row_before["pace_proxy_prior"])


def test_matchup_features_target_uses_box_score_not_future_leak(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A future game with an extreme score must not change an earlier game's y or diffs."""
    _insert_game(con, "g1", "2023-10-24", 1, 2, 100, 90)
    _insert_game(con, "g2", "2023-10-26", 2, 1, 95, 105)

    before = build_matchup_features(con).filter(pl.col("game_id") == "g1")
    row_before = before.row(0, named=True)

    _insert_game(con, "g3_future", "2023-12-01", 1, 2, 300, 1)

    after = build_matchup_features(con).filter(pl.col("game_id") == "g1")
    row_after = after.row(0, named=True)

    assert row_after["y"] == row_before["y"]
    assert row_after["win_pct_diff"] == pytest.approx(row_before["win_pct_diff"])
    assert row_after["pts_for_diff"] == pytest.approx(row_before["pts_for_diff"])
