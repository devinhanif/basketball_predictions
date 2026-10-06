"""CLI-contract tests for ``python -m nba.eval`` (CLAUDE.md "Registry retrofit").

``--scenario`` and ``--promote`` must never be combinable; a normal
``--register`` run against the fixture config must produce a report file
and (since ``REGISTRY_BACKEND`` defaults to ``local``) log each rung as a
candidate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nba.eval.__main__ import main

FIXTURE_CONFIG = str(Path(__file__).resolve().parents[2] / "configs" / "rung_ladder_fixture.yaml")


def test_promote_and_scenario_together_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        main(["--config", FIXTURE_CONFIG, "--promote", "--scenario"])


def test_main_runs_fixture_and_writes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report_path = tmp_path / "report.md"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "seed: 1\n"
        "use_fixture: true\n"
        "holdout_season: null\n"
        "min_train_games: 1\n"
        "n_boot: 50\n"
        f"report_path: {report_path}\n"
    )
    monkeypatch.chdir(tmp_path)
    exit_code = main(["--config", str(config_path)])
    assert exit_code == 0
    assert report_path.exists()
    content = report_path.read_text()
    assert "Architecture-ladder backtest report" in content
