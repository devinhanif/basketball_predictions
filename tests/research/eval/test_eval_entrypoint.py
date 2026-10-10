"""End-to-end smoke test: the fixture produces a valid, non-NaN report.

Mirrors the ``python -m research.eval`` entry point's acceptance criteria: run
end to end on the fixture quickly and never crash or silently emit NaN
metrics (CLAUDE.md milestone 4's "degrade gracefully" requirement).
"""

from __future__ import annotations

import math
import time

from research.eval.report import render_report
from research.eval.run import run_experiment
from tests.fixtures.loader import build_fixture_db


def test_fixture_run_completes_quickly_and_produces_a_report() -> None:
    con = build_fixture_db(":memory:")
    try:
        start = time.monotonic()
        result = run_experiment(con, seed=0, holdout_season=None, min_train_games=1, n_boot=200)
        elapsed = time.monotonic() - start
        assert elapsed < 60.0  # well under the 3-minute acceptance bound

        report_md = render_report(result)
        assert len(report_md) > 0
        assert "NaN" not in report_md or "insufficient" in report_md.lower()

        assert len(result.rungs) == 5
        for bundle in result.rungs:
            # Every finite-sample metric must be a real finite float, never NaN,
            # even on a 3-game fixture -- NaN here would mean a silent crash
            # was swallowed somewhere in the metric pipeline.
            if bundle.n_games > 0:
                assert math.isfinite(bundle.log_loss.point)
                assert math.isfinite(bundle.brier.point)
    finally:
        con.close()


def test_fixture_run_is_deterministic_given_same_seed() -> None:
    con_a = build_fixture_db(":memory:")
    con_b = build_fixture_db(":memory:")
    try:
        result_a = run_experiment(con_a, seed=0, min_train_games=1, n_boot=100)
        result_b = run_experiment(con_b, seed=0, min_train_games=1, n_boot=100)
        for a, b in zip(result_a.rungs, result_b.rungs, strict=True):
            assert a.name == b.name
            assert a.log_loss.point == b.log_loss.point
            assert a.brier.point == b.brier.point
    finally:
        con_a.close()
        con_b.close()
