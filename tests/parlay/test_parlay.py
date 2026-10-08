from __future__ import annotations

from datetime import datetime

import duckdb
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nba.parlay.calibration import compare_engines
from nba.parlay.config import FeeModel, load_config
from nba.parlay.copula import (
    PairCorr,
    ResidualRow,
    build_corr_matrix,
    estimate_pair_corr,
    nearest_correlation,
    sample_latent_uniforms,
)
from nba.parlay.engines import PossessionSimEngine, make_engine
from nba.parlay.ev import (
    NO_POSITIVE_EV,
    evaluate,
    fee_per_contract,
    kelly_fraction,
    shrink_to_market,
)
from nba.parlay.legs import Leg, dist_from_params, leg_prob
from nba.parlay.papertrade import log_trade, rolling_report, settle_trades

CFG = load_config()
FEE = FeeModel("t", "p_one_minus_p", 0.07)


def _leg(pid: int, stat: str = "pts", thr: float = 20, side: str = "yes", team: int = 1) -> Leg:
    return Leg("g1", pid, stat, thr, side, team)  # type: ignore[arg-type]


@settings(max_examples=40, deadline=None)
@given(st.integers(2, 6), st.integers(0, 10_000))
def test_nearest_correlation_valid(k: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    a = rng.uniform(-1.5, 1.5, (k, k))
    a = (a + a.T) / 2
    np.fill_diagonal(a, 1.0)
    r = nearest_correlation(a)
    assert np.allclose(r, r.T)
    assert np.allclose(np.diag(r), 1.0)
    assert np.linalg.eigvalsh(r).min() > 0
    assert np.abs(r).max() <= 1 + 1e-9
    np.linalg.cholesky(r)


@settings(max_examples=40, deadline=None)
@given(
    st.sampled_from(["gamma", "negbin", "zinb", "normal"]),
    st.floats(0.5, 40),
    st.floats(0.05, 1.5),
)
def test_p_ge_monotone_and_bounded(family: str, mu: float, a: float) -> None:
    params = {
        "gamma": {"shape": 1 / a, "scale": mu * a},
        "negbin": {"mu": mu, "alpha": a},
        "zinb": {"pi": 0.2, "mu": mu, "alpha": a},
        "normal": {"mean": mu, "std": mu * a + 0.5},
    }[family]
    d = dist_from_params(family, params)
    ps = [leg_prob(d, _leg(1, thr=n)) for n in range(0, 50, 3)]
    assert all(0 <= p <= 1 for p in ps)
    assert all(x >= y - 1e-12 for x, y in zip(ps, ps[1:], strict=False))
    no = leg_prob(d, _leg(1, thr=10, side="no"))
    assert no == pytest.approx(1 - leg_prob(d, _leg(1, thr=10)))


@settings(max_examples=50, deadline=None)
@given(st.floats(0, 1), st.integers(1, 500))
def test_fee_edge_cases(p: float, c: int) -> None:
    f = fee_per_contract(p, FEE, c)
    assert f >= 0
    assert f <= 0.07 * 0.25 + 0.01 + 1e-9  # per-contract, max at p=.5 plus cent rounding
    if p in (0.0, 1.0):
        assert f == 0.0


def test_fee_validation_and_known_value() -> None:
    assert fee_per_contract(0.5, FEE, 100) == pytest.approx(0.0175)  # $1.75 over 100
    with pytest.raises(ValueError):
        fee_per_contract(1.2, FEE)
    with pytest.raises(ValueError):
        fee_per_contract(0.5, FEE, 0)


def test_kelly_capped_and_zero_when_no_edge() -> None:
    assert kelly_fraction(0.3, 0.4, 0.25, 0.02) == 0.0
    assert kelly_fraction(0.99, 0.1, 0.25, 0.02) == 0.02
    assert kelly_fraction(0.5, 1.0, 0.25, 0.02) == 0.0


def test_market_shrink_weights() -> None:
    p, w = shrink_to_market(0.8, 0.5, 0, 100, 0.5)
    assert w == 0 and p == 0.5
    p, w = shrink_to_market(0.8, 0.5, 100, 100, -0.1)
    assert w == 0
    p, w = shrink_to_market(0.8, 0.5, 100, 100, 0.1)
    assert w == 0.5 and p == pytest.approx(0.65)


def _resid(n_games: int, rho: float, seed: int = 0) -> list[ResidualRow]:
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        z = rng.standard_normal(2)
        z2 = rho * z[0] + np.sqrt(1 - rho**2) * z[1]
        rows += [
            ResidualRow(str(g), 1, 10, "pts", float(z[0])),
            ResidualRow(str(g), 2, 10, "pts", z2),
        ]
    return rows


def test_estimate_corr_recovers_rho_and_shrinks() -> None:
    pc = estimate_pair_corr(_resid(3000, 0.4), shrink_k=200)
    r = pc.get("teammate", "pts", "pts")
    assert 0.3 < r < 0.42
    assert estimate_pair_corr(_resid(3000, 0.4), shrink_k=1e9).get("teammate", "pts", "pts") < 0.01


def test_copula_seeded_matches_marginals_and_captures_dependence() -> None:
    pc = estimate_pair_corr(_resid(3000, 0.5), 10)
    legs = [_leg(1), _leg(2)]
    marg = [0.5, 0.5]
    ind = make_engine("independence").joint(legs, marg).prob
    g1 = make_engine("gaussian_copula", pc, n_sims=40000, seed=3).joint(legs, marg)
    g2 = make_engine("gaussian_copula", pc, n_sims=40000, seed=3).joint(legs, marg)
    t = make_engine("t_copula", pc, n_sims=40000, seed=3).joint(legs, marg).prob
    assert g1 == g2
    assert ind == 0.25
    assert g1.prob > 0.30 and t > 0.30  # positive corr lifts joint above product
    # opposite sides flip dependence
    anti = make_engine("gaussian_copula", pc, n_sims=40000, seed=3).joint(
        [_leg(1), _leg(2, side="no")], marg
    )
    assert anti.prob < 0.20
    u = sample_latent_uniforms(build_corr_matrix(legs, pc), 20000, 1)
    assert abs(u.mean() - 0.5) < 0.01


def test_different_games_independent() -> None:
    pc = PairCorr({("teammate", "pts", "pts"): 0.9}, {})
    legs = [Leg("a", 1, "pts", 10, "yes", 1), Leg("b", 2, "pts", 10, "yes", 1)]
    assert np.allclose(build_corr_matrix(legs, pc), np.eye(2))


def test_sim_stub_raises() -> None:
    with pytest.raises(NotImplementedError):
        PossessionSimEngine().joint([_leg(1)], [0.5])
    with pytest.raises(ValueError):
        make_engine("bogus")


def test_compare_engines_copula_beats_independence_on_correlated_data() -> None:
    rng = np.random.default_rng(0)
    n = 1500
    rho = 0.7
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + np.sqrt(1 - rho**2) * rng.standard_normal(n)
    y = ((z1 > 0) & (z2 > 0)).astype(int)
    from scipy.stats import multivariate_normal

    cop = multivariate_normal([0, 0], [[1, rho], [rho, 1]]).cdf(np.zeros(2))
    probs = {"independence": [0.25] * n, "gaussian_copula": [float(cop)] * n}
    rows = compare_engines(probs, y, n_boot=500)
    ll = next(r for r in rows if r.metric == "log_loss")
    assert ll.n == n and ll.mean_diff < 0 and ll.a_better_significant


def test_evaluate_verdicts_and_interval_ordering() -> None:
    legs = [_leg(1)]
    r = evaluate(legs, 0.40, 8000, 20000, 0.40, CFG, n_settled=500, skill=0.1)
    assert r.verdict == NO_POSITIVE_EV and r.ev < 0 and r.ev_low < r.ev
    assert r.prob_interval[0] <= r.model_prob <= r.prob_interval[1]
    r2 = evaluate(legs, 0.80, 16000, 20000, 0.40, CFG, n_settled=2000, skill=0.2)
    assert r2.verdict != NO_POSITIVE_EV and r2.ev_low > 0 and r2.kelly > 0
    # no track record => defers to market => no positive EV by construction
    r3 = evaluate(legs, 0.80, 16000, 20000, 0.40, CFG, n_settled=0, skill=0.0, bid=0.35)
    assert r3.verdict == NO_POSITIVE_EV and r3.spread == pytest.approx(0.05)
    assert set(r3.to_dict()) >= {
        "legs",
        "model_prob",
        "prob_interval",
        "price",
        "fees",
        "ev",
        "ev_low",
        "verdict",
    }


def test_paper_trade_roundtrip() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE paper_trades (trade_id VARCHAR PRIMARY KEY, created_at TIMESTAMP, legs JSON,"
        " model_prob FLOAT, model_prob_lo FLOAT, model_prob_hi FLOAT, price FLOAT,"
        " fee_model VARCHAR,"
        " expected_value FLOAT, settled BOOLEAN, outcome BOOLEAN, realized_pnl FLOAT)"
    )
    con.execute(
        "CREATE TABLE player_game_stats (game_id VARCHAR, player_id INT, team_id INT,"
        " minutes FLOAT,"
        " pts INT, reb INT, ast INT, fg3m INT, stl INT, blk INT, tov INT, starter BOOLEAN)"
    )
    con.execute("INSERT INTO player_game_stats VALUES ('g1',1,1,30,25,5,6,2,0,0,1,TRUE)")
    legs = [_leg(1, "pts", 20), _leg(1, "pra", 40, "no"), Leg("g1", 9, "reb", 5, "yes", 1)]
    rec = evaluate(legs[:2], 0.3, 6000, 20000, 0.3, CFG, n_settled=10, skill=0.1)
    t1 = log_trade(con, rec, CFG.fee, now=datetime(2026, 1, 1))
    rec_b = evaluate([legs[2]], 0.3, 6000, 20000, 0.3, CFG)
    log_trade(con, rec_b, CFG.fee, trade_id="open")
    assert settle_trades(con) == 1  # second trade's player has no box score yet
    out = con.execute(
        "SELECT outcome, realized_pnl FROM paper_trades WHERE trade_id=?", [t1]
    ).fetchone()
    assert out is not None and out[0] is True  # 25>=20 and pra 36 < 40
    assert out[1] == pytest.approx(1 - 0.3 - fee_per_contract(0.3, CFG.fee), abs=1e-5)
    rep = rolling_report(con, min_settled=30)
    assert "settled: n=1" in rep and "WARNING" in rep
