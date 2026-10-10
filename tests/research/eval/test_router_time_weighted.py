"""ROUTER_TIME_WEIGHTED (frozen sha256 bb633f7a): routing, cells, candidates, as-of discipline."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl

from nba.props.context_residual import CRPS_TAUS, player_states
from nba.props.forward import recency_weighted_dist
from research.eval import router_time_weighted as rt
from research.eval.f11_lineup_context import frozen_sha256

DOC = Path(__file__).resolve().parents[3] / "docs/prereg/ROUTER_TIME_WEIGHTED.md"


def test_frozen_prefix_matches_doc() -> None:
    assert frozen_sha256(DOC) == rt.FROZEN_FULL
    assert rt.FROZEN_FULL.startswith(rt.FROZEN_PREFIX)


def test_merge_groups_up_then_down() -> None:
    assert rt.merge_groups([500, 400, 300, 900]) == [[0], [1], [2], [3]]
    assert rt.merge_groups([100, 100, 100, 100]) == [[0, 1, 2, 3]]
    g = rt.merge_groups([1500, 91, 47, 0])
    assert sorted(i for grp in g for i in grp) == [0, 1, 2, 3]


def test_tier_edges() -> None:
    t = rt.tier_of(np.array([9.99, 10.0, 16.99, 17.0, 23.99, 24.0, 40.0]))
    assert t.tolist() == [0, 1, 1, 2, 2, 3, 3]


def _toy(n: int = 600) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(0)
    days = np.sort(rng.integers(19000, 19120, n))
    cell = rng.integers(0, 2, n)
    crps = rng.random((n, 5)) + np.array([0.5, 0.0, 0.2, 0.3, 0.4])
    return crps, days, cell


def test_trailing_ignores_block_and_future() -> None:
    crps, days, cell = _toy()
    start = 19060
    cnt0, avg0 = rt.trailing(crps, days, cell, start, 2)
    c2 = np.where((days >= start)[:, None], 1e6, crps)
    d2 = np.where(days >= start, days + 1, days)
    cnt1, avg1 = rt.trailing(c2, d2, cell, start, 2)
    assert np.array_equal(cnt0, cnt1) and np.array_equal(avg0, avg1)
    c3 = np.where((days < start)[:, None], crps + 1.0, crps)
    assert not np.array_equal(rt.trailing(c3, days, cell, start, 2)[1], avg0)


def test_trailing_weights_halve_at_60_days() -> None:
    crps = np.array([[1.0, 0.0], [3.0, 0.0]])
    days = np.array([100, 40])
    cnt, avg = rt.trailing(crps, days, np.zeros(2, dtype=int), 160, 1)
    # ages 60 and 120 -> weights 0.5 and 0.25
    assert np.isclose(avg[0, 0], (0.5 * 1.0 + 0.25 * 3.0) / 0.75)
    assert cnt[0] == 2


def test_route_params() -> None:
    cnt = np.array([299.0, 300.0])
    avg = np.array([[1.0, 0.5, 2, 2, 2], [1.0, 0.5, 2, 2, 2]])
    pick, w = rt.route_params(cnt, avg)
    assert pick.tolist() == [0, 1]
    assert w[0].tolist() == [1, 0, 0, 0, 0]
    assert np.isclose(w[1].sum(), 1.0) and w[1].argmax() == 1
    # tau = 0.05: a 0.5 gap is e^-10
    assert np.isclose(w[1][0] / w[1][1], np.exp(-0.5 / rt.TAU))


def test_ties_go_to_c0() -> None:
    pick, _ = rt.route_params(np.array([1000.0]), np.ones((1, 5)))
    assert pick.tolist() == [0]


def test_rolling_stats_brute_force_and_asof() -> None:
    rng = np.random.default_rng(1)
    gids = [f"g{i:03d}" for i in range(40)]
    ordmap = {g: i for i, g in enumerate(gids)}
    played = pl.DataFrame(
        {
            "game_id": gids,
            "player_id": [7] * 40,
            "pts": rng.integers(0, 30, 40).astype(float),
        }
    )
    rows = pl.DataFrame({"game_id": ["g012", "g003"], "player_id": [7, 7]})
    mean, var, win = rt.rolling_stats(played, rows, ordmap, "pts", 10)
    x = played["pts"].to_numpy()
    assert np.isclose(mean[0], x[2:12].mean()) and np.isclose(var[0], x[2:12].var(ddof=1))
    assert win.tolist() == [10, 3]
    # changing the row's own and later games changes nothing
    played2 = played.with_columns(
        pl.when(pl.col("game_id") >= "g012").then(99.0).otherwise(pl.col("pts")).alias("pts")
    )
    m2, v2, _ = rt.rolling_stats(played2, rows, ordmap, "pts", 10)
    assert m2[0] == mean[0] and v2[0] == var[0]


def test_c1_equals_recency_weighted_dist() -> None:
    rng = np.random.default_rng(2)
    n = 60
    played = pl.DataFrame(
        {
            "player_id": [1] * n,
            "team_id": [1] * n,
            "season": [2023] * n,
            "game_date": [dt.date(2023, 10, 1) + dt.timedelta(days=i) for i in range(n)],
            "pts": rng.integers(0, 35, n).astype(float),
            "reb": rng.integers(0, 12, n).astype(float),
            "ast": rng.integers(0, 9, n).astype(float),
            "fg3m": rng.integers(0, 6, n).astype(float),
            "minutes": rng.uniform(10, 36, n),
            "starter": [True] * n,
        }
    )
    st = player_states(played)
    from nba.props.context_residual import _sel

    for k in (5, 17, 59):
        for s_ in ("pts", "reb", "ast", "fg3m"):
            vals = played[s_].to_numpy()[:k]
            d = recency_weighted_dist(vals, s_, rt.RECENCY_HALFLIFE_GAMES)
            m = _sel(st["pre"], 10.0, s_)[k]
            var = max(_sel(st["pre"], 10.0, f"{s_}2")[k] - m**2, 0.0)
            sd = (var * k / (k - 1)) ** 0.5
            assert np.isclose(m, d.mean_)
            if sd > 0:
                assert np.isclose(sd, d.std)
    assert st["n_prior"][17] == 17


def test_grids_are_integer_and_monotone() -> None:
    g = rt.grid_normal(np.array([5.2, 20.0]), np.array([2.0, 6.0]))
    assert g.dtype == np.int16 and (np.diff(g, axis=1) >= 0).all() and g.shape[1] == len(CRPS_TAUS)
    nb = rt.grid_nbinom(np.array([0.0, 4.0]), np.array([0.0, 9.0]))
    assert (np.diff(nb, axis=1) >= 0).all() and nb.min() >= 0


def test_cell_defs_merge_cold_cells() -> None:
    n = 3000
    rng = np.random.default_rng(3)
    rows = pl.DataFrame(
        {
            "season": [2023] * n,
            "n_prior": rng.integers(5, 60, n),
            "m10_pts": rng.uniform(0, 30, n),
        }
    )
    d = rt.cell_defs(rows)
    assert d["ok"] and d["cell"].min() == 0 and len(d["labels"]) >= 2
