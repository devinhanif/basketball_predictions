"""Smoke-backtest CI guard (CLAUDE.md "CI/CD" -> smoke-backtest).

``make smoke`` runs ``python -m nba.eval --config
configs/rung_ladder_fixture.yaml --register``: the full
train -> walk-forward backtest -> report -> registry-log path on the tiny
committed fixture. This guard re-checks its two observable, build-breaking
failure modes after that run completes:

1. ``report.md`` was actually produced and is non-empty.
2. No headline metric rendered into it is NaN.

The fixture config sets ``use_fixture: true``, which opens an **in-memory**
DuckDB connection (see ``nba/eval/__main__.py::_open_connection``) -- so the
``experiments`` registry row written during that run only exists for the
lifetime of that process and cannot be re-queried from a fresh connection
afterwards. ``report.md`` is the durable, on-disk record of the run instead,
and ``nba/eval/report.py``'s ``_fmt`` helper renders the literal string
``"NaN"`` for any NaN metric -- so grepping the rendered report for that
string is an exact, no-false-negative check of the same headline metrics
(log loss, Brier, accuracy, ECE, and the ladder-comparison deltas) without
needing a second database connection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_PATH = REPO_ROOT / "registry_store" / "reports" / "rung_ladder_fixture_report.md"


def check_smoke_report(report_path: Path) -> list[str]:
    """Return a list of failure reasons (empty list == the smoke run is healthy)."""
    if not report_path.exists():
        return [f"report not found at {report_path} (did `make smoke` run and succeed?)"]

    text = report_path.read_text()
    if not text.strip():
        return [f"report at {report_path} is empty"]

    errors: list[str] = []
    nan_lines = [line.strip() for line in text.splitlines() if "NaN" in line]
    if nan_lines:
        errors.append(
            f"report at {report_path} contains NaN metric value(s) on "
            f"{len(nan_lines)} line(s): {nan_lines}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m nba.registry.smoke_guard")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args(argv)

    errors = check_smoke_report(Path(args.report))
    if errors:
        print("smoke-backtest guard FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"smoke-backtest guard passed: {args.report} produced, no NaN headline metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
