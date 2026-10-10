"""Tests for the smoke-backtest CI guard (nba.registry.smoke_guard)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nba.registry.smoke_guard import check_smoke_report, main


def test_missing_report_fails(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.md"
    errors = check_smoke_report(missing)
    assert len(errors) == 1
    assert "not found" in errors[0]


def test_empty_report_fails(tmp_path: Path) -> None:
    empty = tmp_path / "report.md"
    empty.write_text("   \n")
    errors = check_smoke_report(empty)
    assert len(errors) == 1
    assert "empty" in errors[0]


def test_report_with_nan_fails(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("| model | log loss |\n|---|---|\n| rung0 | NaN [NaN, NaN], n=0 |\n")
    errors = check_smoke_report(report)
    assert len(errors) == 1
    assert "NaN" in errors[0]


def test_healthy_report_passes(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("| model | log loss |\n|---|---|\n| rung0 | 0.6931 [0.60, 0.80], n=50 |\n")
    assert check_smoke_report(report) == []


def test_main_fails_on_missing_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--report", str(tmp_path / "missing.md")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "FAILED" in captured.out


def test_main_passes_on_real_smoke_report(capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end: run the real fixture ladder, then guard its own report.md."""
    import yaml

    from research.eval.__main__ import main as eval_main

    config_path = "configs/rung_ladder_fixture.yaml"
    with open(config_path) as f:
        report_path = yaml.safe_load(f)["report_path"]

    assert eval_main(["--config", config_path]) == 0
    exit_code = main(["--report", report_path])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "passed" in captured.out
