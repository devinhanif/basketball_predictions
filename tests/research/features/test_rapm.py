"""RAPM: as-of discipline, sign conventions, sparse shapes."""

from __future__ import annotations

import datetime as dt

import numpy as np

from research.features.rapm import (
    PossessionData,
    RapmConfig,
    build_design,
    build_snapshots,
    cv_ridge_lambda,
    player_index,
    solve_ridge,
)

N_PLAYERS = 12


def _synth(
    n_per_month: int,
    months: list[dt.date],
    seed: int = 0,
    off_effect: np.ndarray | None = None,
    def_effect: np.ndarray | None = None,
) -> PossessionData:
    rng = np.random.default_rng(seed)
    ids = np.arange(100, 100 + N_PLAYERS)
    oe = np.zeros(N_PLAYERS) if off_effect is None else off_effect
    de = np.zeros(N_PLAYERS) if def_effect is None else def_effect
    dates, seasons, offs, dfns, pts, home = [], [], [], [], [], []
    for m in months:
        for _ in range(n_per_month):
            perm = rng.permutation(N_PLAYERS)
            o, d = perm[:5], perm[5:10]
            mu = 1.1 + oe[o].sum() - de[d].sum()
            dates.append(np.datetime64(m + dt.timedelta(days=int(rng.integers(0, 20))), "D"))
            seasons.append(m.year if m.month >= 9 else m.year - 1)
            offs.append(ids[o])
            dfns.append(ids[d])
            pts.append(mu + rng.normal(0, 0.3))
            home.append(float(rng.integers(0, 2)))
    order = np.argsort(np.array(dates))
    return PossessionData(
        np.array(dates)[order],
        np.array(seasons)[order],
        np.array(offs)[order],
        np.array(dfns)[order],
        np.array(pts)[order],
        np.array(home)[order],
    )


MONTHS = [dt.date(2022, 11, 1), dt.date(2022, 12, 1), dt.date(2023, 1, 1), dt.date(2023, 2, 1)]


def test_design_shapes_and_signs() -> None:
    poss = _synth(50, MONTHS[:1])
    idx = player_index(poss)
    x = build_design(poss.off, poss.dfn, poss.off_home, idx)
    assert x.shape == (len(poss), len(idx) + 2)
    dense = x.toarray()
    assert np.all(dense[:, : len(idx)].sum(axis=1) == 0)  # 5 x (+1) and 5 x (-1)
    assert np.all((dense[:, : len(idx)] == 1).sum(axis=1) == 5)
    assert np.all(dense[:, len(idx)] == 1.0)  # intercept
    assert x.nnz <= len(poss) * 12


def test_sign_offense_and_defense() -> None:
    oe = np.zeros(N_PLAYERS)
    oe[0] = 0.03  # player 0 adds points on offense
    de = np.zeros(N_PLAYERS)
    de[1] = 0.03  # player 1 removes points on defense
    poss = _synth(600, MONTHS, off_effect=oe, def_effect=de)
    snap = build_snapshots(poss, RapmConfig(ridge_lambda=1.0, history_decay=1.0))
    last = snap.filter(snap["cutoff"] == snap["cutoff"].max())
    r = dict(zip(last["player_id"].to_list(), last["rapm"].to_list(), strict=True))
    others = np.mean([v for k, v in r.items() if k not in (100, 101)])
    assert r[100] > others + 1.0
    assert r[101] > others + 1.0
    # 0.03 pts/poss = 3 per 100 on one side; net rapm ~ 2*beta ~ (3 + small)/..., positive & sane
    assert r[100] < 20.0


def test_solve_ridge_limits() -> None:
    poss = _synth(400, MONTHS[:2], off_effect=np.linspace(0, 0.05, N_PLAYERS))
    idx = player_index(poss)
    x = build_design(poss.off, poss.dfn, poss.off_home, idx)
    y = poss.pts * 100
    g = np.asarray((x.T @ x).todense())
    b = np.asarray(x.T @ y).ravel()
    p = len(idx)
    huge = solve_ridge(g, b, p, 1e12)
    assert np.abs(huge[:p]).max() < 1e-3  # lambda -> inf: players shrink to prior (0)
    small = solve_ridge(g, b, p, 1e-6)
    ols = np.linalg.lstsq(x.toarray(), y, rcond=None)[0]
    assert np.allclose(small @ small, ols @ ols, rtol=1e-3) or np.allclose(
        x @ small, x @ ols, atol=1e-3
    )
    prior = np.zeros(p + 2)
    prior[:p] = 3.0
    assert np.allclose(solve_ridge(g, b, p, 1e12, prior)[:p], 3.0, atol=1e-3)


def test_snapshot_is_asof_future_possessions_do_not_leak() -> None:
    oe = np.linspace(0, 0.04, N_PLAYERS)
    base = _synth(200, MONTHS[:3], seed=1, off_effect=oe)
    extra = _synth(200, MONTHS[3:], seed=2, off_effect=-oe)  # opposite regime in the future
    both = PossessionData(
        *(
            np.concatenate([getattr(base, f), getattr(extra, f)])
            for f in ("dates", "seasons", "off", "dfn", "pts", "off_home")
        )
    )
    cfg = RapmConfig(ridge_lambda=50.0, history_decay=0.5)
    s0 = build_snapshots(base, cfg)
    s1 = build_snapshots(both, cfg)
    cut = dt.date(2023, 1, 1)  # last cutoff before any future-regime possessions exist
    a = s0.filter(s0["cutoff"] <= cut).sort(["cutoff", "player_id"])
    b = s1.filter(s1["cutoff"] <= cut).sort(["cutoff", "player_id"])
    assert a["cutoff"].to_list() == b["cutoff"].to_list()
    assert np.allclose(a["rapm"].to_numpy(), b["rapm"].to_numpy())
    assert s1["cutoff"].max() == dt.date(2023, 2, 1)


def test_first_cutoff_has_no_data() -> None:
    poss = _synth(100, MONTHS[:2])
    snap = build_snapshots(poss, RapmConfig())
    assert snap.filter(snap["cutoff"] == MONTHS[0]).height == 0


def test_cv_returns_one_row_per_lambda() -> None:
    poss = _synth(300, MONTHS, off_effect=np.linspace(0, 0.3, N_PLAYERS))
    cv = cv_ridge_lambda(poss, [10.0, 1000.0], season=2022, history_decay=0.5, min_train_months=1)
    assert cv.height == 3  # 2 lambdas + null
    mse = dict(zip(cv["lambda"].to_list(), cv["mse"].to_list(), strict=True))
    assert mse[10.0] < mse[float("inf")]  # real signal beats the null
