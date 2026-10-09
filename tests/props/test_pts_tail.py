"""pts upper-tail candidates (docs/PTS_TAIL.md): flag-off identity, validity, no season 2025."""

from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.eval.pts_tail_eval import CANDIDATES, arm_quantiles, candidate_screen, p_ge
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    stat_feature_names,
    stat_frame,
)
from nba.props.pts_tail import PTS_TAIL_KINDS, tail_quantiles
from tests.props.test_context_residual import ELO, _league


@pytest.fixture(scope="module")
def fitted() -> tuple[pl.DataFrame, pl.DataFrame]:
    gdf, pdf, static = _league(n_days=330)
    feats = build_features(gdf, pdf, static, {}, ELO)
    fr = stat_frame(feats, "pts").sort(["game_date", "game_id", "player_id"])
    cut = date(2023, 6, 1)
    return fr.filter(pl.col("game_date") < cut), fr.filter(pl.col("game_date") >= cut)


def _fit(train: pl.DataFrame, **kw: object) -> ContextResidualModel:
    cfg = ContextResidualConfig(n_estimators=30, min_child_samples=30, min_cal_rows=300, **kw)  # type: ignore[arg-type]
    return ContextResidualModel("pts", cfg, stat_feature_names("pts")).fit(train)


def test_flag_off_byte_identical_and_kinds_valid(
    fitted: tuple[pl.DataFrame, pl.DataFrame],
) -> None:
    train, test = fitted
    assert ContextResidualConfig().pts_tail == "off"
    m0 = _fit(train)
    mean0, q0 = m0.predict(test, CRPS_TAUS)
    assert m0.cal_tail_ is None  # nothing extra kept when off
    assert _fit(train).predict(test, CRPS_TAUS)[1].tobytes() == q0.tobytes()
    for kind in CANDIDATES:
        m = _fit(train, pts_tail=kind)
        mean, q = m.predict(test, CRPS_TAUS)
        assert mean.tobytes() == mean0.tobytes()  # mean untouched
        assert np.isfinite(q).all() and (q >= 0).all() and (np.diff(q, axis=1) >= 0).all()
        c, s = m.predict_components(test)
        assert np.array_equal(q, m.tail_quantiles(kind, c, s, CRPS_TAUS))
        # other stats are never touched
        m_reb = ContextResidualModel(
            "reb", ContextResidualConfig(pts_tail=kind), stat_feature_names("reb")
        )
        assert m_reb.cal_tail_ is None


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.sampled_from(PTS_TAIL_KINDS),
    st.integers(min_value=1, max_value=60),
)
@settings(max_examples=40, deadline=None)
def test_tail_quantiles_valid_and_p_ge_non_increasing(seed: int, kind: str, n: int) -> None:
    rng = np.random.default_rng(seed)
    nc = 1500
    c_cal = rng.uniform(4.0, 30.0, nc)
    s_cal = c_cal * 0.3 + 1.0
    y_cal = np.maximum(c_cal + s_cal * rng.gamma(2.0, 0.5, nc) - s_cal, 0.0)
    z_cal = (y_cal - c_cal) / s_cal
    c = rng.uniform(2.0, 35.0, 60)
    s = c * 0.3 + 1.0
    q = tail_quantiles(
        kind, y_cal=y_cal, c_cal=c_cal, s_cal=s_cal, z_cal=z_cal, c=c, s=s, taus=CRPS_TAUS
    )
    assert np.isfinite(q).all() and (q >= 0).all() and (np.diff(q, axis=1) >= 0).all()
    p = [float(p_ge(q, k).mean()) for k in range(1, 50)]
    assert all(a >= b - 1e-12 for a, b in zip(p, p[1:], strict=False))
    assert ((p_ge(q, n) >= 0) & (p_ge(q, n) <= 1)).all()


def test_eval_refuses_2025_and_arms_run() -> None:
    rng = np.random.default_rng(0)
    nr, nc = 200, 800
    comp = {
        "row_gid": np.array([f"g{i // 10}" for i in range(nr)]),
        "row_pid": np.arange(nr),
        "row_season": np.where(np.arange(nr) < 100, 2023, 2024),
        "row_y": rng.poisson(12, nr).astype(float),
        "row_c": rng.uniform(6, 20, nr),
        "row_s": rng.uniform(3, 6, nr),
        "row_blk": np.zeros(nr, int),
        "cal_y": rng.poisson(12, nc).astype(float),
        "cal_c": rng.uniform(6, 20, nc),
        "cal_s": rng.uniform(3, 6, nc),
        "cal_blk": np.zeros(nc, int),
    }
    comp["cal_z"] = (comp["cal_y"] - comp["cal_c"]) / comp["cal_s"]
    q = arm_quantiles("gamma_tail", comp, CRPS_TAUS, np.ones(nr, bool))
    assert q.shape == (nr, len(CRPS_TAUS))
    res = candidate_screen(comp, n_boot=50)
    assert set(CANDIDATES) <= set(res["2023"]) and "selected_on_2023" in res
    with pytest.raises(ValueError, match="2025"):
        from nba.eval.pts_tail_eval import _season_mask

        _season_mask(comp, 2025)
