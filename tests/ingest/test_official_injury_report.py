"""Fixture-only tests for the official NBA injury-report PDF parser/puller.

No network access -- parses the committed fixture PDF
(tests/fixtures/injury_report/Injury-Report_2026-10-07_09_45AM.pdf)
directly. The fixture has 2 pages, 19 players, two games (BOS@DET,
GSW@OKC), and was captured specifically to exercise the wrapped-reason and
cross-page reason-assignment edge cases documented in
nba.parse.availability.parse_official_injury_report.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nba.db.connect import connect
from nba.ingest.availability import official_report_url
from nba.parse.availability import (
    UnmatchedPlayerNameError,
    build_name_index,
    normalize_name,
    parse_official_injury_report,
)

FIXTURE_PDF = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "injury_report"
    / "Injury-Report_2026-10-07_09_45AM.pdf"
)

REPORT_DT = datetime(2026, 10, 7, 9, 45)

#: The 19 players on the fixture PDF, in "First Last" form (the report's own
#: order is "Last,First"). Used to build a hand-constructed name index so
#: these tests never depend on real play-by-play data.
_FIXTURE_PLAYERS = [
    "Devin Carter",
    "Tucker DeVries",
    "Luka Garza",
    "Cade Cunningham",
    "Elijah Harkless",
    "Daniss Jenkins",
    "Isaiah Joe",
    "Isaac Jones",
    "Chaz Lanier",
    "Kyle Anderson",
    "Reece Beekman",
    "Stephen Curry",
    "Draymond Green",
    "Jonathan Kuminga",
    "Alex Caruso",
    "Ousmane Dieng",
    "Luguentz Dort",
    "Alex Ducas",
    "Aaron Wiggins",
]


def _name_index(player_ids: dict[str, int] | None = None) -> dict[str, int]:
    pairs = [(name, i + 1) for i, name in enumerate(_FIXTURE_PLAYERS)]
    return build_name_index(pairs)


@pytest.fixture
def name_index() -> dict[str, int]:
    return _name_index()


def test_parses_all_19_players(name_index: dict[str, int]) -> None:
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    assert len(df) == 19


def test_wrapped_reason_assigned_to_correct_player(name_index: dict[str, int]) -> None:
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    garza_id = name_index[normalize_name("Luka Garza")]
    row = df.filter(df["player_id"] == garza_id)
    assert len(row) == 1
    reason = row["reason"][0]
    assert "RightShoulder" in reason
    assert "Contusion" in reason


def test_reason_assignment_scoped_per_page(name_index: dict[str, int]) -> None:
    """Ducas (page 2) must get his own "Rest-xyz" reason, not Carter's
    (page 1, same visual-row y) -- the one bug found during validation:
    reason-fragment-to-player assignment must never pool y across pages."""
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    ducas_id = name_index[normalize_name("Alex Ducas")]
    carter_id = name_index[normalize_name("Devin Carter")]

    ducas_row = df.filter(df["player_id"] == ducas_id)
    carter_row = df.filter(df["player_id"] == carter_id)
    assert len(ducas_row) == 1
    assert len(carter_row) == 1
    assert ducas_row["reason"][0] == "Rest-xyz"
    assert carter_row["reason"][0] == "Injury/Illness-LeftHamstringStrain"
    assert "Rest-xyz" not in carter_row["reason"][0]


def test_last_comma_first_resolves_via_name_index(name_index: dict[str, int]) -> None:
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    # every row resolved to a player_id (no nulls) proves "Last,First" ->
    # "First Last" reordering worked against the hand-built index.
    assert df["player_id"].null_count() == 0
    assert set(df["player_id"].to_list()) == set(range(1, 20))


def test_unmatched_name_raises_with_all_missing_names_listed() -> None:
    # Drop one player from the index -- their name must appear in the error.
    incomplete = _name_index()
    missing_key = normalize_name("Luka Garza")
    del incomplete[missing_key]
    with pytest.raises(UnmatchedPlayerNameError, match="Luka Garza"):
        parse_official_injury_report(FIXTURE_PDF, incomplete, report_dt=REPORT_DT)


def test_status_values_are_valid_set(name_index: dict[str, int]) -> None:
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    assert set(df["status"].to_list()) == {
        "out",
        "doubtful",
        "questionable",
        "probable",
        "available",
    }


def test_as_of_equals_report_dt(name_index: dict[str, int]) -> None:
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    assert all(v == REPORT_DT for v in df["as_of"].to_list())


def test_source_and_game_id(name_index: dict[str, int]) -> None:
    # No `con` supplied -- game_id resolution is opt-in, stays None.
    df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT)
    assert set(df["source"].to_list()) == {"nba_official_report"}
    assert all(v is None for v in df["game_id"].to_list())


def test_game_id_resolves_when_con_supplied_and_game_is_loaded(
    name_index: dict[str, int],
) -> None:
    """Both fixture games (BOS@DET, GSW@OKC) loaded into `games` -- every
    row's game_id must resolve to the matching game, none left None."""
    con = connect(":memory:")
    try:
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["0022600001", "2026-10-07", 2026, 1610612765, 1610612738, 0, 0],  # DET home, BOS away
        )
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["0022600002", "2026-10-07", 2026, 1610612760, 1610612744, 0, 0],  # OKC home, GSW away
        )
        df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT, con=con)
    finally:
        con.close()
    assert df["game_id"].null_count() == 0
    assert set(df["game_id"].to_list()) == {"0022600001", "0022600002"}


def test_game_id_stays_none_when_game_not_in_games_table(
    name_index: dict[str, int],
) -> None:
    """con is supplied but `games` is empty -- best-effort, never raises,
    game_id stays None for every row."""
    con = connect(":memory:")
    try:
        df = parse_official_injury_report(FIXTURE_PDF, name_index, report_dt=REPORT_DT, con=con)
    finally:
        con.close()
    assert all(v is None for v in df["game_id"].to_list())


def test_official_report_url_format() -> None:
    url = official_report_url(datetime(2026, 10, 7, 9, 45))
    assert url.endswith("Injury-Report_2026-10-07_09_45AM.pdf")


def test_official_report_url_pm_time() -> None:
    url = official_report_url(datetime(2026, 10, 7, 15, 0))
    assert url.endswith("Injury-Report_2026-10-07_03_00PM.pdf")
