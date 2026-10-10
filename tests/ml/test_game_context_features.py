"""Tests for the as-of game-context features (CLAUDE.md "Architecture
ladder"): travel, national TV, playoff positioning/seeding, and the
tanking-incentive score. Fixture-/synthetic-scale only, per the task's
operating constraints (no real DuckDB file, no full backtest).
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.game_context import (
    GameContextConfig,
    build_game_context_matchup_features,
    build_national_tv_features,
    build_standings_features,
)
from nba.features.team_features import (
    MATCHUP_FEATURE_COLUMNS,
    build_matchup_features,
    build_team_game_features,
)
from nba.ingest.arenas import arena_distance_miles
from research.models.rung1_logistic import LogisticRung
from research.models.rung2_gbm import LightGBMRung

# Real franchise ids (see nba.ingest.arenas.ARENA_COORDS / nba.features.
# game_context.TEAM_CONFERENCE), all Eastern Conference so the mini-league
# standings tests have a single, self-contained conference pool.
ATL = 1610612737  # Atlanta Hawks
BOS = 1610612738  # Boston Celtics
CLE = 1610612739  # Cleveland Cavaliers
CHI = 1610612741  # Chicago Bulls
DET = 1610612765  # Detroit Pistons


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
    national_tv: str | None = None,
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
        "home_pts, away_pts, national_tv) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [game_id, game_date, season, home, away, home_pts, away_pts, national_tv],
    )


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------


def test_travel_first_game_of_season_uses_default(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", ATL, BOS, 100, 90)
    team_feats = build_team_game_features(con)
    row = team_feats.filter((pl.col("game_id") == "g1") & (pl.col("team_id") == ATL)).row(
        0, named=True
    )
    assert row["travel_miles_asof"] == pytest.approx(0.0)


def test_travel_known_two_city_sequence_matches_haversine(
    con: duckdb.DuckDBPyConnection,
) -> None:
    # ATL's game1 is hosted in Atlanta; game2 is hosted in Boston (ATL plays
    # away at BOS). ATL's travel for game2 is the ATL<->BOS arena distance.
    _insert_game(con, "g1", "2023-10-24", ATL, CLE, 100, 90)
    _insert_game(con, "g2", "2023-10-26", BOS, ATL, 110, 105)

    team_feats = build_team_game_features(con)
    row = team_feats.filter((pl.col("game_id") == "g2") & (pl.col("team_id") == ATL)).row(
        0, named=True
    )
    expected = arena_distance_miles(ATL, BOS)
    assert row["travel_miles_asof"] == pytest.approx(expected, rel=1e-6)
    assert expected > 500.0  # sanity: ATL<->BOS is a real, nontrivial trip


def test_travel_no_leakage_future_game_does_not_change_past_travel(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "g1", "2023-10-24", ATL, CLE, 100, 90)
    _insert_game(con, "g2", "2023-10-26", BOS, ATL, 110, 105)

    before = build_team_game_features(con).filter(
        (pl.col("game_id") == "g2") & (pl.col("team_id") == ATL)
    )
    row_before = before.row(0, named=True)

    # Plant a future game for ATL hosted somewhere else entirely, dated
    # strictly after g2.
    _insert_game(con, "g3_future", "2023-12-01", CHI, ATL, 120, 50)

    after = build_team_game_features(con).filter(
        (pl.col("game_id") == "g2") & (pl.col("team_id") == ATL)
    )
    row_after = after.row(0, named=True)
    assert row_after["travel_miles_asof"] == pytest.approx(row_before["travel_miles_asof"])


# ---------------------------------------------------------------------------
# National TV
# ---------------------------------------------------------------------------


def test_national_tv_true_only_when_broadcaster_set(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24", ATL, BOS, 100, 90, national_tv="ESPN")
    _insert_game(con, "g2", "2023-10-25", CLE, CHI, 95, 92, national_tv=None)

    nt = build_national_tv_features(con).sort("game_id")
    g1 = nt.filter(pl.col("game_id") == "g1").row(0, named=True)
    g2 = nt.filter(pl.col("game_id") == "g2").row(0, named=True)
    assert g1["is_national_tv"] is True
    assert g2["is_national_tv"] is False

    matchup = build_matchup_features(con)
    assert matchup.filter(pl.col("game_id") == "g1").row(0, named=True)["is_national_tv"] is True
    assert matchup.filter(pl.col("game_id") == "g2").row(0, named=True)["is_national_tv"] is False


# ---------------------------------------------------------------------------
# Standings / tanking
# ---------------------------------------------------------------------------

#: Small, test-scale thresholds so a ~12-game synthetic mini-league can
#: exercise "in playoff position" / "in play-in" / "eliminated" without
#: needing a real 82-game, 15-team conference.
_MINI_LEAGUE_CONFIG = GameContextConfig(
    playoff_seed_cutoff=1,
    playin_seed_cutoff=2,
    tanking_games_into_season_threshold=10,
    tanking_bad_win_pct_threshold=0.30,
)


def _build_mini_league(con: duckdb.DuckDBPyConnection, n_rounds: int) -> None:
    """DET plays ATL/BOS/CLE on a rotation, losing every game.

    All four teams are Eastern Conference (see module constants), so DET is
    guaranteed to be the worst-ranked team in its (self-contained) conference
    pool after any number of rounds -- it has strictly fewer wins than every
    opponent, all of whom have >=1 win apiece.
    """
    opponents = [ATL, BOS, CLE]
    game_idx = 0
    date = dt.date(2023, 10, 24)
    for _round in range(n_rounds):
        for opp in opponents:
            game_idx += 1
            # Alternate home/away for DET; DET always loses.
            if game_idx % 2 == 0:
                _insert_game(con, f"g{game_idx}", date.isoformat(), DET, opp, 90, 100)
            else:
                _insert_game(con, f"g{game_idx}", date.isoformat(), opp, DET, 100, 90)
            date += dt.timedelta(days=1)


def test_standings_asof_win_pct_and_rank_correct_mid_season(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _build_mini_league(con, n_rounds=3)  # DET has played 6 games entering its 7th

    # DET's 7th game (game_idx 7, the start of round 3).
    standings = build_standings_features(con, _MINI_LEAGUE_CONFIG)
    det_row = standings.filter((pl.col("team_id") == DET) & (pl.col("game_id") == "g7")).row(
        0, named=True
    )
    assert det_row["games_into_season"] == 6
    assert det_row["win_pct_asof"] == pytest.approx(0.0)
    assert det_row["conf_rank_asof"] == 4  # worst of the 4-team East pool
    assert det_row["in_playoff_pos"] is False
    assert det_row["in_playin"] is False


def test_tanking_flag_off_early_season_for_bad_team(con: duckdb.DuckDBPyConnection) -> None:
    _build_mini_league(con, n_rounds=2)  # DET: games_played_prior=4 entering g5 < threshold 10

    standings = build_standings_features(con, _MINI_LEAGUE_CONFIG)
    det_row = standings.filter((pl.col("team_id") == DET) & (pl.col("game_id") == "g5")).row(
        0, named=True
    )
    assert det_row["games_into_season"] == 4
    assert det_row["tanking_flag"] is False
    assert det_row["tanking_incentive"] == pytest.approx(0.0)


def test_tanking_flag_on_late_season_for_eliminated_bad_team(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _build_mini_league(con, n_rounds=5)  # DET plays 15 games; check the 13th (12 games prior)

    standings = build_standings_features(con, _MINI_LEAGUE_CONFIG)
    det_row = standings.filter((pl.col("team_id") == DET) & (pl.col("game_id") == "g13")).row(
        0, named=True
    )
    assert det_row["games_into_season"] == 12
    assert det_row["win_pct_asof"] == pytest.approx(0.0)
    assert det_row["conf_rank_asof"] == 4
    assert det_row["tanking_flag"] is True
    assert det_row["tanking_incentive"] == pytest.approx(1.0)


def test_standings_no_leakage_future_game_does_not_change_past_rank(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _build_mini_league(con, n_rounds=3)
    before = build_standings_features(con, _MINI_LEAGUE_CONFIG).filter(
        (pl.col("team_id") == DET) & (pl.col("game_id") == "g7")
    )
    row_before = before.row(0, named=True)

    # Plant a future blowout WIN for DET, dated strictly after the target.
    _insert_game(con, "g_future", "2023-12-25", DET, ATL, 150, 50)

    after = build_standings_features(con, _MINI_LEAGUE_CONFIG).filter(
        (pl.col("team_id") == DET) & (pl.col("game_id") == "g7")
    )
    row_after = after.row(0, named=True)
    assert row_after["win_pct_asof"] == pytest.approx(row_before["win_pct_asof"])
    assert row_after["conf_rank_asof"] == row_before["conf_rank_asof"]
    assert row_after["games_into_season"] == row_before["games_into_season"]
    assert row_after["tanking_incentive"] == pytest.approx(row_before["tanking_incentive"])


def test_game_context_matchup_features_one_row_per_game(con: duckdb.DuckDBPyConnection) -> None:
    _build_mini_league(con, n_rounds=2)
    ctx = build_game_context_matchup_features(con, _MINI_LEAGUE_CONFIG)
    assert ctx.height == ctx["game_id"].n_unique()


# ---------------------------------------------------------------------------
# Feature-frame integration: new columns reach rungs 1/2 and models still fit.
# ---------------------------------------------------------------------------


def test_new_columns_present_in_matchup_feature_frame_and_models_fit(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _build_mini_league(con, n_rounds=3)
    matchup = build_matchup_features(con)

    for col in MATCHUP_FEATURE_COLUMNS:
        assert col in matchup.columns, f"missing feature column: {col}"
        assert matchup.select(pl.col(col)).null_count().item() == 0, f"nulls in {col}"

    y = matchup.select("y").to_series().to_numpy().astype(int)
    if len(np.unique(y)) < 2:
        pytest.skip("mini-league schedule produced a single-class target; not what's under test")

    for model in (LogisticRung(seed=0), LightGBMRung(seed=0)):
        model.fit(matchup, y)
        preds = model.predict(matchup)
        assert preds.shape == (matchup.height,)
        assert np.all((preds >= 0.0) & (preds <= 1.0))
