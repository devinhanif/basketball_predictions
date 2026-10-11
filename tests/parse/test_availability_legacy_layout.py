"""2019 official injury-report layouts (Category / trailing Previous Status columns).

Fixtures (copied from data/history/availability_official/, no network):

* ``Injury-Report_2019-10-21_05PM.pdf`` -- layout A (2019-10-21 .. 2019-11-14): header
  ``... Player Name Category Reason Current Status Previous Status``; one page, two games
  (NOP@TOR 08:00 and LAL@LAC 10:30 on the same date), "G League - Two-Way" rows, a
  two-team/multi-player block per game and "NOT YET SUBMITTED" for both LAL@LAC teams.
* ``Injury-Report_2019-11-19_01PM.pdf`` -- layout B (2019-11-15 .. 2019-11-19): no Category column
  (category is already inside Reason), ``Reason Current Status Previous Status``; 3 pages.

``later_layout_records.json`` is the raw record dump of the two later-layout fixtures made with
the parser as it stood BEFORE the legacy-layout detector was added; the regression test asserts the
current parser reproduces it exactly (the production daily path reads the later layouts).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pdfplumber
import pytest

from nba.parse.availability import (
    _parse_report_pages,
    build_name_index,
    parse_official_injury_report,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "injury_report"
LAYOUT_A = FIX / "Injury-Report_2019-10-21_05PM.pdf"
LAYOUT_B = FIX / "Injury-Report_2019-11-19_01PM.pdf"
REPORT_DT_A = datetime(2019, 10, 21, 17)

_A_PLAYERS = [
    "Zylan Cheatham",
    "Josh Gray",
    "Darius Miller",
    "Zion Williamson",
    "Rondae Hollis-Jefferson",
    "Patrick McCaw",
]


def _records(path: Path) -> list[tuple[str | None, ...]]:
    with pdfplumber.open(path) as pdf:
        return [
            (r.gamedate, r.gametime, r.matchup, r.team, r.player, r.status, r.reason)
            for r in _parse_report_pages(pdf)
        ]


@pytest.fixture
def idx_a() -> dict[str, int]:
    return build_name_index([(n, i + 1) for i, n in enumerate(_A_PLAYERS)])


def test_layout_a_parses_six_player_rows_with_status(idx_a: dict[str, int]) -> None:
    df = parse_official_injury_report(LAYOUT_A, idx_a, report_dt=REPORT_DT_A)
    assert df.height == 6
    assert set(df["status"]) == {"out"}


def test_layout_a_two_way_rows_carry_category_as_reason(idx_a: dict[str, int]) -> None:
    df = parse_official_injury_report(LAYOUT_A, idx_a, report_dt=REPORT_DT_A)
    by_id = dict(zip(df["player_id"], df["reason"], strict=True))
    assert by_id[idx_a["zylan cheatham"]] == "G League - Two-Way"
    assert by_id[idx_a["josh gray"]] == "G League - Two-Way"


def test_layout_a_category_and_detail_joined_like_later_layouts(idx_a: dict[str, int]) -> None:
    df = parse_official_injury_report(LAYOUT_A, idx_a, report_dt=REPORT_DT_A)
    by_id = dict(zip(df["player_id"], df["reason"], strict=True))
    assert by_id[idx_a["zion williamson"]] == "Injury/Illness - Right Knee"
    assert by_id[idx_a["darius miller"]] == "Injury/Illness - Right Achilles Surgery"
    # no Previous Status ("-") text leaks into reason or status
    assert all(str(r).strip() != "-" and not str(r).endswith(" -") for r in by_id.values())


def test_layout_a_multiline_team_blocks_forward_fill_team() -> None:
    recs = [r for r in _records(LAYOUT_A) if r[5]]
    teams = [(r[3], r[4]) for r in recs]
    assert teams == [
        ("New Orleans Pelicans", "Cheatham, Zylan"),
        ("New Orleans Pelicans", "Gray, Josh"),
        ("New Orleans Pelicans", "Miller, Darius"),
        ("New Orleans Pelicans", "Williamson, Zion"),
        ("Toronto Raptors", "Hollis-Jefferson, Rondae"),
        ("Toronto Raptors", "McCaw, Patrick"),
    ]
    assert {r[5] for r in recs} == {"Out"}


def test_layout_a_not_yet_submitted_teams_make_no_records() -> None:
    recs = _records(LAYOUT_A)
    assert len(recs) == 6
    assert not any("submitted" in " ".join(str(x) for x in r).lower() for r in recs)
    assert not any(r[2] == "LAL@LAC" for r in recs)


def test_layout_a_not_yet_submitted_never_in_output(idx_a: dict[str, int]) -> None:
    df = parse_official_injury_report(LAYOUT_A, idx_a, report_dt=REPORT_DT_A)
    assert not any("submitted" in str(r).lower() for r in df["reason"])
    assert df.height == 6  # none of the 2 NYS teams produced a row


def test_layout_a_second_game_does_not_leak_into_first_game_rows() -> None:
    recs = _records(LAYOUT_A)
    assert {r[1] for r in recs} == {"08:00 (ET)"}
    assert {r[2] for r in recs} == {"NOP@TOR"}
    assert {r[0] for r in recs} == {"10/22/2019"}  # date prints once, forward-filled


def test_layout_b_1030_et_second_game_same_date() -> None:
    davis = next(r for r in _records(LAYOUT_B) if r[4] == "Davis, Anthony")
    assert (davis[0], davis[1], davis[2]) == ("11/19/2019", "10:30 (ET)", "OKC@LAL")
    assert davis[5] == "Probable"


def test_layout_b_status_and_reason_columns_in_the_right_order() -> None:
    recs = [r for r in _records(LAYOUT_B) if r[5]]
    assert len(recs) == 42
    assert {r[5] for r in recs} == {"Out", "Questionable", "Probable", "Doubtful"}
    assert all((r[6] or "").startswith(("Injury/Illness", "G League", "Not With")) for r in recs)
    lee = next(r for r in recs if r[4] == "Lee, Damion")
    assert lee[6] == "Injury/Illness - Right Hand; Non- displaced Fracture"
    lowry = next(r for r in recs if r[4] == "Lowry, Kyle")
    assert lowry[5] == "Out"
    assert lowry[6] == "Injury/Illness - Left Thumb; Fracture - distal phalanx"


def test_later_layouts_unchanged_by_legacy_detector() -> None:
    golden = json.loads((FIX / "later_layout_records.json").read_text())
    assert set(golden) == {
        "Injury-Report_2022-10-19_11AM.pdf",
        "Injury-Report_2026-10-07_09_45AM.pdf",
    }
    for name, expected in golden.items():
        with pdfplumber.open(FIX / name) as pdf:
            got = [
                [r.gamedate, r.gametime, r.matchup, r.team, r.player, r.status, r.reason, r.y]
                for r in _parse_report_pages(pdf)
            ]
        assert got == expected, name


# --- layout C (2019-11-20 .. 2019-12-17): ``Current Status / Reason / Previous Status`` ----------
# Fixtures: ``2019-11-21_01PM`` (single Previous Status column, "-" previous values) and
# ``2019-12-16_08PM`` (adds a ``Previous Reason`` column). The trailing Previous columns must not
# leak into ``reason`` (they once did: "Left knee soreness Out" carried the previous status).

LAYOUT_C = FIX / "Injury-Report_2019-11-21_01PM.pdf"
LAYOUT_C_PREV_REASON = FIX / "Injury-Report_2019-12-16_08PM.pdf"
_STATUS_WORDS = ("Out", "Doubtful", "Questionable", "Probable", "Available")


def _ends_with_status_word(reason: str | None) -> bool:
    return bool(reason) and str(reason).split()[-1] in _STATUS_WORDS


def test_layout_c_reason_excludes_previous_status_column() -> None:
    recs = _records(LAYOUT_C)
    assert len(recs) == 28
    assert not any(_ends_with_status_word(r[6]) for r in recs)
    assert not any((r[6] or "").rstrip().endswith(" -") for r in recs)


def test_layout_c_multiword_reason_and_dash_previous_status() -> None:
    by = {r[4]: r for r in _records(LAYOUT_C)}
    # multi-word, wrapped reason (detail wraps onto a second line), previous status "-"
    assert by["Favors, Derrick"][6] == "Injury/Illness - Left Lower Back; Spasms"
    assert by["Favors, Derrick"][5] == "Out"
    assert by["Ball, Lonzo"][6] == "Injury/Illness - Right Adductor; Strain"
    assert by["Cheatham, Zylan"][6] == "G League - Two-Way"


def test_layout_c_previous_reason_variant_1216() -> None:
    recs = _records(LAYOUT_C_PREV_REASON)
    assert len(recs) == 127
    assert not any(_ends_with_status_word(r[6]) for r in recs)
    by = {r[4]: r for r in recs}
    assert by["Griffin, Blake"][6] == "Injury/Illness - Left Knee; Soreness"
    assert by["Bryant, Thomas"][6] == "Injury/Illness - Right Foot; Stress reaction right foot"
    assert by["King, Louis"][6] == "G League - Two-Way"
    assert {r[5] for r in recs if r[5]} <= {
        "Out",
        "Doubtful",
        "Questionable",
        "Probable",
        "Available",
    }
