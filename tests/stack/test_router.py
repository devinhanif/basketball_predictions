from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nba.stack.adapters import quantile_grid
from nba.stack.data import StackData
from nba.stack.evaluate import (
    benjamini_hochberg,
    evaluate_stack,
    standard_slices,
)
from nba.stack.oof import N_Q
from nba.stack.router import (
    LGBMGate,
    SoftmaxGate,
    WalkForwardConfig,
    equal_pool,
    hard_bucket_walk_forward,
    walk_forward,
)


def synth(n_days: int = 300, per_day: int = 24, seed: int = 0) -> StackData:
    """Candidate A is sharp when context x>0, candidate B when x<=0 (other is
    biased by +/-4)."""
    rng = np.random.default_rng(seed)
    n = n_days * per_day
    day = np.repeat(np.arange(n_days), per_day)
    x = rng.integers(0, 2, n)  # 1 -> A is right
    mu = rng.normal(15, 3, n)
    y = mu + rng.normal(0, 3, n)
    bias_a = np.where(x == 1, 0.0, 4.0)
    bias_b = np.where(x == 1, 4.0, 0.0)
    qa = quantile_grid(mu + bias_a, np.full(n, 3.0), "normal")
    qb = quantile_grid(mu + bias_b, np.full(n, 3.0), "normal")
    keys = pd.DataFrame(
        {
            "game_id": [f"g{i // 2}" for i in range(n)],
            "player_id": np.arange(n),
            "game_date": pd.Timestamp("2022-11-01") + pd.to_timedelta(day, "D"),
            "season": 2023,
        }
    )
    ctx = pd.DataFrame({"x": x.astype(float), "noise": rng.normal(size=n)})
    return StackData("pts", ["A", "B"], keys, y, np.stack([qa, qb], axis=1), ctx)


CFG = WalkForwardConfig(block_days=30, min_train_rows=1500)


def _mean(data, res, j=None):
    from nba.stack.scoring import score_rows

    m = res.scored
    return score_rows(data.target, res.pooled[m], data.y[m]).mean()


@pytest.mark.parametrize("gate", [lambda: SoftmaxGate(), lambda: LGBMGate(min_child_samples=50)])
def test_router_learns_context_and_beats_both(gate):
    d = synth()
    res = walk_forward(d, gate, CFG)
    sc = d.cand_scores()[res.scored].mean(axis=0)
    eq = np.mean(_scores(d, equal_pool(d)[res.scored], res.scored))
    r = _mean(d, res)
    assert r < sc.min() - 0.3 and r < eq
    # weights favour A where x==1 and B where x==0
    x = d.context["x"].to_numpy()[res.scored]
    w = res.weights[res.scored]
    assert w[x == 1, 0].mean() > 0.65 and w[x == 0, 1].mean() > 0.65


def _scores(d, pooled, mask):
    from nba.stack.scoring import score_rows

    return score_rows(d.target, pooled, d.y[mask])


def test_no_leakage_in_walk_forward():
    d = synth()
    res = walk_forward(d, SoftmaxGate, CFG)
    for b in res.blocks:
        assert np.datetime64(b["train_max_date"]) < np.datetime64(b["start"])
    # corrupting outcomes at/after a block must not change that block's weights
    first = res.blocks[3]["start"]
    cut = d.dates >= np.datetime64(first)
    d2 = synth()
    d2.y[cut] = 1000.0
    res2 = walk_forward(d2, SoftmaxGate, CFG)
    blk = (d.dates >= np.datetime64(first)) & (
        d.dates < np.datetime64(first) + np.timedelta64(30, "D")
    )
    np.testing.assert_allclose(res.weights[blk], res2.weights[blk])


def test_frozen_season_rejected():
    d = synth(60)
    d.keys.loc[0, "season"] = 2025
    with pytest.raises(ValueError, match="frozen"):
        walk_forward(d, SoftmaxGate, WalkForwardConfig(min_train_rows=100))
    with pytest.raises(ValueError):
        walk_forward(synth(60), SoftmaxGate, WalkForwardConfig(max_season=2025))


def test_hard_route_and_evaluate_decision():
    d = synth()
    res_s = walk_forward(d, SoftmaxGate, CFG, name="softmax")
    res_l = walk_forward(d, lambda: LGBMGate(min_child_samples=50), CFG, name="lgbm")
    # hard route on a coarse bucket that is uninformative (noise sign)
    hard = hard_bucket_walk_forward(d, (d.context["noise"] > 0).to_numpy(), CFG)
    ctx = pd.DataFrame(
        {
            "cold_start_bucket": np.where(d.context["noise"] > 0, "cold", "est"),
            "games_played_season": np.arange(d.n) % 40,
            "starter": (np.arange(d.n) % 2),
        }
    )
    rep = evaluate_stack(
        d, {"softmax": res_s, "lgbm": res_l}, hard, standard_slices(ctx), n_boot=300
    )
    assert rep.best_single in ("A", "B")
    assert all(x.kept for x in rep.decisions), rep.to_markdown()
    assert "KEPT" in rep.to_markdown()
    # missing required slice -> undecidable
    rep2 = evaluate_stack(d, {"softmax": res_s}, hard, {}, n_boot=200)
    assert not rep2.decisions[0].kept


def test_null_router_not_kept():
    """Candidates identical in quality everywhere: nothing to route, must not be kept."""
    rng = np.random.default_rng(1)
    d = synth()
    d.cand[:, 1] = d.cand[:, 0] + rng.normal(0, 0.01, d.cand[:, 0].shape).cumsum(axis=1) * 0
    d.cand[:, 1] = np.sort(d.cand[:, 0] + 0.05, axis=1)
    res = walk_forward(d, SoftmaxGate, CFG)
    hard = hard_bucket_walk_forward(d, np.zeros(d.n), CFG)
    ctx = pd.DataFrame(
        {"cold_start_bucket": "a", "games_played_season": np.arange(d.n) % 40, "starter": 1}
    )
    rep = evaluate_stack(d, {"s": res}, hard, standard_slices(ctx), n_boot=200)
    assert not rep.decisions[0].kept


def test_win_target_logit_pool_and_platt():
    rng = np.random.default_rng(2)
    n = 6000
    day = np.repeat(np.arange(n // 20), 20)
    x = rng.integers(0, 2, n)
    z = rng.normal(0, 1, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
    good = 1 / (1 + np.exp(-z))
    bad = np.clip(good + rng.normal(0, 0.25, n), 0.02, 0.98)
    p_a = np.where(x == 1, good, bad)
    p_b = np.where(x == 1, bad, good)
    keys = pd.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(n)],
            "player_id": -1,
            "game_date": pd.Timestamp("2022-11-01") + pd.to_timedelta(day, "D"),
            "season": 2023,
        }
    )
    d = StackData(
        "win",
        ["A", "B"],
        keys,
        y,
        np.column_stack([p_a, p_b]),
        pd.DataFrame({"x": x.astype(float)}),
    )
    cfg = WalkForwardConfig(block_days=30, min_train_rows=1500, calibration="platt")
    res = walk_forward(d, SoftmaxGate, cfg)
    from nba.stack.scoring import row_log_loss

    m = res.scored
    r = row_log_loss(res.pooled[m], y[m]).mean()
    singles = d.cand_scores()[m].mean(axis=0)
    assert r < singles.min()
    assert any(b["calibrated"] for b in res.blocks)
    assert ((res.pooled[m] >= 0) & (res.pooled[m] <= 1)).all()


def test_bh_monotone_and_grid_size():
    q = benjamini_hochberg(np.array([0.01, 0.04, 0.03, 0.5]))
    assert np.all(q >= np.array([0.01, 0.04, 0.03, 0.5]) - 1e-12) and q.max() <= 1
    assert synth(5).cand.shape[2] == N_Q
