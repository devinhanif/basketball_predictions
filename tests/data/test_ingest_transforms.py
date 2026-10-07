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
            "points": [30, 0, 0],
            "reboundsTotal": [6, 1, 0],
            "assists": [7, 0, 0],
            "threePointersMade": [3, 0, 0],
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
