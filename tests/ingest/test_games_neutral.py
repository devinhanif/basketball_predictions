"""Neutral-site games carry the same "A @ B" MATCHUP on both rows (e.g. NBA Mexico City Game)."""

from __future__ import annotations

import polars as pl

from nba.ingest.games import _normalize_games_frame


def _rows(
    game_id: str,
    away: tuple[str, int, int],
    home: tuple[str, int, int],
    matchup_home: str,
    matchup_away: str,
):
    return [
        {
            "GAME_ID": game_id,
            "GAME_DATE": "2024-11-02",
            "TEAM_ABBREVIATION": away[0],
            "TEAM_ID": away[1],
            "PTS": away[2],
            "MATCHUP": matchup_away,
        },
        {
            "GAME_ID": game_id,
            "GAME_DATE": "2024-11-02",
            "TEAM_ABBREVIATION": home[0],
            "TEAM_ID": home[1],
            "PTS": home[2],
            "MATCHUP": matchup_home,
        },
    ]


def test_neutral_site_game_is_kept_with_home_from_matchup() -> None:
    mia, was = ("MIA", 1610612748, 118), ("WAS", 1610612764, 98)
    raw = pl.DataFrame(
        _rows("0022400147", mia, was, "MIA @ WAS", "MIA @ WAS")  # neutral: identical strings
        + _rows("0022400148", mia, was, "WAS vs. MIA", "MIA @ WAS")  # ordinary game
    )
    out = _normalize_games_frame(raw, "2024-25").sort("game_id")
    assert out["game_id"].to_list() == ["0022400147", "0022400148"]
    assert out["home_team"].to_list() == [1610612764, 1610612764]
    assert out["away_team"].to_list() == [1610612748, 1610612748]
    assert out["home_pts"].to_list() == [98, 98]
    assert out["away_pts"].to_list() == [118, 118]
