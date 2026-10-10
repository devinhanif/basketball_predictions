"""Learned-heads sim adapter: torch tests (run in their own pytest invocation)."""

from __future__ import annotations

import numpy as np
import torch

from nba.sim.engine import simulate_game
from research.features.possession_step_features import (
    POSSESSION_STEP_FEATURE_COLUMNS,
    POSSESSION_STEP_TEAM_FEATURE_COLUMNS,
)
from research.models.rung4_stepheads import StepHeadsNet, StepHeadsRung
from research.sim.learned_heads import (
    OUTCOME_POINTS,
    LearnedHeads,
    build_game_head_inputs,
    compare_learned_vs_rung3,
)
from tests.research.features.test_rung4_player_features import make_con


def _heads(cols: list[str], seed: int = 0) -> LearnedHeads:
    torch.manual_seed(seed)
    rung = StepHeadsRung(seed=seed, hidden=8, feature_columns=cols)
    rung.net = StepHeadsNet(len(cols), hidden=8)
    rung.feature_mean_ = np.zeros(len(cols))
    rung.feature_std_ = np.ones(len(cols))
    return LearnedHeads(rung)


def test_engine_override_is_default_off_and_exact_when_used() -> None:
    kw = dict(
        home_off_rtg=1.15, home_def_rtg=1.1, home_pace=100.0, away_off_rtg=1.1,
        away_def_rtg=1.12, away_pace=100.0, league_avg_ppp=1.146, n_sims=50, seed=3,
    )  # fmt: skip
    base = simulate_game(**kw)
    assert simulate_game(**kw).total_mean == base.total_mean  # deterministic, hook off
    support = np.array([0.0, 3.0])
    always3 = np.array([0.0, 1.0])
    res = simulate_game(
        **kw, outcome_support=support, home_probs_override=always3, away_probs_override=always3
    )
    assert res.margin_mean == 0.0 and res.total_mean == 6.0 * res.n_poss_mean


def test_sample_possessions_shapes_and_frequencies() -> None:
    cols = POSSESSION_STEP_FEATURE_COLUMNS
    h = _heads(cols)
    raw = np.zeros((3, len(cols)))
    rng = np.random.default_rng(0)
    d = h.sample_possessions(raw, rng, n_draws=4000)
    assert d["outcome"].shape == (3, 4000)
    p = h.head_probabilities(raw)["outcome"]
    freq = np.stack([(d["outcome"][0] == k).mean() for k in range(6)])
    assert np.allclose(freq, p[0], atol=0.03)
    shot = np.isin(d["outcome"], [0, 1, 2])
    assert (d["zone"][shot] >= 0).all() and (d["zone"][~shot] == -1).all()
    assert not d["oreb"][d["outcome"] != 2].any()
    assert (d["made"] == np.isin(d["outcome"], [0, 1])).all()
    assert OUTCOME_POINTS.shape == (6,)


def test_game_inputs_are_asof_and_shaped() -> None:
    cols = POSSESSION_STEP_FEATURE_COLUMNS
    con = make_con()
    con2 = make_con(g3_outcome="TOV")  # change g3's OWN outcomes only
    try:
        a = build_game_head_inputs(con, ["g1", "g3"], cols, n_pairs=6)
        b = build_game_head_inputs(con2, ["g1", "g3"], cols, n_pairs=6)
    finally:
        con.close()
        con2.close()
    assert a["g3"].home_rows.shape == (6, len(cols))
    # g3's own outcomes never reach its prediction-time inputs
    assert np.array_equal(a["g3"].home_rows, b["g3"].home_rows)
    assert np.array_equal(a["g3"].away_rows, b["g3"].away_rows)
    # g1 has no previous game -> neutral lineup pool, so two-sided identical player columns
    n_team = len(POSSESSION_STEP_TEAM_FEATURE_COLUMNS)
    assert np.allclose(a["g1"].home_rows[:, n_team:], a["g1"].home_rows[0, n_team:])
    # g3 saw g1/g2 history -> player columns moved off the neutral prior
    assert not np.allclose(a["g3"].home_rows[:, n_team:], a["g1"].home_rows[:, n_team:])
    h = _heads(cols)
    probs = h.game_outcome_probs(a)
    assert probs["g3"].shape == (2, 6) and np.allclose(probs["g3"].sum(axis=1), 1.0)


def test_team_only_heads_build_team_columns_only() -> None:
    cols = POSSESSION_STEP_TEAM_FEATURE_COLUMNS
    con = make_con()
    try:
        a = build_game_head_inputs(con, ["g3"], cols, n_pairs=3)
    finally:
        con.close()
    assert a["g3"].home_rows.shape == (3, len(cols))
    assert (a["g3"].home_rows[:, 3] == 1.0).all() and (a["g3"].away_rows[:, 3] == 0.0).all()


def test_compare_refuses_holdout_season() -> None:
    import inspect

    assert "season < 2025" in inspect.getsource(compare_learned_vs_rung3)
