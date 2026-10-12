from __future__ import annotations

import numpy as np

from research.eval import pplay_into_props_live as P


def test_row_delta_zero_when_arms_equal_and_signed_when_one_is_right() -> None:
    qi = np.tile(np.arange(2, 21), (4, 1)).astype(np.int16)
    y = np.array([0.0, 0.0, 10.0, 10.0])
    p = np.full(4, 0.5)
    assert np.allclose(P.row_delta(qi, p, p, y), 0.0)
    good = np.array([0.02, 0.02, 0.98, 0.98])  # right about who plays
    bad = np.array([0.98, 0.98, 0.02, 0.02])
    assert (P.row_delta(qi, good, bad, y) < 0).all()


def test_prev_row_p_is_strictly_earlier_same_player() -> None:
    player = np.array([1, 1, 1, 2, 2])
    key = np.array([3, 1, 2, 5, 4])
    p = np.array([0.3, 0.1, 0.2, 0.9, 0.8])
    out = P.prev_row_p(player, key, p)
    assert out[1] == 0.1  # player 1's first row keeps its own p
    assert out[2] == 0.1 and out[0] == 0.2  # previous in key order
    assert out[4] == 0.8 and out[3] == 0.8


def test_shuffle_keeps_day_multiset_and_is_not_inert() -> None:
    day = np.repeat(np.arange(5), 20)
    p = np.random.default_rng(1).uniform(size=100)
    s = P.shuffle_p_within_day(p, day, 0)
    assert not np.array_equal(s, p)
    for d in range(5):
        assert np.allclose(np.sort(s[day == d]), np.sort(p[day == d]))


def test_n_required_and_week() -> None:
    n = P.n_required(0.1, 1.0, 0.005)
    assert abs(P.Z * 0.1 / np.sqrt(n) - 0.005) < 1e-4
    assert P.week_reached(0) == P.BURN_WEEKS
    assert P.week_reached(int(P.ROWS_PER_GAME * P.GAMES_PER_WEEK)) == P.BURN_WEEKS + 1


def test_cluster_stats_deff_near_one_for_independent_games() -> None:
    rng = np.random.default_rng(0)
    gid = np.repeat(np.arange(500), 20)
    cs = P.cluster_stats(rng.normal(size=gid.size), gid)
    assert 0.8 < cs["deff"] < 1.25
