"""Model-gate CI check (CLAUDE.md "CI/CD" -> model-gate).

Run as ``python -m research.registry.model_gate`` (``make gate``).

On PRs touching ``nba/models/``, ``nba/props/``, or ``nba/parlay/``, compare
a candidate run's metrics against the metrics of the current ``production``
registry version, and fail on regression beyond a configured tolerance in
log loss, CRPS, and calibration ECE (Brier is gated too, as a cheap
companion to log loss).

**Why a committed baseline file instead of querying the registry:** the
local registry backend (:mod:`nba.registry.local`) stores its state in the
``experiments`` DuckDB table plus ``./registry_store/`` on disk --
``registry_store/`` is gitignored and the project's ``.duckdb`` file is
never committed (see CLAUDE.md "Hard constraints" and "Cheapness rules"),
and neither persists between CI runs. There is therefore nothing durable in
a fresh CI checkout to query for "the current production version's
metrics". ``tests/registry/baselines/fixture_metrics.json`` is the CI
stand-in for that persisted production registry state: a snapshot of the
fixture ladder's metrics at the point it was last intentionally promoted.
See ``docs/ci_cd.md`` for exactly how and when to refresh it.

The candidate side is produced by re-running the same fixture ladder
in-process (:func:`candidate_metrics_from_fixture`), the same way
``make smoke`` / ``python -m research.eval`` does, but without registering
anything -- the model-gate is a pure comparison, never a registry write.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import cast

import duckdb

from nba.registry.gate import (
    DEFAULT_TOLERANCES_PATH,
    GATED_METRICS,
    GateFailure,
    evaluate_gate,
    load_tolerances,
)
from research.eval.__main__ import load_config
from research.eval.run import run_experiment

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = REPO_ROOT / "tests" / "registry" / "baselines" / "fixture_metrics.json"
DEFAULT_CONFIG_PATH = "configs/rung_ladder_fixture.yaml"

#: The pure comparison lives in ``nba.registry.gate`` (live code: ``registry gate`` uses it); this
#: module is the CI check that feeds it a fixture candidate, and re-exports the names it always had.
__all__ = [
    "DEFAULT_BASELINE_PATH",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_TOLERANCES_PATH",
    "GATED_METRICS",
    "GateFailure",
    "candidate_metrics_from_fixture",
    "evaluate_gate",
    "load_baseline",
    "load_tolerances",
    "main",
]


def load_baseline(path: Path = DEFAULT_BASELINE_PATH) -> dict[str, dict[str, float]]:
    """Load the committed baseline: the CI stand-in for the persisted production registry."""
    with open(path) as f:
        raw = cast(dict[str, dict[str, float | None]], json.load(f))
    out: dict[str, dict[str, float]] = {}
    for model_name, metrics in raw.items():
        out[model_name] = {k: float(v) for k, v in metrics.items() if v is not None}
    return out


def candidate_metrics_from_fixture(
    config_path: str = DEFAULT_CONFIG_PATH,
) -> dict[str, dict[str, float]]:
    """Re-run the ladder (fixture by default) and extract the gated metrics per rung.

    Mirrors ``research/eval/__main__.py``'s connection setup (fixture vs. a real
    DuckDB file, selected by the config's ``use_fixture`` flag) but never
    registers or writes a report -- purely a metrics comparison.
    """
    config = load_config(config_path)
    seed = int(config.get("seed", 0))
    min_train_games = int(config.get("min_train_games", 1))
    n_boot = int(config.get("n_boot", 500))
    holdout_season = cast("int | None", config.get("holdout_season"))

    con: duckdb.DuckDBPyConnection
    if config.get("use_fixture", False):
        # Imported dynamically (not at module scope, and not as a static
        # `from ... import` even here) so this production module's
        # dependency on the test fixtures module is a runtime convenience
        # only -- mypy never statically follows into tests/fixtures/ from
        # anything under nba/. Mirrors research/eval/__main__.py's same pattern.
        loader = importlib.import_module("tests.fixtures.loader")
        con = loader.build_fixture_db(":memory:")
    else:
        from nba.db.connect import connect

        db_path = config.get("db_path") or str(REPO_ROOT / "nba.duckdb")
        con = connect(str(db_path))

    try:
        result = run_experiment(
            con,
            seed=seed,
            holdout_season=holdout_season,
            min_train_games=min_train_games,
            n_boot=n_boot,
        )
    finally:
        con.close()

    out: dict[str, dict[str, float]] = {}
    for bundle in result.rungs:
        out[bundle.name] = {
            "log_loss": bundle.log_loss.point,
            "brier": bundle.brier.point,
            "ece": bundle.ece,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.registry.model_gate")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument("--tolerances", default=str(DEFAULT_TOLERANCES_PATH))
    args = parser.parse_args(argv)

    tolerances = load_tolerances(Path(args.tolerances))
    baseline = load_baseline(Path(args.baseline))
    candidate = candidate_metrics_from_fixture(args.config)

    print("candidate metrics:")
    for model_name, metrics in candidate.items():
        print(f"  {model_name}: {metrics}")

    failures = evaluate_gate(candidate, baseline, tolerances)
    if failures:
        print("\nmodel-gate FAILED -- regression beyond tolerance vs. committed baseline:")
        for failure in failures:
            print(f"  - {failure}")
        print(
            f"\nIf this regression is intentional (a new rung is being promoted), refresh "
            f"{DEFAULT_BASELINE_PATH.relative_to(REPO_ROOT)} -- see docs/ci_cd.md."
        )
        return 1

    print("\nmodel-gate passed: no gated metric regressed beyond tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
