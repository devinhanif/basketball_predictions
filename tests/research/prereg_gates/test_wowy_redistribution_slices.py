"""WOWY_REDISTRIBUTION follow-up slices (DRAFT): the gain helper and the slice cut-offs."""

from __future__ import annotations

import numpy as np

from nba.props.context_residual import CRPS_TAUS
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_slices as S


def test_expected_gain_var_mean_matches_common_helper() -> None:
    rng = np.random.default_rng(0)
    n = 12
    q_old = np.sort(rng.integers(0, 30, size=(n, len(CRPS_TAUS))), axis=1)
    q_new = np.sort(q_old + rng.integers(0, 3, size=(n, 1)), axis=1)
    mean, var = S.expected_gain_var(q_old, q_new)
    assert np.allclose(mean, C.expected_crps_gain(q_old, q_new))
    assert (var >= 0).all()


def test_slice_masks_cut_within_base_and_nest() -> None:
    rng = np.random.default_rng(1)
    n = 1000
    cols = {
        c: rng.normal(size=n) for c in ("wo_d_pts", "wo_d_reb", "wo_d_ast", "wo_d_fg3m", "wo_d_min")
    }
    base = np.zeros(n, dtype=bool)
    base[:800] = True
    m = S.slice_masks(cols, base)
    t25, t10 = m["top25|wo_d_pts"], m["top10|wo_d_pts"]
    assert not (t25 & ~base).any() and (t10 & ~t25).sum() == 0
    assert abs(t25.sum() / base.sum() - 0.25) < 0.01 and abs(t10.sum() / base.sum() - 0.10) < 0.01
