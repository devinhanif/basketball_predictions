# ruff: noqa: E501
"""G1 and the slice guard exactly as rule 6(d) and 6(e) state them (fixed row order, text tolerances)."""

from __future__ import annotations

import numpy as np

from nba.eval.lower_tail_eval import randomized_pit as eval_pit
from nba.props import combo_g1 as G


def _world(n: int = 4000, seed: int = 0):
    rng = np.random.default_rng(seed)
    mu = rng.uniform(0.5, 6.0, n)
    taus = (np.arange(1, 200) - 0.5) / 199
    from scipy import stats

    q = stats.poisson.ppf(taus[None, :], mu[:, None])
    y = rng.poisson(mu)
    gdate = rng.integers(0, 150, n)
    gid = (gdate * 10 + rng.integers(0, 3, n)).astype(str)
    pid = rng.permutation(n)
    return q, y, gdate, gid, pid


def test_pit_matches_the_evaluation_module_on_sorted_rows() -> None:
    q, y, gdate, gid, pid = _world()
    o = G.sort_order(gdate, gid, pid)
    assert np.array_equal(G.randomized_pit(q[o], y[o], 0), eval_pit(q[o], y[o].astype(float), 0))


def test_g1_is_independent_of_the_input_row_order() -> None:
    q, y, gdate, gid, pid = _world()
    a = G.g1(q, y, gdate, gid, pid)
    p = np.random.default_rng(3).permutation(len(y))
    b = G.g1(q[p], y[p], gdate[p], gid[p], pid[p])
    assert a == b  # the order is fixed by the sort, not by the caller
    assert a["ok"] and a["enough_rows"]


def test_g1_fails_a_miscalibrated_forecast_and_a_short_log() -> None:
    q, y, gdate, gid, pid = _world()
    bad = G.g1(q + 2.0, y, gdate, gid, pid)  # grid shifted up: too high
    assert not bad["ok"]
    short = G.g1(q[:500], y[:500], gdate[:500], gid[:500], pid[:500])
    assert not short["ok"] and not short["enough_rows"]


def test_slice_guard_counts_only_slices_with_enough_rows() -> None:
    d = np.zeros(1000)
    d[:400] = 0.05
    masks = {"a": np.arange(1000) < 400, "b": np.arange(1000) >= 400, "tiny": np.arange(1000) < 100}
    out = G.slice_guard(d, masks, floor=0.02)
    assert not out["ok"] and out["slices"]["a"]["violates"] and not out["slices"]["b"]["violates"]
    out2 = G.slice_guard(d, {"tiny": masks["tiny"]}, floor=0.02)
    assert out2["ok"] and not out2["slices"]["tiny"]["counted"]
