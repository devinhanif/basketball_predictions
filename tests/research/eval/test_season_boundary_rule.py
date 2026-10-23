# ruff: noqa: E501
"""SEASON_BOUNDARY pure functions: weights, as-of states (naive recomputation, planted future), league table."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.eval import season_boundary_rule as R


def _toy() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    pid = np.repeat([1, 2], 60)
    season = np.tile(np.repeat([2022, 2023], 30), 2)
    x = rng.uniform(0, 30, size=(120, 2))
    return pid, season, x


def test_weights() -> None:
    assert R.w_this(np.array([0, 10]), 10)[0] == 0.0
    assert R.w_this(np.array([10]), 10)[0] == 0.5
    assert abs(R.a0_equivalent_weight(np.array([10]))[0] - 0.5) < 1e-12
    assert R.FLOORS == {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}


def test_states_match_naive_and_ignore_future() -> None:
    pid, season, x = _toy()
    st = R.decayed_states(pid, season, x)
    i = 105  # player 2, season 2023, g = 15, 45 prior games
    assert st["g"][i] == 15 and st["n_prior"][i] == 45
    prior = x[60:i]
    w = 0.5 ** (np.arange(len(prior))[::-1] / 10.0)
    assert np.allclose(st["m10"][i], (w[:, None] * prior).sum(0) / w.sum())
    this = x[90:i]
    wt = 0.5 ** (np.arange(len(this))[::-1] / 10.0)
    assert np.allclose(st["m_this"][i], (wt[:, None] * this).sum(0) / wt.sum())
    assert np.isnan(st["L0"][60:90]).all() and np.isfinite(st["L0"][i]).all()
    assert np.isnan(st["L0"][:30]).all()  # first season: no prior level
    x2 = x.copy()
    x2[i:] = x2[i:] * 50 + 3  # plant a wild future from row i on
    st2 = R.decayed_states(pid, season, x2)
    for k in ("m_this", "m10", "L0"):
        assert np.array_equal(st[k][: i + 1], st2[k][: i + 1], equal_nan=True)


def test_a1_base_fallback_and_limits() -> None:
    pid, season, x = _toy()
    st = R.decayed_states(pid, season, x)
    b = R.a1_base(st, 10.0)
    assert np.array_equal(b[:30], st["m10"][:30], equal_nan=True)  # no L0: production value
    i = 90  # g = 0: all weight on the prior level
    assert np.allclose(b[i], st["L0"][i])
    assert np.allclose(R.a1_base(st, 10.0, np.log(1.1))[i], 1.1 * st["L0"][i])


def test_league_asof_uses_only_earlier_dates() -> None:
    d = pd.DataFrame(
        {
            "game_id": ["a", "a", "b", "b", "c", "c"],
            "game_date": pd.to_datetime(
                ["2023-10-24"] * 2 + ["2023-10-25"] * 2 + ["2023-10-26"] * 2
            ),
            "minutes": [240.0] * 6,
            "pts": [100.0, 100.0, 100.0, 100.0, 5000.0, 5000.0],
        }
    )
    prev = {"pts": 36.0 * 100 / 240.0}
    t = R.league_asof(d, ["pts"], prev)
    assert t["f_pts"].iloc[0] == 1.0 and abs(t["f_pts"].iloc[1] - 1.0) < 1e-12
    d2 = d.copy()
    d2.loc[d2.game_id == "c", "pts"] = 1.0
    assert np.array_equal(
        R.league_asof(d2, ["pts"], prev).iloc[:3].to_numpy(), t.iloc[:3].to_numpy()
    )
