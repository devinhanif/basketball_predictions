# ruff: noqa: E501
"""AVG shadow install-deadline check: a missing, late or invalid November bundle fails loudly; the October bundle does not satisfy November."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nba.daily.avg_shadow import bundle as B
from nba.daily.avg_shadow import install_check as IC


def _bundle(root: Path, month: str, fit_through: str, installed_at: datetime | None) -> None:
    B.write_fixture_bundle(
        root / month, month_block=month, fit_through=fit_through, installed_at=installed_at
    )


def test_october_bundle_does_not_satisfy_november(tmp_path: Path) -> None:
    _bundle(tmp_path, "2026-10", "2026-06-13", datetime(2026, 10, 14, 21, 16))
    # the deadline is 12:00 CT; 13:00 CT is after it -> FAIL
    assert IC.main(["--root", str(tmp_path), "--now-ct", "2026-11-01T13:00:00"]) == IC.FAIL
    assert IC.main(["--root", str(tmp_path), "--now-ct", "2026-11-01T11:00:00"]) == IC.WARN
    assert (
        IC.main(["--root", str(tmp_path), "--now-ct", "2026-10-20T09:00:00"]) == IC.OK
    )  # October: installed late, history


def test_on_time_install_passes_and_a_late_install_is_reported_not_failed(tmp_path: Path) -> None:
    _bundle(tmp_path, "2026-11", "2026-10-31", datetime(2026, 11, 1, 16, 59))
    assert IC.main(["--root", str(tmp_path), "--now-ct", "2026-11-02T08:00:00"]) == IC.OK
    late = tmp_path / "late"
    _bundle(late, "2026-11", "2026-10-31", datetime(2026, 11, 2, 15, 0))
    assert IC.main(["--root", str(late), "--now-ct", "2026-11-02T16:00:00"]) == IC.OK
    v = IC.check_month(late, "2026-11", datetime(2026, 11, 2, 22, 0))
    assert "LATE by" in v.line


def test_bundle_with_fit_through_in_the_month_is_invalid(tmp_path: Path) -> None:
    _bundle(tmp_path, "2026-11", "2026-11-01", datetime(2026, 11, 1, 12, 0))
    assert IC.main(["--root", str(tmp_path), "--now-ct", "2026-11-02T08:00:00"]) == IC.FAIL


def test_uninstalled_bundle_and_empty_root(tmp_path: Path) -> None:
    _bundle(tmp_path, "2026-11", "2026-10-31", None)  # downloaded, never stamped installed
    assert IC.main(["--root", str(tmp_path), "--now-ct", "2026-11-02T08:00:00"]) == IC.FAIL
    assert (
        IC.main(["--root", str(tmp_path / "nothing"), "--now-ct", "2026-11-02T08:00:00"]) == IC.FAIL
    )


def test_deadline_is_noon_central_including_daylight_saving() -> None:
    assert IC.deadline_utc("2026-11") == datetime(
        2026, 11, 1, 18, 0
    )  # DST ends 2026-11-01 02:00: noon is CST = UTC-6
    assert IC.deadline_utc("2026-10") == datetime(2026, 10, 1, 17, 0)
