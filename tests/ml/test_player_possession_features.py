"""Tests for ``nba.features.player_possession_features`` (as-of player shot rates).

Synthetic in-memory DuckDB only -- no real DB access, fast (CLAUDE.md "no
multi-minute jobs").
"""

from __future__ import annotations

import duckdb
import pytest

from nba.db.connect import connect
from nba.features.player_possession_features import (
    LEAGUE_ZONE_FG_PCT_DEFAULT,
    LEAGUE_ZONE_MIX_DEFAULT,
    build_player_shot_rates,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(con: duckdb.DuckDBPyConnection, game_id: str, game_date: str) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, 2023, 1, 2, 100, 90)",
        [game_id, game_date],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    fta: int = 0,
    ftm: int = 0,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov,
             starter, fta, ftm)
        VALUES (?, ?, ?, 30.0, 10, 5, 3, 1, 1, 0, 1, true, ?, ?)
        """,
        [game_id, player_id, team_id, fta, ftm],
    )


def _insert_shot(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    poss_idx: int,
    off_team: int,
    def_team: int,
    shooter_id: int,
    shot_zone: str,
    outcome: str,
    pts: int,
) -> None:
    con.execute(
        """
        INSERT INTO possessions
            (game_id, poss_idx, period, clock_start, clock_end, off_team, def_team,
             score_diff, outcome, shooter_id, shot_zone, fta, pts)
        VALUES (?, ?, 1, 700.0, 680.0, ?, ?, 0, ?, ?, ?, 0, ?)
        """,
        [game_id, poss_idx, off_team, def_team, outcome, shooter_id, shot_zone, pts],
    )


def test_cold_start_player_shrinks_fully_to_prior(con: duckdb.DuckDBPyConnection) -> None:
    """A player's very first game has zero as-of history: every rate should
    come out exactly at (or very near) the league/position prior, not 0 and
    not some fluke small-sample value."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, 1)
    _insert_shot(con, "g1", 0, 1, 2, 101, "rim", "FGM2", 2)

    rates = build_player_shot_rates(con)
    row = rates.filter(rates["game_id"] == "g1").filter(rates["player_id"] == 101)
    assert row.height == 1
    r = row.to_dicts()[0]
    # n_fga_prior == 0 (no history yet) -> zone mix should equal the league
    # default (normalized), not whatever this game's own shot happened to be.
    assert r["n_fga_prior"] == 0.0
    total = (
        LEAGUE_ZONE_MIX_DEFAULT["rim"]
        + LEAGUE_ZONE_MIX_DEFAULT["mid"]
        + (LEAGUE_ZONE_MIX_DEFAULT["above3"])
    )
    assert abs(r["zone_mix_rim_prior"] - LEAGUE_ZONE_MIX_DEFAULT["rim"] / total) < 1e-6
    assert abs(r["zone_fg_pct_rim_prior"] - LEAGUE_ZONE_FG_PCT_DEFAULT["rim"]) < 1e-6


def test_shrinkage_converges_toward_observed_rate_with_many_games(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A player with a long, consistent history of all-rim makes should end
    up with a rim zone_fg_pct_prior much closer to 1.0 (observed) than to
    the league default -- CLAUDE.md shrinkage convergence requirement."""
    n_games = 60
    for i in range(n_games):
        gid = f"g{i}"
        _insert_game(con, gid, f"2023-10-{(i % 27) + 1:02d}")
        _insert_pgs(con, gid, 202, 1)
        _insert_shot(con, gid, 0, 1, 2, 202, "rim", "FGM2", 2)

    # One more game to read the as-of prior built from all 60 prior games.
    _insert_game(con, "g_last", "2023-12-15")
    _insert_pgs(con, "g_last", 202, 1)

    rates = build_player_shot_rates(con)
    row = rates.filter(rates["game_id"] == "g_last").filter(rates["player_id"] == 202)
    r = row.to_dicts()[0]
    assert r["n_fga_prior"] == float(n_games)
    # Observed rim make rate is 1.0 over 60 shots; should pull well away
    # from the league default (~0.72) toward the observed rate.
    assert r["zone_fg_pct_rim_prior"] > 0.9
    assert r["zone_mix_rim_prior"] > 0.9


def test_as_of_no_leakage_future_game_does_not_change_past_prediction(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Planted-future-game proof (CLAUDE.md "no leakage"): a wild future
    game's shots must not change an earlier game's as-of feature row."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 303, 1)
    _insert_shot(con, "g1", 0, 1, 2, 303, "rim", "FGM2", 2)

    _insert_game(con, "g2", "2023-10-26")
    _insert_pgs(con, "g2", 303, 1)
    _insert_shot(con, "g2", 0, 1, 2, 303, "above3", "FGM3", 3)

    before = build_player_shot_rates(con)
    row_before = before.filter(before["game_id"] == "g1").filter(before["player_id"] == 303)
    snapshot = row_before.to_dicts()[0]

    # Plant an extreme future game (wild above3-only volume) for the same player.
    for i in range(50):
        gid = f"future_{i}"
        _insert_game(con, gid, "2024-01-01")
        _insert_pgs(con, gid, 303, 1)
        for j in range(20):
            _insert_shot(con, gid, j, 1, 2, 303, "above3", "FGM3", 3)

    after = build_player_shot_rates(con)
    row_after = after.filter(after["game_id"] == "g1").filter(after["player_id"] == 303)
    assert row_after.to_dicts()[0] == snapshot


def test_zone_mix_sums_to_one(con: duckdb.DuckDBPyConnection) -> None:
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 404, 1)
    for i, (zone, outcome, pts) in enumerate(
        [("rim", "FGM2", 2), ("mid", "FGA_miss", 0), ("above3", "FGM3", 3)]
    ):
        _insert_shot(con, "g1", i, 1, 2, 404, zone, outcome, pts)

    _insert_game(con, "g2", "2023-10-26")
    _insert_pgs(con, "g2", 404, 1)

    rates = build_player_shot_rates(con)
    row = rates.filter(rates["game_id"] == "g2").filter(rates["player_id"] == 404).to_dicts()[0]
    mix_sum = row["zone_mix_rim_prior"] + row["zone_mix_mid_prior"] + row["zone_mix_above3_prior"]
    assert abs(mix_sum - 1.0) < 1e-6


def test_empty_possessions_table_degrades_gracefully(con: duckdb.DuckDBPyConnection) -> None:
    """Works even when ``possessions`` is empty (mirrors
    ``nba.features.possession_features``'s own fixture-compatibility test)."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 505, 1)
    rates = build_player_shot_rates(con)
    assert rates.height == 1
    row = rates.to_dicts()[0]
    for v in row.values():
        assert v == v  # not NaN
