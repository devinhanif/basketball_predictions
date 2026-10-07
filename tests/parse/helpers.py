"""Shared synthetic play-by-play builder for parser unit tests.

Builds minimal frames in the real V3 schema (see
``nba/ingest/pbp.py:_PBP_SCHEMA`` and the real-column docstring in
``nba/parse/possessions.py``) so segmentation/outcome tests exercise the
exact same code path as real data, without needing network access or the
(gitignored, multi-GB) ``data/pbp/`` parquet cache.
"""

from __future__ import annotations

import polars as pl

GAME_ID = "0099900001"
HOME = 1610612700
AWAY = 1610612701


class PbpBuilder:
    """Accumulates rows for one synthetic game; `.df()` returns the frame."""

    def __init__(self, game_id: str = GAME_ID) -> None:
        self.game_id = game_id
        self._an = 0
        self.rows: list[dict] = []

    def add(
        self,
        period: int,
        clock: str,
        team_id: int,
        player_id: int,
        action_type: str,
        *,
        sub_type: str = "",
        description: str = "",
        player_name: str = "",
        score_home: str = "",
        score_away: str = "",
        shot_value: int = 0,
        shot_distance: int = 0,
        is_field_goal: int = 0,
        location: str = "",
    ) -> PbpBuilder:
        self._an += 1
        self.rows.append(
            {
                "game_id": self.game_id,
                "action_number": self._an,
                "period": period,
                "clock": clock,
                "team_id": team_id,
                "player_id": player_id,
                "player_name": player_name,
                "action_type": action_type,
                "sub_type": sub_type,
                "description": description,
                "score_home": score_home,
                "score_away": score_away,
                "points_total": 0,
                "shot_result": "",
                "shot_value": shot_value,
                "shot_distance": shot_distance,
                "is_field_goal": is_field_goal,
                "location": location,
            }
        )
        return self

    def df(self) -> pl.DataFrame:
        return pl.DataFrame(self.rows)
