"""TEAM_BUDGET as-of correctness: roster window, coach habits, planted-future/same-game."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl

from research.budget import asof as A


def _world(n_games: int = 14, margin_late: int = 3) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Team 1 (coach 'A') hosts team 2 (coach 'B') every day; 8 players each, fixed minutes."""
    rows, games = [], []
    d0 = date(2024, 1, 1)
    for k in range(n_games):
        gid = f"g{k:02d}"
        games.append(
            {
                "game_id": gid,
                "game_date": d0 + timedelta(days=k),
                "home_team": 1,
                "away_team": 2,
                "home_pts": 100 + (25 if k % 3 == 0 else margin_late),
                "away_pts": 100,
            }
        )
        for team in (1, 2):
            mins = [34.0, 32.0, 30.0, 28.0, 26.0, 20.0, 18.0, 12.0, 0.0][:9]
            for i, m in enumerate(mins):
                pid = team * 100 + i
                rows.append(
                    {
                        "game_id": gid,
                        "team_id": team,
                        "player_id": pid,
                        "minutes": m * (240.0 / sum(mins)) if m else 0.0,
                        "starter": i < 5,
                    }
                )
    return pl.DataFrame(rows), pl.DataFrame(games)


def _tg_with_coach(box: pl.DataFrame, games: pl.DataFrame) -> pl.DataFrame:
    shape = A.team_game_shape(box, games)
    return shape.with_columns(
        pl.when(pl.col("team_id") == 1).then(pl.lit("A")).otherwise(pl.lit("B")).alias("coach_key")
    )


def test_roster_window_is_prior_games_only() -> None:
    box, games = _world()
    tgm = pl.concat(
        [
            games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
        ]
    )
    r = A.asof_roster(box, tgm, lookback=3)
    first = r.filter(pl.col("game_id") == "g00")
    assert first.is_empty()  # no prior games, no roster: never the box
    g5 = r.filter((pl.col("game_id") == "g05") & (pl.col("team_id") == 1))
    assert g5["apps"].max() == 3
    # the DNP (minutes == 0) player never enters the roster
    assert 108 not in g5["player_id"].to_list()


def test_roster_ignores_the_games_own_box() -> None:
    box, games = _world()
    tgm = pl.concat(
        [
            games.select("game_id", "game_date", pl.col("home_team").alias("team_id")),
            games.select("game_id", "game_date", pl.col("away_team").alias("team_id")),
        ]
    )
    base = A.asof_roster(box, tgm, lookback=5)
    planted = box.with_columns(
        pl.when((pl.col("game_id") == "g07") & (pl.col("player_id") == 108))
        .then(20.0)
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    new = A.asof_roster(planted, tgm, lookback=5)
    key = ["game_id", "team_id", "player_id"]
    a = base.filter(pl.col("game_id") == "g07").sort(key)
    b = new.filter(pl.col("game_id") == "g07").sort(key)
    assert a.equals(b)  # tonight's own appearance does not change tonight's roster
    # but it does reach the next game
    assert 108 in new.filter(pl.col("game_id") == "g08")["player_id"].to_list()


def test_coach_habits_planted_same_game_and_future() -> None:
    box, games = _world()
    base = A.coach_habits_asof(_tg_with_coach(box, games)).sort(["game_id", "team_id"])
    target = "g09"
    # plant: rewrite the target game's box minutes and score; later games too
    planted = box.with_columns(
        pl.when(pl.col("game_id") >= target)
        .then(pl.when(pl.col("player_id") % 100 == 0).then(48.0).otherwise(pl.col("minutes") * 0.5))
        .otherwise(pl.col("minutes"))
        .alias("minutes")
    )
    pg = games.with_columns(
        pl.when(pl.col("game_id") >= target)
        .then(pl.col("home_pts") + 40)
        .otherwise(pl.col("home_pts"))
        .alias("home_pts")
    )
    new = A.coach_habits_asof(_tg_with_coach(planted, pg)).sort(["game_id", "team_id"])
    old_rows = base.filter(pl.col("game_id") <= target)
    new_rows = new.filter(pl.col("game_id") <= target)
    assert old_rows.equals(new_rows)  # target game and everything before: byte-identical
    # control is not inert: a game AFTER the target does move
    later_old = base.filter(pl.col("game_id") == "g12")
    later_new = new.filter(pl.col("game_id") == "g12")
    assert not later_old.equals(later_new)


def test_unknown_previous_coach_gets_the_league_prior_and_zero_history() -> None:
    box, games = _world()
    tg = _tg_with_coach(box, games).with_columns(
        pl.when(pl.col("game_id") == "g06")
        .then(None)
        .otherwise(pl.col("coach_key"))
        .alias("coach_key")
    )
    h = A.coach_habits_asof(tg)
    # the identity of game g07 is the coach of g06, which is unknown: no history, league prior only
    assert (h.filter(pl.col("game_id") == "g07")["coach_n_prior"] == 0).all()
    # g06's own identity is g05's coach (known): history of 6 games
    assert (h.filter(pl.col("game_id") == "g06")["coach_n_prior"] == 6).all()


def test_coach_change_first_game_gets_the_predecessor_not_the_new_coach() -> None:
    box, games = _world()
    tg = _tg_with_coach(box, games).with_columns(
        pl.when((pl.col("team_id") == 1) & (pl.col("game_id") >= "g08"))
        .then(pl.lit("C"))
        .otherwise(pl.col("coach_key"))
        .alias("coach_key")
    )
    h = A.coach_habits_asof(tg).filter(pl.col("team_id") == 1)
    n = {r["game_id"]: r["coach_n_prior"] for r in h.iter_rows(named=True)}
    assert n["g08"] == 8.0  # still the predecessor's habits: the change is not foreknown
    assert n["g09"] == 1.0  # the new coach has exactly one game of history
    # and the actual coach of a game never feeds that game's own row
    tg2 = tg.with_columns(
        pl.when(pl.col("game_id") == "g09")
        .then(pl.lit("Z"))
        .otherwise(pl.col("coach_key"))
        .alias("coach_key")
    )
    h2 = A.coach_habits_asof(tg2).filter(pl.col("team_id") == 1)
    key = ["game_id", "team_id"]
    assert (
        h.filter(pl.col("game_id") <= "g09")
        .sort(key)
        .equals(h2.filter(pl.col("game_id") <= "g09").sort(key))
    )


def test_habits_shrink_toward_league_with_few_games() -> None:
    box, games = _world()
    h = A.coach_habits_asof(_tg_with_coach(box, games))
    first = h.filter(pl.col("game_id") == "g00")
    # no history anywhere: the documented defaults
    assert np.allclose(first["h_depth"].to_numpy(), 9.0)
    assert (first["coach_n_prior"] == 0).all()
    late = h.filter((pl.col("game_id") == "g13") & (pl.col("team_id") == 1))
    assert float(late["coach_n_prior"][0]) == 13.0


def test_league_prior_uses_strictly_earlier_dates() -> None:
    # two coaches play the SAME date: neither sees the other's same-day value
    box, games = _world(n_games=3)
    tg = _tg_with_coach(box, games)
    base = A.coach_habits_asof(tg).filter(pl.col("game_id") == "g01")
    tweak = tg.with_columns(
        pl.when((pl.col("game_id") == "g01") & (pl.col("team_id") == 2))
        .then(99.0)
        .otherwise(pl.col("depth"))
        .alias("depth")
    )
    new = A.coach_habits_asof(tweak).filter(pl.col("game_id") == "g01")
    assert base.sort("team_id").equals(new.sort("team_id"))
