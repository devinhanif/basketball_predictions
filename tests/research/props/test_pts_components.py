"""PTS_COMPONENTS (docs/prereg/PTS_COMPONENTS.md): distributions, enumeration, as-of features."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from nba.props.context_residual import CRPS_TAUS
from research.eval import pts_components_eval as ev
from research.eval.f11_lineup_context import frozen_sha256
from research.props import pts_components as pc

DOC = Path(__file__).resolve().parents[3] / "docs/prereg/PTS_COMPONENTS.md"


def test_frozen_prefix_matches_doc() -> None:
    assert frozen_sha256(DOC).startswith(ev.FROZEN_PREFIX)


def test_nb_pmf_sums_to_one() -> None:
    a = np.arange(0, 400, dtype=float)
    for mu, r in ((0.5, 3.0), (6.0, 8.0), (14.0, 40.0)):
        assert np.exp(pc.nb_logpmf(a, np.full_like(a, mu), r)).sum() == pytest.approx(1.0, abs=1e-9)


def test_beta_binomial_kernel_rows_sum_to_one() -> None:
    ker = pc._make_kernel(np.array([0.2, 0.5, 0.9]), 40.0, 25)
    assert np.allclose(ker.sum(axis=2), 1.0, atol=1e-10)
    # zero attempts means zero makes
    assert np.allclose(ker[:, 0, 0], 1.0)


def _brute(mu: np.ndarray, p: np.ndarray, r: np.ndarray, beta: np.ndarray, kappa: np.ndarray):
    """One row, explicit loops over nodes, attempts and makes (no FFT, no einsum)."""
    x, w = pc.gh_nodes() if np.any(beta != 0) else (np.zeros(1), np.ones(1))
    pts = np.zeros(pc.MAX_PTS + 1)
    for xj, wj in zip(x, w, strict=True):
        comp = []
        for k in range(3):
            cap = pc.CAPS[k]
            a = np.arange(cap + 1, dtype=float)
            pa = np.exp(
                pc.nb_logpmf(
                    a, np.full_like(a, mu[k] * np.exp(beta[k] * xj - 0.5 * beta[k] ** 2)), r[k]
                )
            )
            pa[cap] = max(1.0 - pa[:cap].sum(), 0.0)
            pm = np.zeros(cap + 1)
            for ai in range(cap + 1):
                m = np.arange(ai + 1, dtype=float)
                pm[: ai + 1] += pa[ai] * np.exp(
                    pc.bb_logpmf(m, np.full_like(m, ai), np.full_like(m, p[k]), kappa[k])
                )
            comp.append(pm)
        for m2, q2 in enumerate(comp[0]):
            for m3, q3 in enumerate(comp[1]):
                for f, qf in enumerate(comp[2]):
                    pts[2 * m2 + 3 * m3 + f] += wj * q2 * q3 * qf
    return pts


@pytest.mark.parametrize("beta", [np.zeros(3), np.array([0.4, 0.5, 0.3])])
def test_points_pmf_matches_brute_force_and_sums_to_one(beta: np.ndarray) -> None:
    mu = np.array([[7.0, 4.0, 3.0], [1.2, 0.3, 0.4]])
    p = np.array([[0.52, 0.36, 0.78], [0.4, 0.2, 0.6]])
    r = np.array([9.0, 6.0, 5.0])
    kappa = np.array([60.0, 30.0, 25.0])
    pts, m3 = pc.points_pmf(mu, p, r, beta, kappa)
    assert np.abs(pts.sum(axis=1) - 1.0).max() < 1e-9
    assert np.abs(m3.sum(axis=1) - 1.0).max() < 1e-9
    for i in range(2):
        assert np.allclose(pts[i], _brute(mu[i], p[i], r, beta, kappa), atol=1e-10)


def test_shared_factor_widens_the_points_distribution() -> None:
    mu = np.array([[7.0, 4.0, 3.0]])
    p = np.array([[0.52, 0.36, 0.78]])
    r = np.array([30.0, 30.0, 30.0])
    kappa = np.array([500.0, 500.0, 500.0])
    ks = np.arange(pc.MAX_PTS + 1)
    sd = []
    for beta in (np.zeros(3), np.full(3, 0.4)):
        pts, _ = pc.points_pmf(mu, p, r, beta, kappa)
        m = (pts[0] * ks).sum()
        sd.append(np.sqrt((pts[0] * (ks - m) ** 2).sum()))
    assert sd[1] > sd[0]


def test_pmf_to_quantiles_is_smallest_k_with_cdf_at_least_tau() -> None:
    pmf = np.zeros((1, 186))
    pmf[0, [2, 5, 9]] = [0.2, 0.5, 0.3]
    q = pc.pmf_to_quantiles(pmf)[0]
    assert q[np.searchsorted(CRPS_TAUS, 0.1)] == 2
    assert q[np.searchsorted(CRPS_TAUS, 0.5)] == 5
    assert q[-1] == 9
    assert np.all(np.diff(q) >= 0)


def test_fit_activity_recovers_a_shared_factor() -> None:
    rng = np.random.default_rng(0)
    n = 6000
    mu = np.column_stack([np.full(n, 6.0), np.full(n, 3.0), np.full(n, 3.0)])
    z = rng.standard_normal(n)
    beta = np.array([0.4, 0.4, 0.3])
    lam = mu * np.exp(beta[None, :] * z[:, None] - 0.5 * beta[None, :] ** 2)
    r = np.array([40.0, 40.0, 40.0])
    y = rng.poisson(rng.gamma(r, lam / r))
    rh, bh = pc.fit_activity(y.astype(float), mu, shared=True)
    assert np.all(np.abs(bh) > 0.2)
    _, b0 = pc.fit_activity(y.astype(float), mu, shared=False)
    assert np.all(b0 == 0.0)


# --------------------------------------------------------------------------- features


def _league(days: int = 60, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    g_rows, p_rows = [], []
    d0 = dt.date(2024, 1, 1)
    for d in range(days):
        pairs = [(1, 2), (3, 4)] if d % 2 == 0 else [(1, 3), (2, 4)]
        for j, (h, a) in enumerate(pairs):
            gid = f"002{d:03d}{j}"
            g_rows.append(
                {
                    "game_id": gid,
                    "game_date": d0 + dt.timedelta(days=d),
                    "season": 2024,
                    "home_team": h,
                    "away_team": a,
                    "home_pts": 100,
                    "away_pts": 98,
                }
            )
            for t in (h, a):
                for k in range(7):
                    fg3a = int(rng.integers(0, 9))
                    fg3m = int(rng.binomial(fg3a, 0.35))
                    fg2a = int(rng.integers(0, 14))
                    fg2m = int(rng.binomial(fg2a, 0.5))
                    fta = int(rng.integers(0, 8))
                    ftm = int(rng.binomial(fta, 0.75))
                    p_rows.append(
                        {
                            "game_id": gid,
                            "player_id": t * 100 + k,
                            "team_id": t,
                            "minutes": float(rng.integers(12, 36)),
                            "starter": k < 5,
                            "pts": 2 * fg2m + 3 * fg3m + ftm,
                            "reb": int(rng.integers(0, 10)),
                            "ast": int(rng.integers(0, 8)),
                            "fg3m": fg3m,
                            "fgm": fg2m + fg3m,
                            "fga": fg2a + fg3a,
                            "fg3a": fg3a,
                            "ftm": ftm,
                            "fta": fta,
                        }
                    )
    return pl.DataFrame(g_rows), pl.DataFrame(p_rows)


def test_identity_and_derived_columns() -> None:
    _, p = _league(4)
    assert bool(pc.identity_ok(p).all())
    bad = p.with_columns(
        pl.when(pl.col("player_id") == 100).then(99).otherwise(pl.col("pts")).alias("pts")
    )
    assert not bool(pc.identity_ok(bad).all())
    d = pc.add_derived(p)
    assert (d["fg2m"] + d["fg3m"] == d["fgm"]).all()


def test_component_features_are_as_of_and_planted_future_inert() -> None:
    g, p = _league()
    base = pc.build_component_features(g, p, {})
    assert base.select(pc.KEYS).n_unique() == base.height
    cutoff = dt.date(2024, 1, 31)
    late = g.filter(pl.col("game_date") >= cutoff)["game_id"].to_list()
    p2 = ev.bump(p, late, 5)
    alt = pc.build_component_features(g, p2, {})
    before = set(g.filter(pl.col("game_date") <= cutoff)["game_id"].to_list())
    assert ev.feat_equal(base, alt, before)
    assert not ev.feat_equal(base, alt, set(g["game_id"].to_list()) - before)


def test_component_features_planted_same_game_inert_through_the_day() -> None:
    g, p = _league()
    day = dt.date(2024, 1, 31)
    tg = g.filter(pl.col("game_date") == day)["game_id"].to_list()
    base = pc.build_component_features(g, p, {})
    alt = pc.build_component_features(g, ev.bump(p, tg, 9), {})
    upto = set(g.filter(pl.col("game_date") <= day)["game_id"].to_list())
    assert ev.feat_equal(base, alt, upto)
    assert not ev.feat_equal(base, alt, set(g["game_id"].to_list()) - upto)


def test_make_rates_are_shrunk_and_in_unit_interval() -> None:
    g, p = _league()
    f = pc.build_component_features(g, p, {})
    for c in pc.RATE_COLS:
        v = f[c].to_numpy()
        assert np.all((v > 0) & (v < 1))
    # shrunk toward the league rate, never degenerate
    assert f["pr_fg3"].min() > 0.1


def test_permute_targets_within_player_keeps_joint_vectors() -> None:
    g, p = _league(20)
    f = pc.build_component_features(g, p, {})
    q = pc.permute_targets(f, 0)
    for pid in f["player_id"].unique().to_list()[:3]:
        a = f.filter(pl.col("player_id") == pid).select(pc.TG_COLS)
        b = q.filter(pl.col("player_id") == pid).select(pc.TG_COLS)
        assert sorted(map(tuple, a.rows())) == sorted(map(tuple, b.rows()))
    assert not q["tg_fg3a"].equals(f["tg_fg3a"])
    assert q.select(pc.KEYS).equals(f.select(pc.KEYS))


def test_season_2025_refused() -> None:
    with pytest.raises(ValueError):
        next(ev._blocks(pl.DataFrame(), (2025,), None))


def test_rules_need_both_seasons_and_all_gates() -> None:
    def row(
        season: int, arm: str, d: float | None, hi: float | None
    ) -> dict[str, float | str | None]:
        return {
            "season": season,
            "arm": arm,
            "dcrps": d,
            "ci_hi": hi,
            "bias": 0.0,
            "cov80": 0.8,
            "pit_q90": 0.9,
        }

    rows = [
        row(s, a, -0.02 if a != "A0" else None, -0.01 if a != "A0" else None)
        for s in (2023, 2024)
        for a in ev.ARMS
    ]
    ci = {"point": -0.02, "lo": -0.03, "hi": -0.01}
    atk = [
        {
            "season": s,
            "A1_minus_A2": ci,
            "A1_minus_A3": ci,
            "A1_minus_A4": ci,
            "shared_factor_did_nothing": False,
        }
        for s in (2023, 2024)
    ]
    out = ev.evaluate_rules({"rows": rows, "slices": [], "attack": atk})
    assert out["final_pass"] is True
    atk[1]["A1_minus_A4"] = {"point": -0.001, "lo": -0.01, "hi": 0.01}
    out = ev.evaluate_rules({"rows": rows, "slices": [], "attack": atk})
    assert out["final_pass"] is False and out["fails_on_2023_close"] is False
