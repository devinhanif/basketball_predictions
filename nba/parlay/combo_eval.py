"""Combo mode: measured lift, calibrated-vs-raw marginals and the script calibration check.

PRE-STATED RULE (written before the first run of this module; nothing below changes after a result)

Data. Seasons 2023 and 2024 only (season 2025 is the frozen holdout and is refused by name):
stored production OOF forecasts (``reports/odds_history/model_rows_{season}.parquet``, integer
survival grids, conditional on the player playing), DraftKings T-60 main lines
(``data/ops/model_clv_pairs/pairs_{season}.parquet``), box-score game results (``nba.duckdb``,
``season <= 2024`` in SQL, read-only). Two folds: train 2023 -> test 2024 (walk-forward) and train
2024 -> test 2023 (cross-fit, not walk-forward; disclosed). Everything fitted (calibration map,
pooled correlations, script parameters) is fitted on the TRAIN season only.

Unit and clustering. A leg is an OVER at a DraftKings main x.5 line (outcome ``Y >= x + 1``).
Two-leg combos of legs in one game are classed same player (SP, different stats), teammates (TM),
opponents (OP); cross-game (XG, same date, different games, random sample) and XG70 (both raw legs
>= 0.70). Clusters: the game (SP, TM, OP, legs) or the date (XG, XG70). 95% CI by 2,000 cluster
bootstrap resamples. Joint P is the exact bivariate Gaussian copula with CALIBRATED marginals and
the train season's pooled correlations; independence is the product of the same marginals.

Reported (no pass/fail, descriptive with n and CI): predicted lift (sum joint / sum product),
realised lift (hits / sum product), paired log-loss difference copula minus independence, and
calibration by raw-probability band, raw vs calibrated.

Scripts. blowout |margin| >= 15, close |margin| <= 5, shootout total >= ceil(mu_t + 12), grind
total <= floor(mu_t - 12) (``mu_t`` the as-of game-model expectation). Player normal-score residuals
z (calibrated randomized PIT) get a per-(script, stat, role) mean and sd on the train season,
shrunk toward (0, 1) with k = 200. A script is labelled ``calibrated`` iff in BOTH folds, over the
test season's main-line over legs in games that realised the script: (i) paired Brier difference
(script-conditional minus unconditional) has CI upper bound < 0, (ii) the 95% CI of (observed rate
minus mean conditional prediction) lies inside +-0.02, and (iii) for same-player pairs the 95% CI of
observed / predicted conditional joint contains 1. Otherwise ``exploratory``.

AMENDMENT (same day, after the first run, before the second; thresholds above unchanged). The first
run built the calibrated grid by applying the map at EVERY rung. A PIT diagnostic on 2024 showed the
normal score of those grids had sd 0.63-0.90 (raw grids 1.07-1.13): the map's logit slope below 1
fattens both tails away from the posted lines, so the script parameters (estimated from that PIT)
were estimated on an invalid latent. ``combo_build.calibrate_surv_banded`` applies the map only for
|z| <= 1 and holds the logit offset beyond. Main-line legs (z in about [-0.5, 1.0]) are untouched,
so the first run's pair and band numbers carry over; the script block is re-run. Added, descriptive
only: the standardised residual cross-product ``resid_r`` per pair class (a calibration-free
dependence measure) and the PIT sd per stat.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from numpy.typing import NDArray
from scipy import stats

from nba.parlay import combo_build as cb
from nba.parlay import line_cal as lc
from nba.parlay import line_cal_build as lcb
from nba.parlay.game_model import GameModelParams, asof_total_features
from nba.parlay.joint import STATS, GameResiduals, corr_to_json, estimate_game_corr

SEASONS = (2023, 2024)
FOLDS = ((2023, 2024, "walk-forward"), (2024, 2023, "cross-fit"))
SHRINK_K = 200.0
SCRIPT_K = 200.0
B_BOOT = 2000
SEED = 20261109
BLOWOUT_ABS = 15
CLOSE_ABS = 5
TOTAL_DELTA = 12.0
BANDS = (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)
MIN_PAIR_CAP = 150_000
F64 = NDArray[np.float64]
DEFINITIONS = {
    "blowout_abs_margin": BLOWOUT_ABS,
    "close_abs_margin": CLOSE_ABS,
    "total_delta": TOTAL_DELTA,
}


def _refuse(season: int) -> None:
    if season > 2024:
        raise ValueError("season 2025 is the frozen holdout and is never loaded here")


# ---------------------------------------------------------------- loading


def load_season(model_dir: Path, season: int) -> dict[str, Any]:
    """Per stat: ids, outcome, starter flag, raw survival (N, K), median / spread of the
    continuous grid (recovered per stat x month exactly as the CAL map was built)."""
    _refuse(season)
    path = model_dir / f"model_rows_{season}.parquet"
    d = pl.read_parquet(
        path,
        columns=["game_id", "player_id", "team_id", "game_date", "season", "y", "starter10",
                 "stat", "c", "s", "p_ge"],
    )  # fmt: skip
    if d["season"].n_unique() != 1 or int(d["season"][0]) != season:
        raise ValueError(f"{path.name}: expected exactly season {season}")
    d = d.with_columns(
        pl.col("player_id").cast(pl.Int64),
        pl.col("team_id").cast(pl.Int64),
        pl.col("game_date").dt.strftime("%Y-%m").alias("mo"),
    ).sort(["stat", "game_id", "player_id"])
    out: dict[str, Any] = {}
    for stat in STATS:
        ds = d.filter(pl.col("stat") == stat)
        plist = ds["p_ge"].to_list()
        k_max = int(ds["p_ge"].list.len().max() or 0)
        raw = np.zeros((ds.height, k_max))
        for i, row in enumerate(plist):
            raw[i, : len(row)] = row
        med = np.zeros(ds.height)
        spr = np.zeros(ds.height)
        mo = ds["mo"].to_numpy()
        c, s = ds["c"].to_numpy(), ds["s"].to_numpy()
        for m in np.unique(mo):
            sel = np.nonzero(mo == m)[0]
            z = lcb.block_zq(c[sel], s[sel], [plist[i] for i in sel])
            q = lc.zq_to_quantiles(c[sel], s[sel], z["mid"], lcb.TAU_POS)
            med[sel] = q["q50"]
            spr[sel] = (q["q75"] - q["q25"]) / lc.IQR_TO_SD
        out[stat] = {
            "game_id": ds["game_id"].to_numpy(),
            "player_id": ds["player_id"].to_numpy(),
            "team_id": ds["team_id"].to_numpy(),
            "date": ds["game_date"].to_numpy().astype("datetime64[D]"),
            "y": ds["y"].to_numpy().astype(float),
            "st10": ds["starter10"].to_numpy().astype(float),
            "raw": raw,
            "med": med,
            "spr": spr,
        }
    return out


def block_inputs_ok(raw: F64) -> bool:
    return bool(np.all(np.diff(raw, axis=1) <= 1e-9))


def games_table(db: Path, joint_model: Path, game_rows_dir: Path) -> pl.DataFrame:
    """Games of seasons <= 2024 with margin, total, as-of mu_t and mu_m (game model of the
    shipped joint_model.json: five numbers fitted on 2022-24, disclosed)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        g = con.execute(
            "SELECT game_id, game_date, season, home_team, away_team, home_pts, away_pts "
            "FROM games WHERE season <= 2024 AND home_pts IS NOT NULL AND home_pts > 0"
        ).pl()
    finally:
        con.close()
    params = GameModelParams.from_json(json.dumps(json.loads(joint_model.read_text())["params"]))
    feats = asof_total_features(g, params.halflife, params.prior_k)
    p = pl.concat(
        [pl.read_parquet(game_rows_dir / f"game_rows_{s}.parquet").select("game_id", "p") for s in SEASONS]
    )
    g = g.join(feats, on="game_id", how="left").join(p, on="game_id", how="left")
    mu_t = [params.total_mu(f) for f in g["total_feat"].to_list()]
    mu_m = [
        float("nan") if pp is None else params.margin_mu(float(pp)) for pp in g["p"].to_list()
    ]
    return g.with_columns(
        pl.Series("mu_t", mu_t),
        pl.Series("mu_m", mu_m),
        (pl.col("home_pts") - pl.col("away_pts")).alias("margin"),
        (pl.col("home_pts") + pl.col("away_pts")).alias("total"),
    ).with_columns(
        pl.lit(params.margin_sd).alias("sd_m"),
        pl.lit(params.total_sd).alias("sd_t"),
        (pl.col("margin").abs() >= BLOWOUT_ABS).alias("blowout"),
        (pl.col("margin").abs() <= CLOSE_ABS).alias("close"),
        (pl.col("total") >= (pl.col("mu_t") + TOTAL_DELTA).ceil()).alias("shootout"),
        (pl.col("total") <= (pl.col("mu_t") - TOTAL_DELTA).floor()).alias("grind"),
    )


# ---------------------------------------------------------------- calibration per fold


def fit_fold_map(
    pairs_dir: Path, model_dir: Path, train: int
) -> lc.CalMap:
    _refuse(train)
    d, _ = lcb.load_history(pairs_dir, model_dir, (train,))
    block = np.datetime64(f"{train + 1}-07-01", "D")
    cells = lc.fit_cells(lcb.to_arrays(d), block, (cb.BOOK,), STATS)
    return lc.CalMap({}, {(c.book, c.stat): c for c in cells})


def calibrated_season(data: dict[str, Any], cal: lc.CalMap) -> None:
    for stat in STATS:
        a = data[stat]
        a["cal"], a["k_lo"], a["k_hi"] = cb.calibrate_surv_banded(
            cal, stat, a["raw"], a["med"], a["spr"], a["st10"]
        )


def add_z(data: dict[str, Any], seed: int) -> None:
    rng = np.random.default_rng(seed)
    for stat in STATS:
        a = data[stat]
        a["z"] = cb.pit_z(a["cal"], a["y"], rng.random(len(a["y"])))


# ---------------------------------------------------------------- correlations and script params


def game_residuals(
    data: dict[str, Any], games: pl.DataFrame
) -> list[GameResiduals]:
    gm = {r["game_id"]: r for r in games.iter_rows(named=True)}
    ids = np.concatenate([data[s]["game_id"] for s in STATS])
    pl_id = np.concatenate([data[s]["player_id"] for s in STATS])
    tm = np.concatenate([data[s]["team_id"] for s in STATS])
    st = np.concatenate([np.full(len(data[s]["y"]), i) for i, s in enumerate(STATS)])
    z = np.concatenate([data[s]["z"] for s in STATS])
    order = np.argsort(ids, kind="stable")
    ids, pl_id, tm, st, z = ids[order], pl_id[order], tm[order], st[order], z[order]
    cuts = np.nonzero(np.r_[True, ids[1:] != ids[:-1]])[0]
    ends = np.r_[cuts[1:], len(ids)]
    out: list[GameResiduals] = []
    for a, b in zip(cuts, ends, strict=True):
        g = gm.get(str(ids[a]))
        if g is None or not math.isfinite(g["mu_m"]) or g["mu_t"] is None:
            continue
        out.append(
            GameResiduals(
                int(g["home_team"]),
                float((g["margin"] - g["mu_m"]) / g["sd_m"]),
                float((g["total"] - g["mu_t"]) / g["sd_t"]),
                pl_id[a:b].astype(np.int64),
                tm[a:b].astype(np.int64),
                st[a:b].astype(np.int64),
                z[a:b].astype(np.float64),
            )
        )
    return out


def role_of(st10: F64) -> NDArray[np.str_]:
    return np.where(st10 >= 0.5, "starter", "bench")


def fit_script_params(
    data: dict[str, Any], games: pl.DataFrame
) -> list[list[Any]]:
    """[script, stat, role, mu, sigma, n] per cell, shrunk toward (0, 1) with k = SCRIPT_K."""
    flags = {
        sc: dict(zip(games["game_id"].to_list(), games[sc].to_list(), strict=True))
        for sc in ("blowout", "close", "shootout", "grind")
    }
    rows: list[list[Any]] = []
    for sc, fl in flags.items():
        for stat in STATS:
            a = data[stat]
            in_sc = np.array([bool(fl.get(g, False)) for g in a["game_id"]])
            role = role_of(a["st10"])
            for r in ("starter", "bench"):
                sel = in_sc & (role == r)
                n = int(sel.sum())
                zz = a["z"][sel]
                mu = float(zz.mean()) if n else 0.0
                var = float(zz.var()) if n else 1.0
                w = n / (n + SCRIPT_K)
                mu_s = w * mu
                var_s = (n * (var + (mu - mu_s) ** 2) + SCRIPT_K * 1.0) / (n + SCRIPT_K)
                rows.append([sc, stat, r, mu_s, math.sqrt(var_s), n])
    return rows


# ---------------------------------------------------------------- legs and pairs


def build_legs(
    pairs_dir: Path, season: int, data: dict[str, Any]
) -> dict[str, Any]:
    """DraftKings T-60 over legs at x.5 main lines with raw and calibrated P and the outcome."""
    _refuse(season)
    p = lcb.read_pairs(pairs_dir / f"pairs_{season}.parquet", season, (cb.BOOK,))
    p = p.filter(((pl.col("line") % 1.0) - 0.5).abs() < 1e-9)
    cols: dict[str, list[Any]] = {k: [] for k in
        ("game_id", "date", "player_id", "team_id", "stat", "k", "raw", "cal", "hit", "st10", "idx")}  # fmt: skip
    for stat in STATS:
        a = data[stat]
        key = {
            (g, int(pid)): i
            for i, (g, pid) in enumerate(zip(a["game_id"], a["player_id"], strict=True))
        }
        ps = p.filter(pl.col("stat") == stat)
        for r in ps.iter_rows(named=True):
            i = key.get((r["game_id"], int(r["player_id"])))
            if i is None:
                continue
            k = int(math.floor(r["line"])) + 1
            if k > a["raw"].shape[1]:
                continue
            cols["game_id"].append(r["game_id"])
            cols["date"].append(np.datetime64(r["game_date"], "D"))
            cols["player_id"].append(int(r["player_id"]))
            cols["team_id"].append(int(a["team_id"][i]))
            cols["stat"].append(STATS.index(stat))
            cols["k"].append(k)
            cols["raw"].append(float(a["raw"][i, k - 1]))
            cols["cal"].append(float(a["cal"][i, k - 1]))
            cols["hit"].append(float(a["y"][i] >= k))
            cols["st10"].append(float(a["st10"][i]))
            cols["idx"].append(i)
    return {k: np.asarray(v) for k, v in cols.items()}


def _pairs_indices(legs: dict[str, Any], seed: int, cap: int) -> dict[str, tuple[NDArray[np.int64], NDArray[np.int64]]]:
    rng = np.random.default_rng(seed)
    order = np.argsort(legs["game_id"], kind="stable")
    gid = legs["game_id"][order]
    cuts = np.nonzero(np.r_[True, gid[1:] != gid[:-1]])[0]
    ends = np.r_[cuts[1:], len(gid)]
    acc: dict[str, list[tuple[NDArray[np.int64], NDArray[np.int64]]]] = {"SP": [], "TM": [], "OP": []}
    for a, b in zip(cuts, ends, strict=True):
        idx = order[a:b]
        ii, jj = np.triu_indices(len(idx), 1)
        x, y = idx[ii], idx[jj]
        same_p = legs["player_id"][x] == legs["player_id"][y]
        same_t = legs["team_id"][x] == legs["team_id"][y]
        acc["SP"].append((x[same_p], y[same_p]))
        acc["TM"].append((x[~same_p & same_t], y[~same_p & same_t]))
        acc["OP"].append((x[~same_t], y[~same_t]))
    out: dict[str, tuple[NDArray[np.int64], NDArray[np.int64]]] = {}
    for cls, parts in acc.items():
        x = np.concatenate([p[0] for p in parts])
        y = np.concatenate([p[1] for p in parts])
        if len(x) > cap:
            sel = np.sort(rng.choice(len(x), cap, replace=False))
            x, y = x[sel], y[sel]
        out[cls] = (x, y)
    # cross-game, same date
    d = legs["date"]
    xs: list[NDArray[np.int64]] = []
    ys: list[NDArray[np.int64]] = []
    for day in np.unique(d):
        idx = np.nonzero(d == day)[0]
        if len(idx) < 2:
            continue
        m = min(len(idx) * 8, 4000)
        u = rng.choice(idx, m)
        v = rng.choice(idx, m)
        ok = legs["game_id"][u] != legs["game_id"][v]
        xs.append(u[ok])
        ys.append(v[ok])
    x, y = np.concatenate(xs), np.concatenate(ys)
    if len(x) > cap:
        sel = np.sort(rng.choice(len(x), cap, replace=False))
        x, y = x[sel], y[sel]
    out["XG"] = (x, y)
    hi = np.nonzero(legs["raw"] >= 0.70)[0]
    hx: list[NDArray[np.int64]] = []
    hy: list[NDArray[np.int64]] = []
    for day in np.unique(d[hi]):
        idx = hi[d[hi] == day]
        if len(idx) < 2:
            continue
        ii, jj = np.triu_indices(len(idx), 1)
        u, v = idx[ii], idx[jj]
        ok = legs["game_id"][u] != legs["game_id"][v]
        hx.append(u[ok])
        hy.append(v[ok])
    x, y = np.concatenate(hx), np.concatenate(hy)
    if len(x) > cap:
        sel = np.sort(rng.choice(len(x), cap, replace=False))
        x, y = x[sel], y[sel]
    out["XG70"] = (x, y)
    return out


# ---------------------------------------------------------------- exact bivariate (vectorised)

_GL_X, _GL_W = np.polynomial.legendre.leggauss(96)


def biv_over(a1: F64, a2: F64, rho: F64, upper: float = 9.0) -> F64:
    """P(Z1 > a1, Z2 > a2) for standard bivariate normals, Gauss-Legendre on the conditional form.
    A test pins this to the reference implementation in ``combo.py``."""
    rho = np.clip(rho, -0.9999, 0.9999)
    s = np.sqrt(1.0 - rho * rho)
    lo = np.minimum(a1, upper)
    half = (upper - lo) / 2.0
    out = np.zeros(len(a1))
    chunk = 20000
    for i in range(0, len(a1), chunk):
        sl = slice(i, i + chunk)
        z = lo[sl, None] + half[sl, None] * (_GL_X[None, :] + 1.0)
        f = stats.norm.pdf(z) * stats.norm.sf((a2[sl, None] - rho[sl, None] * z) / s[sl, None])
        out[sl] = half[sl] * (f * _GL_W[None, :]).sum(axis=1)
    return np.clip(out, 0.0, 1.0)


def latent_a(p: F64) -> F64:
    return np.asarray(stats.norm.ppf(np.clip(1.0 - p, 1e-12, 1 - 1e-12)))


# ---------------------------------------------------------------- bootstrap


def _cluster_sums(cl: NDArray[Any], *vals: F64) -> tuple[F64, ...]:
    _, inv = np.unique(cl, return_inverse=True)
    return tuple(np.bincount(inv, weights=v) for v in vals)


def boot_ratio(cl: NDArray[Any], num: F64, den: F64, rng: np.random.Generator) -> dict[str, float]:
    sn, sd = _cluster_sums(cl, num, den)
    g = len(sn)
    idx = rng.integers(0, g, size=(B_BOOT, g))
    r = sn[idx].sum(axis=1) / sd[idx].sum(axis=1)
    return {
        "est": float(sn.sum() / sd.sum()),
        "lo": float(np.quantile(r, 0.025)),
        "hi": float(np.quantile(r, 0.975)),
        "n_clusters": g,
    }


def boot_mean(cl: NDArray[Any], v: F64, rng: np.random.Generator) -> dict[str, float]:
    sv, sn = _cluster_sums(cl, v, np.ones_like(v))
    g = len(sv)
    idx = rng.integers(0, g, size=(B_BOOT, g))
    r = sv[idx].sum(axis=1) / sn[idx].sum(axis=1)
    return {
        "est": float(sv.sum() / sn.sum()),
        "lo": float(np.quantile(r, 0.025)),
        "hi": float(np.quantile(r, 0.975)),
        "n_clusters": g,
    }


def ll(p: F64, y: F64) -> F64:
    q = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(q) + (1 - y) * np.log(1 - q))


# ---------------------------------------------------------------- evaluation


def _rho_lookup(table: Sequence[Sequence[Any]]) -> dict[tuple[str, str, str], float]:
    return {(r[0], r[1], r[2]): float(r[3]) for r in table}


def pair_rho_vec(legs: dict[str, Any], x: NDArray[Any], y: NDArray[Any], cls: str, tab: dict[tuple[str, str, str], float]) -> F64:
    if cls in ("XG", "XG70"):
        return np.zeros(len(x))
    rel = {"SP": "same_player", "TM": "teammate", "OP": "opponent"}[cls]
    out = np.zeros(len(x))
    sx, sy = legs["stat"][x], legs["stat"][y]
    for i in range(len(x)):
        a, b = sorted((STATS[sx[i]], STATS[sy[i]]))
        out[i] = tab.get((rel, a, b), 0.0)
    return out


def eval_pairs(
    legs: dict[str, Any], pairs: dict[str, tuple[Any, Any]], tab: dict[tuple[str, str, str], float], rng: np.random.Generator
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cls, (x, y) in pairs.items():
        if len(x) == 0:
            continue
        rho = pair_rho_vec(legs, x, y, cls, tab)
        pc1, pc2 = legs["cal"][x], legs["cal"][y]
        pr1, pr2 = legs["raw"][x], legs["raw"][y]
        hit = legs["hit"][x] * legs["hit"][y]
        prod_cal, prod_raw = pc1 * pc2, pr1 * pr2
        resid = (legs["hit"][x] - pc1) * (legs["hit"][y] - pc2)
        resid_den = np.sqrt(pc1 * (1 - pc1) * pc2 * (1 - pc2))
        joint = biv_over(latent_a(pc1), latent_a(pc2), rho) if cls not in ("XG", "XG70") else prod_cal
        cl = legs["date"][x] if cls in ("XG", "XG70") else legs["game_id"][x]
        res: dict[str, Any] = {
            "n_pairs": int(len(x)),
            "mean_rho": float(rho.mean()),
            "pred_lift": boot_ratio(cl, joint, prod_cal, rng),
            "realised_vs_cal_product": boot_ratio(cl, hit, prod_cal, rng),
            "realised_vs_raw_product": boot_ratio(cl, hit, prod_raw, rng),
            "realised_vs_copula": boot_ratio(cl, hit, joint, rng),
            "resid_r": boot_ratio(cl, resid, resid_den, rng),
            "mean_hit": float(hit.mean()),
            "mean_cal_product": float(prod_cal.mean()),
            "mean_raw_product": float(prod_raw.mean()),
            "mean_copula": float(joint.mean()),
            "logloss_copula_minus_indep": boot_mean(cl, ll(joint, hit) - ll(prod_cal, hit), rng),
            "logloss_cal_minus_raw_product": boot_mean(cl, ll(prod_cal, hit) - ll(prod_raw, hit), rng),
        }
        out[cls] = res
    return out


def eval_bands(legs: dict[str, Any], rng: np.random.Generator) -> list[dict[str, Any]]:
    rows = []
    for lo, hi in zip(BANDS[:-1], BANDS[1:], strict=True):
        sel = (legs["raw"] >= lo) & (legs["raw"] < hi + (1e-9 if hi == 1.0 else 0.0))
        n = int(sel.sum())
        if n < 50:
            rows.append({"band": [lo, hi], "n": n})
            continue
        cl = legs["game_id"][sel]
        rows.append(
            {
                "band": [lo, hi],
                "n": n,
                "mean_raw": float(legs["raw"][sel].mean()),
                "mean_cal": float(legs["cal"][sel].mean()),
                "observed": float(legs["hit"][sel].mean()),
                "gap_raw": boot_mean(cl, legs["hit"][sel] - legs["raw"][sel], rng),
                "gap_cal": boot_mean(cl, legs["hit"][sel] - legs["cal"][sel], rng),
            }
        )
    return rows


def eval_scripts(
    legs: dict[str, Any],
    sp_pairs: tuple[Any, Any],
    params: list[list[Any]],
    corr_tab: dict[tuple[str, str, str], float],
    games: pl.DataFrame,
    rng: np.random.Generator,
) -> dict[str, Any]:
    ptab = {(r[0], r[1], r[2]): (float(r[3]), float(r[4])) for r in params}
    out: dict[str, Any] = {}
    for sc in ("blowout", "close", "shootout", "grind"):
        fl = dict(zip(games["game_id"].to_list(), games[sc].to_list(), strict=True))
        in_sc = np.array([bool(fl.get(g, False)) for g in legs["game_id"]])
        n = int(in_sc.sum())
        if n < 100:
            out[sc] = {"n_legs": n}
            continue
        role = role_of(legs["st10"])
        mu = np.array([ptab[(sc, STATS[s], r)][0] for s, r in zip(legs["stat"], role, strict=True)])
        sg = np.array([ptab[(sc, STATS[s], r)][1] for s, r in zip(legs["stat"], role, strict=True)])
        a = latent_a(legs["cal"])
        p_s = stats.norm.sf((a - mu) / sg)
        sel = in_sc
        cl = legs["game_id"][sel]
        hit, p0, ps = legs["hit"][sel], legs["cal"][sel], p_s[sel]
        res: dict[str, Any] = {
            "n_legs": n,
            "n_games": int(len(np.unique(cl))),
            "observed": float(hit.mean()),
            "mean_uncond": float(p0.mean()),
            "mean_script": float(ps.mean()),
            "brier_script_minus_uncond": boot_mean(cl, (hit - ps) ** 2 - (hit - p0) ** 2, rng),
            "obs_minus_script": boot_mean(cl, hit - ps, rng),
            "obs_minus_uncond": boot_mean(cl, hit - p0, rng),
        }
        x, y = sp_pairs
        both = in_sc[x] & in_sc[y]
        if both.sum() >= 100:
            xs, ys = x[both], y[both]
            a1 = (latent_a(legs["cal"][xs]) - mu[xs]) / sg[xs]
            a2 = (latent_a(legs["cal"][ys]) - mu[ys]) / sg[ys]
            rho = pair_rho_vec(legs, xs, ys, "SP", corr_tab)
            pj_s = biv_over(a1, a2, rho)
            pj_0 = biv_over(latent_a(legs["cal"][xs]), latent_a(legs["cal"][ys]), rho)
            hh = legs["hit"][xs] * legs["hit"][ys]
            cl2 = legs["game_id"][xs]
            res["same_player_pairs"] = {
                "n_pairs": int(both.sum()),
                "observed": float(hh.mean()),
                "mean_script_joint": float(pj_s.mean()),
                "mean_uncond_joint": float(pj_0.mean()),
                "obs_over_script_joint": boot_ratio(cl2, hh, pj_s, rng),
                "obs_over_uncond_joint": boot_ratio(cl2, hh, pj_0, rng),
            }
        out[sc] = res
    return out


def script_label(folds: list[dict[str, Any]], sc: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    for f in folds:
        r = f["scripts"].get(sc, {})
        if "brier_script_minus_uncond" not in r:
            return "exploratory", ["insufficient sample"]
        if not r["brier_script_minus_uncond"]["hi"] < 0:
            reasons.append(f"{f['fold']}: Brier CI upper {r['brier_script_minus_uncond']['hi']:+.5f} not < 0")
        g = r["obs_minus_script"]
        if not (g["lo"] >= -0.02 and g["hi"] <= 0.02):
            reasons.append(f"{f['fold']}: calibration-in-the-large CI [{g['lo']:+.4f}, {g['hi']:+.4f}] outside +-0.02")
        sp = r.get("same_player_pairs")
        if sp is None:
            reasons.append(f"{f['fold']}: no same-player pairs")
        else:
            q = sp["obs_over_script_joint"]
            if not (q["lo"] <= 1.0 <= q["hi"]):
                reasons.append(f"{f['fold']}: same-player joint ratio CI [{q['lo']:.3f}, {q['hi']:.3f}] excludes 1")
    return ("calibrated" if not reasons else "exploratory"), reasons


def run(
    model_dir: Path, pairs_dir: Path, db: Path, joint_model: Path, game_rows_dir: Path
) -> dict[str, Any]:
    games = games_table(db, joint_model, game_rows_dir)
    seasons_data = {s: load_season(model_dir, s) for s in SEASONS}
    rng = np.random.default_rng(SEED)
    folds: list[dict[str, Any]] = []
    for train, test, kind in FOLDS:
        cal = fit_fold_map(pairs_dir, model_dir, train)
        for s in SEASONS:
            calibrated_season(seasons_data[s], cal)
            add_z(seasons_data[s], SEED + s)
        res_train = game_residuals(seasons_data[train], games)
        pc = estimate_game_corr(res_train, SHRINK_K)
        corr_rows = corr_to_json(pc)
        corr_tab = _rho_lookup(corr_rows)
        sparams = fit_script_params(seasons_data[train], games)
        legs = build_legs(pairs_dir, test, seasons_data[test])
        pairs = _pairs_indices(legs, SEED + test, MIN_PAIR_CAP)
        folds.append(
            {
                "fold": f"train {train} -> test {test} ({kind})",
                "n_legs": int(len(legs["hit"])),
                "n_games": int(len(np.unique(legs["game_id"]))),
                "pairs": eval_pairs(legs, pairs, corr_tab, rng),
                "bands": eval_bands(legs, rng),
                "scripts": eval_scripts(legs, pairs["SP"], sparams, corr_tab, games, rng),
                "script_params": sparams,
                "corr": corr_rows,
                "map_n_fit": {k[1]: v.n_fit for k, v in cal.cells.items()},
                "pit_sd_test_season": {
                    stat: float(seasons_data[test][stat]["z"].std()) for stat in STATS
                },
                "pit_mean_test_season": {
                    stat: float(seasons_data[test][stat]["z"].mean()) for stat in STATS
                },
            }
        )
    labels = {}
    for sc in ("blowout", "close", "shootout", "grind"):
        lab, why = script_label(folds, sc)
        labels[sc] = {"status": lab, "why_not": why}
    prior = {
        sc: {
            "realised_rate": {
                int(s): float(games.filter(pl.col("season") == s)[sc].mean() or 0.0) for s in SEASONS
            }
        }
        for sc in ("blowout", "close", "shootout", "grind")
    }
    return {"folds": folds, "script_labels": labels, "script_base_rates": prior}


def production_params(
    model_dir: Path, pairs_dir: Path, db: Path, joint_model: Path, game_rows_dir: Path, map_path: Path
) -> dict[str, Any]:
    """Pooled correlations and script parameters fitted on BOTH seasons with the shipped map
    (the payload's shared block). In-sample by construction; the fold results are the check."""
    games = games_table(db, joint_model, game_rows_dir)
    cal = lc.load_map(map_path)
    both: dict[str, Any] = {}
    parts = {s: load_season(model_dir, s) for s in SEASONS}
    for stat in STATS:
        both[stat] = {k: np.concatenate([parts[s][stat][k] for s in SEASONS]) if k != "raw" else _cat_raw([parts[s][stat]["raw"] for s in SEASONS]) for k in parts[2023][stat]}
    calibrated_season(both, cal)
    add_z(both, SEED)
    pc = estimate_game_corr(game_residuals(both, games), SHRINK_K)
    return {
        "corr": corr_to_json(pc),
        "script_params": fit_script_params(both, games),
        "map_sha8": str(cal.meta.get("map_sha256", ""))[:8],
        "n_games_corr": int(games.filter(pl.col("season").is_in(list(SEASONS))).height),
    }


def _cat_raw(arrs: list[F64]) -> F64:
    k = max(a.shape[1] for a in arrs)
    return np.concatenate([np.pad(a, ((0, 0), (0, k - a.shape[1]))) for a in arrs])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--pairs-dir", required=True)
    ap.add_argument("--db", required=True, help="nba.duckdb (opened read-only)")
    ap.add_argument("--joint-model", required=True)
    ap.add_argument("--game-rows-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--params-out", default=None, help="write combo_params_v1.json")
    a = ap.parse_args(argv)
    args = (Path(a.model_dir), Path(a.pairs_dir), Path(a.db), Path(a.joint_model), Path(a.game_rows_dir))
    res = run(*args)
    res["made"] = date.today().isoformat()
    Path(a.out).write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    print("wrote", a.out)
    if a.params_out:
        prod = production_params(*args, lc.MAP_PATH)
        prod["script_status"] = res["script_labels"]
        prod["definitions"] = DEFINITIONS
        Path(a.params_out).write_text(json.dumps(prod, indent=1, sort_keys=True) + "\n")
        print("wrote", a.params_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
