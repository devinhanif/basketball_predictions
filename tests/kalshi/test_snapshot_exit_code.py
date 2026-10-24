"""`snapshot` exits 1 only on fetch failures; unmatched names / unparsed props are warnings."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nba.kalshi import __main__ as kmain
from nba.kalshi.snapshot import SnapshotReport

NOW = datetime(2026, 10, 23, 12, 0)


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rep: SnapshotReport) -> int:
    def fake_run_snapshot(*_a: Any, **_k: Any) -> SnapshotReport:
        return rep

    monkeypatch.setattr(kmain, "run_snapshot", fake_run_snapshot)
    return kmain.main(["--db", str(tmp_path / "k.duckdb"), "snapshot"])


def test_clean_run_exits_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert _run(monkeypatch, tmp_path, SnapshotReport(snapshot_ts=NOW)) == 0


def test_unmatched_and_unparsed_are_warnings_not_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rep = SnapshotReport(
        snapshot_ts=NOW, unmatched_names={"Some Rookie": 3}, unparsed_props=["KXNBAPTS-X"]
    )
    assert not rep.ok  # the strict "fully clean" flag is unchanged
    assert _run(monkeypatch, tmp_path, rep) == 0
    err = capsys.readouterr().err
    assert "UNMATCHED player names" in err and "UNPARSED prop-series" in err


def test_fetch_failure_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rep = SnapshotReport(snapshot_ts=NOW, failures={"KXNBAGAME": "ConnectError"})
    assert _run(monkeypatch, tmp_path, rep) == 1
    assert "FAILED KXNBAGAME" in capsys.readouterr().err


def test_fetch_failure_plus_unmatched_still_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rep = SnapshotReport(
        snapshot_ts=NOW, failures={"KXNBAPTS": "timeout"}, unmatched_names={"X Y": 1}
    )
    assert _run(monkeypatch, tmp_path, rep) == 1
