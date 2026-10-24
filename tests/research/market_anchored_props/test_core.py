# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS core on synthetic arrays (no real data, no outcome of any real leg)."""

from __future__ import annotations

import numpy as np
import pytest

from research.eval import market_anchored_props_core as core


def synth(n_per_month: int = 400, months: int = 8, seed: int = 0) -> dict[str, np.ndarray]:
    """Legs over 2023-10 .. 2024-05 ISO-ish months, four stats, with the columns the arms read."""
    rng = np.random.default_rng(seed)
    n = n_per_month * months
    month_idx = np.repeat(np.arange(months), n_per_month)
    base = np.datetime64("2023-10-01", "D")
    day = base + (month_idx * 30 + rng.integers(0, 28, n)).astype("timedelta64[D]")
    order = np.argsort(day)
    day = day[order]
    m = rng.normal(0.0, 0.5, n)
    a = m + rng.normal(0.0, 0.4, n)
    A = {
        "stat_i": rng.integers(0, 4, n),
        "m": m,
        "a": a,
        "delta": np.clip(a - m, -core.DCLIP, core.DCLIP),
        "hi": (rng.random(n) < 0.2).astype(float),
        "u1": np.clip(rng.normal(0.0, 0.1, n), -1, 1),
        "miss_u1": (rng.random(n) < 0.05).astype(float),
        "disp10": np.abs(rng.normal(0.5, 0.2, n)),
        "bl": np.abs(rng.normal(0.8, 0.4, n)),
        "bench": (rng.random(n) < 0.2).astype(float),
        "tmout": (rng.random(n) < 0.3).astype(float),
        "date": day,
        "month": day.astype("datetime64[M]"),
        "game_idx": np.arange(n) // 6,
        "ps_idx": rng.integers(0, 150, n),
        "week_idx": (day.astype("datetime64[W]").astype(int)),
    }
    A["season"] = np.where(day < np.datetime64("2024-07-01"), 2023, 2024).astype(int)
    # force a two-season layout: second half of the months is "2024"
    A["season"] = np.where(month_idx[order] >= months // 2, 2024, 2023)
    return A


def test_fit_logit_recovers_coefficients() -> None:
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(20000), rng.normal(size=20000), rng.normal(size=20000)])
    beta = np.array([-0.3, 1.2, 0.0])
    y = (rng.random(20000) < core.sigmoid(X @ beta)).astype(float)
    got = core.fit_logit(
        X, y, np.ones(20000), np.array([0.0, 1.0, 0.0]), lam=1e-6, free=np.array([0])
    )
    assert np.allclose(got, beta, atol=0.06)


def test_ridge_centres_on_start_point() -> None:
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(50), rng.normal(size=50)])
    y = (rng.random(50) < 0.5).astype(float)
    start = np.array([0.0, 1.0])
    heavy = core.fit_logit(X, y, np.ones(50), start, lam=1e9, free=np.array([0]))
    assert abs(heavy[1] - 1.0) < 1e-3  # a huge ridge pins the slope at the market identity


def test_oracle_kl_zero_when_same_and_positive_otherwise() -> None:
    p = np.linspace(0.2, 0.8, 50)
    assert core.oracle_kl(p, p) == pytest.approx(0.0, abs=1e-12)
    assert core.oracle_kl(np.clip(p + 0.05, 0, 1), p) > 0


def test_derangements_have_no_fixed_points_inside_groups() -> None:
    A = synth()
    perms = core.derangements(A, 3)
    key = A["date"].astype("datetime64[D]").astype(np.int64) * 8 + A["stat_i"]
    for perm in perms:
        assert (perm != np.arange(len(perm))).mean() > 0.97
        # a placebo draws only from the same (date, stat) group, or from the pooled singletons of the same stat
        same_group = key[perm] == key
        same_stat = A["stat_i"][perm] == A["stat_i"]
        assert same_stat.all()
        assert same_group.mean() > 0.95


def test_walk_forward_planted_future_and_nesting() -> None:
    A = synth(seed=3)
    rng = np.random.default_rng(4)
    y = (rng.random(len(A["m"])) < core.sigmoid(A["m"])).astype(float)
    base = core.walk_forward(A, y, 2024, (2023, 2024), n_min=150)
    scored = base["scored"]
    assert scored.sum() > 500
    month = np.unique(A["month"][scored])[1]
    start = month.astype("datetime64[D]")
    y1 = y.copy()
    y1[A["date"] >= start] = 1.0 - y1[A["date"] >= start]
    flip = core.walk_forward(A, y1, 2024, (2023, 2024), n_min=150)
    rows = scored & (A["month"] == month)
    assert rows.sum() > 50
    assert np.array_equal(
        base["lp_mm"][rows], flip["lp_mm"][rows]
    )  # the future never moves a prediction
    assert np.array_equal(base["lp_mr"][rows], flip["lp_mr"][rows])
    y2 = y.copy()
    y2[A["date"] < start] = 1.0 - y2[A["date"] < start]
    moved = core.walk_forward(A, y2, 2024, (2023, 2024), n_min=150)
    assert np.nanmax(np.abs(base["lp_mm"][rows] - moved["lp_mm"][rows])) > 0.01


def test_market_arm_is_the_identity_and_null_world_costs_little() -> None:
    A = synth(n_per_month=1500, seed=5)
    y = core.draw_labels(A["m"], 6)
    wf = core.walk_forward(A, y, 2024, (2023, 2024), n_min=300)
    s = wf["scored"]
    assert np.allclose(wf["ll_m"][s], core.ll_vec(A["m"][s], y[s]))
    assert (
        abs(float(np.mean(wf["ll_mm"][s] - wf["ll_m"][s]))) < 0.004
    )  # overfit cost with ~36 free parameters


def test_planted_model_information_is_found_in_c_mod_not_c_mkt() -> None:
    A = synth(n_per_month=2500, seed=7)
    eta = core.world_eta(A, "mod", 0.4)
    y = core.draw_labels(eta, 8)
    wf = core.walk_forward(A, y, 2024, (2023, 2024), n_min=300)
    s = wf["scored"]
    c_mod = float(np.mean(wf["ll_mm"][s] - wf["ll_mr"][s]))
    c_mkt = float(np.mean(wf["ll_mr"][s] - wf["ll_m"][s]))
    assert c_mod < -0.002
    assert abs(c_mkt) < 0.002


def test_planted_market_miscalibration_is_found_in_c_mkt_not_c_mod() -> None:
    A = synth(n_per_month=2500, seed=9)
    eta = -0.15 + 0.7 * A["m"]
    y = core.draw_labels(eta, 10)
    wf = core.walk_forward(A, y, 2024, (2023, 2024), n_min=300)
    s = wf["scored"]
    assert float(np.mean(wf["ll_mr"][s] - wf["ll_m"][s])) < -0.002
    assert abs(float(np.mean(wf["ll_mm"][s] - wf["ll_mr"][s]))) < 0.002


def _comp(mean: float, half: float) -> dict[str, float]:
    return {"mean": mean, "lo": mean - half, "hi": mean + half, "n": 1000}


def _rep(
    c_mod: float, c_mkt: float, c_pl: float, half: float = 0.0003, c_mkt_after: float | None = None
) -> dict[str, dict[str, float]]:
    return {
        "C_mod": _comp(c_mod, half),
        "C_mkt": _comp(c_mkt, half),
        "C_mkt_after": _comp(c_mkt_after if c_mkt_after is not None else c_mkt, half),
        "C_pl": _comp(c_pl, half),
        "C_total": _comp(c_mod + c_mkt, half),
    }


def test_verdict_words() -> None:
    sel_ok = _rep(-0.0005, -0.0005, -0.0005)
    sel_bad = _rep(+0.0002, +0.0002, 0.0)
    v = core.verdict(_rep(-0.0035, 0.0, -0.0035), sel_ok)
    assert v["model"] == "BET_FLOOR_PASS" and v["market"] == "NONE"
    v = core.verdict(_rep(-0.0015, -0.0012, -0.0015), sel_ok)
    assert v["model"] == "INFO" and v["market"] == "MISCALIBRATION_INFO"
    v = core.verdict(_rep(-0.0006, -0.0004, -0.0006), sel_ok)
    assert (
        v["model"] == "DETECTED_BELOW_INFO_FLOOR"
        and v["market"] == "MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR"
    )
    v = core.verdict(_rep(-0.0015, 0.0, 0.0001), sel_ok)
    assert v["model"] == "PLACEBO_EXPLAINS"
    v = core.verdict(_rep(-0.0015, -0.0012, -0.0015, c_mkt_after=0.0002), sel_ok)
    assert v["market"] == "MODEL_EXPLAINS"
    v = core.verdict(_rep(-0.0015, 0.0, -0.0015), sel_bad)
    assert v["model"] == "FAIL_SELECT_SIGN"
    v = core.verdict(_rep(0.0, 0.0, 0.0), sel_ok)
    assert v == {"model": "NONE", "market": "NONE", "override": ""}
    worse = _rep(0.002, 0.001, 0.0)
    assert core.verdict(worse, sel_ok)["override"] == "WORSE_THAN_MARKET"


def test_season_2025_is_refused() -> None:
    with pytest.raises(ValueError):
        core.odds_extras(2025)
    with pytest.raises(ValueError):
        core.load_legs(seasons=(2024, 2025))


def test_market_info_tier_needs_the_guard_ci_and_run_words() -> None:
    sel_ok = _rep(-0.0005, -0.0005, -0.0005)
    v = core.verdict(_rep(0.0, -0.0012, 0.0, c_mkt_after=-0.0001), sel_ok)
    assert v["market"] == "MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR"
    assert core.run_words(0.0004) is None
    assert core.run_words(0.0020) == {"model": "NONE", "market": "NONE", "override": "KILLED_K1"}
    bad = core.run_words(0.0020, invalid_reason="pin")
    assert bad is not None and bad["override"] == "NO_RESULT_INVALID_RUN"


def test_off_span_directions_are_model_free_and_centred() -> None:
    A = synth(seed=11)
    A["line"] = np.abs(np.random.default_rng(1).normal(10, 4, len(A["m"])))
    for kind in core.OFF_SPAN_KINDS:
        z = core.off_span_z(A, kind)
        assert z.shape == A["m"].shape and np.isfinite(z).all()
    assert abs(float(core.off_span_z(A, "line").mean())) < 0.2


def test_gain_p_is_the_losing_tail() -> None:
    rng = np.random.default_rng(3)
    n = 4000
    gi = np.arange(n) // 4
    from research.eval.model_clv_eval import Boot

    boots = {"game": Boot(gi, int(gi.max()) + 1, seed=0, B=500)}
    mask = np.ones(n, dtype=bool)
    gain = rng.normal(-0.2, 1.0, n)  # a real gain: negative mean difference
    loss = rng.normal(+0.2, 1.0, n)
    assert core.gain_p(boots, gain, mask) < 0.01
    assert core.gain_p(boots, loss, mask) > 0.99
