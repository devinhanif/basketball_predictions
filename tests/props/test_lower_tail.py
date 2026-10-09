"""Lower-tail candidates (docs/LOWER_TAIL.md): flag-off identity, validity, no season 2025."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date

import numpy as np
import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.eval.lower_tail_eval import (
    CANDIDATES,
    attach_realised,
    coverage_row,
    randomized_pit,
    screen_stat,
)
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    stat_feature_names,
    stat_frame,
)
from nba.props.lower_tail import (
    LOWER_TAIL_KINDS,
    lower_quantiles,
    mixture_quantiles,
)
from tests.props.test_context_residual import ELO, _league


@pytest.fixture(scope="module")
def fitted() -> tuple[pl.DataFrame, pl.DataFrame]:
    gdf, pdf, static = _league(n_days=330)
    feats = attach_realised(build_features(gdf, pdf, static, {}, ELO), gdf, pdf)
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
    assert ContextResidualConfig().lower_tail == "off"
    m0 = _fit(train)
    mean0, q0 = m0.predict(test, CRPS_TAUS)
    assert m0.lt_ is None  # nothing extra kept when off
    # same result with the minutes column present (flag off ignores it)
    assert _fit(train.drop("minutes")).predict(test, CRPS_TAUS)[1].tobytes() == q0.tobytes()
    for kind in LOWER_TAIL_KINDS[1:]:
        m = _fit(train, lower_tail=kind)
        mean, q = m.predict(test, CRPS_TAUS)
        assert mean.tobytes() == mean0.tobytes()  # mean untouched
        assert np.isfinite(q).all() and (q >= 0).all() and (np.diff(q, axis=1) >= 0).all()
        c, s = m.predict_components(test)
        assert np.array_equal(q, m.lower_tail_quantiles(test, c, s, CRPS_TAUS))
        # upper tail of the mondrian candidates is production's (the monotone rearrangement
        # can lift the median region only when a lower bin quantile exceeds the pooled median)
        if kind.startswith("mondrian"):
            hi = CRPS_TAUS > 0.8
            assert np.array_equal(q[:, hi], q0[:, hi])
        # stats outside lower_tail_stats are untouched
        m_reb = ContextResidualModel(
            "reb",
            ContextResidualConfig(lower_tail=kind, lower_tail_stats=("pts",)),
            stat_feature_names("reb"),
        )
        assert m_reb.lt_ is None


def test_guards(fitted: tuple[pl.DataFrame, pl.DataFrame]) -> None:
    train, _ = fitted
    with pytest.raises(ValueError, match="minutes"):
        _fit(train.drop("minutes"), lower_tail="mixture")
    with pytest.raises(ValueError, match="mutually exclusive"):
        _fit(train, lower_tail="mixture", pts_tail="sqrt")


def test_fingerprint_unchanged_when_off() -> None:
    d = asdict(ContextResidualConfig())
    assert d["lower_tail"] == "off"  # forward.py drops it while off
    import inspect

    from nba.props import forward

    src = inspect.getsource(forward)
    assert 'fp["cfg"].pop("lower_tail_stats", None)' in src
    assert 'fp["cfg"].get("lower_tail") == "off"' in src


@given(
    st.integers(min_value=0, max_value=2**31 - 1),
    st.sampled_from(LOWER_TAIL_KINDS),
)
@settings(max_examples=30, deadline=None)
def test_lower_quantiles_valid(seed: int, kind: str) -> None:
    rng = np.random.default_rng(seed)
    nc, n = 1500, 50
    c_cal = rng.uniform(3.0, 25.0, nc)
    z_cal = rng.normal(0, 1, nc)
    m10_cal = rng.uniform(5, 36, nc)
    short_cal = rng.random(nc) < 0.1
    c = rng.uniform(2.0, 30.0, n)
    q = lower_quantiles(
        kind,
        z_cal=z_cal,
        c_cal=c_cal,
        min10_cal=m10_cal,
        short_cal=short_cal,
        c=c,
        s=c * 0.3 + 1.0,
        m_row=c,
        min10_row=rng.uniform(5, 36, n),
        pi=rng.uniform(0.005, 0.5, n),
        w_q=np.sort(rng.uniform(0, 0.8, len(CRPS_TAUS))),
        taus=CRPS_TAUS,
    )
    assert q.shape == (n, len(CRPS_TAUS))
    assert np.isfinite(q).all() and (q >= 0).all() and (np.diff(q, axis=1) >= 0).all()


def test_mixture_limits() -> None:
    rng = np.random.default_rng(1)
    c = rng.uniform(5, 20, 40)
    s = np.full(40, 3.0)
    z = rng.normal(0, 1, 800)
    w = np.sort(rng.uniform(0, 0.5, len(CRPS_TAUS)))
    q0 = mixture_quantiles(c, s, c, np.zeros(40), z, w, CRPS_TAUS)
    prod = np.maximum.accumulate(
        np.clip(c[:, None] + s[:, None] * np.quantile(z, CRPS_TAUS)[None, :], 0, None), axis=1
    )
    assert np.allclose(q0, prod, atol=1e-6)
    # more short mass pushes the lower quantiles down
    q1 = mixture_quantiles(c, s, c, np.full(40, 0.4), z, w, CRPS_TAUS)
    assert (q1[:, 20] <= q0[:, 20] + 1e-9).all()


def test_pit_uniform_for_calibrated_integer_outcomes() -> None:
    rng = np.random.default_rng(0)
    n = 20000
    lam = rng.uniform(1.0, 6.0, n)
    y = rng.poisson(lam).astype(float)
    # exact Poisson quantile grid under the N-0.5 convention
    from scipy.stats import poisson

    q = np.stack([poisson.ppf(CRPS_TAUS, l) for l in lam[:2000]])  # noqa: E741
    pit = randomized_pit(q, y[:2000])
    assert abs(float((pit <= 0.1).mean()) - 0.1) < 0.03
    cov = coverage_row(q, y[:2000], np.ones(2000, bool))
    assert cov["n"] == 2000 and cov["naive_0.1"] > cov["pit_0.1"]  # the lattice inflates naive


def test_screen_runs_and_checks_alignment() -> None:
    rng = np.random.default_rng(0)
    nr, nc = 400, 900
    comp = {
        "row_gid": np.array([f"g{i // 10}" for i in range(nr)]),
        "row_pid": np.arange(nr),
        "row_season": np.where(np.arange(nr) < 200, 2023, 2024),
        "row_y": rng.poisson(5, nr).astype(float),
        "row_c": rng.uniform(3, 8, nr),
        "row_s": rng.uniform(1.5, 3, nr),
        "row_m": rng.uniform(3, 8, nr),
        "row_min10": rng.uniform(10, 34, nr),
        "row_minutes": rng.uniform(5, 34, nr),
        "row_final_margin": rng.uniform(0, 30, nr),
        "row_starter_act": rng.integers(0, 2, nr).astype(float),
        "row_b2b": rng.integers(0, 2, nr).astype(float),
        "row_gap_days": rng.uniform(1, 20, nr),
        "row_blk": np.zeros(nr, int),
        "cal_y": rng.poisson(5, nc).astype(float),
        "cal_c": rng.uniform(3, 8, nc),
        "cal_s": rng.uniform(1.5, 3, nc),
        "cal_min10": rng.uniform(10, 34, nc),
        "cal_minutes": rng.uniform(5, 34, nc),
        "cal_blk": np.zeros(nc, int),
    }
    comp["cal_z"] = (comp["cal_y"] - comp["cal_c"]) / comp["cal_s"]
    pic = {
        "row_gid": comp["row_gid"],
        "row_pid": comp["row_pid"],
        "row_pi": rng.uniform(0.01, 0.2, nr),
        "wq_pts": np.sort(rng.uniform(0, 0.8, (1, len(CRPS_TAUS))), axis=1),
    }
    res = screen_stat("pts", comp, pic, n_boot=50)
    assert set(CANDIDATES) <= set(res["2023"]) and "guard" in res["2024"]["mixture"]
    bad = dict(pic, row_pid=comp["row_pid"][::-1])
    with pytest.raises(ValueError, match="aligned"):
        screen_stat("pts", comp, bad, n_boot=10)
