"""Smoke test for ``nba.eval.player_reb_ast_sim_eval`` on the committed
fixture (3 games) -- CLAUDE.md "no multi-minute jobs": this is the ONLY
place this agent runs the sim-vs-baseline reb/ast eval path, on the tiny
fixture, with a small ``n_sims``. The real 4-season run is the
maintainer's to execute (see that module's docstring for the exact
command).
"""

from __future__ import annotations

from nba.eval.player_reb_ast_sim_eval import run_reb_ast_sim_vs_baseline_eval
from tests.fixtures.loader import build_fixture_db


def test_smoke_runs_on_fixture() -> None:
    con = build_fixture_db(":memory:")
    try:
        reb_result, ast_result = run_reb_ast_sim_vs_baseline_eval(
            con, n_sims=200, seed=0, n_boot=50
        )
    finally:
        con.close()

    for result in (reb_result, ast_result):
        assert result.n_player_games > 0
        assert result.crps_sim == result.crps_sim  # not NaN
        assert result.crps_season_avg == result.crps_season_avg
        assert result.mean_bias_sim.n > 0
        assert isinstance(result.summary(), str)
    assert reb_result.stat == "reb"
    assert ast_result.stat == "ast"


def test_smoke_deterministic_given_seed() -> None:
    con = build_fixture_db(":memory:")
    try:
        a_reb, a_ast = run_reb_ast_sim_vs_baseline_eval(con, n_sims=150, seed=5, n_boot=50)
        b_reb, b_ast = run_reb_ast_sim_vs_baseline_eval(con, n_sims=150, seed=5, n_boot=50)
    finally:
        con.close()
    assert a_reb.crps_sim == b_reb.crps_sim
    assert a_ast.crps_sim == b_ast.crps_sim
    assert a_reb.mean_bias_sim.point == b_reb.mean_bias_sim.point
