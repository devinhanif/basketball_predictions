"""Wiring tests: ``build_player_shot_rates(use_time_decay=...)``.

Synthetic in-memory DuckDB only.
"""

from __future__ import annotations

import hashlib

import duckdb
import polars as pl
import pytest

import nba.features.player_possession_features as ppf
from nba.db.connect import connect
from nba.features.player_possession_features import build_player_shot_rates
from nba.features.time_decay import TimeDecayConfig

#: sha256 of the default SQL as it existed before the time-decay wiring.
_DEFAULT_SQL_SHA256 = "8560eaf723f2e7897314047ae47ef81b61f26ca605aee2ce988439c302e682d9"


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = connect(":memory:")
    yield c
    c.close()


def _game(con: duckdb.DuckDBPyConnection, gid: str, date: str, season: int) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, "
        "away_pts) VALUES (?, ?, ?, 1, 2, 100, 90)",
        [gid, date, season],
    )


def _pgs(con: duckdb.DuckDBPyConnection, gid: str, pid: int) -> None:
    con.execute(
        "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, reb, ast, "
        "fg3m, stl, blk, tov, starter, fta, ftm) VALUES (?, ?, 1, 30, 10, 5, 3, 1, 1, 0, 1, "
        "true, 0, 0)",
        [gid, pid],
    )


def _shot(
    con: duckdb.DuckDBPyConnection, gid: str, idx: int, pid: int, outcome: str, zone: str = "mid"
) -> None:
    con.execute(
        "INSERT INTO possessions (game_id, poss_idx, period, clock_start, clock_end, off_team, "
        "def_team, score_diff, outcome, shooter_id, shot_zone, fta, pts) "
        "VALUES (?, ?, 1, 700.0, 680.0, 1, 2, 0, ?, ?, ?, 0, 0)",
        [gid, idx, outcome, pid, zone],
    )


def _seed(con: duckdb.DuckDBPyConnection) -> None:
    # Season 2022: player 7 hits every mid shot (many); season 2023 opener + later games.
    for i in range(3):
        _game(con, f"a{i}", f"2022-11-0{i + 1}", 2022)
        _pgs(con, f"a{i}", 7)
        for j in range(5):
            _shot(con, f"a{i}", j, 7, "FGM2")
    for i in range(3):
        _game(con, f"b{i}", f"2023-11-0{i + 1}", 2023)
        _pgs(con, f"b{i}", 7)
        _shot(con, f"b{i}", 0, 7, "FGA_miss")


def test_default_sql_unchanged_and_flag_off_is_identical(con: duckdb.DuckDBPyConnection) -> None:
    assert hashlib.sha256(ppf._PLAYER_SHOT_RATES_SQL.encode()).hexdigest() == _DEFAULT_SQL_SHA256
    _seed(con)
    default = build_player_shot_rates(con)
    off = build_player_shot_rates(con, use_time_decay=False, time_decay_config=TimeDecayConfig())
    assert default.equals(off)


def test_decay_on_changes_opener_and_matches_off_in_first_season(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _seed(con)
    off = build_player_shot_rates(con)
    on = build_player_shot_rates(con, use_time_decay=True)
    assert on.height == off.height
    j = off.join(on, on=["game_id", "player_id"], suffix="_on")
    first = j.filter(pl.col("game_id").str.starts_with("a"))
    assert first["n_fga_prior"].to_list() == first["n_fga_prior_on"].to_list()
    assert first["zone_fg_pct_mid_prior"].to_list() == first["zone_fg_pct_mid_prior_on"].to_list()
    # Season opener: last season's makes carry weight (decay-weighted, < equal-weight).
    opener = j.filter(pl.col("game_id") == "b0").row(0, named=True)
    assert 0 < opener["n_fga_prior_on"] <= opener["n_fga_prior"]
    assert opener["zone_fg_pct_mid_prior_on"] > 0.0


def test_decay_on_planted_future_rows_do_not_leak(con: duckdb.DuckDBPyConnection) -> None:
    """Rows for game G must be identical whether or not games on/after G exist."""
    _seed(con)
    cfg = TimeDecayConfig(half_life_seasons=1.0, carryover_decay_weight=0.0)
    before = build_player_shot_rates(con, use_time_decay=True, time_decay_config=cfg)
    # Plant extreme future games: same-season later date, and a later season.
    _game(con, "b9", "2023-12-30", 2023)
    _pgs(con, "b9", 7)
    for j in range(40):
        _shot(con, "b9", j, 7, "FGM3", "above3")
    _game(con, "c0", "2024-11-01", 2024)
    _pgs(con, "c0", 7)
    for j in range(40):
        _shot(con, "c0", j, 7, "FGM3", "above3")
    after = build_player_shot_rates(con, use_time_decay=True, time_decay_config=cfg)
    keep = before["game_id"].to_list()
    a = after.filter(pl.col("game_id").is_in(keep)).sort(["game_date", "game_id", "player_id"])
    b = before.sort(["game_date", "game_id", "player_id"])
    assert a.equals(b)
    # The planted 2024 game does see 2023's (incl. b9) totals, never its own.
    c0 = after.filter(pl.col("game_id") == "c0").row(0, named=True)
    assert c0["n_fga_prior"] > 0
    own = after.filter(pl.col("game_id") == "b9").row(0, named=True)
    assert own["n_fga_prior"] < 40
