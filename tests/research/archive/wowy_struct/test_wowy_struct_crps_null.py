"""WOWY_STRUCT G1 conversion check: pure pieces (permutation, clustered null, decision rule)."""

from __future__ import annotations

import numpy as np

from research.archive.wowy_struct import wowy_struct_crps_null as N


def test_perm_player_season_keeps_each_group_multiset() -> None:
    player = np.array([1, 1, 1, 2, 2, 2])
    season = np.array([2020, 2020, 2021, 2021, 2021, 2021])
    v = np.arange(6, dtype=float)
    out = N.perm_player_season(v, player, season, np.random.default_rng(0))
    assert out[2] == 2.0  # singleton group (player 1, 2021) is untouched
    assert sorted(out[:2]) == [0.0, 1.0] and sorted(out[3:]) == [3.0, 4.0, 5.0]
    again = N.perm_player_season(v, player, season, np.random.default_rng(0))
    assert np.array_equal(out, again)  # seeded


def test_clustered_null_recovers_known_sd() -> None:
    rng = np.random.default_rng(1)
    n, k = 4000, 30
    games = np.repeat(np.arange(400), 10)
    shared = rng.normal(0, 1, size=(k, 1)) * 0.002  # per-draw level shift -> sd across draws 0.002
    gain = shared + rng.normal(0, 0.5, size=(k, n)) / np.sqrt(n)
    r = N.clustered_null(gain, games, n_boot=500, seed=0)
    assert abs(r["null_sd"] - 0.002) < 0.0006
    assert abs(r["two_sd"] - 2 * r["null_sd"]) < 1e-12
    assert r["n_rows"] == n and r["n_games"] == 400
    lo, hi = r["two_sd_ci95"]
    assert lo < r["two_sd"] < hi


def test_decide_rule_boundaries() -> None:
    ok = N.decide(
        {
            "pts|leader_out": 0.0050 * 1.19,
            "pts|top25_fixed": 0.0063 * 0.81,
            "ast|leader_out": 0.0016,
        }
    )
    assert ok["all_within"]  # ast is floor-bound: max(0.003, 0.0016) = 0.003
    bad = N.decide(
        {"pts|leader_out": 0.0050 * 1.25, "pts|top25_fixed": 0.0063, "ast|leader_out": 0.0016}
    )
    assert not bad["all_within"]
    assert not bad["cells"]["pts|leader_out"]["within_tolerance"]
    big_ast = N.decide(
        {"pts|leader_out": 0.0050, "pts|top25_fixed": 0.0063, "ast|leader_out": 0.0040}
    )
    assert not big_ast["cells"]["ast|leader_out"]["within_tolerance"]
