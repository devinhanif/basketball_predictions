"""Unit tests for nba.ingest.national_tv.

No network, no nba_api import -- normalize/migration/resolve logic only,
exercised against synthetic frames and a tmp/in-memory DuckDB.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.cache import EmptyFetchError, fetch_cached
from nba.ingest.national_tv import (
    _normalize_scoreboard_header,
    ensure_national_tv_column,
    game_dates_for_season,
    is_national,
    update_national_tv,
)


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    yield con
    con.close()


def test_is_national() -> None:
    assert is_national("ESPN") is True
    assert is_national("ABC/ESPN") is True
    assert is_national("") is False
    assert is_national(None) is False


def test_normalize_scoreboard_header_maps_real_column_names() -> None:
    # Column names/values as observed live from
    # ScoreboardV2(game_date='2023-12-25').get_data_frames()[0].
    raw = pl.DataFrame(
        {
            "GAME_ID": ["0022300401", "0022300402", "0022300403"],
            "NATL_TV_BROADCASTER_ABBREVIATION": ["ESPN", "ABC/ESPN", ""],
        }
    )
    out = _normalize_scoreboard_header(raw)
    rows = {r["game_id"]: r["national_tv"] for r in out.iter_rows(named=True)}
    assert rows["0022300401"] == "ESPN"
    assert rows["0022300402"] == "ABC/ESPN"
    assert rows["0022300403"] is None


def test_ensure_national_tv_column_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    ensure_national_tv_column(con)
    ensure_national_tv_column(con)
    cols = {row[1] for row in con.execute("PRAGMA table_info('games')").fetchall()}
    assert "national_tv" in cols


def test_update_national_tv_only_touches_existing_games(con: duckdb.DuckDBPyConnection) -> None:
    ensure_national_tv_column(con)
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES ('0022300401', '2023-12-25', 2023, 1610612738, 1610612747, 100, 90)"
    )
    df = pl.DataFrame(
        {
            "game_id": ["0022300401", "9999999999"],  # second id has no games row
            "national_tv": ["ESPN", "TNT"],
        }
    )
    update_national_tv(con, df)
    row = con.execute("SELECT national_tv FROM games WHERE game_id = '0022300401'").fetchone()
    assert row == ("ESPN",)
    count = con.execute("SELECT count(*) FROM games").fetchone()
    assert count == (1,), "update must never insert a new games row"


def test_game_dates_for_season_resolves_from_games_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO games (game_id, game_date, season, home_team, away_team, home_pts, away_pts) "
        "VALUES "
        "('0022300401', '2023-12-25', 2023, 1610612738, 1610612747, 100, 90), "
        "('0022300402', '2023-12-25', 2023, 1610612737, 1610612755, 110, 95), "
        "('0022300500', '2023-12-26', 2023, 1610612737, 1610612755, 101, 92), "
        "('0022400001', '2024-10-22', 2024, 1610612737, 1610612755, 101, 92)"
    )
    dates = game_dates_for_season(con, "2023-24")
    assert dates == ["2023-12-25", "2023-12-26"]


def test_empty_scoreboard_marks_failed_not_done(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    def empty_fetch() -> pl.DataFrame:
        return pl.DataFrame({"game_id": [], "national_tv": []})

    with pytest.raises(EmptyFetchError):
        fetch_cached(
            con, "national-tv", "2023-12-25", empty_fetch, data_dir=tmp_path, allow_empty=False
        )
    row = con.execute(
        "SELECT status FROM ingest_log WHERE source = 'national-tv' AND key = '2023-12-25'"
    ).fetchone()
    assert row == ("failed",)
