"""Counts logic of nba.data.injury_sections on a hand-built cache (no PDFs, no network)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from nba.data.injury_sections import report, report_dt


def test_report_dt_both_filename_forms() -> None:
    assert report_dt("Injury-Report_2022-10-18_05PM.pdf") == datetime(2022, 10, 18, 17, 0)
    assert report_dt("Injury-Report_2026-07-19_05_45PM.pdf") == datetime(2026, 7, 19, 17, 45)
    with pytest.raises(ValueError):
        report_dt("other.pdf")


def _row(g: str, t: int, status: str, n: int, asof: str) -> dict[str, object]:
    return {"as_of": asof, "game_id": g, "team_id": t, "section_status": status, "n_rows": n}


def test_classes(tmp_path: Path) -> None:
    rows1 = [
        _row("g1", 1, "filed", 2, "2022-10-18 11:00:00"),
        _row("g1", 2, "not_yet_submitted", 0, "2022-10-18 11:00:00"),
        _row("g2", 3, "not_yet_submitted", 0, "2022-10-18 11:00:00"),
    ]
    rows2 = [_row("g1", 2, "filed", 1, "2022-10-18 17:00:00")]
    (tmp_path / "a.json").write_text(json.dumps({"file": "a", "rows": rows1}))
    (tmp_path / "b.json").write_text(json.dumps({"file": "b", "rows": rows2}))
    games = [("g1", 2022, 1, 2), ("g2", 2022, 3, 4), ("g3", 2022, 5, 6)]
    c = report(tmp_path, games)["by_season"]["2022"]
    assert c["team_games"] == 6
    assert c["filed_ever"] == 2  # g1 both teams (team 2 filed later)
    assert c["nys_only_ever"] == 1  # g2 team 3
    assert c["absent_game_visible"] == 1  # g2 team 4: opponent has a section
    assert c["absent_game_invisible"] == 2  # g3
    assert c["last_snapshot_filed"] == 2
