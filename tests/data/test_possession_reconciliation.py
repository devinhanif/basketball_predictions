"""Possession-count reconciliation on the committed fixture dataset.

CLAUDE.md requires validating possession counts against the box-score
estimate ``FGA + 0.44*FTA + TOV - OREB`` per team-game (mean error <= 1).
The parser (nba/parse/possessions.py) and reconciliation helper
(nba/parse/reconcile.py) now exist; this test proves the gate on the tiny
committed fixture (no network, no nba.duckdb). The *real* multi-season
gate (against the live box-score backfill) is the maintainer's to run
once that backfill lands -- see nba/parse/possessions.py and
nba/parse/reconcile.py docstrings.
"""

from __future__ import annotations

import polars as pl

from nba.parse.possessions import parse_possessions
from nba.parse.reconcile import (
    mean_abs_error,
    reconcile_possessions,
    team_box_from_player_game_stats,
)
from tests.fixtures.loader import build_fixture_db, load_pbp


def test_possession_count_reconciliation_mean_error_within_one() -> None:
    con = build_fixture_db(":memory:")
    try:
        pbp_map = load_pbp()
        possessions = pl.concat([parse_possessions(df) for df in pbp_map.values()])
        assert possessions.height > 0, "parser produced no possessions from the fixture"

        player_game_stats = con.execute("SELECT * FROM player_game_stats").pl()
        team_box = team_box_from_player_game_stats(player_game_stats)
        rec = reconcile_possessions(possessions, team_box)

        mae = mean_abs_error(rec)
        assert mae <= 1.0, (
            f"mean abs error {mae} exceeds the CLAUDE.md reconciliation gate of 1 possession"
        )
    finally:
        con.close()
