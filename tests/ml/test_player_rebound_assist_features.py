"""Tests for ``nba.features.player_rebound_assist_features`` (as-of player
rebound + assist rates).

Synthetic in-memory DuckDB only -- no real DB access, fast (CLAUDE.md "no
multi-minute jobs"). Mirrors ``tests/ml/test_player_possession_features.py``'s
conventions for the companion points-rates module.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.features.player_rebound_assist_features import (
    LEAGUE_ASSISTED_FG_RATE_DEFAULT,
    LEAGUE_AST_RATE_DEFAULT,
    LEAGUE_DRB_RATE_DEFAULT,
    LEAGUE_ORB_RATE_DEFAULT,
    build_player_reb_ast_rates,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _insert_game(
    con: duckdb.DuckDBPyConnection, game_id: str, game_date: str, home: int = 1, away: int = 2
) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, 2023, ?, ?, 100, 90)",
        [game_id, game_date, home, away],
    )


def _insert_pgs(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    player_id: int,
    team_id: int,
    fgm: int = 0,
    fga: int = 0,
    oreb: int = 0,
    dreb: int = 0,
    ast: int = 0,
) -> None:
    con.execute(
        """
        INSERT INTO player_game_stats
            (game_id, player_id, team_id, minutes, pts, reb, ast, fg3m, stl, blk, tov,
             starter, fgm, fga, oreb, dreb)
        VALUES (?, ?, ?, 20.0, 0, 0, ?, 0, 0, 0, 0, true, ?, ?, ?, ?)
        """,
        [game_id, player_id, team_id, ast, fgm, fga, oreb, dreb],
    )


def _row(rates: pl.DataFrame, game_id: str, player_id: int) -> dict[str, object]:
    """Filter down to exactly one row, re-deriving the boolean mask from
    each intermediate frame (not the original, unfiltered one) so the
    mask length always matches the frame being filtered."""
    sub = rates.filter(pl.col("game_id") == game_id)
    sub = sub.filter(pl.col("player_id") == player_id)
    assert sub.height == 1, f"expected exactly 1 row for {game_id}/{player_id}, got {sub.height}"
    return sub.to_dicts()[0]


def test_cold_start_player_shrinks_fully_to_prior(con: duckdb.DuckDBPyConnection) -> None:
    """A player's very first game has zero as-of history: every rate should
    come out exactly at the league/position prior, not fit to this game."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 101, 1, fgm=5, fga=10, oreb=3, dreb=2, ast=1)
    _insert_pgs(con, "g1", 102, 2, fgm=4, fga=12, oreb=1, dreb=4, ast=2)

    rates = build_player_reb_ast_rates(con)
    row = _row(rates, "g1", 101)
    assert row["n_oreb_chances_prior"] == 0.0
    assert abs(row["orb_rate_prior"] - LEAGUE_ORB_RATE_DEFAULT) < 1e-6
    assert abs(row["drb_rate_prior"] - LEAGUE_DRB_RATE_DEFAULT) < 1e-6
    assert abs(row["ast_rate_prior"] - LEAGUE_AST_RATE_DEFAULT) < 1e-6
    assert abs(row["assisted_fg_rate_prior"] - LEAGUE_ASSISTED_FG_RATE_DEFAULT) < 1e-6


def test_shrinkage_converges_toward_observed_rate_with_many_games(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """A player who always grabs every one of their team's own misses (and
    the opponent never scores, so misses are the only chances) should end
    up with orb_rate_prior much closer to 1.0 than to the league default."""
    n_games = 60
    for i in range(n_games):
        gid = f"g{i}"
        _insert_game(con, gid, f"2023-10-{(i % 27) + 1:02d}")
        # Player 202 (team 1): 0-for-10 shooting -> 10 misses, all 10
        # rebounded by player 202 themselves (oreb=10).
        _insert_pgs(con, gid, 202, 1, fgm=0, fga=10, oreb=10, dreb=0, ast=0)
        # A teammate so team_fga isn't entirely player 202's own shots.
        _insert_pgs(con, gid, 203, 1, fgm=0, fga=0, oreb=0, dreb=0, ast=0)
        _insert_pgs(con, gid, 301, 2, fgm=0, fga=0, oreb=0, dreb=0, ast=0)

    _insert_game(con, "g_last", "2023-12-15")
    _insert_pgs(con, "g_last", 202, 1)

    rates = build_player_reb_ast_rates(con)
    row = _row(rates, "g_last", 202)
    assert row["n_oreb_chances_prior"] == float(n_games * 10)
    # 600 observed chances at obs rate 1.0, shrunk with k=150 toward the
    # ~0.05 league default -> (600*1 + 150*0.05) / 750 = 0.81: well above
    # the league default, not fully shrunk toward it.
    assert row["orb_rate_prior"] > 0.75


def test_as_of_no_leakage_future_game_does_not_change_past_row(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """Planted-future-game proof (CLAUDE.md "no leakage"): a wild future
    game's stats must not change an earlier game's as-of feature row."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 303, 1, fgm=3, fga=6, oreb=1, dreb=2, ast=1)
    _insert_pgs(con, "g1", 304, 2, fgm=2, fga=5, oreb=0, dreb=3, ast=0)

    _insert_game(con, "g2", "2023-10-26")
    _insert_pgs(con, "g2", 303, 1, fgm=1, fga=2, oreb=0, dreb=1, ast=0)
    _insert_pgs(con, "g2", 304, 2, fgm=4, fga=7, oreb=1, dreb=2, ast=3)

    before = build_player_reb_ast_rates(con)
    snapshot = _row(before, "g1", 303)

    # Plant an extreme future game (wild rebound/assist volume) for the
    # same player.
    for i in range(50):
        gid = f"future_{i}"
        _insert_game(con, gid, "2024-01-01")
        _insert_pgs(con, gid, 303, 1, fgm=0, fga=20, oreb=20, dreb=20, ast=20)
        _insert_pgs(con, gid, 304, 2, fgm=10, fga=20, oreb=0, dreb=0, ast=0)

    after = build_player_reb_ast_rates(con)
    assert _row(after, "g1", 303) == snapshot


def test_empty_boxscore_degrades_gracefully(con: duckdb.DuckDBPyConnection) -> None:
    """Works even when a player has 0 FGA/FGM everywhere (mirrors
    ``player_possession_features``'s own fixture-compatibility test)."""
    _insert_game(con, "g1", "2023-10-24")
    _insert_pgs(con, "g1", 505, 1)
    rates = build_player_reb_ast_rates(con)
    assert rates.height == 1
    row = rates.to_dicts()[0]
    for v in row.values():
        assert v == v  # not NaN
