"""Entry point: ``python -m nba.props [--use-fixture] [--register]``.

Standard CLI contract per CLAUDE.md "Registry retrofit" -> "Patterns to
port": ``--register`` logs the run as a candidate; ``--promote`` is a
separate, explicit, manual step; ``--scenario`` and ``--promote`` can
never be combined. ``nba/props/`` does not own ``configs/`` (see the
props-modeler status file), so this entry point takes its parameters as
plain CLI flags instead of a YAML config.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import time
import uuid
from pathlib import Path

import duckdb

from nba.db.connect import connect
from nba.props.config import PropsConfig
from nba.props.report import render_props_report
from nba.props.run import run_props_experiment, write_prop_predictions
from nba.registry import build_run_metadata, build_tags, get_registry, validate_cli_flags

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_NAME = "props_minutes_distributions"


def _open_connection(use_fixture: bool, db_path: str | None) -> duckdb.DuckDBPyConnection:
    if use_fixture:
        loader = importlib.import_module("tests.fixtures.loader")
        con: duckdb.DuckDBPyConnection = loader.build_fixture_db(":memory:")
        return con
    resolved = db_path or str(REPO_ROOT / "nba.duckdb")
    return connect(str(resolved))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nba.props")
    parser.add_argument(
        "--use-fixture", action="store_true", help="run against the committed fixture"
    )
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--n-boot", type=int, default=500)
    parser.add_argument(
        "--holdout-season",
        type=int,
        default=None,
        help="frozen holdout season (CLAUDE.md canonical value: 2025); "
        "default None disables the filter entirely (prior behavior)",
    )
    parser.add_argument(
        "--holdout-mode",
        choices=["all", "exclude_holdout", "holdout_only"],
        default="all",
        help="'all' (default, no filter), 'exclude_holdout' (tunable pool only), "
        "'holdout_only' (the single permitted confirmatory touch -- log it to "
        "docs/HOLDOUT_ACCESS_LOG.md before running)",
    )
    parser.add_argument("--report-path", default="registry_store/reports/props_report.md")
    parser.add_argument("--register", action="store_true", help="log this run as a candidate")
    parser.add_argument("--promote", action="store_true", help="promote this version to production")
    parser.add_argument("--scenario", action="store_true", help="what-if run, never registered")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    validate_cli_flags(promote=args.promote, scenario=args.scenario)

    start = time.monotonic()
    con = _open_connection(args.use_fixture, args.db_path)
    try:
        config = PropsConfig(
            seed=args.seed,
            n_boot=args.n_boot,
            holdout_season=args.holdout_season,
            holdout_mode=args.holdout_mode,
        )
        result = run_props_experiment(con, config=config, seed=args.seed, n_boot=args.n_boot)
        runtime_s = time.monotonic() - start

        run_id = str(uuid.uuid4())
        if not args.scenario:
            write_prop_predictions(con, result.predictions, run_id)
            write_prop_predictions(con, result.combo_predictions, run_id)

        report_md = render_props_report(result)
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md)
        print(report_md)
        print(f"\nreport written to {report_path}")

        if args.register and not args.scenario:
            registry = get_registry(con)
            metadata = build_run_metadata(
                game_date_min=result.game_date_min,
                game_date_max=result.game_date_max,
                possession_count=0,
                seed=args.seed,
                runtime_seconds=runtime_s,
            )
            tags = build_tags(
                rung=2,
                model_method="minutes_hurdle+freq_severity+combos+coherence+conformal",
                cold_start_flags=["shrinkage", "role_change_cusum"],
                source_config="cli_flags",
            )
            with tempfile.TemporaryDirectory() as tmp:
                artifact_dir = Path(tmp) / MODEL_NAME
                artifact_dir.mkdir()
                (artifact_dir / "report.md").write_text(report_md)
                version = registry.next_version(MODEL_NAME)
                registry.log_model(
                    MODEL_NAME,
                    version,
                    str(artifact_dir),
                    metrics={
                        "stats": [
                            {
                                "stat": s.stat,
                                "n": s.n,
                                "mean_bias": s.mean_bias.point,
                                "coverage": s.coverage,
                                "crps": s.crps_point,
                            }
                            for s in result.stats
                        ],
                    },
                    metadata=metadata,
                    tags=tags,
                )
                if args.promote:
                    registry.promote(MODEL_NAME, version)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
