"""CLI dispatch tests for `python -m nba.ingest`.

Network-touching pull_* functions are monkeypatched; this only exercises
argument parsing and dispatch, never nba_api.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

import nba.ingest.__main__ as cli


@pytest.fixture
def no_network_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "test.duckdb"
    con = cli.open_db(db_path)
    monkeypatch.setattr(cli, "open_db", lambda: con)
    return con


def test_games_command_dispatches(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    def fake_pull(con: Any, season: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(season)
        return pl.DataFrame({"game_id": ["x"]})

    monkeypatch.setattr(cli, "pull_season_games", fake_pull)
    rc = cli.main(["games", "--season", "2023-24"])
    assert rc == 0
    assert calls == ["2023-24"]
    assert "games[2023-24]: 1 rows" in capsys.readouterr().out


def test_boxscore_command_dispatches(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    def fake_pull(con: Any, game_id: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(game_id)
        return pl.DataFrame({"player_id": [1, 2]})

    monkeypatch.setattr(cli, "pull_game_boxscore", fake_pull)
    rc = cli.main(["boxscore", "--game-id", "0022300001"])
    assert rc == 0
    assert calls == ["0022300001"]
    assert "boxscore[0022300001]: 2 rows" in capsys.readouterr().out


def test_team_advanced_command_dispatches(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    def fake_pull(con: Any, game_id: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(game_id)
        return pl.DataFrame({"team_id": [1, 2]})

    monkeypatch.setattr(cli, "pull_game_team_advanced", fake_pull)
    rc = cli.main(["team-advanced", "--game-id", "0022300001"])
    assert rc == 0
    assert calls == ["0022300001"]
    assert "team-advanced[0022300001]: 2 rows" in capsys.readouterr().out


def test_pbp_command_dispatches(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    def fake_pull(con: Any, game_id: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(game_id)
        return pl.DataFrame({"eventnum": [1]})

    monkeypatch.setattr(cli, "pull_game_pbp", fake_pull)
    rc = cli.main(["pbp", "--game-id", "0022300001"])
    assert rc == 0
    assert calls == ["0022300001"]
    assert "pbp[0022300001]: 1 rows" in capsys.readouterr().out


def test_national_tv_command_dispatches_by_date(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    calls: list[str] = []

    def fake_pull(con: Any, date: str, **kwargs: Any) -> pl.DataFrame:
        calls.append(date)
        return pl.DataFrame({"game_id": ["0022300401", "0022300402"]})

    monkeypatch.setattr(cli, "pull_national_tv_for_date", fake_pull)
    rc = cli.main(["national-tv", "--date", "2023-12-25"])
    assert rc == 0
    assert calls == ["2023-12-25"]
    assert "national-tv[2023-12-25]: 2 rows" in capsys.readouterr().out


def test_national_tv_command_dispatches_by_season(
    no_network_db: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    pull_calls: list[str] = []

    monkeypatch.setattr(cli, "game_dates_for_season", lambda con, season: ["2023-12-25"])

    def fake_pull(con: Any, date: str, **kwargs: Any) -> pl.DataFrame:
        pull_calls.append(date)
        return pl.DataFrame({"game_id": ["0022300401"]})

    monkeypatch.setattr(cli, "pull_national_tv_for_date", fake_pull)
    rc = cli.main(["national-tv", "--season", "2023-24"])
    assert rc == 0
    assert pull_calls == ["2023-12-25"]
    out = capsys.readouterr().out
    assert "national-tv[season 2023-24]: 1 dates" in out


def test_national_tv_command_requires_date_or_season(
    no_network_db: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(SystemExit):
        cli.main(["national-tv"])
