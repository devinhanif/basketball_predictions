# ruff: noqa: E501
"""Series and clinch state (COACH_PHASE): as-of, same-day blindness, planted-future."""

from __future__ import annotations

import datetime as dt

import polars as pl

from research.features.phase import clinch_state, series_state

A, B = 1610612738, 1610612752  # BOS, NYK


def _po(rows: list[tuple[str, int, int, int, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "game_id": gid,
                "game_date": dt.date(2024, 4, 20) + dt.timedelta(days=d),
                "season": 2023,
                "home_team": h,
                "away_team": a,
                "home_pts": hp,
                "away_pts": ap,
            }
            for gid, d, h, a, hp, ap in [(x[0], x[1], x[2], x[3], 100, 90) for x in rows]
        ]
    )


def test_series_counts_and_elimination() -> None:
    # the home team always wins: A takes g1, g2, g5 at home, B takes g3, g4 at home
    games = _po(
        [
            ("g1", 0, A, B, 0, 0),
            ("g2", 2, A, B, 0, 0),
            ("g3", 4, B, A, 0, 0),
            ("g4", 6, B, A, 0, 0),
            ("g5", 8, A, B, 0, 0),
        ]
    )
    st = series_state(games).filter(pl.col("team_id") == A).sort("game_id")
    assert st["series_game"].to_list() == [1, 2, 3, 4, 5]
    assert st["wins_for"].to_list() == [0, 1, 2, 2, 2]
    assert st["wins_against"].to_list() == [0, 0, 0, 1, 2]
    assert st["home_court"].all()


def test_planted_future_game_does_not_change_earlier_rows() -> None:
    base = _po([("g1", 0, A, B, 0, 0), ("g2", 2, A, B, 0, 0)])
    plus = _po([("g1", 0, A, B, 0, 0), ("g2", 2, A, B, 0, 0), ("g3", 4, B, A, 0, 0)])
    a = series_state(base).sort(["game_id", "team_id"])
    b = series_state(plus).filter(pl.col("game_id") != "g3").sort(["game_id", "team_id"])
    assert a.equals(b)


def test_same_day_games_blind() -> None:
    games = _po([("g1", 0, A, B, 0, 0), ("g2", 0, B, A, 0, 0)])
    st = series_state(games)
    assert st["wins_for"].sum() == 0 and st["wins_against"].sum() == 0


def test_clinch_flags_run_and_first_game_unclinched() -> None:
    rs = pl.DataFrame(
        [
            {
                "game_id": "r1",
                "game_date": dt.date(2023, 10, 24),
                "season": 2023,
                "home_team": A,
                "away_team": B,
                "home_pts": 100,
                "away_pts": 90,
            },
            {
                "game_id": "r2",
                "game_date": dt.date(2023, 10, 26),
                "season": 2023,
                "home_team": A,
                "away_team": B,
                "home_pts": 100,
                "away_pts": 90,
            },
        ]
    )
    cs = clinch_state(rs)
    first = cs.filter(pl.col("game_id") == "r1")
    assert not first["clinched_6"].any() and not first["elim_6"].any()
