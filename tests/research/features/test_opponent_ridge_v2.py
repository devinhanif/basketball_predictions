# ruff: noqa: E501
"""Tests for the ridge_v2 feature variants, the sweep job wiring and the pre-registered eval rule."""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import json
from pathlib import Path
from typing import Any

import lightgbm  # noqa: F401  # macOS: LightGBM must load its OpenMP runtime before torch
import numpy as np
import polars as pl
import pytest
import scipy.sparse as sp
from sklearn.linear_model import Ridge

from research.colab.jobs import load_job
from research.eval import ridge_v2_eval as ev
from research.features import opponent_ridge_v2 as R
from research.features.opponent_ridge_v2 import MonthlyRidge, RidgeCfg, RidgeInputs

REF = R.REF_CFG
JOB_DIR = Path(__file__).resolve().parents[3] / "research" / "colab" / "jobs" / "ridge_v2_sweep"
STATS = R.PROP_STATS


@pytest.fixture(autouse=True)
def _small_min_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(R, "MIN_ROWS", 400)


def league(
    seed: int = 0,
    n_days: int = 230,
    star_effect: float = 0.5,
    pace_var: bool = True,
) -> RidgeInputs:
    """Synthetic league: 6 teams x 8 players, one 'star' per team who misses ~15% of games."""
    rng = np.random.default_rng(seed)
    teams = list(range(101, 107))
    players = {t: [1000 + 10 * i + j for j in range(8)] for i, t in enumerate(teams)}
    pos = {p: j % 3 for t in teams for j, p in enumerate(players[t])}
    base = {
        p: 6.0 + 4.0 * (j == 0) + 2.0 * (pos[p] == 2) + rng.normal(0, 1.0)
        for t in teams
        for j, p in enumerate(players[t])
    }
    opp_eff = {t: rng.normal(0, 0.8) for t in teams}
    rows: list[tuple[Any, ...]] = []
    flagged: dict[str, set[int]] = {}
    gid = 0
    start = dt.date(2022, 10, 20)
    for day in range(n_days):
        d = start + dt.timedelta(days=day * 3)
        order = rng.permutation(len(teams))
        pace = float(rng.normal(98, 4)) if pace_var else 98.0
        for k in range(0, len(teams), 2):
            h, a = teams[int(order[k])], teams[int(order[k + 1])]
            gid += 1
            g = f"{gid:07d}"
            star_out = {t: bool(rng.random() < 0.15) for t in (h, a)}
            for t, o, home in ((h, a, 1), (a, h, 0)):
                if star_out[t]:
                    flagged.setdefault(g, set()).add(players[t][0])
                for j, p in enumerate(players[t]):
                    if j == 0 and star_out[t]:
                        continue
                    mult = 1.0 + (star_effect if (star_out[t] and j > 0) else 0.0)
                    minutes = float(rng.uniform(8, 36)) if j > 0 else float(rng.uniform(28, 38))
                    if rng.random() < 0.05:
                        minutes = float(rng.uniform(0.5, 4.0))  # garbage-time cameo
                    rate = (base[p] + opp_eff[o]) * mult
                    ys = {
                        s: float(rng.poisson(max(rate, 0.1) * minutes / 36.0 * (1 + 0.1 * i)))
                        for i, s in enumerate(STATS)
                    }
                    usage = float(base[p] * minutes / 36.0 + rng.normal(0, 0.5))
                    rows.append(
                        (g, p, t, o, np.datetime64(d), minutes, ys, pos[p], home, pace, usage)
                    )
    rows.sort(key=lambda r: (r[4], r[0], r[1]))
    dates = np.array([r[4] for r in rows], dtype="datetime64[D]")
    return RidgeInputs(
        game_id=np.array([r[0] for r in rows]),
        player_id=np.array([r[1] for r in rows], dtype=np.int64),
        team_id=np.array([r[2] for r in rows], dtype=np.int64),
        opp_id=np.array([r[3] for r in rows], dtype=np.int64),
        date=dates,
        season=np.where(dates >= np.datetime64("2023-10-01"), 2023, 2022).astype(np.int64),
        minutes=np.array([r[5] for r in rows]),
        y={s: np.array([r[6][s] for r in rows]) for s in STATS},
        pos=np.array([r[7] for r in rows], dtype=np.int64),
        is_home=np.array([r[8] for r in rows], dtype=np.int64),
        pace=np.array([r[9] for r in rows]),
        usage=np.array([r[10] for r in rows]),
        flagged=flagged,
        demo={p: (190.0 + 3 * pos[p], 190.0 + 15 * pos[p], "GFC"[pos[p]]) for p in pos},
    )


@pytest.fixture(scope="module")
def inp() -> RidgeInputs:
    return league()


ALL_CFGS = [
    REF,
    dataclasses.replace(REF, halflife=60.0, alpha_p=10.0, alpha_o=1000.0),
    dataclasses.replace(REF, hier="pos"),
    dataclasses.replace(REF, hier="arch", opp_grp="arch"),
    dataclasses.replace(REF, opp_grp="pos"),
    dataclasses.replace(REF, venue=True),
    dataclasses.replace(REF, pace=True),
    dataclasses.replace(REF, star=True),
    dataclasses.replace(
        REF, halflife=90.0, hier="pos", opp_grp="pos", venue=True, pace=True, star=True
    ),
]


def run_all(
    inp: RidgeInputs, cfgs: list[RidgeCfg] = ALL_CFGS, stat: str = "pts"
) -> dict[Any, dict[str, np.ndarray]]:
    return MonthlyRidge(inp).run([(stat, c) for c in cfgs])


def take(inp: RidgeInputs, idx: np.ndarray) -> RidgeInputs:
    return dataclasses.replace(
        inp,
        game_id=inp.game_id[idx],
        player_id=inp.player_id[idx],
        team_id=inp.team_id[idx],
        opp_id=inp.opp_id[idx],
        date=inp.date[idx],
        season=inp.season[idx],
        minutes=inp.minutes[idx],
        y={s: v[idx] for s, v in inp.y.items()},
        pos=inp.pos[idx],
        is_home=inp.is_home[idx],
        pace=inp.pace[idx],
        usage=inp.usage[idx],
    )


# ------------------------------------------------------------------------------- estimator


def test_reference_matches_sklearn_exactly(inp: RidgeInputs) -> None:
    """The block ridge reproduces sklearn Ridge(no intercept, sample_weight) on one month."""
    out = run_all(inp, [REF])[("pts", REF)]
    mth = np.datetime64("2023-03", "M")
    start = mth.astype("datetime64[D]")
    ok = np.isfinite(inp.minutes) & (inp.minutes >= 5.0)
    tr = (inp.date < start) & (inp.date >= start - np.timedelta64(R.LOOKBACK_DAYS, "D")) & ok
    pu, pinv = np.unique(inp.player_id[tr], return_inverse=True)
    ou, oinv = np.unique(inp.opp_id[tr], return_inverse=True)
    n = int(tr.sum())
    rows = np.arange(n)
    x = sp.csr_matrix(
        (np.ones(2 * n), (np.r_[rows, rows], np.r_[pinv, len(pu) + oinv])),
        shape=(n, len(pu) + len(ou)),
    )
    w = inp.minutes[tr]
    y = inp.y["pts"][tr] / w * 36.0
    mu = float(np.average(y, weights=w))
    coef = (
        Ridge(alpha=50.0, fit_intercept=False, solver="cholesky")
        .fit(x, y - mu, sample_weight=w)
        .coef_
    )
    cur = np.where(inp.date.astype("datetime64[M]") == mth)[0]
    pmap = {int(k): i for i, k in enumerate(pu)}
    omap = {int(k): len(pu) + i for i, k in enumerate(ou)}
    exp_p = np.array([coef[pmap[int(k)]] if int(k) in pmap else 0.0 for k in inp.player_id[cur]])
    oc = coef[len(pu) :] - coef[len(pu) :].mean()
    exp_o = np.array(
        [oc[omap[int(k)] - len(pu)] if int(k) in omap else 0.0 for k in inp.opp_id[cur]]
    )
    np.testing.assert_allclose(out["p"][cur], exp_p, atol=1e-9)
    np.testing.assert_allclose(out["o"][cur], exp_o, atol=1e-9)
    np.testing.assert_allclose(out["rate"][cur], mu + exp_p + exp_o, atol=1e-9)


def test_ridge_limits_large_and_tiny_penalty(inp: RidgeInputs) -> None:
    big = dataclasses.replace(REF, alpha_p=1e12, alpha_o=1e12)
    res = run_all(inp, [big])[("pts", big)]
    ok = np.isfinite(res["p"])
    assert ok.any()
    assert np.abs(res["p"][ok]).max() < 1e-6 and np.abs(res["o"][ok]).max() < 1e-6
    # noiseless additive truth: player/opponent DIFFERENCES are recovered when the penalty -> 0
    teams, per = list(range(1, 5)), 6
    rows = []
    gid = 0
    for day in range(160):
        d = np.datetime64("2022-10-20") + np.timedelta64(3 * day, "D")
        for h in teams:
            o = teams[(h + day) % 4]
            if o == h:
                continue
            gid += 1
            for j in range(per):
                p = 100 * h + j
                rows.append((f"{gid:06d}", p, h, o, d, 30.0, 4.0 * j + 2.0 * o, 1, 1, 98.0, 5.0))
    rows.sort(key=lambda r: (r[4], r[0], r[1]))
    n = len(rows)
    ex = RidgeInputs(
        game_id=np.array([r[0] for r in rows]),
        player_id=np.array([r[1] for r in rows], dtype=np.int64),
        team_id=np.array([r[2] for r in rows], dtype=np.int64),
        opp_id=np.array([r[3] for r in rows], dtype=np.int64),
        date=np.array([r[4] for r in rows], dtype="datetime64[D]"),
        season=np.full(n, 2023, dtype=np.int64),
        minutes=np.full(n, 30.0),
        y={s: np.array([r[6] * 30.0 / 36.0 for r in rows]) for s in STATS},
        pos=np.ones(n, dtype=np.int64),
        is_home=np.ones(n, dtype=np.int64),
        pace=np.full(n, 98.0),
        usage=np.full(n, 5.0),
        flagged={},
        demo={},
    )
    tiny = dataclasses.replace(REF, alpha_p=1e-9, alpha_o=1e-9).canonical()
    out = MonthlyRidge(ex).run([("pts", tiny)])[("pts", tiny)]
    last = np.where(np.isfinite(out["rate"]))[0]
    cur = last[ex.date[last] >= np.datetime64("2023-02-01")]
    assert len(cur) > 100
    pid, oid = ex.player_id[cur], ex.opp_id[cur]
    truth = 4.0 * (pid % 100) + 2.0 * oid
    # same opponent, different players: rate differences equal the truth differences
    a, b = cur[(oid == 1)][:50], cur[(oid == 1)][50:100]
    d_pred = out["rate"][a][:, None] - out["rate"][b][None, :]
    t = 4.0 * (ex.player_id[a] % 100) + 2.0
    t2 = 4.0 * (ex.player_id[b] % 100) + 2.0
    np.testing.assert_allclose(d_pred, t[:, None] - t2[None, :], atol=1e-3)
    assert np.isfinite(truth).all()


def test_recency_weights_and_flat_limit(inp: RidgeInputs) -> None:
    flat = REF
    huge = dataclasses.replace(REF, halflife=1e12).canonical()
    short = dataclasses.replace(REF, halflife=5.0).canonical()
    res = run_all(inp, [flat, huge, short])
    ok = np.isfinite(res[("pts", flat)]["p"])
    np.testing.assert_allclose(res[("pts", huge)]["p"][ok], res[("pts", flat)]["p"][ok], atol=1e-6)
    assert np.abs(res[("pts", short)]["p"][ok] - res[("pts", flat)]["p"][ok]).max() > 1e-3


def test_pace_constant_is_identity_and_variable_pace_changes(inp: RidgeInputs) -> None:
    const = league(pace_var=False)
    cfgp = dataclasses.replace(REF, pace=True).canonical()
    r = MonthlyRidge(const).run([("pts", REF), ("pts", cfgp)])
    ok = np.isfinite(r[("pts", REF)]["p"])
    np.testing.assert_allclose(r[("pts", cfgp)]["p"][ok], r[("pts", REF)]["p"][ok], atol=1e-9)
    v = MonthlyRidge(inp).run([("pts", REF), ("pts", cfgp)])
    ok2 = np.isfinite(v[("pts", REF)]["p"])
    assert np.abs(v[("pts", cfgp)]["p"][ok2] - v[("pts", REF)]["p"][ok2]).max() > 1e-3


def test_hierarchical_prior_gives_unseen_players_the_group_mean() -> None:
    lg = league(seed=3)
    # a rookie: appears only in the final month; take the last team's roster slot 7
    rookie = int(lg.player_id[-1])
    cut = np.datetime64(lg.date[-1], "M").astype("datetime64[D]")
    keep = ~((lg.player_id == rookie) & (lg.date < cut))
    lg2 = take(lg, np.where(keep)[0])
    hier = dataclasses.replace(REF, hier="pos", alpha_g=1.0).canonical()
    res = MonthlyRidge(lg2).run([("pts", REF), ("pts", hier)])
    rows = np.where((lg2.player_id == rookie) & (lg2.date >= cut))[0]
    assert len(rows) > 0
    assert np.all(res[("pts", REF)]["p"][rows] == 0.0)  # reference: unseen -> 0
    grp_effect = res[("pts", hier)]["p"][rows]
    assert np.abs(grp_effect).min() > 1e-6  # hierarchical: position-group mean effect
    seen = np.where((lg2.pos == lg2.pos[rows[0]]) & (lg2.date >= cut) & (lg2.player_id != rookie))[
        0
    ]
    # shrinking players to the group (huge player penalty) -> every player of a group shares p
    stiff = dataclasses.replace(hier, alpha_p=1e12).canonical()
    s = MonthlyRidge(lg2).run([("pts", stiff)])[("pts", stiff)]["p"]
    assert np.ptp(s[seen]) < 1e-6
    np.testing.assert_allclose(s[rows], s[seen][:1].repeat(len(rows)), atol=1e-6)


def test_star_out_uses_report_not_tonights_box(inp: RidgeInputs) -> None:
    cfg = dataclasses.replace(REF, star=True, alpha_pz=50.0).canonical()
    base = MonthlyRidge(inp).run([("pts", cfg)])[("pts", cfg)]
    ok = np.isfinite(base["rate"])
    star_games = {g for g, s in inp.flagged.items() if s}
    in_star = np.array([g in star_games for g in inp.game_id])
    assert np.abs(base["tm"][ok & in_star]).sum() > 0.0
    assert np.all(base["tm"][ok & ~in_star] == 0.0)
    # remove the report for those games: the box score still shows the absence, but tm must vanish
    no_report = dataclasses.replace(inp, flagged={})
    nr = MonthlyRidge(no_report).run([("pts", cfg)])[("pts", cfg)]
    assert np.all(nr["tm"][np.isfinite(nr["rate"])] == 0.0)
    # the learned effect has the planted sign: teammates produce more when the star is out
    pos_mask = ok & in_star & (inp.player_id % 10 != 0)
    assert base["tm"][pos_mask].mean() > 0.0


def test_venue_and_opp_groups_emit_components(inp: RidgeInputs) -> None:
    cfg = dataclasses.replace(REF, venue=True, opp_grp="pos").canonical()
    assert cfg.components() == ("p", "o", "ox", "px")
    r = MonthlyRidge(inp).run([("reb", cfg)])[("reb", cfg)]
    ok = np.isfinite(r["rate"])
    assert ok.any() and all(np.isfinite(r[c][ok]).all() for c in cfg.components())


# ------------------------------------------------------------------------------- temporal correctness


def _plant_future(inp: RidgeInputs, cut: np.datetime64, seed: int = 9) -> RidgeInputs:
    """Rewrite every row dated AFTER ``cut`` (stats, minutes, usage, pace, reports) and append new ones."""
    rng = np.random.default_rng(seed)
    fut = inp.date > cut
    y = {s: np.where(fut, rng.integers(0, 60, inp.n).astype(float), v) for s, v in inp.y.items()}
    flagged = {
        g: (set(s) if d <= cut else {int(x) for x in rng.integers(1000, 1100, 3)})
        for (g, s), d in zip(
            inp.flagged.items(), [inp.date[inp.game_id == g][0] for g in inp.flagged], strict=True
        )
    }
    planted = dataclasses.replace(
        inp,
        minutes=np.where(fut, rng.uniform(0.0, 48.0, inp.n), inp.minutes),
        y=y,
        usage=np.where(fut, rng.uniform(0, 40, inp.n), inp.usage),
        pace=np.where(fut, rng.uniform(60, 140, inp.n), inp.pace),
        flagged=flagged,
    )
    # append extra future rows (a new player and new games) and re-sort
    extra = np.where(fut)[0][:200]
    ext = take(planted, extra)
    ext = dataclasses.replace(
        ext,
        player_id=ext.player_id + 777_000,
        game_id=np.array([g + "x" for g in ext.game_id]),
        date=ext.date + np.timedelta64(1, "D"),
    )
    cat = {
        f.name: np.concatenate([getattr(planted, f.name), getattr(ext, f.name)])
        for f in dataclasses.fields(RidgeInputs)
        if f.name not in ("y", "flagged", "demo")
    }
    cat_y = {s: np.concatenate([planted.y[s], ext.y[s]]) for s in STATS}
    order = np.lexsort((cat["player_id"], cat["game_id"], cat["date"]))
    cat = {k: v[order] for k, v in cat.items()}
    cat_y = {s: v[order] for s, v in cat_y.items()}
    return RidgeInputs(**cat, y=cat_y, flagged=planted.flagged, demo=planted.demo)


def test_planted_future_rows_change_nothing_at_or_before_the_cut(inp: RidgeInputs) -> None:
    cut = np.datetime64("2023-04-15")
    base = run_all(inp)
    planted = _plant_future(inp, cut)
    pl_res = run_all(planted)
    key_b = {
        (g, int(p)): i for i, (g, p) in enumerate(zip(inp.game_id, inp.player_id, strict=True))
    }
    idx_b, idx_p = [], []
    for j, (g, p, d) in enumerate(
        zip(planted.game_id, planted.player_id, planted.date, strict=True)
    ):
        if d <= cut and (g, int(p)) in key_b:
            idx_b.append(key_b[(g, int(p))])
            idx_p.append(j)
    assert len(idx_b) > 2000
    n_checked = 0
    for k in base:
        for comp in base[k]:
            a, b = base[k][comp][idx_b], pl_res[k][comp][idx_p]
            np.testing.assert_allclose(a, b, atol=1e-9, equal_nan=True)
            n_checked += int(np.isfinite(a).sum())
    assert n_checked > 20000


def test_own_row_values_never_enter_own_features(inp: RidgeInputs) -> None:
    """Tonight's minutes / box line / usage / pace of the row itself are never read."""
    base = run_all(inp)
    mth = np.datetime64("2023-05", "M")
    cur = inp.date.astype("datetime64[M]") == mth
    rng = np.random.default_rng(1)
    mod = dataclasses.replace(
        inp,
        minutes=np.where(cur, rng.uniform(0, 48, inp.n), inp.minutes),
        y={s: np.where(cur, rng.integers(0, 70, inp.n).astype(float), v) for s, v in inp.y.items()},
        usage=np.where(cur, 99.0, inp.usage),
        pace=np.where(cur, 70.0, inp.pace),
    )
    res = run_all(mod)
    for k in base:
        for comp in base[k]:
            np.testing.assert_allclose(
                base[k][comp][cur], res[k][comp][cur], atol=1e-9, equal_nan=True
            )


def test_dnp_and_short_cameos_never_train_but_still_get_features(inp: RidgeInputs) -> None:
    short = ~np.isfinite(inp.minutes) | (inp.minutes < R.MIN_TRAIN_MINUTES)
    assert short.sum() > 50
    rng = np.random.default_rng(2)
    wild = dataclasses.replace(
        inp,
        y={
            s: np.where(short, rng.integers(500, 900, inp.n).astype(float), v)
            for s, v in inp.y.items()
        },
    )
    a, b = run_all(inp), run_all(wild)
    for k in a:
        for comp in a[k]:
            np.testing.assert_allclose(a[k][comp], b[k][comp], atol=1e-9, equal_nan=True)
    # the experiment-2 leak (null exactly when tonight's minutes < 5) is gone
    p = a[("pts", REF)]["p"]
    audit = R.missing_by_tonight_minutes(p, inp.minutes)
    assert audit["n_low"] > 50
    trainable = np.isfinite(a[("pts", REF)]["rate"])
    assert np.isfinite(p[short & trainable]).all()
    assert audit["null_share_low_minutes"] == pytest.approx(
        audit["null_share_normal_minutes"], abs=0.05
    )


def test_missing_by_tonight_minutes_flags_a_leaky_column() -> None:
    minutes = np.array([30.0, 25.0, 2.0, 1.0, 33.0, 0.0])
    leaky = np.where(minutes < 5, np.nan, 1.0)
    out = R.missing_by_tonight_minutes(leaky, minutes)
    assert out["null_share_low_minutes"] == 1.0 and out["null_share_normal_minutes"] == 0.0


def test_season_2025_is_refused(inp: RidgeInputs) -> None:
    bad = dataclasses.replace(inp, season=np.full(inp.n, 2025, dtype=np.int64))
    with pytest.raises(ValueError, match="2025"):
        R.tune_selection_season(bad)


# ------------------------------------------------------------------------------- plumbing


def test_solver_backends_agree() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(size=(40, 25))
    gram = a.T @ a
    rhs = rng.normal(size=25)
    pen = rng.uniform(0.5, 3.0, 25)
    np.testing.assert_allclose(
        R.solve_spd(gram, rhs, pen, "numpy"), R.solve_spd(gram, rhs, pen, "torch"), atol=1e-9
    )


def test_cfg_canonical_and_column_names() -> None:
    a = RidgeCfg(alpha_g=9.0)  # r5 off: the penalty is irrelevant
    assert a.canonical() == REF.canonical() and a.tag() == REF.tag()
    assert RidgeCfg(hier="pos", alpha_g=9.0).tag() != RidgeCfg(hier="pos", alpha_g=1.0).tag()
    assert R.column_name("pts", REF, "p").startswith("rg_") and R.column_name(
        "pts", REF, "p"
    ).endswith("_p_pts")


def _fake_tuned() -> dict[str, dict[str, Any]]:
    comp = {
        "ref": {"cfg": dataclasses.asdict(REF), "rel_gain": 0.01},
        "L1": {"cfg": dataclasses.asdict(REF), "rel_gain": 0.01},
    }
    out = {}
    for s in STATS:
        l1 = dataclasses.asdict(
            dataclasses.replace(REF, halflife=120.0, alpha_p=10.0, alpha_o=1000.0)
        )
        out[s] = {
            "r1": {"cfg": dataclasses.asdict(dataclasses.replace(REF, halflife=60.0))},
            "r6": {
                "cfg": dataclasses.asdict(dataclasses.replace(REF, alpha_p=25.0, alpha_o=4000.0))
            },
            "L1": {"cfg": l1},
            "components": {
                "r5pos": {
                    "ref": {"cfg": {"hier": "pos", "alpha_g": 5.0}, "rel_gain": 0.02},
                    "L1": {"cfg": {"hier": "pos", "alpha_g": 5.0}, "rel_gain": 0.02},
                },
                "r5arch": {
                    "ref": {"cfg": {"hier": "arch", "alpha_g": 5.0}, "rel_gain": 0.02},
                    "L1": {"cfg": {"hier": "arch", "alpha_g": 5.0}, "rel_gain": 0.02},
                },
                "r2pos": {
                    "ref": {"cfg": {"opp_grp": "pos", "alpha_og": 200.0}, "rel_gain": 0.001},
                    "L1": {"cfg": {"opp_grp": "pos", "alpha_og": 200.0}, "rel_gain": 0.001},
                },
                "r2arch": {
                    "ref": {"cfg": {"opp_grp": "arch", "alpha_og": 200.0}, "rel_gain": 0.0},
                    "L1": {"cfg": {"opp_grp": "arch", "alpha_og": 200.0}, "rel_gain": 0.0},
                },
                "r3a": {
                    "ref": {"cfg": {"venue": True, "alpha_v": 200.0}, "rel_gain": -0.01},
                    "L1": {"cfg": {"venue": True, "alpha_v": 200.0}, "rel_gain": -0.01},
                },
                "r4": {
                    "ref": {"cfg": {"star": True, "alpha_pz": 200.0}, "rel_gain": 0.003},
                    "L1": {"cfg": {"star": True, "alpha_pz": 200.0}, "rel_gain": 0.003},
                },
            },
        }
    _ = comp
    return out


def test_build_arms_structure_and_ablation() -> None:
    arms = R.build_arms(_fake_tuned())
    s = "pts"
    assert arms["ALL"][s].components() == ("p", "o", "ox", "px", "tm")
    assert arms["ALL"][s].halflife == 120.0 and arms["ALL"][s].star and arms["ALL"][s].pace
    for name, field in (
        ("drop_r1", "halflife"), ("drop_r5", "hier"), ("drop_r2", "opp_grp"),
        ("drop_r3a", "venue"), ("drop_r3b", "pace"), ("drop_r4", "star"),
    ):  # fmt: skip
        d, a = arms[name][s], arms["ALL"][s]
        diff = [f.name for f in dataclasses.fields(a) if getattr(a, f.name) != getattr(d, f.name)]
        assert field in diff
    assert arms["L4"][s].star is False and arms["L4"][s].venue and arms["L3"][s].venue is False
    # proxy_sel keeps components with rel_gain >= 0.2% (r5 yes, r2 no, r3a no, r4 yes)
    ps = arms["proxy_sel"][s]
    assert ps.hier == "pos" and ps.opp_grp is None and ps.venue is False and ps.star
    assert arms["ref_fixed"][s] == REF


def test_export_columns_marks_aliases_and_is_deterministic(inp: RidgeInputs) -> None:
    arms = R.build_arms(_fake_tuned())
    sub = {k: arms[k] for k in ("ref_fixed", "L4", "ALL", "drop_r4")}
    f1, spec = R.export_columns(inp, sub, log=lambda *_: None)
    assert spec["drop_r4"]["alias_of"] == "L4" and spec["L4"]["alias_of"] is None
    f2, _ = R.export_columns(inp, sub, log=lambda *_: None)
    assert f1.equals(f2)
    assert f1.height == inp.n
    cols = [c for c in f1.columns if c.startswith("rg_")]
    assert all(f1[c].null_count() >= 0 for c in cols)
    # NaN months are exported as NULL (not NaN) so the early-month gap is a plain missing value
    assert any(f1[c].null_count() > 0 for c in cols)
    assert all(f1[c].is_nan().sum() == 0 for c in cols if f1[c].dtype == pl.Float32)


def test_validate_against_exp2_detects_a_mismatch() -> None:
    frame = pl.DataFrame(
        {"game_id": ["a", "b"], "player_id": [1, 2], "pc": [1.0, 2.0], "oc": [0.1, 0.2]}
    )
    base = pl.DataFrame(
        {"game_id": ["a", "b"], "player_id": [1, 2]}
        | {f"ridge_{k}_{s}": [1.0, None] for s in STATS for k in ("player", "opp")}
    )
    arm = {"columns": {s: ["pc", "oc"] for s in STATS}}
    out = R.validate_against_exp2(frame, base, arm)
    assert out["ridge_player_pts"]["n_both"] == 1
    assert out["ridge_player_pts"]["old_null_new_nonnull"] == 1
    assert out["ridge_opp_pts"]["max_abs_diff"] == pytest.approx(0.9)


# ------------------------------------------------------------------------------- job wiring


def _load_job_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "ridge_v2_sweep_job", JOB_DIR / "ridge_v2_sweep.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_job_definition_and_notebook_in_sync() -> None:
    job = load_job("ridge_v2_sweep")
    assert job.touches_holdout is False
    paths = [i.path for i in job.inputs]
    assert paths[0].endswith("ctxres_v2.parquet") and any(
        p.endswith("ridge_v2.parquet") for p in paths
    )
    nb = json.loads(job.notebook.read_text())
    code = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    script = (
        (JOB_DIR / "ridge_v2_sweep.py")
        .read_text()
        .replace('if __name__ == "__main__":\n    entry()\n', "")
    )
    assert script.rstrip("\n") in code, (
        "run: uv run python research/colab/jobs/ridge_v2_sweep/build_notebook.py"
    )
    assert "metrics = entry()" in code


def test_job_refuses_2025_and_names_are_guarded() -> None:
    mod = _load_job_module()
    assert mod.FROZEN_SEASON == 2025 and mod.SELECT_SEASON == 2023 and mod.REPORT_SEASON == 2024
    assert mod.RIDGE_COL.match(R.column_name("pts", REF, "p"))
    assert mod.RIDGE_COL.match(R.column_name("fg3m", REF, "tm"))
    assert not mod.RIDGE_COL.match("pts") and not mod.RIDGE_COL.match("rg_zzzzzzzz_p_pts")
    assert mod.FAMILY == {
        "pts": "quantile",
        "reb": "poisson_nb",
        "ast": "poisson_nb",
        "fg3m": "poisson_nb",
    }


def test_job_shard_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_job_module()
    mod.C.arms = {a: {} for a in ("ref_fixed", "exp2_ref", "no_ridge", "r1", "r6", "L1", "ALL")}
    monkeypatch.delenv("NBA_ARMS", raising=False)
    monkeypatch.setenv("NBA_SHARD", "0/2")
    s0 = mod.select_arms()
    monkeypatch.setenv("NBA_SHARD", "1/2")
    s1 = mod.select_arms()
    assert set(s0).isdisjoint(s1) and set(s0) | set(s1) == set(mod.C.arms)
    monkeypatch.delenv("NBA_SHARD")
    monkeypatch.setenv("NBA_ARMS", "r1,ref_fixed")
    assert mod.select_arms() == ["ref_fixed", "r1"]
    monkeypatch.setenv("NBA_ARMS", "nope")
    with pytest.raises(ValueError, match="unknown arms"):
        mod.select_arms()


# ------------------------------------------------------------------------------- eval rule


def _oof(
    variants: dict[str, float], n: int = 3000, seed: int = 0
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Near-continuous integer outcomes; each variant's CRPS shifted by its offset; q19 well calibrated."""
    from scipy.stats import norm

    rng = np.random.default_rng(seed)
    gids = np.repeat([f"g{i:05d}" for i in range(n // 10)], 10)
    pids = np.tile(np.arange(10), n // 10)
    frames = []
    export = pl.DataFrame(
        {
            "game_id": gids,
            "player_id": pids,
            "season": np.full(n, 2024),
            "has_report": rng.integers(0, 2, n),
            "n_out_rot": rng.integers(0, 3, n),
            "min_gap": rng.normal(0, 4, n),
            "team_game_no": rng.integers(1, 80, n),
            "starter10": rng.uniform(0, 1, n),
        }
    )
    y = np.round(rng.normal(50, 10, n))
    taus = np.arange(1, 20) / 20
    for name, off in variants.items():
        sd = 10.0
        q19 = 50 + sd * norm.ppf(taus)[None, :] * np.ones((n, 1))
        crps = np.abs(y - 50) / 10 + off + rng.normal(0, 0.01, n)
        for st in ev.STATS:
            frames.append(
                pl.DataFrame(
                    {
                        "variant": name,
                        "stat": st,
                        "game_id": gids,
                        "player_id": pids,
                        "fold_id": np.full(n, 202401),
                        "y": y.astype(np.float32),
                        "mean": np.full(n, 50.0, dtype=np.float32),
                        "crps": crps.astype(np.float32),
                        "tll": np.zeros(n, dtype=np.float32),
                        "q19": q19.astype(np.float32).tolist(),
                        "p_ge": [[0.5]] * n,
                    }
                )
            )
    return pl.concat(frames), export


def test_choose_candidates_excludes_references_and_uses_2023_only() -> None:
    sel = {
        "ref_fixed": dict.fromkeys(ev.STATS, 1.0),
        "exp2_ref": dict.fromkeys(ev.STATS, 0.1),  # leaky reference must never be a candidate
        "no_ridge": dict.fromkeys(ev.STATS, 0.2),
        "r1": {"pts": 0.9, "reb": 1.1, "ast": 0.95, "fg3m": 0.99},
        "L1": {"pts": 0.95, "reb": 0.97, "ast": 0.95, "fg3m": 0.99},
    }
    cand = ev.choose_candidates(sel)
    assert cand == {"pts": "r1", "reb": "L1", "ast": "L1", "fg3m": "L1"}  # ties -> arm name order


def test_keep_rule_passes_a_clear_win_and_fails_a_tie() -> None:
    oof, export = _oof({"ref_fixed": 0.0, "winner": -0.02, "tie": 0.0})
    win = ev.keep_rule(oof, export, dict.fromkeys(ev.STATS, "winner"), n_boot=300)
    assert all(v["keep"] for v in win.values()), {s: v["checks"] for s, v in win.items()}
    tie = ev.keep_rule(oof, export, dict.fromkeys(ev.STATS, "tie"), n_boot=300)
    assert not any(v["keep"] for v in tie.values())
    assert not tie["pts"]["checks"]["floor"] and not tie["pts"]["checks"]["ci_excludes_0"]


def test_keep_rule_blocks_a_slice_regression() -> None:
    oof, export = _oof({"ref_fixed": 0.0, "cand": -0.02}, seed=1)
    bad = oof.join(export.select("game_id", "player_id", "starter10"), on=["game_id", "player_id"])
    bump = (
        pl.when((pl.col("variant") == "cand") & (pl.col("starter10") >= 0.5))
        .then(0.2)
        .otherwise(0.0)
    )
    bad = bad.with_columns((pl.col("crps") + bump).cast(pl.Float32)).drop("starter10")
    res = ev.keep_rule(bad, export, dict.fromkeys(ev.STATS, "cand"), n_boot=300)
    assert any("sl_role=starter" in r for r in res["pts"]["slice_regressions"])
    assert res["pts"]["checks"]["no_slice_regression"] is False and res["pts"]["keep"] is False


def test_run_requires_complete_audited_runs(tmp_path: Path) -> None:
    d = tmp_path / "run"
    d.mkdir()
    (d / "metrics.json").write_text(
        json.dumps({"complete": False, "experiment_tag": "ridge_v2_exp4"})
    )
    with pytest.raises(ValueError, match="incomplete"):
        ev.run(str(d), str(tmp_path / "x.parquet"))
    (d / "metrics.json").write_text(
        json.dumps({"complete": True, "experiment_tag": "ridge_v2_exp4"})
    )
    with pytest.raises(ValueError, match="leak_audit"):
        ev.run(str(d), str(tmp_path / "x.parquet"))
