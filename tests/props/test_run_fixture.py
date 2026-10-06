"""End-to-end smoke test on the committed fixture (CLAUDE.md milestone 7,
point 4: "Degrade gracefully on the tiny fixture").

Checks: determinism (same seed -> identical predictions), metric sanity
(CRPS/log loss finite and in [0, inf) / [0, inf) respectively), the
report renders and explicitly states the sample size is insufficient for
a trustworthy CI, and no row is NaN/inf.
"""

from __future__ import annotations

import json
import math

from nba.props.config import PropsConfig
from nba.props.report import render_props_report
from nba.props.run import run_props_experiment, write_prop_predictions
from tests.fixtures.loader import build_fixture_db


def _run() -> object:
    con = build_fixture_db(":memory:")
    try:
        return run_props_experiment(con, config=PropsConfig(), seed=7, n_boot=100)
    finally:
        con.close()


def test_run_is_deterministic_given_seed() -> None:
    result_a = _run()
    result_b = _run()
    assert result_a.predictions.equals(result_b.predictions)
    for a, b in zip(result_a.stats, result_b.stats, strict=True):
        assert a.mean_bias.point == b.mean_bias.point
        assert a.crps_point == b.crps_point


def test_metrics_are_finite_on_the_fixture() -> None:
    result = _run()
    assert len(result.stats) == 4  # pts, reb, ast, fg3m
    for s in result.stats:
        assert s.n == 18
        assert math.isfinite(s.mean_bias.point)
        assert math.isfinite(s.crps_point)
        for t in s.threshold_calibration:
            assert math.isfinite(t.log_loss)
        # Sample size is far below CLAUDE.md's >=500-per-stat bar.
        assert "NOT statistically meaningful" in s.data_sufficiency_note


def test_predictions_have_valid_shape_and_no_nans() -> None:
    result = _run()
    preds = result.predictions
    assert preds.height == 18 * 4
    for col in ("mean", "q10", "q50", "q90"):
        values = preds.select(col).to_series().to_numpy()
        assert not any(v != v for v in values)  # no NaN
        assert all(abs(v) < float("inf") for v in values)
    for row in preds.iter_rows(named=True):
        assert row["q10"] <= row["q50"] + 1e-6
        assert row["q50"] <= row["q90"] + 1e-6
        p_ge = json.loads(row["p_ge"])
        for p in p_ge.values():
            assert 0.0 <= p <= 1.0
        json.loads(row["dist_params"])  # must round-trip as valid JSON


def test_report_renders_and_flags_tiny_sample() -> None:
    result = _run()
    report = render_props_report(result)
    assert "mean-bias CI and coverage are NOT statistically meaningful" in report
    assert "fixture artifact" in report
    assert "pts" in report and "reb" in report and "ast" in report and "fg3m" in report


def test_write_prop_predictions_round_trips_through_duckdb() -> None:
    con = build_fixture_db(":memory:")
    try:
        result = run_props_experiment(con, config=PropsConfig(), seed=1, n_boot=50)
        write_prop_predictions(con, result.predictions, run_id="test-run")
        count = con.execute("SELECT COUNT(*) FROM prop_predictions").fetchone()
        assert count is not None
        assert count[0] == result.predictions.height
        row = con.execute("SELECT run_id, stat, p_ge FROM prop_predictions LIMIT 1").fetchone()
        assert row is not None
        assert row[0] == "test-run"
        parsed = json.loads(row[2])
        assert isinstance(parsed, dict)
    finally:
        con.close()


def test_empty_db_degrades_gracefully() -> None:
    import duckdb

    from nba.db.connect import apply_schema

    con = duckdb.connect(":memory:")
    apply_schema(con)
    result = run_props_experiment(con, seed=0, n_boot=50)
    con.close()
    assert result.stats == []
    report = render_props_report(result)
    assert "no games" in report
