# ruff: noqa: E501
"""Plan item A (2026-10-20): top_source resolves teams for a game absent from `games` (OKC@SAS) from
the schedule cache, and never offers a withdrawn player."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import duckdb
from test_top_source import CALMAP, GID, HOME, MADE, PID, SLATE, TIP, build_nba, build_odds, quote

from nba.daily.schedule import ScheduledGame, save_schedule_cache
from nba.daily.store import ensure_tables
from nba.parlay import top_source as ts

AWAY = 1610612752


def _legs(nba: duckdb.DuckDBPyConnection, cache: Path) -> ts.Loaded:
    odds = build_odds([quote("draftkings", "over", -115), quote("draftkings", "under", -105)])
    return ts.load_legs(
        nba, odds, SLATE, now=MADE + timedelta(minutes=5), calmap=CALMAP, schedule_cache_dir=cache
    )


def _without_games_row(nba: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    nba.execute("DELETE FROM games WHERE game_id = ?", [GID])
    return nba


def test_game_absent_from_games_resolves_from_the_schedule_cache(tmp_path: Path) -> None:
    save_schedule_cache([ScheduledGame(GID, TIP, HOME, AWAY)], "2026-27", tmp_path)
    out = _legs(_without_games_row(build_nba()), tmp_path)
    assert len(out.legs) == 2, dict(out.dropped)
    leg = out.legs[0]
    assert leg.team_id == HOME and leg.opp_id == AWAY and leg.team != leg.opp


def test_game_absent_from_games_falls_back_to_the_win_row_teams(tmp_path: Path) -> None:
    nba = _without_games_row(build_nba())
    nba.execute(
        "INSERT INTO forward_predictions VALUES ('r1', ?, ?, ?, 'rung0_injury_elo', 'v', 'win_prob_home', -1, ?)",
        [MADE, GID, TIP, json.dumps({"p_home": 0.6, "home_team": HOME, "away_team": AWAY})],
    )
    out = _legs(nba, tmp_path / "no_cache_here")
    assert len(out.legs) == 2 and out.legs[0].opp_id == AWAY


def test_game_with_no_teams_anywhere_is_dropped_and_counted(tmp_path: Path) -> None:
    out = _legs(_without_games_row(build_nba()), tmp_path / "none")
    assert out.legs == [] and out.dropped["game_teams_unknown"] == 1


def test_withdrawn_player_is_never_offered(tmp_path: Path) -> None:
    nba = build_nba()
    ensure_tables(nba)
    assert len(_legs(nba, tmp_path).legs) == 2
    nba.execute(
        "INSERT INTO forward_withdrawals VALUES (?, 'r2', ?, ?, 'pts', ?, 1610612738, 'official_roster_team_not_in_game')",
        [MADE, GID, ts.PROPS_MODEL, PID],
    )
    out = _legs(nba, tmp_path)
    assert out.legs == [] and out.no_slate
