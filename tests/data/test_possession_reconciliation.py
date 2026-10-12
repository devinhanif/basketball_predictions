"""Possession-count reconciliation on the committed fixture dataset.

Hard gate (audit F12, restated 2026-10-11): possession points equal box points per team-game.
The possession-count formula ``FGA + 0.44*FTA + TOV - OREB`` is a diagnostic on real data
(docs/adr/0001) with the fixture bound restored to 1.0. Tiny committed fixture, no network,
no nba.duckdb.
"""

from __future__ import annotations

import polars as pl

from nba.parse.possessions import parse_possessions
from nba.parse.reconcile import (
    FIXTURE_MAE_MAX,
    FORMULA_BIAS_BAND,
    POINTS_MATCH_MIN,
    bias_outside_band,
    mean_abs_error,
    points_match_rate,
    reconcile_points,
    reconcile_possessions,
    signed_bias,
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


def test_possession_count_formula_holds_the_fixture_gate() -> None:
    con = build_fixture_db(":memory:")
    try:
        stats = con.execute("SELECT * FROM player_game_stats").pl()
        rec = reconcile_possessions(_fixture_possessions(), team_box_from_player_game_stats(stats))
        mae = mean_abs_error(rec)
        assert mae <= FIXTURE_MAE_MAX, f"formula MAE {mae} above the fixture gate"
    finally:
        con.close()


def _plant(possessions: pl.DataFrame, extra: int) -> pl.DataFrame:
    """Add ``extra`` duplicated possessions to every team-game (a parser that over-counts)."""
    dup = (
        possessions.group_by(["game_id", "off_team"], maintain_order=True)
        .head(extra)
        .select(possessions.columns)
    )
    return pl.concat([possessions, dup])


def test_planted_two_extra_possessions_per_team_game_fails_the_fixture_gate() -> None:
    con = build_fixture_db(":memory:")
    try:
        stats = con.execute("SELECT * FROM player_game_stats").pl()
        box = team_box_from_player_game_stats(stats)
        clean = _fixture_possessions()
        assert mean_abs_error(reconcile_possessions(clean, box)) <= FIXTURE_MAE_MAX
        planted = reconcile_possessions(_plant(clean, 2), box)
        assert mean_abs_error(planted) > FIXTURE_MAE_MAX
        assert signed_bias(planted) > signed_bias(reconcile_possessions(clean, box)) + 1.5
    finally:
        con.close()


def test_bias_band_flags_a_shift_of_the_stored_real_bias() -> None:
    lo, hi = FORMULA_BIAS_BAND
    assert lo < -2.0 and hi > -1.6  # the stored per-season biases (-1.6 .. -2.0) sit inside
    inside = pl.DataFrame({"error": [-1.8, -1.7, -1.9]})
    shifted = pl.DataFrame({"error": [0.2, 0.1, 0.3]})  # stored bias plus a planted +2
    assert not bias_outside_band(inside)
    assert bias_outside_band(shifted)
