"""Unit tests for the pure data-transform helpers in nba.ingest.*.

These never import nba_api or touch the network -- only the normalization
logic that turns a raw nba_api-shaped frame into our target schema.
"""

from __future__ import annotations

import polars as pl

from nba.ingest.boxscores import _normalize_boxscore_frame, _parse_minutes
from nba.ingest.games import _normalize_games_frame, season_to_int


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


def test_parse_minutes_handles_mm_ss_and_empty() -> None:
    assert _parse_minutes("34:30") == 34.5
    assert _parse_minutes(None) is None
    assert _parse_minutes("") is None
    assert _parse_minutes("12") == 12.0


def test_normalize_boxscore_frame() -> None:
    raw = pl.DataFrame(
        {
            "PLAYER_ID": [101, 104],
            "TEAM_ID": [1610612738, 1610612755],
            "MIN": ["36:30", "0:00"],
            "START_POSITION": ["G", ""],
            "PTS": [30, 0],
            "REB": [6, 0],
            "AST": [7, 0],
            "FG3M": [3, 0],
            "STL": [1, 0],
            "BLK": [0, 0],
            "TO": [2, 0],
        }
    )
    out = _normalize_boxscore_frame(raw, "0022300001")
    assert out.height == 2
    assert out["game_id"].to_list() == ["0022300001", "0022300001"]
    assert out["starter"].to_list() == [True, False]
    assert out["minutes"].to_list() == [36.5, 0.0]
