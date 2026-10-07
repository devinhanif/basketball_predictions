"""Tests for season-level bulk resolution of game_ids for boxscore/pbp pulls.

No network: the per-game fetch is monkeypatched, and the games table is
populated directly rather than via nba_api.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

import nba.ingest.__main__ as cli
from nba.db.connect import connect
from nba.ingest.cache import mark_done
from nba.ingest.games import game_ids_for_season


def _insert_game(
    con: duckdb.DuckDBPyConnection, game_id: str, season: int, date: str = "2023-10-24"
) -> None:
    con.execute(
        """
        INSERT INTO games (game_id, game_date, season, home_team, away_team,
                            home_pts, away_pts)
        VALUES (?, ?, ?, 1610612738, 1610612755, 100, 99)
        """,
        [game_id, date, season],
    )


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    yield con
    con.close()


def test_game_ids_for_season_resolves_from_games_table(
    con: duckdb.DuckDBPyConnection,
) -> None:
    _insert_game(con, "0022300001", 2023)
    _insert_game(con, "0022300002", 2023)
    _insert_game(con, "0022200001", 2022)  # different season -- excluded

    ids = game_ids_for_season(con, "2023-24")
    assert ids == ["0022300001", "0022300002"]


@pytest.fixture
def no_network_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "test.duckdb"
    con = cli.open_db(db_path)
    monkeypatch.setattr(cli, "open_db", lambda: con)
    return con


def test_boxscore_season_bulk_skips_already_cached(
    no_network_db: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
) -> None:
    con = no_network_db
    _insert_game(con, "0022300001", 2023)
    _insert_game(con, "0022300002", 2023)
    _insert_game(con, "0022300003", 2023)

    # Pretend 0022300001 was already pulled in a prior run.
    cached_path = tmp_path / "already_cached.parquet"
    cached_path.write_bytes(b"not a real parquet file but existence is all that matters")
    mark_done(con, "boxscore", "0022300001", cached_path)

    calls: list[str] = []

    import polars as pl

    def fake_pull(con: Any, game_id: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(game_id)
        return pl.DataFrame({"player_id": [1]})

    monkeypatch.setattr(cli, "pull_game_boxscore", fake_pull)
    rc = cli.main(["boxscore", "--season", "2023-24"])
    assert rc == 0

    # The monkeypatched pull_fn is still invoked per game_id (idempotent upsert
    # behavior lives inside pull_game_boxscore itself); what matters here is
    # that is_cached correctly identifies the pre-cached game for the summary.
    assert calls == ["0022300001", "0022300002", "0022300003"]
    out = capsys.readouterr().out
    assert "boxscore[season 2023-24]: 3 games, 1 cached, 2 fetched" in out


def test_boxscore_requires_game_id_or_season(
    no_network_db: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["boxscore"])


def test_team_advanced_season_bulk_resolves_and_skips_cached(
    no_network_db: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
) -> None:
    con = no_network_db
    _insert_game(con, "0022300001", 2023)
    _insert_game(con, "0022300002", 2023)

    cached_path = tmp_path / "already_cached.parquet"
    cached_path.write_bytes(b"not a real parquet file but existence is all that matters")
    mark_done(con, "team-advanced", "0022300001", cached_path)

    calls: list[str] = []

    import polars as pl

    def fake_pull(con: Any, game_id: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(game_id)
        return pl.DataFrame({"team_id": [1, 2]})

    monkeypatch.setattr(cli, "pull_game_team_advanced", fake_pull)
    rc = cli.main(["team-advanced", "--season", "2023-24"])
    assert rc == 0
    assert calls == ["0022300001", "0022300002"]
    out = capsys.readouterr().out
    assert "team-advanced[season 2023-24]: 2 games, 1 cached, 1 fetched" in out
