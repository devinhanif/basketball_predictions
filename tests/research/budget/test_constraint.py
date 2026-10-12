"""TEAM_BUDGET constraint: the budget is met exactly, caps hold, order is preserved."""

from __future__ import annotations

import numpy as np
import pytest

from research.budget import constraint as C


def test_rake_sums_to_budget_and_preserves_order() -> None:
    e = np.array([32.0, 30.0, 28.0, 25.0, 20.0, 15.0, 10.0, 5.0, 2.0, 0.5])
    out = C.rake_to_budget(e, 240.0)
    assert abs(out.sum() - 240.0) < 1e-9
    assert np.all(np.argsort(-out, kind="stable") == np.argsort(-e, kind="stable"))
    # proportional when no cap binds
    assert np.allclose(out / e, out[0] / e[0])


def test_rake_respects_caps_and_still_sums() -> None:
    e = np.array([40.0, 40.0, 40.0, 40.0, 40.0, 5.0, 5.0, 5.0])
    out = C.rake_to_budget(e, 240.0)
    assert out.max() <= 48.0 + 1e-9
    assert abs(out.sum() - 240.0) < 1e-9


def test_rake_infeasible_and_bad_inputs_raise() -> None:
    with pytest.raises(ValueError):
        C.rake_to_budget(np.array([10.0, 10.0]), 240.0)  # caps sum to 96
    with pytest.raises(ValueError):
        C.rake_to_budget(np.array([-1.0, 10.0]), 10.0)
    with pytest.raises(ValueError):
        C.rake_to_budget(np.array([np.nan, 10.0]), 10.0)


def test_rake_all_zero_and_zero_budget() -> None:
    assert abs(C.rake_to_budget(np.zeros(8), 240.0).sum() - 240.0) < 1e-9
    assert C.rake_to_budget(np.array([3.0, 4.0]), 0.0).sum() == 0.0
    assert abs(C.rake_to_budget(np.zeros(3), 90.0, cap=np.inf).sum() - 90.0) < 1e-9


def test_rake_is_idempotent() -> None:
    e = np.array([34.0, 31.0, 22.0, 18.0, 12.0, 9.0, 6.0, 3.0])
    once = C.rake_to_budget(e, 240.0)
    assert np.allclose(C.rake_to_budget(once, 240.0), once)


def test_budget_formula() -> None:
    assert float(C.team_budget(0.0)) == 240.0
    assert float(C.team_budget(1.0)) == 265.0
    assert abs(float(C.team_budget(0.06)) - 241.5) < 1e-12
    with pytest.raises(ValueError):
        C.team_budget(1.5)


def test_rake_groups_per_team_game() -> None:
    e = np.array([30.0, 20.0, 10.0, 40.0, 30.0, 20.0, 10.0, 5.0, 5.0, 5.0, 5.0, 5.0])
    grp = np.array([0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    out = C.rake_groups(e, grp, np.array([100.0, 240.0]))
    assert abs(out[grp == 0].sum() - 100.0) < 1e-9
    assert abs(out[grp == 1].sum() - 240.0) < 1e-9


def test_joint_draws_sum_to_budget_exactly_and_match_means() -> None:
    rng = np.random.default_rng(0)
    e = np.array([30.0, 28.0, 25.0, 20.0, 15.0, 10.0, 6.0, 0.0])
    d = C.joint_minutes_draws(e, 241.5, kappa=60.0, n_draws=4000, rng=rng)
    assert np.allclose(d.sum(axis=1), 241.5)
    assert np.all(d[:, -1] == 0.0)
    raked = C.rake_to_budget(e, 241.5, cap=np.inf)
    assert np.allclose(d.mean(axis=0), raked, atol=0.5)
