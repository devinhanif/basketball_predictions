"""Entry point: ``python -m research.eval --config configs/rung_ladder_fixture.yaml``.

Runs the full train -> walk-forward backtest -> report.md -> registry-log
path for rungs 0-2. Standard CLI contract per CLAUDE.md "Registry
retrofit" -> "Patterns to port": ``--register`` logs each rung as a
``candidate``; ``--promote`` is a separate, explicit, manual step;
``--scenario`` and ``--promote`` can never be combined.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import duckdb
import yaml

from nba.db.connect import connect
from nba.registry import build_run_metadata, build_tags, get_registry, validate_cli_flags
from research.eval.report import render_report
from research.eval.run import run_experiment

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        config = yaml.safe_load(f)
    return cast(dict[str, Any], config)


def _open_connection(config: dict[str, Any]) -> duckdb.DuckDBPyConnection:
    if config.get("use_fixture", False):
        # Imported dynamically (not at module scope) so this production
        # entry point's dependency on the test fixtures module is purely
        # a runtime convenience for the smoke config, not a static import
        # mypy needs to type-check as part of `nba/`.
        loader = importlib.import_module("tests.fixtures.loader")
        con: duckdb.DuckDBPyConnection = loader.build_fixture_db(":memory:")
        return con
    db_path = config.get("db_path") or str(REPO_ROOT / "nba.duckdb")
    return connect(str(db_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m research.eval")
    parser.add_argument("--config", default="configs/rung_ladder_fixture.yaml")
    parser.add_argument("--register", action="store_true", help="log each rung as a candidate")
    parser.add_argument("--promote", action="store_true", help="promote rung2 to production")
    parser.add_argument("--scenario", action="store_true", help="what-if run, never registered")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    validate_cli_flags(promote=args.promote, scenario=args.scenario)

    config = load_config(args.config)
    seed = int(cast(int, config.get("seed", 0)))
    holdout_season = cast("int | None", config.get("holdout_season"))
    min_train_games = int(cast(int, config.get("min_train_games", 1)))
    n_boot = int(cast(int, config.get("n_boot", 2000)))
    report_path = Path(cast(str, config.get("report_path", "registry_store/reports/report.md")))
    start = time.monotonic()

    con = _open_connection(config)
    try:
        result = run_experiment(
            con,
            seed=seed,
            holdout_season=holdout_season,
            min_train_games=min_train_games,
            n_boot=n_boot,
        )
        runtime_s = time.monotonic() - start

        report_md = render_report(result)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md)
        print(report_md)
        print(f"\nreport written to {report_path}")

        if args.register and not args.scenario:
            registry = get_registry(con)
            possession_row = con.execute("SELECT COUNT(*) FROM possessions").fetchone()
            possession_count = int(possession_row[0]) if possession_row else 0
            metadata = build_run_metadata(
                game_date_min=result.game_date_min,
                game_date_max=result.game_date_max,
                possession_count=possession_count,
                seed=seed,
                runtime_seconds=runtime_s,
            )
            for bundle in result.rungs:
                tags = build_tags(
                    rung=bundle.rung,
                    model_method=bundle.model_method,
                    cold_start_flags=[],
                    source_config=args.config,
                )
                with tempfile.TemporaryDirectory() as tmp:
                    artifact_dir = Path(tmp) / bundle.name
                    artifact_dir.mkdir()
                    (artifact_dir / "config.yaml").write_text(yaml.safe_dump(bundle.config))
                    (artifact_dir / "report.md").write_text(report_md)
                    version = registry.next_version(bundle.name)
                    registry.log_model(
                        bundle.name,
                        version,
                        str(artifact_dir),
                        metrics={
                            "log_loss": bundle.log_loss.point,
                            "log_loss_ci": [bundle.log_loss.lo, bundle.log_loss.hi],
                            "brier": bundle.brier.point,
                            "brier_ci": [bundle.brier.lo, bundle.brier.hi],
                            "accuracy": bundle.accuracy_point,
                            "ece": bundle.ece,
                            "n_games": bundle.n_games,
                        },
                        metadata=metadata,
                        tags=tags,
                    )
                    if args.promote and bundle.name == "rung2_lightgbm":
                        registry.promote(bundle.name, version)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
