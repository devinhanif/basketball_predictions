"""Possession-count reconciliation on the committed fixture dataset.

Hard gate (audit F12, restated 2026-10-11): possession points equal box points per team-game.
The possession-count formula ``FGA + 0.44*FTA + TOV - OREB`` is a diagnostic with a loose bound
(docs/adr/0001). Tiny committed fixture, no network, no nba.duckdb.
"""

from __future__ import annotations

import polars as pl

from nba.parse.possessions import parse_possessions
from nba.parse.reconcile import (
    FORMULA_MAE_DIAGNOSTIC,
    POINTS_MATCH_MIN,
    mean_abs_error,
    points_match_rate,
    reconcile_points,
    reconcile_possessions,
    team_box_from_player_game_stats,
)
from tests.fixtures.loader import build_fixture_db, load_pbp


def _fixture_possessions() -> pl.DataFrame:
    pbp_map = load_pbp()
    possessions = pl.concat([parse_possessions(df) for df in pbp_map.values()])
    assert possessions.height > 0, "parser produced no possessions from the fixture"
    return possessions


def test_possession_points_equal_box_points_is_the_hard_gate() -> None:
    con = build_fixture_db(":memory:")
    try:
        stats = con.execute("SELECT * FROM player_game_stats").pl()
        rec = reconcile_points(_fixture_possessions(), stats)
        rate = points_match_rate(rec)
        assert rate >= POINTS_MATCH_MIN, (
            f"points match {rate:.3f}\n{rec.filter(pl.col('diff') != 0)}"
        )
    finally:
        con.close()


def test_possession_count_formula_is_a_reported_diagnostic() -> None:
    con = build_fixture_db(":memory:")
    try:
        stats = con.execute("SELECT * FROM player_game_stats").pl()
        rec = reconcile_possessions(_fixture_possessions(), team_box_from_player_game_stats(stats))
        mae = mean_abs_error(rec)
        assert mae <= FORMULA_MAE_DIAGNOSTIC, f"formula MAE {mae} above the diagnostic level"
    finally:
        con.close()
