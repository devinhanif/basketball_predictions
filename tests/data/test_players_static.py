"""Unit tests for nba.ingest.players_static.

No network, no nba_api import -- parse/normalize/load logic only, against
synthetic CommonPlayerInfo frames and an in-memory DuckDB.
"""

from __future__ import annotations

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.players_static import (
    _normalize_player_info,
    _parse_int,
    load_players_static,
    parse_height_to_inches,
    player_ids_needing_pull,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    yield con
    con.close()


def test_parse_height_to_inches() -> None:
    assert parse_height_to_inches("6-7") == 79.0
    assert parse_height_to_inches("7-0") == 84.0
    assert parse_height_to_inches("5-11") == 71.0
    assert parse_height_to_inches("") is None
    assert parse_height_to_inches(None) is None
    assert parse_height_to_inches("abc") is None


def test_parse_int_handles_undrafted() -> None:
    assert _parse_int("11") == 11
    assert _parse_int(3) == 3
    assert _parse_int("Undrafted") is None
    assert _parse_int("") is None
    assert _parse_int(None) is None


def test_normalize_player_info_maps_real_columns() -> None:
    # Column names/values as returned by CommonPlayerInfo.get_data_frames()[0].
    raw = pl.DataFrame(
        {
            "PERSON_ID": [201939],
            "POSITION": ["Guard"],
            "HEIGHT": ["6-2"],
            "WEIGHT": ["185"],
            "BIRTHDATE": ["1988-03-14T00:00:00"],
            "DRAFT_YEAR": ["2009"],
            "DRAFT_NUMBER": ["7"],
            "SCHOOL": ["Davidson"],
        }
    )
    out = _normalize_player_info(raw, 201939)
    row = out.to_dicts()[0]
    assert row["player_id"] == 201939
    assert row["position"] == "Guard"
    assert row["height_in"] == 74.0
    assert row["weight_lb"] == 185.0
    assert str(row["birth_date"]) == "1988-03-14"
    assert row["draft_year"] == 2009
    assert row["draft_pick"] == 7
    assert row["college"] == "Davidson"


def test_normalize_player_info_undrafted_and_empty() -> None:
    raw = pl.DataFrame(
        {
            "PERSON_ID": [1630],
            "POSITION": [""],
            "HEIGHT": [""],
            "WEIGHT": [""],
            "BIRTHDATE": [""],
            "DRAFT_YEAR": ["Undrafted"],
            "DRAFT_NUMBER": ["Undrafted"],
            "SCHOOL": [""],
        }
    )
    row = _normalize_player_info(raw, 1630).to_dicts()[0]
    assert row["position"] is None
    assert row["height_in"] is None
    assert row["draft_pick"] is None
    assert row["draft_year"] is None
    assert row["college"] is None

    empty = _normalize_player_info(pl.DataFrame(), 42).to_dicts()[0]
    assert empty["player_id"] == 42
    assert empty["position"] is None


def test_player_ids_needing_pull_and_load(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES ('001', DATE '2023-01-01', 2022, 1, 2, 100, 99)"
    )
    for pid in (10, 20, 30):
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts) "
            "VALUES ('001', ?, 1, 20.0, 5)",
            [pid],
        )
    assert player_ids_needing_pull(con) == [10, 20, 30]

    df = pl.DataFrame(
        {
            "player_id": [10, 20],
            "position": ["Guard", "Center"],
            "height_in": [74.0, 83.0],
            "weight_lb": [185.0, 250.0],
            "birth_date": [None, None],
            "draft_year": [2009, None],
            "draft_pick": [7, None],
            "college": ["Davidson", None],
        },
        schema={
            "player_id": pl.Int64,
            "position": pl.Utf8,
            "height_in": pl.Float64,
            "weight_lb": pl.Float64,
            "birth_date": pl.Date,
            "draft_year": pl.Int64,
            "draft_pick": pl.Int64,
            "college": pl.Utf8,
        },
    )
    assert load_players_static(con, df) == 2
    # 10 and 20 are now present; only 30 still needs a pull. Idempotent re-load.
    assert player_ids_needing_pull(con) == [30]
    assert load_players_static(con, df) == 2
    assert con.execute("SELECT COUNT(*) FROM players_static").fetchone()[0] == 2
