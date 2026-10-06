"""CLI smoke test for ``python -m nba.props`` (CLAUDE.md standard CLI contract)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nba.props.__main__ import main


def test_cli_runs_on_fixture_and_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "props_report.md"
    rc = main(
        [
            "--use-fixture",
            "--scenario",
            "--n-boot",
            "20",
            "--report-path",
            str(report_path),
        ]
    )
    assert rc == 0
    assert report_path.exists()
    assert "Props backtest report" in report_path.read_text()


def test_cli_rejects_promote_and_scenario_together(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        main(["--use-fixture", "--scenario", "--promote"])


def test_cli_register_logs_a_candidate_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("nba.registry.factory.DEFAULT_STORE_DIR", tmp_path / "registry_store")
    report_path = tmp_path / "report.md"
    rc = main(
        [
            "--use-fixture",
            "--register",
            "--n-boot",
            "20",
            "--report-path",
            str(report_path),
        ]
    )
    assert rc == 0
