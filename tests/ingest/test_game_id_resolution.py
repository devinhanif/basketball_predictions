"""Fixture-only tests for the two official-injury-report follow-ups:

1. ``resolve_game_id`` -- mapping a report's team abbreviations to a
   ``games.game_id``.
2. ``build_name_index_from_static_players`` / ``build_merged_name_index`` --
   static-player-backed name coverage (so a valid report player who isn't
   in cached play-by-play still resolves).

No network access: the ``games`` table here is the tiny committed fixture
(tests/fixtures/games.json), and the static-player index reads nba_api's
bundled (offline) static player list -- not a network call.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from nba.db.connect import connect
from nba.ingest.availability import build_merged_name_index
from nba.ingest.cache import insert_rows
from nba.parse.availability import (
    build_name_index_from_static_players,
    normalize_name,
    parse_matchup,
    resolve_game_id,
)
from tests.fixtures.loader import load_games, load_pbp

# Fixture games.json: game 0022300001 is home=BOS(1610612738) away=PHI(1610612755)
# on 2023-10-24; game 0022300003 is home=MIA away=MIL on 2023-10-25.


@pytest.fixture
def fixture_con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    games = load_games()
    insert_rows(con, "games", games.columns, games)
    yield con
    con.close()


# --------------------------------------------------------------------------
# Follow-up 1: resolve_game_id
# --------------------------------------------------------------------------


def test_resolve_game_id_matches_away_at_home(fixture_con: duckdb.DuckDBPyConnection) -> None:
    game_id = resolve_game_id(fixture_con, date(2023, 10, 24), "PHI", "BOS")
    assert game_id == "0022300001"


def test_resolve_game_id_unknown_date_returns_none(fixture_con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_game_id(fixture_con, date(2099, 1, 1), "PHI", "BOS") is None


def test_resolve_game_id_unknown_abbrev_returns_none(
    fixture_con: duckdb.DuckDBPyConnection,
) -> None:
    assert resolve_game_id(fixture_con, date(2023, 10, 24), "ZZZ", "BOS") is None
    assert resolve_game_id(fixture_con, date(2023, 10, 24), "PHI", "ZZZ") is None


def test_resolve_game_id_swapped_orientation_fallback(
    fixture_con: duckdb.DuckDBPyConnection,
) -> None:
    # games row is home=BOS, away=PHI; ask with the orientation reversed
    # (away=BOS, home=PHI) -- the swapped-fallback query must still find it.
    game_id = resolve_game_id(fixture_con, date(2023, 10, 24), "BOS", "PHI")
    assert game_id == "0022300001"


def test_resolve_game_id_other_fixture_game(fixture_con: duckdb.DuckDBPyConnection) -> None:
    assert resolve_game_id(fixture_con, date(2023, 10, 25), "MIL", "MIA") == "0022300003"


def test_parse_matchup_splits_away_home() -> None:
    assert parse_matchup("BOS@DET") == ("BOS", "DET")


def test_parse_matchup_malformed_returns_none() -> None:
    assert parse_matchup("BOSDET") is None
    assert parse_matchup(None) is None


# --------------------------------------------------------------------------
# Follow-up 2: static-player-backed name coverage
# --------------------------------------------------------------------------


def test_build_name_index_from_static_players_nontrivial() -> None:
    pytest.importorskip("nba_api")
    index = build_name_index_from_static_players(active_only=True)
    assert len(index) > 300  # ~530 active players, well above a trivial/empty index


def test_static_player_index_resolves_player_not_in_pbp_fixture() -> None:
    pytest.importorskip("nba_api")
    # A real, currently-active player who is certainly not one of the
    # synthetic "P###" names in tests/fixtures/pbp/*.json.
    index = build_name_index_from_static_players(active_only=True)
    key = normalize_name("Nikola Jokic")
    assert key in index
    assert isinstance(index[key], int)


def test_merged_index_includes_both_static_and_pbp_names(tmp_path: Path) -> None:
    pytest.importorskip("nba_api")
    pbp_dir = tmp_path / "pbp"
    pbp_dir.mkdir()
    for game_id, df in load_pbp().items():
        df.write_parquet(pbp_dir / f"{game_id}.parquet")

    merged = build_merged_name_index(data_dir=tmp_path)
    # pbp-only synthetic name still present.
    assert merged["p101"] == 101
    # static-only real player, absent from the pbp fixture, now resolves.
    assert normalize_name("Nikola Jokic") in merged


def test_merged_index_degrades_to_pbp_only_without_static(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the static-player import path fails, the merged index still
    returns the pbp-derived names rather than raising."""
    pbp_dir = tmp_path / "pbp"
    pbp_dir.mkdir()
    for game_id, df in load_pbp().items():
        df.write_parquet(pbp_dir / f"{game_id}.parquet")

    import nba.ingest.availability as avail_mod

    def _boom(active_only: bool = True) -> dict[str, int]:
        raise ImportError("nba_api not available in this environment")

    monkeypatch.setattr(avail_mod, "build_name_index_from_static_players", _boom)
    merged = avail_mod.build_merged_name_index(data_dir=tmp_path)
    assert merged["p101"] == 101
