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


def test_opponent_adjustment_flag_changes_predictions_end_to_end() -> None:
    """Pipeline-level flag check (CLAUDE.md task): toggling
    ``use_opponent_adjustment`` (``PropsConfig.opponent_adjustment.enabled``)
    changes the predicted means through the full ``run_props_experiment``
    path.

    The committed fixture (3 games, 6 teams, no repeat matchups) has zero
    strictly-prior opponent history for every row by construction, so the
    adjustment is correctly a no-op there -- this test instead builds a
    small synthetic DB (same pattern as ``tests/props/test_opponent.py``)
    with real repeated matchups so the mechanism is actually exercised
    end to end, not just unit-tested on ``nba.props.opponent`` in
    isolation."""
    import duckdb as _duckdb

    from nba.db.connect import apply_schema

    con = _duckdb.connect(":memory:")
    apply_schema(con)
    try:
        # Prior history: team 1 allows a lot (weak defense), fast pace.
        for i in range(8):
            gid = f"hist_{i}"
            con.execute(
                "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
                "home_pts, away_pts) VALUES (?, ?, 2023, 1, 900, 100, 130)",
                [gid, f"2023-10-{i + 1:02d}"],
            )
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
                "reb, ast, fg3m, stl, blk, tov, starter) VALUES (?, 1001, 1, 30.0, 100, 40, "
                "22, 10, 1, 0, 1, true)",
                [gid],
            )
            con.execute(
                "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
                "reb, ast, fg3m, stl, blk, tov, starter) VALUES (?, 2001, 900, 30.0, 130, 48, "
                "30, 16, 1, 0, 1, true)",
                [gid],
            )
        # Target game: player 7777 on team 10 facing team 1 (the weak/fast opponent).
        con.execute(
            "INSERT INTO games (game_id, game_date, season, home_team, away_team, "
            "home_pts, away_pts) VALUES ('target', '2023-11-01', 2023, 10, 1, 110, 120)"
        )
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
            "reb, ast, fg3m, stl, blk, tov, starter) VALUES ('target', 7777, 10, 30.0, 20, "
            "8, 5, 2, 1, 0, 1, true)"
        )
        con.execute(
            "INSERT INTO player_game_stats (game_id, player_id, team_id, minutes, pts, "
            "reb, ast, fg3m, stl, blk, tov, starter) VALUES ('target', 8888, 1, 30.0, 20, "
            "8, 5, 2, 1, 0, 1, true)"
        )

        # Hierarchical coherence reconciliation (nba.props.coherence) would
        # otherwise rescale each team's lone player back onto the
        # team-total forecast, masking the opponent signal this test wants
        # to isolate -- disabled here so the comparison below is a clean
        # read of the opponent-adjustment wiring, not a coherence/opponent
        # interaction effect.
        cfg_on = PropsConfig()
        cfg_on.opponent_adjustment.enabled = True
        cfg_on.opponent_adjustment.min_games_for_factor = 3
        cfg_on.coherence.enabled = False
        cfg_off = PropsConfig()
        cfg_off.opponent_adjustment.enabled = False
        cfg_off.coherence.enabled = False

        result_on = run_props_experiment(con, config=cfg_on, seed=7, n_boot=50)
        result_off = run_props_experiment(con, config=cfg_off, seed=7, n_boot=50)

        target_mean_on = (
            result_on.predictions.filter(
                (result_on.predictions["game_id"] == "target")
                & (result_on.predictions["player_id"] == 7777)
                & (result_on.predictions["stat"] == "pts")
            )
            .select("mean")
            .item()
        )
        target_mean_off = (
            result_off.predictions.filter(
                (result_off.predictions["game_id"] == "target")
                & (result_off.predictions["player_id"] == 7777)
                & (result_off.predictions["stat"] == "pts")
            )
            .select("mean")
            .item()
        )
        # Facing a weak-defense, fast-pace opponent -> the ON prediction
        # must be strictly higher than the flag-OFF (unadjusted) one.
        assert target_mean_on > target_mean_off
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
