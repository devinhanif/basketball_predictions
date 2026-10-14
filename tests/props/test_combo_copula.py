# ruff: noqa: E501
"""Gaussian-copula combo joint (nba.props.combo_copula): known-truth recovery, determinism, invariants."""

from __future__ import annotations

import numpy as np
import polars as pl
from scipy import stats
from scipy.special import ndtr

from nba.props import combo_copula as C
from nba.props.context_residual import CRPS_TAUS

D = len(C.STAT_ORDER)  # one joint over pts, reb, ast, fg3m, blk, stl


def _nb_world(n: int, true_r: np.ndarray, seed: int = 1):
    """Rows with their own negative-binomial marginals (pts, reb, blk) joined by a Gaussian copula."""
    rng = np.random.default_rng(seed)
    names = ["pts", "reb", "blk"]
    mus = {
        "pts": rng.uniform(4, 25, n),
        "reb": rng.uniform(1, 9, n),
        "blk": rng.uniform(0.1, 1.2, n),
    }
    u = ndtr(rng.standard_normal((n, 3)) @ np.linalg.cholesky(true_r).T)
    ys, cdfs = [], []
    for j, s in enumerate(names):
        r = 4.0
        p = r / (r + mus[s])
        ys.append(stats.nbinom.ppf(u[:, j], r, p).astype(int))
        ks = np.arange(C.KMAX[s] + 1)
        cdf = stats.nbinom.cdf(ks[None, :], r, p[:, None])
        cdf[:, -1] = 1.0
        cdfs.append(cdf)
    return ys, cdfs


def test_latent_correlation_recovered_on_known_truth() -> None:
    """Known-truth simulation: the indirect-inference estimate recovers the planted latent rho (the raw
    randomised-PIT correlation is attenuated, most for the low-count margin)."""
    true_r = np.array([[1, 0.4, 0.1], [0.4, 1, 0.35], [0.1, 0.35, 1]])
    ys, cdfs = _nb_world(20000, true_r)
    r, r_obs, info = C.estimate_latent_corr(cdfs, ys, seed=0)
    assert np.abs(r - true_r).max() < 0.05
    assert (
        r_obs[1, 2] < true_r[1, 2] - 0.05
    )  # the raw correlation of the discrete margin IS attenuated
    assert abs(r[1, 2] - true_r[1, 2]) < abs(r_obs[1, 2] - true_r[1, 2])
    assert info["max_abs_psd_shift"] < 1e-9


def test_cross_moment_method_recovers_known_truth_too() -> None:
    true_r = np.array([[1, 0.4, 0.1], [0.4, 1, 0.35], [0.1, 0.35, 1]])
    ys, cdfs = _nb_world(20000, true_r, seed=11)
    r, r_obs, _ = C.estimate_latent_corr(cdfs, ys, seed=0, method="cov")
    assert np.abs(r - true_r).max() < 0.06
    assert (
        r_obs[1, 2] < true_r[1, 2]
    )  # the raw cross-moment of the integer counts is attenuated as well


def test_independent_world_gives_zero_correlation() -> None:
    ys, cdfs = _nb_world(15000, np.eye(3), seed=7)
    r, _, _ = C.estimate_latent_corr(cdfs, ys, seed=0)
    assert np.abs(r - np.eye(3)).max() < 0.05


def test_nearest_corr_is_positive_definite_unit_diagonal() -> None:
    bad = np.array([[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]])
    assert np.linalg.eigvalsh(bad).min() < 0
    ok = C.nearest_corr(bad)
    assert np.linalg.eigvalsh(ok).min() > 0
    assert np.allclose(np.diag(ok), 1.0) and np.allclose(ok, ok.T)


def _grid_marginals(n: int = 60, seed: int = 3):
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    for s, lo, hi in (
        ("pts", 4, 25),
        ("reb", 1, 9),
        ("ast", 0.5, 8),
        ("fg3m", 0.2, 3),
        ("blk", 0.1, 1.2),
        ("stl", 0.2, 1.6),
    ):
        mu = rng.uniform(lo, hi, n)
        p = 4.0 / (4.0 + mu)
        q = stats.nbinom.ppf(CRPS_TAUS[None, :], 4.0, p[:, None])
        out[s] = C.int_cdf(q, C.KMAX[s])
    return out


def test_identity_correlation_matches_exact_convolution() -> None:
    """R = I through the sampler is the independent sum: it matches the exact convolution up to Monte-Carlo noise."""
    cd = _grid_marginals()
    g = C.joint_grids(cd, np.eye(D), C.base_normals())
    for name, parts in C.COMBOS.items():
        cdf = np.cumsum(C.convolve_exact(cd, parts), axis=1)
        exact = (cdf[:, :, None] < (CRPS_TAUS - 1e-12)[None, None, :]).sum(axis=1)
        assert np.abs(g[name].astype(float) - exact).mean() < 0.25  # sampling noise of 10,000 draws
        assert abs(g[name].mean() - exact.mean()) < 0.04


def test_positive_correlation_widens_the_sum_and_keeps_margins() -> None:
    cd = _grid_marginals()
    r = np.full((D, D), 0.5)
    np.fill_diagonal(r, 1.0)
    dep = C.joint_grids(cd, r, C.base_normals())
    ind = C.joint_grids(cd, np.eye(D), C.base_normals())
    for name in ("pra", "pr", "pa", "ra", "bs"):
        assert dep[name].std(axis=1).mean() > ind[name].std(axis=1).mean()
        assert (
            np.abs(dep[name].mean(axis=1) - ind[name].mean(axis=1)).mean() < 0.3
        )  # means (about) preserved
        assert (np.diff(dep[name], axis=1) >= 0).all() and dep[name].min() >= 0


def test_joint_grids_do_not_depend_on_the_batch() -> None:
    """Common random numbers: a row's grid is the same alone, in a batch, or in a different chunk size."""
    cd = _grid_marginals(40)
    r = np.full((D, D), 0.3)
    np.fill_diagonal(r, 1.0)
    base = C.base_normals()
    whole = C.joint_grids(cd, r, base, chunk=40)
    part = C.joint_grids(cd, r, base, chunk=7)
    one = C.joint_grids({k: v[11:12] for k, v in cd.items()}, r, base)
    for name in C.COMBOS:
        assert np.array_equal(whole[name], part[name])
        assert np.array_equal(whole[name][11], one[name][0])
    assert np.array_equal(C.base_normals(), C.base_normals())  # frozen seed
    b = C.base_normals()
    assert b.shape == (10_000, D) and np.allclose(b.mean(axis=0), 0, atol=1e-12)
    assert np.allclose(np.cov(b.T, bias=True), np.eye(D), atol=1e-9)


def test_pmf_and_q19_helpers() -> None:
    g = np.tile(np.array([0] * 100 + [1] * 60 + [3] * 39), (2, 1)).astype(np.int16)
    pm = C.pmf_from_grid(g)
    assert np.allclose(pm.sum(axis=1), 1.0) and abs(pm[0, 0] - 100 / 199) < 1e-12
    q19 = C.q19_of_grid199(g)
    assert q19.shape == (2, 19) and (np.diff(q19, axis=1) >= 0).all()
    assert q19[0, 0] == 0 and q19[0, -1] == 3


def test_empirical_grid_is_the_recency_weighted_empirical_distribution() -> None:
    played = pl.DataFrame(
        {
            "player_id": [1] * 8 + [2] * 3,
            "game_date": list(range(8)) + list(range(3)),
            "game_id": [str(i) for i in range(8)] + [str(i) for i in range(100, 103)],
            "v": [0, 0, 0, 0, 0, 0, 0, 5, 9, 9, 9],
        }
    )
    w = C.recent_windows(played, "v", window=5)
    assert w.shape == (11, 5) and np.isnan(w[0]).all()
    assert np.array_equal(w[7], [0, 0, 0, 0, 0])  # row 7 (value 5) sees only earlier games
    g = C.empirical_grid(w, halflife=10.0)
    assert (g[7] == 0).all() and (g[10, :] == 9).sum() > 0  # player 2's last row sees 9, 9
    # planted future: changing a row's own value or later ones never changes its window
    played2 = played.with_columns(
        pl.when(pl.col("game_id") == "7").then(99).otherwise(pl.col("v")).alias("v")
    )
    w2 = C.recent_windows(played2, "v", window=5)
    assert np.array_equal(w[:7], w2[:7], equal_nan=True) and np.array_equal(
        w[7], w2[7], equal_nan=True
    )


def test_grid_from_survival_reproduces_the_integer_grid_of_production_rows() -> None:
    """Production stores p_ge_full (n = 199, c_k = #(q > k - 0.5)); the live shadow rebuilds the integer grid from it."""
    from nba.props import full_support
    from nba.props.context_residual import to_integer_support

    rng = np.random.default_rng(5)
    for stat, mu, sd in (
        ("pts", 18.0, 6.0),
        ("reb", 5.0, 2.5),
        ("ast", 2.0, 1.5),
        ("fg3m", 0.7, 0.8),
    ):
        q = np.sort(rng.normal(mu, sd, 199))
        fs = full_support.from_samples(q, stat)
        surv = full_support.survival(fs)
        assert surv is not None and full_support.is_complete(surv)
        got = C.grid_from_survival(surv)
        want = to_integer_support(q).astype(np.int16)
        assert np.array_equal(got, want), stat


def test_all_combos_come_from_the_same_joint_draws() -> None:
    """One simulated game set per player-game: PRA dominates PR, PA and RA quantile by quantile, and the
    blk+stl sum equals the independent convolution when R carries no blk-stl dependence."""
    cd = _grid_marginals(30)
    r = np.full((D, D), 0.3)
    np.fill_diagonal(r, 1.0)
    g = C.joint_grids(cd, r, C.base_normals())
    for sub in ("pr", "pa", "ra"):
        assert (g["pra"] >= g[sub]).all()
    assert (g["pra"] >= g["pr"]).all() and (g["pr"] >= 0).all()
    pos = {s: i for i, s in enumerate(C.STAT_ORDER)}
    r2 = r.copy()
    r2[pos["blk"], pos["stl"]] = r2[pos["stl"], pos["blk"]] = 0.0
    r2[pos["blk"], :] = r2[:, pos["blk"]] = 0.0
    r2[pos["stl"], :] = r2[:, pos["stl"]] = 0.0
    r2[pos["blk"], pos["blk"]] = r2[pos["stl"], pos["stl"]] = 1.0
    g2 = C.joint_grids(cd, r2, C.base_normals())
    ind = C.joint_grids(cd, np.eye(D), C.base_normals())
    assert (
        np.abs(g2["bs"].astype(float) - ind["bs"]).mean() < 0.02
    )  # same draws, no blk-stl dependence: same grid up to rounding of the other stats' mixing
