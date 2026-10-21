# ruff: noqa: E501
"""WOWY_REDISTRIBUTION follow-up 2, variant PROP: propensity-weighted structural allocation (DRAFT rule; gate only).

    cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_prop_gate

PROP: each remaining player's share of the vacated V_s is proportional to (as-of per-game stat s = rate x expected
minutes) x g(traits), g = max(1 + sum_k theta_k z_k, 0.05), z the standardised as-of traits of
``nba/props/struct_alloc.py::TRAITS`` (usage per 36, assist-to-usage, 3PA share, FT rate, starter share, minutes,
touches per 36, passes per touch, secondary + FT assists per 36, rebound chances per 36, uncontested share, position
group G / C). Unassisted-FG share is NOT used (possessions.assister_id is NULL for accented passers: defect D15).

Fit of g, per stat, pooled over seasons with partial pooling: the target is the excess of the stat over its as-of mean
(`stat - m10_stat`) in past games with a rotation player OUT; the model is excess = kappa0 x0 + sum_k kappa_k x0 z_k
with x0 = the plain RATE allocation; ridge on the interaction terms only (lambda chosen by two-fold season CV on 2020
and 2021 MSE), theta_k = kappa_k / kappa0. No per-player intercepts (cost). Coefficients and game-clustered SEs come from
the unpenalised fit. OUT-OF-FOLD: the g used on season Y is fitted on other seasons only (2020 <- 2021, 2021 <- 2020,
2022 <- 2020-21, 2023 and 2024 <- 2020-22); outcomes of 2023, 2024 and 2025 are never read.
Adds the key ``prop`` to ``reports/prereg_wowy_redistribution/gate.json``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import polars as pl
from scipy.stats import chi2

from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import (
    PROP_STATS,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_redistribution_slices as SL

FLOOR = -0.005
LAMBDAS = (0.0, 0.001, 0.01, 0.1, 1.0)
FOLDS = {
    2020: (2021,),
    2021: (2020,),
    2022: (2020, 2021),
    2023: (2020, 2021, 2022),
    2024: (2020, 2021, 2022),
}


def _f(x: Any) -> float:
    return float(x)


def clustered_ols(
    y: np.ndarray, x: np.ndarray, key: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """OLS (no intercept) with a game-clustered sandwich: coefficients, standard errors, covariance."""
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    e = y - x @ beta
    _, inv = np.unique(key.astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    s = np.zeros((g, x.shape[1]))
    np.add.at(s, inv, x * e[:, None])
    bread = np.linalg.pinv(x.T @ x)
    n, k = x.shape
    cov = bread @ (s.T @ s) @ bread * (g / (g - 1)) * ((n - 1) / (n - k))
    return beta, np.sqrt(np.diag(cov)), cov


def design(
    df: pl.DataFrame, stat: str, mu: np.ndarray | None = None, sd: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """x0 (plain RATE allocation), excess, game ids, and the interaction design [x0, x0 z_1 ... x0 z_K]."""
    tr = df.select(list(SA.TRAITS)).to_numpy()
    x0 = df[f"struct_d_{stat}"].to_numpy()
    if mu is None or sd is None:
        mu = tr.mean(axis=0)
        sd = tr.std(axis=0)
        sd = np.where(sd > 0, sd, 1.0)
    z = (tr - mu) / sd
    d = np.column_stack([x0, x0[:, None] * z])
    return d, df["resid"].to_numpy(), df["game_id"].to_numpy(), mu, sd


def ridge_fit(d: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Ridge on the interaction columns only (columns scaled to unit rms so lambda is comparable)."""
    scale = np.sqrt((d**2).mean(axis=0))
    scale = np.where(scale > 0, scale, 1.0)
    ds = d / scale
    pen = np.diag([0.0] + [lam * len(y)] * (d.shape[1] - 1))
    beta = np.linalg.solve(ds.T @ ds + pen, ds.T @ y)
    return np.asarray(beta / scale)


def train_rows(frame: pl.DataFrame, stat: str) -> pl.DataFrame:
    sf = stat_frame(
        frame.filter((pl.col("has_report") == 1) & (pl.col("n_out_rot") > 0)), stat
    ).filter(pl.col(f"struct_d_{stat}") > 0)
    return sf.filter(pl.col("resid").is_finite())


def fit_theta(frames: list[pl.DataFrame], stat: str, lam: float) -> dict[str, Any]:
    df = pl.concat([train_rows(f_, stat) for f_ in frames], how="vertical_relaxed")
    d, y, _g, mu, sd = design(df, stat)
    beta = ridge_fit(d, y, lam)
    k0 = float(beta[0])
    theta = (beta[1:] / k0) if k0 > 1e-6 else np.zeros(d.shape[1] - 1)
    return {
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "theta": theta.tolist(),
        "kappa0": k0,
        "n": len(y),
    }


def oof_mse(
    train: list[pl.DataFrame], test: pl.DataFrame, stat: str, lam: float
) -> dict[str, float]:
    th = fit_theta(train, stat, lam)
    te = train_rows(test, stat)
    d, y, _g, _m, _s = design(te, stat, np.array(th["mu"]), np.array(th["sd"]))
    # prediction x0 (kappa0 + sum kappa_k z_k) = d @ beta with beta = kappa0 (1, theta)
    beta = np.concatenate([[th["kappa0"]], th["kappa0"] * np.array(th["theta"])])
    pred = d @ beta
    plain = d[:, 0] * th["kappa0"]
    return {
        "mse0": float((y**2).mean()),
        "mse_plain": float(((y - plain) ** 2).mean()),
        "mse_prop": float(((y - pred) ** 2).mean()),
        "n": float(len(y)),
    }


def gbm_arms4(
    train: pl.DataFrame, test: pl.DataFrame, stat: str, plain: str, prop: str
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """Squared error of a production-feature GBM on test rows: base, + plain STRUCT, + PROP, + permuted PROP (noise)."""
    import lightgbm as lgb

    names = [c for c in stat_feature_names(stat) if c in train.columns]
    rng = np.random.default_rng(0)

    def prep(df: pl.DataFrame) -> pl.DataFrame:
        sf = stat_frame(df, stat)
        return sf.with_columns(pl.Series("_noise", rng.permutation(sf[prop].to_numpy())))

    tr, te = prep(train), prep(test)
    errs: dict[str, np.ndarray] = {}
    for arm, extra in (("base", []), ("plain", [plain]), ("prop", [prop]), ("noise", ["_noise"])):
        cols = [*names, *extra]
        x = tr.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()
        ok = np.isfinite(tr["resid"].to_numpy())
        m = lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.04,
            num_leaves=15,
            min_child_samples=100,
            colsample_bytree=0.8,
            subsample=0.8,
            subsample_freq=1,
            random_state=0,
            n_jobs=2,
            verbose=-1,
        )
        m.fit(x[ok], tr["resid"].to_numpy()[ok])
        xt = te.select([pl.col(c).cast(pl.Float64) for c in cols]).to_numpy()
        errs[arm] = np.asarray((te["resid"].to_numpy() - m.predict(xt)) ** 2)
    return te, errs


def gbm_summary4(
    te: pl.DataFrame, errs: dict[str, np.ndarray], mask: np.ndarray, n_boot: int = 400
) -> dict[str, Any]:
    ok = mask & np.all([np.isfinite(e) for e in errs.values()], axis=0)
    _, inv = np.unique(te["game_id"].to_numpy()[ok], return_inverse=True)
    g = int(inv.max()) + 1
    sums = {k: np.bincount(inv, weights=e[ok], minlength=g) for k, e in errs.items()}
    draws = np.random.default_rng(0).integers(0, g, size=(n_boot, g))

    def rel(a: str, b: str) -> dict[str, Any]:
        r = 1.0 - sums[a][draws].sum(axis=1) / sums[b][draws].sum(axis=1)
        return {
            "relative_gain": _f(1.0 - sums[a].sum() / sums[b].sum()),
            "ci": [_f(np.quantile(r, 0.025)), _f(np.quantile(r, 0.975))],
        }

    return {
        "n": int(ok.sum()),
        "games": g,
        "plain": rel("plain", "base"),
        "prop": rel("prop", "base"),
        "noise": rel("noise", "base"),
        "prop_vs_plain": rel("prop", "plain"),
    }


def main() -> dict[str, Any]:
    t0 = time.time()
    db = str(G.REAL / "nba.duckdb")
    hdb = str(G.REAL / "data/history/nba_history.duckdb")
    feats, _sets, outp, games = G.frame_and_out_sets(db)
    import nba.features.game_tipoff as GT

    old = GT.SCHEDULE_DIR
    GT.SCHEDULE_DIR = G.REAL / "data/history/schedule"
    try:
        hfeats, _hs, houtp, hgames = G.frame_and_out_sets(hdb)
    finally:
        GT.SCHEDULE_DIR = old
    box_m, box_h = SG.load_box_state(db), SG.load_box_state(hdb)
    full = pl.concat([box_h, box_m], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = pl.concat(
        [
            games.select("game_id", pl.col("game_date").cast(pl.Date)),
            hgames.select("game_id", pl.col("game_date").cast(pl.Date)),
        ]
    )
    pos = pl.concat([SG.load_positions(hdb), SG.load_positions(db)]).unique("player_id")
    trk_cov = {
        "tracking_rows_with_touches>0": _f((full["touches"].fill_null(0) > 0).mean()),
        "rows": full.height,
    }
    print(f"[{time.time() - t0:5.1f}s] inputs ready; tracking coverage {trk_cov}", flush=True)

    def attach(
        fe: pl.DataFrame,
        op: pl.DataFrame,
        bx: pl.DataFrame,
        seasons: list[int],
        prop: dict[str, dict[str, Any]] | None,
    ) -> pl.DataFrame:
        sub = fe.filter(pl.col("season").is_in(seasons))
        rows = sub.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
        op2 = op.join(rows.select("game_id").unique(), on="game_id", how="inner")
        st = SA.build_struct(rows, op2, pool, states, dates, pos, prop=prop)
        j = sub.join(st, on=["game_id", "team_id", "player_id"], how="left")
        zero = [c for c in st.columns if c.startswith(("struct_", "leader_out_", "prop_d_"))]
        j = j.with_columns([pl.col(c).fill_null(0.0) for c in zero])
        return j.join(
            bx.select("game_id", "player_id", pl.col("minutes").alias("real_min")),
            on=["game_id", "player_id"],
            how="left",
        )

    src = {
        2020: (hfeats, houtp, box_h),
        2021: (hfeats, houtp, box_h),
        2022: (feats, outp, box_m),
        2023: (feats, outp, box_m),
        2024: (feats, outp, box_m),
    }
    plain = {y: attach(*src[y][:3], [y], None) for y in src}
    print(f"[{time.time() - t0:5.1f}s] plain frames built", flush=True)
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "tracking_coverage": trk_cov,
        "traits": list(SA.TRAITS),
    }

    # ---- lambda by two-fold season CV (2020 <-> 2021) and the pooled fit with clustered SEs (2020-22)
    cv: dict[str, Any] = {}
    lam_best: dict[str, float] = {}
    for stat in PROP_STATS:
        tab = {}
        for lam in LAMBDAS:
            a = oof_mse([plain[2020]], plain[2021], stat, lam)
            b = oof_mse([plain[2021]], plain[2020], stat, lam)
            tab[str(lam)] = {
                "mse_prop_2020to2021": a["mse_prop"],
                "mse_prop_2021to2020": b["mse_prop"],
                "mse_plain_2020to2021": a["mse_plain"],
                "mse_plain_2021to2020": b["mse_plain"],
                "mse0_2020to2021": a["mse0"],
                "mse0_2021to2020": b["mse0"],
            }
            tab[str(lam)]["total"] = (
                tab[str(lam)]["mse_prop_2020to2021"] / a["mse0"]
                + tab[str(lam)]["mse_prop_2021to2020"] / b["mse0"]
            )
        lam_best[stat] = float(min(LAMBDAS, key=lambda lm: tab[str(lm)]["total"]))
        cv[stat] = {"table": tab, "lambda": lam_best[stat]}
    out["cv"] = cv
    print(f"[{time.time() - t0:5.1f}s] lambda: {lam_best}", flush=True)

    coef: dict[str, Any] = {}
    for stat in PROP_STATS:
        df = pl.concat(
            [train_rows(plain[y], stat) for y in (2020, 2021, 2022)], how="vertical_relaxed"
        )
        d, y, g, mu, sd = design(df, stat)
        beta, se, cov = clustered_ols(y, d, g)
        wald = _f(beta[1:] @ np.linalg.pinv(cov[1:, 1:]) @ beta[1:])
        ridge = fit_theta([plain[y_] for y_ in (2020, 2021, 2022)], stat, lam_best[stat])
        coef[stat] = {
            "n": int(len(y)),
            "games": int(len(np.unique(g))),
            "kappa0": {"coef": _f(beta[0]), "se": _f(se[0])},
            "interactions(z-scored trait x x0)": {
                t: {"coef": _f(b), "se": _f(s_), "t": _f(b / s_)}
                for t, b, s_ in zip(SA.TRAITS, beta[1:], se[1:], strict=True)
            },
            "wald_all_interactions": {
                "chi2": wald,
                "df": len(SA.TRAITS),
                "p": _f(chi2.sf(wald, len(SA.TRAITS))),
            },
            "ridge_theta(lambda chosen)": dict(zip(SA.TRAITS, ridge["theta"], strict=True)),
            "ridge_kappa0": ridge["kappa0"],
            "lambda": lam_best[stat],
        }
    out["g_coefficients_pooled_2020_22"] = coef
    print(f"[{time.time() - t0:5.1f}s] coefficients done", flush=True)

    # ---- out-of-fold PROP frames
    props: dict[int, dict[str, dict[str, Any]]] = {}
    for y_, trn in FOLDS.items():
        props[y_] = {
            stat: fit_theta([plain[t] for t in trn], stat, lam_best[stat]) for stat in PROP_STATS
        }
    full_frames = {y_: attach(*src[y_][:3], [y_], props[y_]) for y_ in src}
    print(f"[{time.time() - t0:5.1f}s] PROP frames built", flush=True)
    out["theta_by_fold"] = {
        str(y_): {
            st: {"kappa0": v["kappa0"], "theta": dict(zip(SA.TRAITS, v["theta"], strict=True))}
            for st, v in d_.items()
        }
        for y_, d_ in props.items()
    }

    version = {
        "pts": "struct_u_pts",
        "reb": "struct_d_reb",
        "ast": "struct_d_ast",
        "fg3m": "struct_d_fg3m",
    }  # plain STRUCT choice (2020-22)
    # ---- kappa by slice: plain RATE, plain chosen, PROP (all out-of-fold on 2020-22)
    frames = {"2020": full_frames[2020], "2021": full_frames[2021], "2022": full_frames[2022]}
    kap: dict[str, Any] = {}
    for stat in PROP_STATS:
        kap[stat] = {}
        for vname, col in (
            ("plain_rate", f"struct_d_{stat}"),
            ("plain_chosen", version[stat]),
            ("prop", f"prop_d_{stat}"),
        ):
            parts: dict[str, list[pl.DataFrame]] = {}
            per: dict[str, Any] = {}
            for fname, frame in frames.items():
                msd = SG.slice_masks_struct(frame, col, f"leader_out_{stat}")
                for sn, m_ in msd.items():
                    sub = frame.filter(pl.Series(m_))
                    parts.setdefault(sn, []).append(sub)
                    if fname == "2022":
                        per.setdefault("2022", {})[sn] = {
                            "rows": sub.height,
                            **(SG.slope_on(sub, stat, col) or {}),
                        }
            per["pooled"] = {
                sn: {
                    "rows": sum(p.height for p in lst),
                    **(SG.slope_on(pl.concat(lst, how="vertical_relaxed"), stat, col) or {}),
                }
                for sn, lst in parts.items()
            }
            kap[stat][vname] = per
    out["kappa_inside_slices"] = kap
    print(f"[{time.time() - t0:5.1f}s] kappas done", flush=True)

    # ---- GBM held-out check: train 2020-21 (out-of-fold PROP), test 2022
    train_all = pl.concat(
        [
            full_frames[2020].filter(pl.col("has_report") == 1),
            full_frames[2021].filter(pl.col("has_report") == 1),
        ],
        how="vertical_relaxed",
    )
    test_all = full_frames[2022].filter(pl.col("has_report") == 1)
    gb: dict[str, Any] = {}
    for stat in PROP_STATS:
        te, errs = gbm_arms4(train_all, test_all, stat, version[stat], f"prop_d_{stat}")
        msd = SG.slice_masks_struct(te, f"prop_d_{stat}", f"leader_out_{stat}")
        gb[stat] = {sn: gbm_summary4(te, errs, m_) for sn, m_ in msd.items()}
    out["gbm_train2020_21_test2022"] = gb
    print(f"[{time.time() - t0:5.1f}s] gbm done", flush=True)

    # ---- sizing on 2023 / 2024 stored A0 grids: plain chosen vs PROP
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    lt = G.REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    full_pi = np.load(G.REAL / "data/ops/recent_role_full_cache/pi_R.npz", allow_pickle=True)["pi"]
    fr_eval = pl.concat([full_frames[2023], full_frames[2024]], how="vertical_relaxed")
    sizing: dict[str, Any] = {}
    for stat in PROP_STATS:
        cs = load_components(stat, lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        gid = cs["row_gid"].astype(str)
        season = cs["row_season"]
        y = cs["row_y"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        cols = [version[stat], f"prop_d_{stat}", f"leader_out_{stat}"]
        jf = keys.join(
            fr_eval.select("game_id", "player_id", "vac_min", "n_out_rot", "has_report", *cols),
            on=["game_id", "player_id"],
            how="left",
        ).with_columns(
            pl.col("vac_min").fill_null(0.0),
            pl.col("n_out_rot").fill_null(0),
            pl.col("has_report").fill_null(0),
            *[pl.col(c).fill_null(0.0) for c in cols],
        )
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        hd = dict(
            np.load(
                G.REAL / f"data/ops/recent_role_full_cache/heads_{stat}_R.npz", allow_pickle=True
            ).items()
        )
        q1 = RF.arm_qi(stat, hd, pic, full_pi)
        d_ = PR.crps_int(q1, y) - PR.crps_int(q0, y)
        grid = arm_grid("mixture", cs, pic, stat)
        sizing[stat] = {}
        for vname, col in (("plain_chosen", version[stat]), ("prop", f"prop_d_{stat}")):
            shift = jf[col].to_numpy()
            kp = kap[stat][vname]["pooled"]
            k_pool = max(kp["VAC24"].get("coef", 0.0), 0.0)
            k_22 = max(kap[stat][vname]["2022"]["VAC24"].get("coef", 0.0), 0.0)
            sizing[stat][vname] = {
                "feature": col,
                "kappa_VAC24": {"pooled_2020_22": k_pool, "2022": k_22},
            }
            for s in G.SEASONS:
                in_s = season == s
                sub = jf.filter(pl.Series(in_s))
                msub = SG.slice_masks_struct(sub, col, f"leader_out_{stat}")
                masks = {k: np.zeros(len(y), dtype=bool) for k in msub}
                for k, v in msub.items():
                    masks[k][np.flatnonzero(in_s)] = v

                def gain(
                    kv: float,
                    mask: np.ndarray,
                    shift: np.ndarray = shift,
                    grid: np.ndarray = grid,
                    q0: np.ndarray = q0,
                ) -> tuple[np.ndarray, np.ndarray]:
                    gm = np.zeros(len(q0))
                    gv = np.zeros(len(q0))
                    sel = mask & (shift != 0) & (kv > 0)
                    if sel.any():
                        gm[sel], gv[sel] = SL.expected_gain_var(
                            q0[sel].astype(float),
                            to_integer_support(grid[sel] + kv * shift[sel, None]),
                        )
                    return gm, gv

                vac = masks["VAC24"]
                union = vac | masks["leader_out"]
                g_pool, g_22 = gain(k_pool, union), gain(k_22, union)
                tot = {"pooled_2020_22": _f(g_pool[0][vac].sum()), "2022": _f(g_22[0][vac].sum())}
                seas: dict[str, Any] = {}
                for sn, ms in masks.items():
                    if not ms.any():
                        continue
                    cg = C.cluster_stats(d_[ms], gid[ms])
                    entry: dict[str, Any] = {"rows": int(ms.sum()), "games": int(cg["clusters"])}
                    kin = max(kp[sn].get("coef", 0.0), 0.0)
                    scen: dict[str, tuple[float, tuple[np.ndarray, np.ndarray]]] = {
                        "pooled_2020_22": (k_pool, g_pool),
                        "2022": (k_22, g_22),
                    }
                    if sn != "VAC24":
                        scen["in_slice_pooled"] = (kin, gain(kin, ms))
                    for kn, (kv, (gm, gv)) in scen.items():
                        e = -_f(gm[ms].mean())
                        sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var()))
                        rb = C.sizing_row("b", max(sd_free, 1e-9), SL.DEFF_FREE, ms.sum(), FLOOR, e)
                        ra = C.sizing_row("a", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e)
                        entry[kn] = {
                            "kappa": kv,
                            "expected_delta": e,
                            "share_of_vac24_gain": (
                                _f(gm[ms].sum()) / tot[kn] if kn in tot and tot[kn] > 0 else None
                            ),
                            "halfwidth_b_outcome_free": rb["halfwidth95"],
                            "power_b_outcome_free": rb.get("power_one_season"),
                            "halfwidth_a": ra["halfwidth95"],
                            "power_a": ra.get("power_one_season"),
                        }
                    seas[sn] = entry
                sizing[stat][vname][str(s)] = seas
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    out["sizing"] = sizing

    gate = json.loads(G.OUT.read_text())
    gate["prop"] = out
    G.OUT.write_text(json.dumps(gate, indent=1, default=float))
    print("done", flush=True)
    return out


if __name__ == "__main__":
    main()
