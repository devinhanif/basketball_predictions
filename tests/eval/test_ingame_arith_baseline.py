"""Tests for the A1 arithmetic in-game baseline (draft rule INGAME_LINE_CAL_FWD)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest
from scipy.stats import nbinom

from nba.eval.ingame_arith_baseline import (
    VMR,
    CheckpointState,
    expected_remaining_minutes,
    expected_remaining_stat,
    p_at_least,
)

BASE = CheckpointState(
    stat="pts",
    k=25,
    cur=14,
    minutes=24.0,
    e=30.0,
    margin=4,
    pregame_mean=22.0,
    pregame_minutes=34.0,
)


def test_hand_computed_value() -> None:
    s = BASE
    r0 = 22.0 / 34.0
    rate = (14 + 10 * r0) / (24 + 10)
    share = (24 + 6 * 34 / 48) / (30 + 6)
    rem_min = min(share * (6 + 12), 18)
    lam = rate * rem_min
    assert expected_remaining_minutes(s) == pytest.approx(rem_min, abs=1e-12)
    assert expected_remaining_stat(s) == pytest.approx(lam, abs=1e-12)
    want = nbinom.sf(11 - 1, lam / 1.5, 1 / 2.5)
    assert p_at_least(s) == pytest.approx(want, abs=1e-12)


def test_negbin_moments_match_the_rule() -> None:
    lam, v = 7.3, VMR["reb"]
    n, p = lam / (v - 1), 1 / v
    assert nbinom.mean(n, p) == pytest.approx(lam)
    assert nbinom.var(n, p) == pytest.approx(v * lam)


def test_already_resolved_and_end_of_game() -> None:
    assert p_at_least(replace(BASE, cur=25)) == 1.0
    end = replace(BASE, e=48.0)
    assert expected_remaining_minutes(end) == 0.0
    assert p_at_least(end) == 0.0


def test_q4_blowout_halves_only_q4_minutes() -> None:
    q3 = replace(BASE, e=30.0)
    blow = replace(q3, margin=-25)
    share = (24 + 6 * 34 / 48) / 36
    assert expected_remaining_minutes(blow) == pytest.approx(share * (6 + 0.5 * 12))
    assert expected_remaining_minutes(q3) == pytest.approx(share * 18)
    # through Q3 with no Q4 left to cut: no effect at e < 36 on the non-Q4 part only
    assert expected_remaining_minutes(replace(blow, e=40.0)) < expected_remaining_minutes(
        replace(q3, e=40.0)
    )


def test_monotone_in_strike_current_and_rate() -> None:
    ps = [p_at_least(replace(BASE, k=k)) for k in range(15, 41)]
    assert all(a >= b for a, b in zip(ps, ps[1:], strict=False))
    assert p_at_least(replace(BASE, cur=18)) > p_at_least(BASE)
    assert p_at_least(replace(BASE, pregame_mean=30.0)) > p_at_least(BASE)


def test_probability_in_unit_interval_on_a_grid() -> None:
    rng = np.random.default_rng(0)
    for _ in range(500):
        s = CheckpointState(
            stat=str(rng.choice(list(VMR))),
            k=int(rng.integers(1, 40)),
            cur=int(rng.integers(0, 35)),
            minutes=float(rng.integers(0, 40)),
            e=float(rng.uniform(5, 47)),
            margin=int(rng.integers(-35, 36)),
            pregame_mean=float(rng.uniform(0, 30)),
            pregame_minutes=float(rng.uniform(0, 40)),
        )
        p = p_at_least(s)
        assert 0.0 <= p <= 1.0 and not math.isnan(p)


def test_planted_future_changes_the_value() -> None:
    later = replace(BASE, cur=BASE.cur + 6, minutes=BASE.minutes + 4, e=BASE.e + 5)
    assert p_at_least(later) != p_at_least(BASE)


def test_overtime_is_refused() -> None:
    with pytest.raises(ValueError):
        expected_remaining_minutes(replace(BASE, e=50.0))
