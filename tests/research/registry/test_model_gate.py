"""Tests for the model-gate CI check (research.registry.model_gate): the rung-ladder fixture run
against the committed baseline. The comparison itself is tested in tests/registry/test_gate.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.registry import model_gate
from research.registry.model_gate import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_TOLERANCES_PATH,
    candidate_metrics_from_fixture,
    evaluate_gate,
    load_baseline,
    load_tolerances,
    main,
)


def test_committed_baseline_file_exists_and_has_no_nan() -> None:
    baseline = load_baseline(DEFAULT_BASELINE_PATH)
    assert baseline, "committed baseline must not be empty"
    for model_name, metrics in baseline.items():
        for metric, value in metrics.items():
            assert value == value, f"{model_name}.{metric} is NaN in the committed baseline"


def test_candidate_metrics_from_fixture_matches_committed_baseline_within_tolerance() -> None:
    """Regression guard on the gate itself: the real fixture run must currently pass."""
    candidate = candidate_metrics_from_fixture()
    baseline = load_baseline(DEFAULT_BASELINE_PATH)
    tolerances = load_tolerances(DEFAULT_TOLERANCES_PATH)
    failures = evaluate_gate(candidate, baseline, tolerances)
    assert failures == []


def test_main_passes_against_the_committed_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "model-gate passed" in captured.out


def test_main_fails_loudly_against_a_strict_fake_baseline(tmp_path: Path) -> None:
    strict_baseline = tmp_path / "fixture_metrics.json"
    strict_baseline.write_text('{"rung2_lightgbm": {"log_loss": -1.0}}')
    exit_code = main(["--baseline", str(strict_baseline)])
    assert exit_code == 1


def test_model_gate_reexports_the_live_gate_names() -> None:
    """Old import paths keep working: model_gate re-exports what moved to nba.registry.gate."""
    from nba.registry import gate

    for name in ("GateFailure", "evaluate_gate", "load_tolerances", "DEFAULT_TOLERANCES_PATH"):
        assert getattr(model_gate, name) is getattr(gate, name)
