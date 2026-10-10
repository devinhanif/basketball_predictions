"""Tests for the model gate's pure comparison (nba.registry.gate)."""

from __future__ import annotations

import yaml

from nba.registry.gate import (
    DEFAULT_TOLERANCES_PATH,
    GateFailure,
    evaluate_gate,
    load_tolerances,
)


def test_tolerances_config_has_expected_keys() -> None:
    tolerances = load_tolerances(DEFAULT_TOLERANCES_PATH)
    for metric in ("log_loss", "brier", "ece", "crps"):
        assert f"{metric}_tolerance" in tolerances
        assert tolerances[f"{metric}_tolerance"] >= 0.0


def test_evaluate_gate_passes_when_candidate_matches_baseline() -> None:
    baseline = {"rung1_logistic": {"log_loss": 0.5, "brier": 0.2, "ece": 0.1}}
    candidate = {"rung1_logistic": {"log_loss": 0.5, "brier": 0.2, "ece": 0.1}}
    tolerances = {"log_loss_tolerance": 0.05, "brier_tolerance": 0.05, "ece_tolerance": 0.05}
    assert evaluate_gate(candidate, baseline, tolerances) == []


def test_evaluate_gate_fails_on_regression_beyond_tolerance() -> None:
    baseline = {"rung1_logistic": {"log_loss": 0.5}}
    candidate = {"rung1_logistic": {"log_loss": 0.7}}
    tolerances = {"log_loss_tolerance": 0.05}
    failures = evaluate_gate(candidate, baseline, tolerances)
    assert len(failures) == 1
    assert failures[0].model_name == "rung1_logistic"
    assert failures[0].metric == "log_loss"
    assert "log_loss" in str(failures[0])


def test_evaluate_gate_within_tolerance_is_not_a_failure() -> None:
    baseline = {"rung1_logistic": {"log_loss": 0.5}}
    candidate = {"rung1_logistic": {"log_loss": 0.53}}
    tolerances = {"log_loss_tolerance": 0.05}
    assert evaluate_gate(candidate, baseline, tolerances) == []


def test_evaluate_gate_skips_unknown_model_in_candidate() -> None:
    baseline = {"rung9_future": {"log_loss": 0.5}}
    candidate: dict[str, dict[str, float]] = {}
    tolerances = {"log_loss_tolerance": 0.05}
    assert evaluate_gate(candidate, baseline, tolerances) == []


def test_evaluate_gate_skips_nan_values_on_either_side() -> None:
    nan = float("nan")
    baseline = {"m": {"log_loss": nan}}
    candidate = {"m": {"log_loss": 0.9}}
    tolerances = {"log_loss_tolerance": 0.05}
    assert evaluate_gate(candidate, baseline, tolerances) == []

    baseline2 = {"m": {"log_loss": 0.5}}
    candidate2 = {"m": {"log_loss": nan}}
    assert evaluate_gate(candidate2, baseline2, tolerances) == []


def test_evaluate_gate_skips_metric_missing_from_tolerances_config() -> None:
    """A metric present on both sides but absent from the tolerances config defaults to 0.0."""
    baseline = {"m": {"log_loss": 0.5}}
    candidate = {"m": {"log_loss": 0.5000001}}
    assert evaluate_gate(candidate, baseline, {}) != []  # any positive delta fails tol=0.0


def test_gate_failure_str_mentions_both_values() -> None:
    failure = GateFailure(
        model_name="rung2_lightgbm", metric="ece", baseline=0.1, candidate=0.3, tolerance=0.05
    )
    text = str(failure)
    assert "rung2_lightgbm" in text
    assert "0.3000" in text
    assert "0.1000" in text


def test_tolerances_file_is_yaml_dict_of_floats() -> None:
    raw = yaml.safe_load(DEFAULT_TOLERANCES_PATH.read_text())
    assert isinstance(raw, dict)
    assert all(isinstance(v, int | float) for v in raw.values())
