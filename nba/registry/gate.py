"""The model gate's pure comparison: ``evaluate_gate`` and the tolerances it reads.

Live code. ``nba.registry.ops`` (``registry gate`` against the real production version) and
``nba.registry.__main__`` import from here. The rung-ladder CI check that feeds it a fixture
candidate lives at ``research/registry/model_gate.py`` (``python -m research.registry.model_gate``,
``make gate``), which re-exports these names.

Gated metrics are log loss, CRPS, calibration ECE, and Brier (a cheap companion to log loss).
Tolerances come from ``model_gate_config.yaml`` next to this file, never hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_TOLERANCES_PATH = Path(__file__).resolve().parent / "model_gate_config.yaml"

#: Metrics gated per CLAUDE.md "model-gate": log loss, CRPS, calibration ECE
#: (Brier added as a cheap, already-computed companion to log loss). CRPS
#: does not exist yet on the rungs 0-2 win-probability ladder (it is a
#: Phase 2 / props metric -- see CLAUDE.md "Phase 2"); ``evaluate_gate``
#: silently skips any metric missing from either side, so the "crps" entry
#: here is a no-op placeholder until nba/props emits it.
GATED_METRICS: tuple[str, ...] = ("log_loss", "brier", "ece", "crps")


@dataclass(frozen=True)
class GateFailure:
    model_name: str
    metric: str
    baseline: float
    candidate: float
    tolerance: float

    def __str__(self) -> str:
        return (
            f"{self.model_name}.{self.metric}: candidate={self.candidate:.4f} vs. "
            f"baseline={self.baseline:.4f} (allowed regression tolerance={self.tolerance:.4f})"
        )


def load_tolerances(path: Path = DEFAULT_TOLERANCES_PATH) -> dict[str, float]:
    """Load ``<metric>_tolerance`` values from a YAML config (never hardcoded)."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return {str(k): float(v) for k, v in raw.items()}


def evaluate_gate(
    candidate: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
    tolerances: dict[str, float],
) -> list[GateFailure]:
    """Return every gated-metric regression beyond tolerance (empty list == pass).

    Each model is only ever compared against its own baseline entry (keyed
    by ``model_name``), matching the local registry's per-model versioning.
    NaN values on either side are skipped rather than treated as a
    regression -- a NaN is the smoke-backtest job's concern, not the gate's.
    """
    failures: list[GateFailure] = []
    for model_name, base_metrics in baseline.items():
        cand_metrics = candidate.get(model_name)
        if cand_metrics is None:
            continue
        for metric in GATED_METRICS:
            base_val = base_metrics.get(metric)
            cand_val = cand_metrics.get(metric)
            if base_val is None or cand_val is None:
                continue
            if base_val != base_val or cand_val != cand_val:  # NaN guard
                continue
            tolerance = tolerances.get(f"{metric}_tolerance", 0.0)
            if cand_val > base_val + tolerance:
                failures.append(GateFailure(model_name, metric, base_val, cand_val, tolerance))
    return failures
