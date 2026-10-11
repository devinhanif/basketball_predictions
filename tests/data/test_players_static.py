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
    cached_missing_status_fields,
    load_players_static,
    parse_draft_status,
    parse_height_to_inches,
    player_ids_needing_pull,
    refresh_status_fields,
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


def test_parse_draft_status_needs_positive_evidence() -> None:
    assert parse_draft_status("Undrafted", "Undrafted") == "undrafted"
    assert parse_draft_status("2020", "Undrafted") == "undrafted"
    assert parse_draft_status("2009", "7") == "drafted"
    assert parse_draft_status("2009", "") == "drafted"
    assert parse_draft_status("", "") == "unknown"
    assert parse_draft_status(None, None) == "unknown"


def test_normalize_reads_from_year_and_status() -> None:
    raw = pl.DataFrame(
        {
            "POSITION": ["Guard"],
            "HEIGHT": ["6-2"],
            "WEIGHT": ["185"],
            "BIRTHDATE": ["1999-03-14T00:00:00"],
            "DRAFT_YEAR": ["Undrafted"],
            "DRAFT_NUMBER": ["Undrafted"],
            "SCHOOL": [""],
            "FROM_YEAR": ["2021"],
        }
    )
    row = _normalize_player_info(raw, 5).to_dicts()[0]
    assert row["first_season"] == 2021
    assert row["draft_status"] == "undrafted"
    assert row["draft_pick"] is None and row["draft_year"] is None


def test_schema_has_f9b_columns_and_load_roundtrip(con: duckdb.DuckDBPyConnection) -> None:
    raw = pl.DataFrame({"DRAFT_YEAR": ["2018"], "DRAFT_NUMBER": ["3"], "FROM_YEAR": ["2018"]})
    load_players_static(con, _normalize_player_info(raw, 9))
    assert con.execute(
        "SELECT first_season, draft_status FROM players_static WHERE player_id=9"
    ).fetchall() == [(2018, "drafted")]


def test_refresh_only_pulls_stale_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    d = tmp_path / "players_static"
    d.mkdir()
    old = _normalize_player_info(pl.DataFrame({"DRAFT_YEAR": ["2010"]}), 1).drop(
        "first_season", "draft_status"
    )
    old.write_parquet(d / "1.parquet")
    new = _normalize_player_info(pl.DataFrame({"FROM_YEAR": ["2015"]}), 2)
    new.write_parquet(d / "2.parquet")
    assert cached_missing_status_fields([1, 2, 3], data_dir=tmp_path) == [1, 3]
    calls: list[int] = []

    def fake(pid: int) -> pl.DataFrame:
        calls.append(pid)
        return _normalize_player_info(pl.DataFrame({"FROM_YEAR": ["2011"]}), pid)

    out = refresh_status_fields([1, 2, 3], data_dir=tmp_path, fetch=fake)
    assert calls == [1, 3] and out["pulled"] == 2
    assert refresh_status_fields([1, 2, 3], data_dir=tmp_path, fetch=fake)["needed"] == 0


def test_zero_column_cache_is_not_a_cache_and_is_never_written(tmp_path) -> None:
    import polars as pl
    import pytest

    from nba.ingest.players_static import _has_cache, _write_cache

    stub = tmp_path / "12737.parquet"
    pl.DataFrame().write_parquet(stub)
    assert not _has_cache(stub)
    with pytest.raises(ValueError):
        _write_cache(pl.DataFrame(), tmp_path / "x.parquet")
    ok = tmp_path / "1.parquet"
    _write_cache(pl.DataFrame({"player_id": [1]}), ok)
    assert _has_cache(ok)
