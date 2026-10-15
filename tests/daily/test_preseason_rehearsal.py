"""Preseason rehearsal helpers: id remap and schedule loading (no network, no database)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from nba.daily.preseason_rehearsal import load_preseason_schedule, remap_game_id
from nba.daily.schedule import is_scorable_game_id


def test_remap_keeps_season_and_lands_in_unused_sequence_range() -> None:
    assert remap_game_id("0012600007") == "0022690007"
    assert is_scorable_game_id(remap_game_id("0012600064"))
    # the regular season never reaches 9xxxx (1230 games)
    assert int(remap_game_id("0012600064")[5:]) >= 90000


def test_remap_rejects_non_preseason_ids() -> None:
    with pytest.raises(ValueError):
        remap_game_id("0022600001")


def test_load_schedule_keeps_only_unplayed_preseason(tmp_path: Path) -> None:
    f = tmp_path / "s.parquet"
    pl.DataFrame(
        {
            "gameId": ["0012600007", "0012600055", "0022600001"],
            "gameStatus": [3, 1, 1],
            "gameDateTimeUTC": [
                "2026-10-12T23:00:00Z",
                "2026-10-15T23:30:00Z",
                "2026-10-20T23:00:00Z",
            ],
            "homeTeam_teamId": [1610612737, 1610612752, 1610612738],
            "awayTeam_teamId": [1610612760, 1610612761, 1610612739],
        }
    ).write_parquet(f)
    games = load_preseason_schedule(f)
    assert [g.game_id for g in games] == ["0022690055"]
    assert games[0].game_date_et.isoformat() == "2026-10-15"
