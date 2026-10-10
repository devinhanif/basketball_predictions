"""A tiny hand-made source: two 2022 games, one 2025 (holdout) game, one planted late snapshot."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.facts.build import build
from nba.lineups.store import DDL

G1, G2, G_HOLD = "0022200001", "0022200002", "0022500001"
# G1 tips 2022-10-19 23:30 UTC = 19:30 ET (EDT). G2 is a matinee, 2022-10-20 17:00 UTC.
TIP1 = dt.datetime(2022, 10, 19, 23, 30)
TIP2 = dt.datetime(2022, 10, 20, 17, 0)


def make_source() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE games (game_id VARCHAR, game_date DATE, season INTEGER, home_team INTEGER, "
        "away_team INTEGER, home_pts INTEGER, away_pts INTEGER, national_tv VARCHAR)"
    )
    c.execute(
        "INSERT INTO games VALUES "
        f"('{G1}','2022-10-19',2022,10,20,100,90,NULL),"
        f"('{G2}','2022-10-20',2022,30,40,95,99,NULL),"
        f"('{G_HOLD}','2026-01-05',2025,10,30,1,2,NULL)"
    )
    c.execute(
        "CREATE TABLE player_availability (player_id INTEGER, as_of TIMESTAMP, game_id VARCHAR, "
        "status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP)"
    )
    c.execute(
        "INSERT INTO player_availability VALUES "
        f"(1,'2022-10-19 11:00:00','{G1}','OUT','x','nba_official_report',NULL),"
        f"(2,'2022-10-19 17:45:00','{G1}','Questionable','x','nba_official_report',NULL),"
        f"(1,'2022-10-19 11:00:00','{G2}','OUT','x','some_other_source',NULL),"
        f"(9,'2026-01-05 11:00:00','{G_HOLD}','OUT','x','nba_official_report',NULL)"
    )
    c.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INTEGER, team_id INTEGER, "
        "minutes FLOAT)"
    )
    c.execute(
        f"INSERT INTO player_game_stats VALUES ('{G1}',2,10,31.5),('{G1}',1,10,NULL),"
        f"('{G_HOLD}',9,10,20.0)"
    )
    c.execute(
        "CREATE TABLE players_static (player_id INTEGER, position VARCHAR, height_in FLOAT, "
        "first_season INTEGER)"
    )
    c.execute("INSERT INTO players_static VALUES (1,'G',75,2019),(2,'F',80,2015),(3,'C',84,NULL)")
    c.execute(
        "CREATE TABLE team_coaches (season INTEGER, team_id INTEGER, coach_id INTEGER, "
        "name VARCHAR, coach_type VARCHAR, is_assistant INTEGER)"
    )
    c.execute(
        "INSERT INTO team_coaches VALUES (2022,10,500,'Ann Coach','Head Coach',1),"
        "(2022,10,501,'Bo Assist','Assistant Coach',2),(2025,10,502,'Late','Head Coach',1)"
    )
    return c


def make_lineups() -> duckdb.DuckDBPyConnection:
    lc = duckdb.connect(":memory:")
    lc.execute(DDL)
    rows = [
        # (fetched_at, player, starter): T-40 snapshot, then a PLANTED snapshot after tip
        (dt.datetime(2022, 10, 19, 22, 50), 2, True),
        (dt.datetime(2022, 10, 19, 23, 45), 1, True),
    ]
    for fetched, pid, starter in rows:
        lc.execute(
            "INSERT INTO lineup_snapshots VALUES ('s', ?, '2022-10-19', ?, 1, 'x', 10, 'AAA', "
            "TRUE, ?, 'n', 'Confirmed', ?, 'G', 'Active', NULL)",
            [fetched, G1, pid, starter],
        )
    lc.execute(
        "INSERT INTO game_tips VALUES (?, '2022-10-19', ?, 'status_text', ?)",
        [G1, TIP1, dt.datetime(2022, 10, 19, 13, 0)],
    )
    return lc


@pytest.fixture
def schedule_dir(tmp_path: Path) -> Path:
    d = tmp_path / "schedule"
    d.mkdir()
    pl.DataFrame(
        {
            "gameId": [G1, G2, G_HOLD],
            "gameDateTimeUTC": [
                "2022-10-19T23:30:00Z",
                "2022-10-20T17:00:00Z",
                "2026-01-05T00:00:00Z",
            ],
            "gameDateTimeEst": [
                "2022-10-19T19:30:00Z",
                "2022-10-20T13:00:00Z",
                "2026-01-04T19:00:00Z",
            ],
        }
    ).write_parquet(d / "raw_2022-23.parquet")
    return d


@pytest.fixture
def built(tmp_path: Path, schedule_dir: Path):
    out = tmp_path / "facts.duckdb"
    counts = build(
        make_source(),
        make_lineups(),
        out,
        schedule_dir=schedule_dir,
        odds_aliases=tmp_path / "none.yaml",
        kalshi_aliases=tmp_path / "none2.yaml",
    )
    con = duckdb.connect(str(out), read_only=True)
    yield con, counts
    con.close()
