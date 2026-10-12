from __future__ import annotations

import numpy as np

from nba.props.context_residual import CRPS_TAUS
from research.eval import minutes_spread_eda as M
from research.eval import minutes_spread_render as R


def test_scale_identity_and_lower_only() -> None:
    g = np.sort(np.random.default_rng(0).normal(10, 3, size=(5, 199)), axis=1)
    assert np.allclose(M.scale(g, 1.0, "S", -1e9), g)
    low = M.scale(g, 1.3, "L", -1e9)
    assert np.allclose(low[:, M.MED :], g[:, M.MED :])
    assert (low[:, : M.MED] <= g[:, : M.MED] + 1e-12).all()


def test_trunc_solve_roundtrip() -> None:
    from scipy import stats

    mu, sg = np.array([20.0, 30.0]), np.array([6.0, 8.0])
    q10 = stats.truncnorm.ppf(CRPS_TAUS[M.I10], (0 - mu) / sg, (48 - mu) / sg, loc=mu, scale=sg)
    q90 = stats.truncnorm.ppf(CRPS_TAUS[M.I90], (0 - mu) / sg, (48 - mu) / sg, loc=mu, scale=sg)
    m2, s2 = M.trunc_solve(q10, q90)
    assert np.allclose(m2, mu, atol=0.05) and np.allclose(s2, sg, atol=0.05)


def test_boot_ci_covers_mean_and_power_monotone() -> None:
    gid = np.repeat(np.arange(200).astype(str), 5)
    x = np.random.default_rng(1).normal(size=len(gid))
    p, lo, hi, _ = M.Boot(gid).ratio(x, np.ones(len(x), dtype=bool))
    assert lo < p < hi
    assert R.power(-0.004, 0.0005, -0.002) > R.power(-0.001, 0.0005, -0.002)
