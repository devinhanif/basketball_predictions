"""Unit tests for the pure data-transform helpers in nba.ingest.*.

These never import nba_api or touch the network -- only the normalization
logic that turns a raw nba_api-shaped frame into our target schema.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.boxscores import _normalize_boxscore_v3_frame, _parse_minutes
from nba.ingest.cache import EmptyFetchError, is_cached
from nba.ingest.games import (
    _filter_competitive_games,
    _normalize_games_frame,
    season_to_int,
)
from nba.ingest.pbp import _normalize_pbp_v3_frame, pull_game_pbp
from nba.ingest.team_advanced import _normalize_team_advanced_frames, pull_game_team_advanced


def test_season_to_int() -> None:
    assert season_to_int("2023-24") == 2023


def test_normalize_games_frame_collapses_to_one_row_per_game() -> None:
    raw = pl.DataFrame(
        {
            "GAME_ID": ["0022300001", "0022300001"],
            "GAME_DATE": ["2023-10-24", "2023-10-24"],
            "MATCHUP": ["BOS vs. PHI", "PHI @ BOS"],
            "TEAM_ID": [1610612738, 1610612755],
            "PTS": [108, 104],
        }
    )
    out = _normalize_games_frame(raw, "2023-24")
    assert out.height == 1
    row = out.row(0, named=True)
    assert row["game_id"] == "0022300001"
    assert row["season"] == 2023
    assert row["home_team"] == 1610612738
    assert row["away_team"] == 1610612755
    assert row["home_pts"] == 108
    assert row["away_pts"] == 104


def test_filter_competitive_games_drops_preseason_allstar_and_exhibitions() -> None:
    raw = pl.DataFrame(
        {
            "game_id": [
                "0012300001",  # preseason -- dropped
                "0032300001",  # all-star -- dropped
                "0022300002",  # regular season vs non-NBA team -- dropped
                "0022300003",  # regular season, valid -- kept
                "0042300001",  # playoffs, valid -- kept
                "0052300001",  # play-in, valid -- kept
            ],
            "home_team": [
                1610612738,
                1610612738,
                1610612738,
                1610612738,
                1610612738,
                1610612738,
            ],
            "away_team": [
                1610612755,
                1610612755,
                15020,  # non-NBA exhibition opponent
                1610612755,
                1610612755,
                1610612755,
            ],
        }
    )
    out = _filter_competitive_games(raw)
    assert out["game_id"].to_list() == ["0022300003", "0042300001", "0052300001"]


def test_parse_minutes_handles_mm_ss_and_empty() -> None:
    assert _parse_minutes("34:30") == 34.5
    assert _parse_minutes(None) is None
    assert _parse_minutes("") is None
    assert _parse_minutes("12") == 12.0


def test_parse_minutes_handles_iso8601_duration() -> None:
    assert _parse_minutes("PT34M12.00S") == 34.2
    assert _parse_minutes("PT0M00.00S") == 0.0
    assert _parse_minutes("PT5M0S") == 5.0


def test_normalize_boxscore_v3_frame() -> None:
    # Column names/shapes as observed live from BoxScoreTraditionalV3's
    # player-level dataframe (get_data_frames()[0]).
    raw = pl.DataFrame(
        {
            "gameId": ["0022300001"] * 3,
            "teamId": [1610612738, 1610612738, 1610612755],
            "personId": [101, 102, 104],
            "position": ["G", "", ""],
            "comment": ["", "", "DNP - Coach's Decision"],
            "minutes": ["PT36M30.00S", "0:00", ""],
            "fieldGoalsMade": [12, 0, 0],
            "fieldGoalsAttempted": [20, 3, 0],
            "points": [30, 0, 0],
            "reboundsTotal": [6, 1, 0],
            "assists": [7, 0, 0],
            "threePointersMade": [3, 0, 0],
            "threePointersAttempted": [5, 1, 0],
            "freeThrowsMade": [3, 0, 0],
            "freeThrowsAttempted": [4, 0, 0],
            "reboundsOffensive": [1, 0, 0],
            "reboundsDefensive": [5, 1, 0],
            "foulsPersonal": [2, 1, 0],
            "steals": [1, 0, 0],
            "blocks": [0, 0, 0],
            "turnovers": [2, 1, 0],
        }
    )
    out = _normalize_boxscore_v3_frame(raw, "0022300001")
    assert out.height == 3
    assert out["game_id"].to_list() == ["0022300001"] * 3
    assert out["player_id"].to_list() == [101, 102, 104]
    assert out["team_id"].to_list() == [1610612738, 1610612738, 1610612755]
    assert out["starter"].to_list() == [True, False, False]
    assert out["minutes"].to_list() == [36.5, 0.0, None]
    assert out["pts"].to_list() == [30, 0, 0]
    assert out["reb"].to_list() == [6, 1, 0]
    assert out["fg3m"].to_list() == [3, 0, 0]
    assert out["tov"].to_list() == [2, 1, 0]
    assert out["fgm"].to_list() == [12, 0, 0]
    assert out["fga"].to_list() == [20, 3, 0]
    assert out["fg3a"].to_list() == [5, 1, 0]
    assert out["ftm"].to_list() == [3, 0, 0]
    assert out["fta"].to_list() == [4, 0, 0]
    assert out["oreb"].to_list() == [1, 0, 0]
    assert out["dreb"].to_list() == [5, 1, 0]
    assert out["pf"].to_list() == [2, 1, 0]


def test_normalize_team_advanced_frames() -> None:
    # Column names/shapes as observed live from BoxScoreAdvancedV3 and
    # BoxScoreFourFactorsV3's team-level dataframes (get_data_frames()[1]).
    adv_team = pl.DataFrame(
        {
            "gameId": ["0022200001", "0022200001"],
            "teamId": [1610612738, 1610612755],
            "offensiveRating": [129.9, 119.4],
            "defensiveRating": [119.4, 129.9],
            "netRating": [10.5, -10.5],
            "pace": [97.5, 97.5],
            "effectiveFieldGoalPercentage": [0.634, 0.581],
            "offensiveReboundPercentage": [0.256, 0.190],
        }
    )
    ff_team = pl.DataFrame(
        {
            "gameId": ["0022200001", "0022200001"],
            "teamId": [1610612738, 1610612755],
            "freeThrowAttemptRate": [0.341, 0.350],
            "teamTurnoverPercentage": [0.113, 0.143],
        }
    )
    out = _normalize_team_advanced_frames(adv_team, ff_team, "0022200001")
    assert out.height == 2
    assert out["game_id"].to_list() == ["0022200001", "0022200001"]
    assert out["team_id"].to_list() == [1610612738, 1610612755]
    assert out["off_rating"].to_list() == [129.9, 119.4]
    assert out["def_rating"].to_list() == [119.4, 129.9]
    assert out["net_rating"].to_list() == [10.5, -10.5]
    assert out["pace"].to_list() == [97.5, 97.5]
    assert out["efg_pct"].to_list() == [0.634, 0.581]
    assert out["tov_pct"].to_list() == [0.113, 0.143]
    assert out["oreb_pct"].to_list() == [0.256, 0.190]
    assert out["ft_rate"].to_list() == [0.341, 0.350]


def test_normalize_team_advanced_frames_empty_returns_empty_frame() -> None:
    empty_adv = pl.DataFrame(
        schema={
            "gameId": pl.Utf8,
            "teamId": pl.Int64,
            "offensiveRating": pl.Float64,
            "defensiveRating": pl.Float64,
            "netRating": pl.Float64,
            "pace": pl.Float64,
            "effectiveFieldGoalPercentage": pl.Float64,
            "offensiveReboundPercentage": pl.Float64,
        }
    )
    empty_ff = pl.DataFrame(
        schema={
            "gameId": pl.Utf8,
            "teamId": pl.Int64,
            "freeThrowAttemptRate": pl.Float64,
            "teamTurnoverPercentage": pl.Float64,
        }
    )
    out = _normalize_team_advanced_frames(empty_adv, empty_ff, "0022200001")
    assert out.is_empty()


def test_pull_game_team_advanced_treats_empty_fetch_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty/throttled response must be marked failed, never cached as done."""
    con: duckdb.DuckDBPyConnection = connect(":memory:")
    try:
        import nba.ingest.team_advanced as team_advanced_mod

        monkeypatch.setattr(
            team_advanced_mod,
            "_fetch_team_advanced_with_retry",
            lambda game_id, rate_limiter: pl.DataFrame({"a": []}),
        )
        with pytest.raises(EmptyFetchError):
            pull_game_team_advanced(con, "0022300001", data_dir=tmp_path)
        assert not is_cached(con, "team-advanced", "0022300001")
        row = con.execute(
            "SELECT status FROM ingest_log WHERE source='team-advanced' AND key='0022300001'"
        ).fetchone()
        assert row == ("failed",)
        count = con.execute("SELECT count(*) FROM team_game_advanced").fetchone()
        assert count == (0,)
    finally:
        con.close()


def test_normalize_pbp_v3_frame() -> None:
    # Column names/shapes as observed live from PlayByPlayV3's player-level
    # dataframe (get_data_frames()[0]), game 0022500500.
    raw = pl.DataFrame(
        {
            "gameId": ["0022500500"] * 2,
            "actionNumber": [4, 9],
            "clock": ["PT11M39.00S", "PT11M32.00S"],
            "period": [1, 1],
            "teamId": [1610612749, 1610612749],
            "teamTricode": ["MIL", "MIL"],
            "personId": [1631260, 1629645],
            "playerName": ["Green", "Porter Jr."],
            "playerNameI": ["A. Green", "K. Porter Jr."],
            "xLegacy": [0, -61],
            "yLegacy": [0, 50],
            "shotDistance": [0, 8],
            "shotResult": ["", "Made"],
            "isFieldGoal": [0, 1],
            "scoreHome": ["", "0"],
            "scoreAway": ["", "2"],
            "pointsTotal": [0, 2],
            "location": ["v", "v"],
            "description": ["Green STEAL (1 STL)", "Porter Jr. 8' Layup (2 PTS)"],
            "actionType": ["", "Made Shot"],
            "subType": ["", "Driving Finger Roll Layup Shot"],
            "videoAvailable": [1, 1],
            "shotValue": [0, 2],
            "actionId": [4, 5],
        }
    )
    out = _normalize_pbp_v3_frame(raw, "0022500500")
    assert out.height == 2
    assert out["game_id"].to_list() == ["0022500500"] * 2
    assert out["period"].to_list() == [1, 1]
    assert out["clock"].to_list() == ["PT11M39.00S", "PT11M32.00S"]
    assert out["team_id"].to_list() == [1610612749, 1610612749]
    assert out["player_id"].to_list() == [1631260, 1629645]
    assert out["action_type"].to_list() == ["", "Made Shot"]
    assert out["shot_result"].to_list() == ["", "Made"]
    assert out["score_home"].to_list() == ["", "0"]
    assert out["score_away"].to_list() == ["", "2"]
    assert out["points_total"].to_list() == [0, 2]


def test_normalize_pbp_v3_frame_empty_returns_empty_frame() -> None:
    empty = pl.DataFrame(
        schema={
            "gameId": pl.Utf8,
            "actionNumber": pl.Int64,
            "clock": pl.Utf8,
            "period": pl.Int64,
            "teamId": pl.Int64,
            "personId": pl.Int64,
            "playerName": pl.Utf8,
            "actionType": pl.Utf8,
            "subType": pl.Utf8,
            "description": pl.Utf8,
            "scoreHome": pl.Utf8,
            "scoreAway": pl.Utf8,
            "pointsTotal": pl.Int64,
            "shotResult": pl.Utf8,
            "shotValue": pl.Int64,
            "shotDistance": pl.Int64,
            "isFieldGoal": pl.Int64,
            "location": pl.Utf8,
        }
    )
    out = _normalize_pbp_v3_frame(empty, "0022500500")
    assert out.is_empty()


def test_pull_game_pbp_treats_empty_fetch_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty/throttled PBP response must be marked failed, never cached as done."""
    con: duckdb.DuckDBPyConnection = connect(":memory:")
    try:
        import nba.ingest.pbp as pbp_mod

        monkeypatch.setattr(
            pbp_mod,
            "_fetch_pbp_with_retry",
            lambda game_id: pl.DataFrame({"a": []}),
        )
        with pytest.raises(EmptyFetchError):
            pull_game_pbp(con, "0022300001", data_dir=tmp_path)
        assert not is_cached(con, "pbp", "0022300001")
        row = con.execute(
            "SELECT status FROM ingest_log WHERE source='pbp' AND key='0022300001'"
        ).fetchone()
        assert row == ("failed",)
    finally:
        con.close()


def test_ensure_player_game_stats_columns_migrates_fresh_db() -> None:
    from nba.ingest.boxscores import ensure_player_game_stats_columns

    con = connect(":memory:")
    try:
        ensure_player_game_stats_columns(con)  # no-op, columns already in schema.sql
        cols = {row[1] for row in con.execute("PRAGMA table_info('player_game_stats')").fetchall()}
        assert {"fgm", "fga", "fg3a", "ftm", "fta", "oreb", "dreb", "pf"} <= cols
    finally:
        con.close()


def test_normalize_boxscore_v3_frame_empty_returns_empty_frame() -> None:
    raw = pl.DataFrame(
        schema={
            "gameId": pl.Utf8,
            "teamId": pl.Int64,
            "personId": pl.Int64,
            "position": pl.Utf8,
            "minutes": pl.Utf8,
            "points": pl.Int64,
            "reboundsTotal": pl.Int64,
            "assists": pl.Int64,
            "threePointersMade": pl.Int64,
            "steals": pl.Int64,
            "blocks": pl.Int64,
            "turnovers": pl.Int64,
        }
    )
    out = _normalize_boxscore_v3_frame(raw, "0022300001")
    assert out.is_empty()
