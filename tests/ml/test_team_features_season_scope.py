"""Season-scoping of team_features / game_context standings columns."""

from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.game_context import build_standings_features
from nba.features.team_features import SHRINK_K, build_team_game_features

ATL = 1610612737
BOS = 1610612738


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _seed(con: duckdb.DuckDBPyConnection, n_per_season: int, seasons: tuple[int, ...]) -> None:
    """ATL beats BOS every game (ATL home), one game per day per season."""
    for si, season in enumerate(seasons):
        start = dt.date(2020 + si, 10, 1)
        for i in range(n_per_season):
            con.execute(
                "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
                "home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [f"g{season}_{i:03d}", start + dt.timedelta(days=i), season, ATL, BOS, 110, 100],
            )


def test_games_into_season_resets_each_season(con: duckdb.DuckDBPyConnection) -> None:
    _seed(con, 70, (2020, 2021, 2022))
    st = build_standings_features(con).filter(pl.col("team_id") == ATL).sort("game_date")
    first = st.group_by("season").agg(pl.col("games_into_season").min())
    assert first["games_into_season"].to_list() == [0, 0, 0]
    assert st["games_into_season"].max() == 69


def test_tanking_cannot_fire_before_60_games(con: duckdb.DuckDBPyConnection) -> None:
    _seed(con, 70, (2020, 2021))
    st = build_standings_features(con).filter(pl.col("team_id") == BOS)  # loses every game
    early = st.filter(pl.col("games_into_season") < 60)
    assert early["tanking_flag"].sum() == 0
    assert early["tanking_incentive"].sum() == 0.0


def test_early_season_win_pct_shrinks_to_prior_not_jump(con: duckdb.DuckDBPyConnection) -> None:
    _seed(con, 40, (2020, 2021))
    tf = build_team_game_features(con).filter(pl.col("team_id") == ATL).sort("game_date")
    s20 = tf.filter(pl.col("season") == 2020)["win_pct_prior"].to_list()
    s21 = tf.filter(pl.col("season") == 2021)
    assert s20[0] == pytest.approx(0.5)  # no history -> 0.5
    assert s20[1] == pytest.approx((1 + SHRINK_K * 0.5) / (1 + SHRINK_K))
    prior = 0.5 + (2 / 3) * (1.0 - 0.5)  # last season 100% regressed 1/3
    assert s21["win_pct_prior"][0] == pytest.approx(prior)
    assert s21["games_played_prior"][0] == 0
    # pts means start at last season's final avg, not the league default
    assert s21["avg_pts_for_prior"][0] == pytest.approx(110.0)


def test_no_future_leakage_planted_later_game(con: duckdb.DuckDBPyConnection) -> None:
    _seed(con, 30, (2020, 2021))
    before = build_team_game_features(con).filter(pl.col("game_date") < dt.date(2021, 10, 10))
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES ('zz', '2021-12-30', 2021, ?, ?, 50, 150)",
        [ATL, BOS],
    )
    after = build_team_game_features(con).filter(pl.col("game_date") < dt.date(2021, 10, 10))
    assert before.sort(["game_id", "team_id"]).equals(after.sort(["game_id", "team_id"]))
