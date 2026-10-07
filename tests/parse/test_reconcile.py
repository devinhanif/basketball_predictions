"""Tests for nba/parse/reconcile.py -- the possession-count reconciliation
gate CLAUDE.md requires: mean |parsed - (FGA + 0.44*FTA + TOV - OREB)| <= 1
per team-game.
"""

from __future__ import annotations

import polars as pl
import pytest

from nba.parse.possessions import parse_possessions
from nba.parse.reconcile import (
    mean_abs_error,
    reconcile_possessions,
    team_box_from_player_game_stats,
)
from tests.fixtures.loader import load_pbp, load_player_game_stats


def test_reconcile_synthetic_exact_match_has_zero_error() -> None:
    """A hand-built possessions frame whose box totals are derived exactly
    from the same trips must reconcile to ~0 error."""
    possessions = pl.DataFrame(
        {
            "game_id": ["g1"] * 4,
            "off_team": [1, 1, 2, 2],
            "outcome": ["FGM2", "TOV", "FGM3", "FGA_miss"],
        }
    )
    # Team 1: 1 FGA (make) + 1 TOV, 0 FTA, 0 OREB -> estimate = 1 + 0 + 1 - 0 = 2
    # Team 2: 2 FGA (make + miss), 0 FTA, 0 TOV, 0 OREB -> estimate = 2
    team_box = pl.DataFrame(
        {
            "game_id": ["g1", "g1"],
            "team_id": [1, 2],
            "fga": [1, 2],
            "fta": [0, 0],
            "tov": [1, 0],
            "oreb": [0, 0],
        }
    )
    rec = reconcile_possessions(possessions, team_box)
    assert rec["n_parsed"].to_list() == [2, 2]
    assert mean_abs_error(rec) == pytest.approx(0.0)


def test_reconcile_missing_box_row_treated_as_zero_parsed() -> None:
    possessions = pl.DataFrame(
        schema={"game_id": pl.Utf8, "off_team": pl.Int64, "outcome": pl.Utf8}
    )
    team_box = pl.DataFrame(
        {"game_id": ["g1"], "team_id": [1], "fga": [10], "fta": [4], "tov": [2], "oreb": [1]}
    )
    rec = reconcile_possessions(possessions, team_box)
    assert rec["n_parsed"].to_list() == [0]
    assert rec["estimate"].to_list() == pytest.approx([10 + 0.44 * 4 + 2 - 1])


def test_reconcile_missing_column_raises() -> None:
    possessions = pl.DataFrame({"game_id": [], "off_team": [], "outcome": []})
    bad_box = pl.DataFrame({"game_id": ["g1"], "team_id": [1], "fga": [1]})
    with pytest.raises(ValueError, match="missing required columns"):
        reconcile_possessions(possessions, bad_box)


def test_fixture_dataset_reconciles_within_gate() -> None:
    """End-to-end on the committed fixture (nba_api V3 schema, no network):
    parse every fixture game and check the CLAUDE.md gate (mean <= 1)."""
    pbp_map = load_pbp()
    possessions = pl.concat([parse_possessions(df) for df in pbp_map.values()])
    team_box = team_box_from_player_game_stats(load_player_game_stats())
    rec = reconcile_possessions(possessions, team_box)
    assert rec.height == 6  # 3 fixture games x 2 teams
    mae = mean_abs_error(rec)
    assert mae <= 1.0, f"mean abs error {mae} exceeds the CLAUDE.md gate of 1 possession"
