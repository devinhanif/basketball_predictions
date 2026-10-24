"""Bootstrap mechanics, exact returns, the identity, the verdict table, guards and paper Kelly."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from research.market_forward import score as sc
from research.market_forward import stats as st
from research.market_forward.fee import load_pinned_fee
from research.market_forward.spec import LONGSHOT, UNDERS


def test_bootstrap_is_reproducible_and_cluster_level() -> None:
    per = np.arange(20, dtype=float).reshape(10, 2)
    a = st.bootstrap_sums(per, 300, np.random.default_rng(2026))
    b = st.bootstrap_sums(per, 300, np.random.default_rng(2026))
    assert np.array_equal(a, b) and a.shape == (300, 2)
    # every replicate is a sum of exactly 10 resampled clusters
    ones = st.bootstrap_sums(np.ones((10, 1)), 50, np.random.default_rng(1))
    assert np.all(ones == 10)


def test_ols_from_sums_matches_numpy() -> None:
    rng = np.random.default_rng(0)
    x = rng.random(200)
    y = 0.3 + 0.7 * x + rng.normal(0, 0.1, 200)
    s = np.array([[200, x.sum(), y.sum(), (x * x).sum(), (x * y).sum()]])
    a, b = st.ols_from_sums(s)
    slope, icpt = np.polyfit(x, y, 1)
    assert b[0] == pytest.approx(slope) and a[0] == pytest.approx(icpt)


def test_decision_interval_is_the_wider_of_the_two() -> None:
    narrow = np.random.default_rng(1).normal(0, 0.01, 4000)
    wide = np.random.default_rng(2).normal(0, 0.05, 4000)
    g, d, dec = st.decision_interval(narrow, wide, 0.005)
    assert dec.clustering == "date" and dec.width == d.width > g.width
    _, _, dec2 = st.decision_interval(wide, narrow, 0.005)
    assert dec2.clustering == "game"
    lo, hi = st.percentile_interval(np.arange(1001.0), 0.02)
    assert (lo, hi) == (20.0, 980.0)  # one-sided 0.02 = the 96% two-sided interval


def _df(rows: list[tuple[str, int, int, int, bool]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "series": ["KXNBAPTS"] * len(rows),
            "game": [r[0] for r in rows],
            "date": [f"d{i % 2}" for i in range(len(rows))],
            "bid_c": [r[1] for r in rows],
            "ask_c": [r[2] for r in rows],
            "y_u": [r[3] * 1_000_000 for r in rows],
            "dnp": [r[4] for r in rows],
            "kind": ["prop"] * len(rows),
            "stat": ["pts"] * len(rows),
            "age_min": [10.0] * len(rows),
            "ladder": [f"l{i}" for i in range(len(rows))],
        }
    )


def test_returns_are_exact_and_spread_and_fee_are_inside() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    # bid 5c ask 8c, YES did not happen: NO paid 0.95 + fee(0.95) and returns 1
    fr = sc.make_frame(_df([("g1", 5, 8, 0, False), ("g2", 5, 8, 1, False)]), fee)
    f_no = 7 * 95 * 5  # 0.07 * 0.95 * 0.05 in micro-dollars
    assert fr.r_no_u.tolist() == [1_000_000 - 950_000 - f_no, 0 - 950_000 - f_no]
    f_yes = 7 * 8 * 92
    assert fr.r_yes_u.tolist() == [0 - 80_000 - f_yes, 1_000_000 - 80_000 - f_yes]
    assert sc.identity_failures(fr) == 0


def test_identity_detects_a_wrong_fee_or_payoff() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    fr = sc.make_frame(_df([("g1", 5, 8, 0, False), ("g2", 20, 22, 1, False)]), fee)
    fr.r_no_u[0] += 1  # one micro-dollar off
    assert sc.identity_failures(fr) == 1


def test_identity_holds_for_fractional_dnp_payoffs() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    d = _df([("g1", 5, 8, 0, True)]).with_columns(y_u=pl.Series([160_000]))
    assert sc.identity_failures(sc.make_frame(d, fee)) == 0


def test_s1_rounding_never_lowers_cost() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    d = _df([("g1", 5, 8, 0, False), ("g2", 40, 42, 1, False)])
    a, b = sc.make_frame(d, fee), sc.make_frame(d, fee, rounded_fee=True)
    assert np.all(b.fee_no_u >= a.fee_no_u) and np.all(b.fee_no_u % 10_000 == 0)


# ---- verdict table (frozen Section 6 / 7 / 8) ----


def good() -> sc.GuardState:
    return sc.GuardState(1.0, True, [], 0.0, True, {"a": 0.01}, True, True)


def test_insufficient_sample_below_counts() -> None:
    v = sc.decide(1, 299, 60, 0.05, 0.04, 0.06, good())
    assert v.name == "INSUFFICIENT_SAMPLE"
    assert sc.decide(1, 400, 49, 0.05, 0.04, 0.06, good()).name == "INSUFFICIENT_SAMPLE"


def test_pass_needs_floor_and_lower_bound_above_zero() -> None:
    assert sc.decide(1, 300, 50, 0.012, 0.0001, 0.02, good()).name == "PASS"
    assert (
        sc.decide(1, 300, 50, 0.009, 0.0001, 0.02, good()).name == "KEEP_SHADOWING"
    )  # below floor
    assert sc.decide(1, 300, 50, 0.02, -0.0001, 0.04, good()).name == "KEEP_SHADOWING"  # bound <= 0
    assert sc.decide(2, 300, 50, 0.02, -0.0001, 0.04, good()).name == "INCONCLUSIVE"  # final read


def test_no_positive_ev_found_when_upper_bound_below_floor() -> None:
    assert sc.decide(1, 300, 50, -0.03, -0.04, 0.0099, good()).name == "NO_POSITIVE_EV_FOUND"
    assert sc.decide(1, 300, 50, -0.01, -0.04, 0.0100, good()).name == "KEEP_SHADOWING"


def test_guards_override() -> None:
    g = good()
    g.selftest_ok = False
    assert sc.decide(1, 900, 150, 0.05, 0.04, 0.06, g).name == "SELFTEST_FAILED"
    g = good()
    g.coverage_ok = False
    assert sc.decide(1, 900, 150, 0.05, 0.04, 0.06, g).name == "INSUFFICIENT_SAMPLE"
    g = good()
    g.settlement_ok = False
    assert sc.decide(1, 900, 150, 0.05, 0.04, 0.06, g).name == "INSUFFICIENT_SAMPLE"
    g = good()
    g.loo_ok = False
    assert sc.decide(1, 900, 150, 0.05, 0.04, 0.06, g).name == "KEEP_SHADOWING"
    assert sc.decide(2, 900, 150, 0.05, 0.04, 0.06, g).name == "INCONCLUSIVE"
    # a failed leave-one-out does not turn a no-edge result into KEEP_SHADOWING
    assert sc.decide(1, 900, 150, -0.05, -0.06, -0.04, g).name == "NO_POSITIVE_EV_FOUND"


def test_coverage_and_settlement_guards() -> None:
    frac, ok, gaps = sc.coverage_guard({"d1": (9, 10), "d2": (5, 10)})
    assert frac == 0.7 and ok and gaps == ["d2"]
    frac, ok, _ = sc.coverage_guard({"d1": (69, 100)})
    assert not ok
    assert sc.settlement_guard(100, 2) == (0.02, True)
    assert sc.settlement_guard(100, 3)[1] is False
    assert sc.settlement_guard(0, 0) == (0.0, True)


def test_leave_one_out_and_concentration() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    rows = [("g1", 5, 8, 0, False)] * 40 + [(f"g{i}", 5, 8, 1, False) for i in range(2, 12)]
    fr = sc.make_frame(_df(rows), fee)
    loo = sc.leave_one_out(fr, fr.col("series"))
    assert set(loo) == {"KXNBAPTS"} and np.isnan(loo["KXNBAPTS"])  # leaving out the only series
    conc = sc.concentrated_games(fr)
    assert conc and conc[0][0] == "g1" and conc[0][1] == pytest.approx(0.8)


def test_slices_below_30_games_say_insufficient_and_nothing_else() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    fr = sc.make_frame(_df([(f"g{i}", 5, 8, 0, False) for i in range(29)]), fee)
    r = sc.slice_row("x", fr)
    assert r.insufficient and r.mean_r_no == 0 and r.mean_y == 0
    fr30 = sc.make_frame(_df([(f"g{i}", 5, 8, 0, False) for i in range(30)]), fee)
    assert not sc.slice_row("x", fr30).insufficient


def test_paper_kelly_is_zero_unless_the_lower_bound_clears_cost() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    rng = np.random.default_rng(3)
    rows = [(f"g{i}", 40, 42, int(rng.random() < 0.41), False) for i in range(300)]
    fr = sc.make_frame(_df(rows), fee)
    out = sc.paper_kelly(UNDERS, fr, 0.02, 400, np.random.default_rng(1))
    k = [x for x in out if isinstance(x, sc.KellyRow)]
    assert len(k) == 1 and k[0].bucket == "U5" and k[0].full == 0.0 and k[0].half == 0.0
    assert all(isinstance(x, str) for x in out if not isinstance(x, sc.KellyRow))
    # a bucket where NO wins 95% of the time at a 60c cost has a positive paper fraction
    rows2 = [(f"g{i}", 40, 42, int(rng.random() < 0.05), False) for i in range(300)]
    k2 = [
        x
        for x in sc.paper_kelly(
            UNDERS, sc.make_frame(_df(rows2), fee), 0.02, 400, np.random.default_rng(1)
        )
        if isinstance(x, sc.KellyRow)
    ]
    assert k2[0].full > 0 and k2[0].half == pytest.approx(k2[0].full / 2)


def test_longshot_has_no_kelly_and_band_mask() -> None:
    fee = load_pinned_fee(UNDERS.all_series)
    fr = sc.make_frame(_df([("g1", 5, 8, 0, False), ("g2", 40, 42, 0, False)]), fee)
    assert sc.band_mask(UNDERS, fr).tolist() == [True, False]
    assert sc.band_mask(LONGSHOT, fr).tolist() == [True, False]
