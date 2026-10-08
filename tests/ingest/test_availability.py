"""Fixture-only tests for the player availability feed: ingest + parser.

No network access, no nba_api import -- uses the tiny committed fixture
snapshots under tests/fixtures/availability_raw/ and
tests/fixtures/availability_manual/.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from nba.db.connect import connect
from nba.ingest.availability import (
    build_name_index_from_cached_pbp,
    load_manual_availability_file,
)
from nba.ingest.cache import insert_rows
from nba.parse.availability import (
    AVAILABILITY_SCHEMA,
    UnmatchedPlayerNameError,
    build_name_index_from_pbp_frames,
    normalize_inactive_frame,
    normalize_name,
    parse_manual_announcements,
    resolve_player_id,
)
from tests.fixtures.loader import load_games, load_pbp

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
AVAIL_RAW_DIR = FIXTURES_DIR / "availability_raw"
AVAIL_MANUAL_DIR = FIXTURES_DIR / "availability_manual"


@pytest.fixture
def fixture_con() -> duckdb.DuckDBPyConnection:
    con = connect(":memory:")
    games = load_games()
    insert_rows(con, "games", games.columns, games)
    yield con
    con.close()


@pytest.fixture
def name_index() -> dict[str, int]:
    return build_name_index_from_pbp_frames(load_pbp())


def _load_raw_snapshot(game_id: str) -> dict:
    return json.loads((AVAIL_RAW_DIR / f"{game_id}.json").read_text())


# --------------------------------------------------------------------------
# nba_inactive_list parsing (PLAYER_ID already present, no name matching)
# --------------------------------------------------------------------------


def test_normalize_inactive_frame_nonempty() -> None:
    snap = _load_raw_snapshot("0022300001")
    raw = pl.DataFrame(snap["inactive_players"])
    out = normalize_inactive_frame(
        raw,
        game_id="0022300001",
        tipoff_as_of=snap["game_summary"][0]["GAME_DATE_EST"][:10],
        pulled_at=datetime(2026, 10, 8),
    )
    assert out.columns == AVAILABILITY_SCHEMA
    assert len(out) == 1
    row = out.row(0, named=True)
    assert row["player_id"] == 106
    assert row["status"] == "inactive"
    assert row["source"] == "nba_inactive_list"
    assert row["game_id"] == "0022300001"
    assert row["as_of"] == datetime(2023, 10, 24)


def test_normalize_inactive_frame_empty_is_legitimate() -> None:
    snap = _load_raw_snapshot("0022300002")
    raw = pl.DataFrame(snap["inactive_players"])
    out = normalize_inactive_frame(
        raw,
        game_id="0022300002",
        tipoff_as_of=snap["game_summary"][0]["GAME_DATE_EST"][:10],
        pulled_at=datetime(2026, 10, 8),
    )
    assert out.columns == AVAILABILITY_SCHEMA
    assert len(out) == 0


def test_all_three_fixture_games_parse_and_load(fixture_con: duckdb.DuckDBPyConnection) -> None:
    total_rows = 0
    for game_id in ("0022300001", "0022300002", "0022300003"):
        snap = _load_raw_snapshot(game_id)
        raw = pl.DataFrame(snap["inactive_players"])
        out = normalize_inactive_frame(
            raw,
            game_id=game_id,
            tipoff_as_of=snap["game_summary"][0]["GAME_DATE_EST"][:10],
            pulled_at=datetime(2026, 10, 8),
        )
        if not out.is_empty():
            insert_rows(fixture_con, "player_availability", AVAILABILITY_SCHEMA, out)
        total_rows += len(out)

    assert total_rows == 2  # one inactive in game 1, zero in game 2, one in game 3
    db_count = fixture_con.execute("SELECT COUNT(*) FROM player_availability").fetchone()[0]
    assert db_count == total_rows


# --------------------------------------------------------------------------
# Name normalization / fuzzy matching (manual_announced path)
# --------------------------------------------------------------------------


def test_normalize_name_strips_punctuation_suffix_and_case() -> None:
    assert normalize_name("Jokic, Nikola Jr.") == "jokic nikola"
    assert normalize_name("nikola  jokic") == "nikola jokic"


def test_build_name_index_from_fixture_pbp(name_index: dict[str, int]) -> None:
    assert name_index["p101"] == 101
    assert name_index["p205"] == 205
    assert len(name_index) == 18  # 6 players x 3 fixture games


def test_resolve_player_id_exact_match(name_index: dict[str, int]) -> None:
    assert resolve_player_id("P101", name_index) == 101


def test_resolve_player_id_fuzzy_match_within_cutoff(name_index: dict[str, int]) -> None:
    # "P 301" normalizes to "p 301", close (but not exact) to indexed "p301".
    assert resolve_player_id("P 301", name_index) == 301


def test_resolve_player_id_fails_loudly_on_unmatched_name(name_index: dict[str, int]) -> None:
    with pytest.raises(UnmatchedPlayerNameError):
        resolve_player_id("Totally Unknown Player", name_index)


def test_build_name_index_raises_on_ambiguous_names() -> None:
    from nba.parse.availability import build_name_index

    with pytest.raises(ValueError, match="ambiguous"):
        build_name_index([("Same Name", 1), ("Same Name", 2)])


# --------------------------------------------------------------------------
# manual_announced parsing + loading
# --------------------------------------------------------------------------


def test_parse_manual_announcements(name_index: dict[str, int]) -> None:
    records = json.loads((AVAIL_MANUAL_DIR / "sample.json").read_text())
    out = parse_manual_announcements(records, name_index, pulled_at=datetime(2026, 10, 8))
    assert len(out) == 2
    assert out.columns == AVAILABILITY_SCHEMA
    assert set(out["source"].to_list()) == {"manual_announced"}
    assert set(out["player_id"].to_list()) == {205, 301}


def test_parse_manual_announcements_rejects_invalid_status(name_index: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="invalid status"):
        parse_manual_announcements(
            [{"player_name": "P101", "status": "bogus", "as_of": "2026-01-01"}],
            name_index,
            pulled_at=datetime(2026, 10, 8),
        )


def test_load_manual_availability_file_is_idempotent(
    fixture_con: duckdb.DuckDBPyConnection, name_index: dict[str, int], tmp_path: Path
) -> None:
    out1 = load_manual_availability_file(
        fixture_con, AVAIL_MANUAL_DIR / "sample.json", name_index, data_dir=tmp_path
    )
    assert len(out1) == 2
    count_after_first = fixture_con.execute(
        "SELECT COUNT(*) FROM player_availability WHERE source = 'manual_announced'"
    ).fetchone()[0]
    assert count_after_first == 2

    # Re-loading the same file must not duplicate rows (resumable/idempotent).
    out2 = load_manual_availability_file(
        fixture_con, AVAIL_MANUAL_DIR / "sample.json", name_index, data_dir=tmp_path
    )
    assert out2.is_empty()
    count_after_second = fixture_con.execute(
        "SELECT COUNT(*) FROM player_availability WHERE source = 'manual_announced'"
    ).fetchone()[0]
    assert count_after_second == 2

    snapshot = tmp_path / "availability_manual" / "sample.json"
    assert snapshot.exists(), "raw snapshot must be stashed for replay"


def test_build_name_index_from_cached_pbp(tmp_path: Path) -> None:
    """End-to-end: writing fixture pbp as cached parquet, then building the index."""
    pbp_dir = tmp_path / "pbp"
    pbp_dir.mkdir()
    for game_id, df in load_pbp().items():
        df.write_parquet(pbp_dir / f"{game_id}.parquet")
    index = build_name_index_from_cached_pbp(data_dir=tmp_path)
    assert index["p101"] == 101
    assert len(index) == 18


# --------------------------------------------------------------------------
# Schema contract + as-of leakage test
# --------------------------------------------------------------------------


def test_player_availability_schema_contract(fixture_con: duckdb.DuckDBPyConnection) -> None:
    cols = {
        row[0]
        for row in fixture_con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'player_availability'"
        ).fetchall()
    }
    assert cols == set(AVAILABILITY_SCHEMA)


def test_no_row_has_as_of_after_the_game_it_informs(
    fixture_con: duckdb.DuckDBPyConnection, name_index: dict[str, int]
) -> None:
    """Leakage gate: for every row with a resolved game_id, as_of must be
    on or before that game's own game_date -- the row can never claim to
    know a status *after* the game it's attached to already happened.

    (This does not prove the stronger forward-looking claim -- see
    docs/INJURY_FEED_2026-10-08.md: nba_inactive_list rows are only ever
    as-of that same game's tip-off, never earlier -- but it is the leakage
    direction that actually matters: a row must never be dated *later*
    than the game_id it is attached to.)
    """
    for game_id in ("0022300001", "0022300002", "0022300003"):
        snap = _load_raw_snapshot(game_id)
        raw = pl.DataFrame(snap["inactive_players"])
        out = normalize_inactive_frame(
            raw,
            game_id=game_id,
            tipoff_as_of=snap["game_summary"][0]["GAME_DATE_EST"][:10],
            pulled_at=datetime(2026, 10, 8),
        )
        if not out.is_empty():
            insert_rows(fixture_con, "player_availability", AVAILABILITY_SCHEMA, out)

    manual_records = json.loads((AVAIL_MANUAL_DIR / "sample.json").read_text())
    manual_df = parse_manual_announcements(
        manual_records, name_index, pulled_at=datetime(2026, 10, 8)
    )
    insert_rows(fixture_con, "player_availability", AVAILABILITY_SCHEMA, manual_df)

    rows = fixture_con.execute(
        """
        SELECT a.game_id, a.as_of, g.game_date
        FROM player_availability a
        JOIN games g ON g.game_id = a.game_id
        WHERE a.game_id IS NOT NULL
        """
    ).fetchall()
    assert len(rows) > 0
    for game_id, as_of, game_date in rows:
        assert as_of.date() <= game_date, (
            f"leakage: {game_id} availability row as_of={as_of} is after game_date={game_date}"
        )
