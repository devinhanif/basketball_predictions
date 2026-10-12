"""Not Yet Submitted team blocks are kept as explicit sections (not dropped, not nobody out)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nba.parse.availability import (
    _parse_report_pages,
    _parse_report_sections,
    parse_official_injury_report_team_sections,
)


class _Page:
    def __init__(self, words: list[dict[str, object]]) -> None:
        self._words = words

    def extract_words(self) -> list[dict[str, object]]:
        return self._words


class _Pdf:
    def __init__(self, pages: list[_Page]) -> None:
        self.pages = pages


def _w(text: str, x0: float, top: float) -> dict[str, object]:
    return {"text": text, "x0": x0, "top": top}


def _fake_pdf() -> _Pdf:
    # default column boundaries: gamedate 0, gametime 119, matchup 200, team 264, player 425,
    # status 585, reason 666
    words = [
        _w("11/29/2022", 10, 100),
        _w("NYK@DET", 205, 100),
        _w("Detroit", 270, 100),
        _w("Pistons", 310, 100),
        _w("Cunningham,Cade", 430, 100),
        _w("Out", 590, 100),
        _w("Knee", 670, 100),
        _w("New", 270, 140),
        _w("York", 300, 140),
        _w("Knicks", 330, 140),
        _w("NOT", 604, 140),
        _w("YET", 620, 140),
        _w("SUBMITTED", 633, 140),
    ]
    return _Pdf([_Page(words)])


def test_placeholder_team_is_a_section_not_a_player_row() -> None:
    records, sections = _parse_report_sections(_fake_pdf())
    assert [r.player for r in records] == ["Cunningham,Cade"]
    assert [(s.team, s.matchup) for s in sections] == [("New York Knicks", "NYK@DET")]
    assert [r.player for r in _parse_report_pages(_fake_pdf())] == ["Cunningham,Cade"]


def test_team_sections_frame_states(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    pdf = _fake_pdf()
    pdf.close = lambda: None  # type: ignore[attr-defined]
    fake = types.ModuleType("pdfplumber")
    fake.open = lambda _p: pdf  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pdfplumber", fake)
    df = parse_official_injury_report_team_sections("x.pdf", report_dt=datetime(2022, 11, 29, 17))
    got = {r["team_id"]: (r["section_status"], r["n_rows"]) for r in df.to_dicts()}
    assert len(got) == 2
    assert sorted(got.values()) == [("filed", 1), ("not_yet_submitted", 0)]


def test_fixture_report_has_only_filed_sections() -> None:
    pdf = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "injury_report"
        / "Injury-Report_2026-10-07_09_45AM.pdf"
    )
    df = parse_official_injury_report_team_sections(pdf, report_dt=datetime(2026, 10, 7, 9, 45))
    assert set(df["section_status"].to_list()) == {"filed"}
    assert int(df["n_rows"].sum()) == 19
