"""Smoke test for ``research.eval.player_points_sim_eval`` on the committed
fixture (3 games) -- CLAUDE.md "no multi-minute jobs": this is the ONLY
place this agent runs the sim-vs-baseline eval path, on the tiny fixture,
with a small ``n_sims``. The real 4-season run is the maintainer's to
execute (see that module's docstring for the exact command).
"""

from __future__ import annotations

from research.eval.player_points_sim_eval import run_sim_vs_baseline_eval
from tests.fixtures.loader import build_fixture_db


def test_smoke_runs_on_fixture() -> None:
    con = build_fixture_db(":memory:")
    try:
        result = run_sim_vs_baseline_eval(con, n_sims=200, seed=0, n_boot=50)
    finally:
        con.close()

    assert result.n_player_games > 0
    assert result.crps_sim == result.crps_sim  # not NaN
    assert result.crps_season_avg == result.crps_season_avg
    assert result.mean_bias_sim.n > 0
    # summary() must render without raising (string formatting on real floats).
    assert isinstance(result.summary(), str)


def test_smoke_deterministic_given_seed() -> None:
    con = build_fixture_db(":memory:")
    try:
        a = run_sim_vs_baseline_eval(con, n_sims=150, seed=5, n_boot=50)
        b = run_sim_vs_baseline_eval(con, n_sims=150, seed=5, n_boot=50)
    finally:
        con.close()
    assert a.crps_sim == b.crps_sim
    assert a.mean_bias_sim.point == b.mean_bias_sim.point
