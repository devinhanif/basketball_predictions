"""Local player-name fallback: used when the static list misses, conflict rule, placeholder."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pytest

from nba.explain import local_names, names
from nba.export.build import _names

MISSING = 9_999_001  # not in the static nba_api list
MISSING2 = 9_999_002
NOBODY = 9_999_003


def _write_roster(root: Path, day: str, rows: list[tuple[int, str]]) -> None:
    d = root / "rosters" / day
    d.mkdir(parents=True)
    con = duckdb.connect()
    con.execute("create table t(team_id bigint, player_id bigint, player_name varchar)")
    con.executemany("insert into t values (1, ?, ?)", rows)
    con.execute(f"copy t to '{d / '1.parquet'}' (format parquet)")
    con.close()


def _write_common(root: Path, rows: list[tuple[int, str]]) -> None:
    d = root / "players_static"
    d.mkdir(parents=True)
    con = duckdb.connect()
    con.execute("create table t(person_id bigint, display_first_last varchar)")
    con.executemany("insert into t values (?, ?)", rows)
    con.execute(f"copy t to '{d / 'commonallplayers_2025-26.parquet'}' (format parquet)")
    con.close()


def test_fallback_used_when_static_misses(tmp_path: Path) -> None:
    assert MISSING not in names._players()
    _write_roster(tmp_path, "2026-11-01", [(MISSING, "New Rookie")])
    got = local_names.load_local_names(tmp_path)
    assert got.names == {MISSING: "New Rookie"}
    assert names.player_name(MISSING, got.names) == "New Rookie"
    players, _ = _names({MISSING}, set(), fallback=got.names)
    assert players == [{"player_id": MISSING, "full_name": "New Rookie"}]


def test_conflict_prefers_roster_over_folded_official_list_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_roster(tmp_path, "2026-11-01", [(MISSING, "Tarik Biberović")])
    _write_common(tmp_path, [(MISSING, "Tarik Biberovic"), (MISSING2, "Only Official")])
    with caplog.at_level(logging.WARNING, logger="nba.explain.local_names"):
        got = local_names.load_local_names(tmp_path)
    assert got.names[MISSING] == "Tarik Biberović"
    assert got.source[MISSING] == "rosters"
    assert got.names[MISSING2] == "Only Official"
    assert got.conflicts == [
        (MISSING, "Tarik Biberović", "rosters", "Tarik Biberovic", "commonallplayers")
    ]
    assert "conflicts" in caplog.text


def test_newest_roster_snapshot_wins(tmp_path: Path) -> None:
    _write_roster(tmp_path, "2026-10-20", [(MISSING, "Old Spelling")])
    _write_roster(tmp_path, "2026-11-05", [(MISSING, "New Spelling")])
    assert local_names.load_local_names(tmp_path).names[MISSING] == "New Spelling"


def test_placeholder_only_when_no_source_has_the_id(tmp_path: Path) -> None:
    _write_roster(tmp_path, "2026-11-01", [(MISSING, "New Rookie")])
    got = local_names.load_local_names(tmp_path).names
    players, _ = _names({MISSING, NOBODY}, set(), fallback=got)
    by_id = {p["player_id"]: p["full_name"] for p in players}
    assert by_id[MISSING] == "New Rookie"
    assert by_id[NOBODY] == f"Player {NOBODY}"


def test_missing_sources_are_empty_not_an_error(tmp_path: Path) -> None:
    assert local_names.load_local_names(tmp_path).names == {}
