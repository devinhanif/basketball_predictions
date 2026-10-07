"""As-of / no-leakage tests for ``nba.features.possession_features``.

Builds a tiny synthetic DuckDB (3 games, one team playing better than the
other) rather than touching the committed fixture loader (which doesn't
populate ``possessions`` -- see ``tests/fixtures/loader.py``), so these
tests stay fast and self-contained.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from nba.db.connect import connect
from nba.features.possession_features import (
    LEAGUE_AVG_PPP_DEFAULT,
    PACE_DEFAULT,
    build_possession_matchup_features,
    build_team_possession_rates,
)
from nba.ingest.cache import insert_rows

TEAM_A = 1
TEAM_B = 2
TEAM_C = 3


def _build_synthetic_db():  # type: ignore[no-untyped-def]
    con = connect(":memory:")
    games = pl.DataFrame(
        {
            "game_id": ["g1", "g2", "g3", "g4"],
            "game_date": [
                dt.date(2024, 1, 1),
                dt.date(2024, 1, 3),
                dt.date(2024, 1, 5),
                dt.date(2024, 1, 7),
            ],
            "season": [2024, 2024, 2024, 2024],
            "home_team": [TEAM_A, TEAM_B, TEAM_A, TEAM_C],
            "away_team": [TEAM_B, TEAM_A, TEAM_C, TEAM_A],
            "home_pts": [110, 100, 115, 95],
            "away_pts": [100, 108, 90, 112],
        }
    )
    insert_rows(con, "games", games.columns, games)

    # Team A is deliberately a high-efficiency offense (lots of 3s), team B
    # a low-efficiency offense (lots of misses), across g1-g3. g4 is held
    # out of the possessions table entirely (simulates a game the parser
    # hasn't processed yet) to test the empty-possessions fallback.
    poss_rows = []
    poss_idx = 0
    for game_id, off, deff, n_scoring, pt_value in [
        ("g1", TEAM_A, TEAM_B, 40, 3),
        ("g1", TEAM_B, TEAM_A, 30, 2),
        ("g2", TEAM_B, TEAM_A, 30, 2),
        ("g2", TEAM_A, TEAM_B, 40, 3),
        ("g3", TEAM_A, TEAM_C, 40, 3),
        ("g3", TEAM_C, TEAM_A, 25, 2),
    ]:
        for _ in range(n_scoring):
            poss_rows.append(
                {
                    "game_id": game_id,
                    "poss_idx": poss_idx,
                    "period": 1,
                    "off_team": off,
                    "def_team": deff,
                    "outcome": "FGM3" if pt_value == 3 else "FGM2",
                    "pts": pt_value,
                }
            )
            poss_idx += 1
        # remainder of a notional 100-possession game is empty trips
        for _ in range(100 - n_scoring):
            poss_rows.append(
                {
                    "game_id": game_id,
                    "poss_idx": poss_idx,
                    "period": 1,
                    "off_team": off,
                    "def_team": deff,
                    "outcome": "FGA_miss",
                    "pts": 0,
                }
            )
            poss_idx += 1

    poss_df = pl.DataFrame(poss_rows)
    insert_rows(
        con,
        "possessions",
        ["game_id", "poss_idx", "period", "off_team", "def_team", "outcome", "pts"],
        poss_df,
    )
    return con


def test_first_game_has_no_prior_history_and_uses_league_default() -> None:
    con = _build_synthetic_db()
    try:
        rates = build_team_possession_rates(con)
        g1 = rates.filter(pl.col("game_id") == "g1")
        assert g1.height == 2
        for row in g1.iter_rows(named=True):
            assert row["games_played_prior"] == 0
            assert row["off_poss_prior_sum"] == 0
            assert row["off_rtg_prior"] == LEAGUE_AVG_PPP_DEFAULT
            assert row["def_rtg_prior"] == LEAGUE_AVG_PPP_DEFAULT
            assert row["pace_prior"] == PACE_DEFAULT
    finally:
        con.close()


def test_as_of_rating_reflects_only_strictly_prior_games() -> None:
    con = _build_synthetic_db()
    try:
        rates = build_team_possession_rates(con)
        # Team A's 3rd game-of-season appearance is g3 (as the home team
        # again) -- its as-of off_rtg_prior should reflect g1+g2 only
        # (team A scored 3*40=120 pts over 100 poss in g1, same in g2:
        # raw observed rate 1.2 ppp over 200 possessions), not g3's own
        # possessions.
        g3_team_a = rates.filter((pl.col("game_id") == "g3") & (pl.col("team_id") == TEAM_A))
        assert g3_team_a.height == 1
        row = g3_team_a.row(0, named=True)
        assert row["off_poss_prior_sum"] == 200
        # Raw observed rate over g1+g2 is exactly 1.2 ppp (240 pts / 200
        # poss); shrunk toward the as-of league average (< 1.2, since the
        # league average itself includes team B's weaker g1/g2 offense) --
        # so the shrunk rating should sit strictly between the two, not
        # equal to (or above) the raw observed rate.
        assert 0.9 < row["off_rtg_prior"] < 1.2

    finally:
        con.close()


def test_team_b_weak_offense_shrinks_below_team_a_strong_offense() -> None:
    con = _build_synthetic_db()
    try:
        rates = build_team_possession_rates(con)
        g2_team_a_prior = rates.filter(
            (pl.col("game_id") == "g2") & (pl.col("team_id") == TEAM_A)
        ).row(0, named=True)
        g2_team_b_prior = rates.filter(
            (pl.col("game_id") == "g2") & (pl.col("team_id") == TEAM_B)
        ).row(0, named=True)
        # By g2, team A has 1 prior game (g1) at a high observed rate (1.2
        # ppp); team B has 1 prior game (g1) at a lower observed rate (0.6
        # ppp, since 30 makes * 2 pts / 100 poss = 0.6). A's as-of rating
        # should be strictly higher.
        assert g2_team_a_prior["off_rtg_prior"] > g2_team_b_prior["off_rtg_prior"]
    finally:
        con.close()


def test_mutating_a_later_game_never_changes_an_earlier_as_of_row() -> None:
    """No-leakage: planting a huge future blowout must not move an earlier row."""
    con = _build_synthetic_db()
    try:
        before = build_team_possession_rates(con)
        before_g1 = before.filter(pl.col("game_id") == "g1").sort("team_id")

        # Plant 1000 extra points for team A in a brand-new future game g5.
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES ('g5', '2024-02-01', 2024, 1, 2, 200, 10)"
        )
        extra_rows = pl.DataFrame(
            [
                {
                    "game_id": "g5",
                    "poss_idx": i,
                    "period": 1,
                    "off_team": TEAM_A,
                    "def_team": TEAM_B,
                    "outcome": "FGM3",
                    "pts": 3,
                }
                for i in range(50)
            ]
        )
        insert_rows(
            con,
            "possessions",
            ["game_id", "poss_idx", "period", "off_team", "def_team", "outcome", "pts"],
            extra_rows,
        )

        after = build_team_possession_rates(con)
        after_g1 = after.filter(pl.col("game_id") == "g1").sort("team_id")

        assert before_g1.select(["off_rtg_prior", "def_rtg_prior", "pace_prior"]).equals(
            after_g1.select(["off_rtg_prior", "def_rtg_prior", "pace_prior"])
        )
    finally:
        con.close()


def test_matchup_features_has_home_and_away_columns_for_every_game() -> None:
    con = _build_synthetic_db()
    try:
        matchup = build_possession_matchup_features(con)
        assert matchup.height == 4  # g1, g2, g3, g4
        for col in [
            "home_off_rtg_prior",
            "home_def_rtg_prior",
            "home_pace_prior",
            "away_off_rtg_prior",
            "away_def_rtg_prior",
            "away_pace_prior",
            "league_avg_ppp_asof",
        ]:
            assert col in matchup.columns
        # g4 has no possessions rows at all (parser hasn't processed it in
        # this synthetic setup) -- must still produce a row, not be dropped.
        g4 = matchup.filter(pl.col("game_id") == "g4")
        assert g4.height == 1
    finally:
        con.close()
