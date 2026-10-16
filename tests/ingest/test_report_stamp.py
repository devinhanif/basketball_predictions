"""Known-at of an official injury report is the time printed in its header, not its file name.

Incident (2026-10-15): hour-only names (``..._06PM.pdf``) print ``06:30 PM``; ingest stamped the
name time, 30 minutes early. Real committed PDFs, no network.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from nba.db.connect import connect
from nba.ingest.availability import pull_official_injury_report
from nba.parse.availability import (
    build_name_index,
    parse_report_header_time,
    read_report_header_time,
    report_known_at,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "injury_report"
FETCHED = datetime(2030, 1, 1, 12, 0)


@pytest.mark.parametrize(
    ("name", "named", "printed"),
    [
        # hour-only names: the header prints :30
        (
            "Injury-Report_2019-10-21_05PM.pdf",
            datetime(2019, 10, 21, 17, 0),
            (2019, 10, 21, 17, 30),
        ),
        (
            "Injury-Report_2019-11-19_01PM.pdf",
            datetime(2019, 11, 19, 13, 0),
            (2019, 11, 19, 13, 30),
        ),
        (
            "Injury-Report_2019-12-16_08PM.pdf",
            datetime(2019, 12, 16, 20, 0),
            (2019, 12, 16, 20, 30),
        ),
        (
            "Injury-Report_2022-10-19_11AM.pdf",
            datetime(2022, 10, 19, 11, 0),
            (2022, 10, 19, 11, 30),
        ),
        # minute-named file: the header still wins (this one prints :50, named :45)
        (
            "Injury-Report_2026-10-07_09_45AM.pdf",
            datetime(2026, 10, 7, 9, 45),
            (2026, 10, 7, 9, 50),
        ),
    ],
)
def test_known_at_is_the_printed_header_time(
    name: str, named: datetime, printed: tuple[int, int, int, int, int]
) -> None:
    got, from_header = report_known_at(FIX / name, FETCHED)
    assert from_header
    assert got == datetime(*printed)
    assert got != named


def test_hour_only_files_are_never_stamped_with_the_file_name_time() -> None:
    hour_only = sorted(p for p in FIX.glob("Injury-Report_*.pdf") if p.name.count("_") == 2)
    assert len(hour_only) >= 4
    for p in hour_only:
        header = read_report_header_time(p)
        assert header is not None
        assert header.minute == 30, p.name


def test_unreadable_header_falls_back_to_fetch_time_never_the_file_name(tmp_path: Path) -> None:
    junk = tmp_path / "Injury-Report_2024-01-17_05PM.pdf"
    junk.write_bytes(b"not a pdf")
    assert report_known_at(junk, FETCHED) == (FETCHED, False)
    assert parse_report_header_time("no header here") is None
    assert parse_report_header_time("Injury Report: 13/45/24 05:30 PM") is None
    assert parse_report_header_time("Injury Report: 01/17/24 12:15 AM") == datetime(
        2024, 1, 17, 0, 15
    )
    assert parse_report_header_time("Injury Report: 01/17/24 12:15 PM") == datetime(
        2024, 1, 17, 12, 15
    )


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get(self, url: str, timeout: float = 0.0) -> _Resp:
        return _Resp(self._content)


def test_pull_stamps_rows_with_header_time_under_an_hour_only_url(tmp_path: Path) -> None:
    """The pull path is handed an hour-only URL and a 09:00 slot; rows carry the printed 09:50."""
    players = [
        "Devin Carter", "Tucker DeVries", "Luka Garza", "Cade Cunningham", "Elijah Harkless",
        "Daniss Jenkins", "Isaiah Joe", "Isaac Jones", "Chaz Lanier", "Kyle Anderson",
        "Reece Beekman", "Stephen Curry", "Draymond Green", "Jonathan Kuminga", "Alex Caruso",
        "Ousmane Dieng", "Luguentz Dort", "Alex Ducas", "Aaron Wiggins",
    ]  # fmt: skip
    idx = build_name_index([(n, i + 1) for i, n in enumerate(players)])
    pdf = (FIX / "Injury-Report_2026-10-07_09_45AM.pdf").read_bytes()
    con = connect(":memory:")
    try:
        df = pull_official_injury_report(
            con,
            datetime(2026, 10, 7, 9, 0),
            name_index=idx,
            data_dir=tmp_path,
            url="https://example.invalid/Injury-Report_2026-10-07_09AM.pdf",
            http_client=_Client(pdf),  # type: ignore[arg-type]
        )
        stamps = con.execute("SELECT DISTINCT as_of, pulled_at FROM player_availability").fetchall()
    finally:
        con.close()
    assert df.height == 19
    assert stamps == [(datetime(2026, 10, 7, 9, 50), datetime(2026, 10, 7, 9, 50))]
