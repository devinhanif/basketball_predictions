# ruff: noqa: E501
"""WOWY_REDISTRIBUTION follow-up 2 (STRUCT) gate: structural allocation of vacated production (DRAFT rule; gate only).

    cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -W ignore -m research.eval.wowy_struct_gate

Feature ``struct_*`` of ``nba/props/struct_alloc.py``: tonight's OUT rotation players' as-of per-game stat (per-minute
rate x expected minutes) is handed out to the remaining as-of rotation pool in proportion to the same quantity (version
RATE) or, for pts and fg3m, to a usage proxy (version USAGE); minutes are handed out pro rata (role) or with weight 2 for
the OUT player's position group (pos). The version is chosen on 2020-22 only (larger clustered t of the coefficient).

Outcomes read: seasons 2020, 2021 (history) and 2022 only, for the coefficient kappa (residual of the stat vs its recency
mean on the feature, controlling vac_<stat> and vac_min, game-clustered). Sizing uses stored A0 quantile grids (forecasts
only) for the expected gain, version B (outcome-free sd, design effect 1.1) as primary and version A (stored FULL-minus-A0
2023/24 deltas, second moments only) as secondary. No 2023, 2024 or 2025 outcome is related to any STRUCT column.
Adds the key ``struct`` to ``reports/prereg_wowy_redistribution/gate.json``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import duckdb
import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import (
    PROP_STATS,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_redistribution_slices as SL

FLOOR = -0.005
QUANT = {"top25": 0.75, "top10": 0.90}


def _f(x: Any) -> float:
    return float(x)


def load_box_state(db: str) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return C.query(
            con,
            "SELECT s.game_id, s.team_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.fga, s.fta, s.tov, "
            "g.game_date, g.season FROM player_game_stats s JOIN games g ON g.game_id = s.game_id "
            "WHERE g.season <= 2024 AND s.minutes > 0",
        ).with_columns(pl.col("game_date").cast(pl.Date))
    finally:
        con.close()


def load_positions(db: str) -> pl.DataFrame:
    con = duckdb.connect(db, read_only=True)
    try:
        return con.execute("SELECT player_id, position FROM players_static").pl()
    finally:
        con.close()


def chosen_col(stat: str, version: dict[str, str]) -> str:
    return version.get(stat, f"struct_d_{stat}")


def slice_masks_struct(df: pl.DataFrame, col: str, leader: str) -> dict[str, np.ndarray]:
    """Masks over ``df`` (one season; rows with has_report and n_out_rot > 0 considered): VAC24, top 25% / 10% of |col| within VAC24, leader out."""
    base = ((df["vac_min"] >= 24.0) & (df["has_report"] == 1)).fill_null(False).to_numpy()
    a = np.abs(df[col].fill_null(0.0).to_numpy())
    out = {"VAC24": base}
    for name, q in QUANT.items():
        cut = np.quantile(a[base], q) if base.any() else np.inf
        out[name] = base & (a >= cut) & (a > 0)
    out["leader_out"] = (
        (df[leader] == 1).fill_null(False).to_numpy()
        & (df["n_out_rot"] > 0).fill_null(False).to_numpy()
        & (df["has_report"] == 1).fill_null(False).to_numpy()
    )
    return out


def slope_on(
    sub: pl.DataFrame, stat: str, col: str, extra: bool = False
) -> dict[str, float] | None:
    """Coefficient of ``col`` on the residual vs the recency mean, controlling vac_<stat> and vac_min; ``extra`` adds the
    player's own as-of per-game stat and the product of his per-minute rate and vac_min (production's own ingredients)."""
    sf = stat_frame(sub, stat)
    if sf.height < 200:
        return None
    ctrl_cols = [pl.col(f"vac_{stat}"), pl.col("vac_min")]
    if extra:
        ctrl_cols += [
            pl.col(f"m10_{stat}"),
            (pl.col(f"rate_{stat}") * pl.col("vac_min")).alias("_rv"),
        ]
    ctrl = sf.select(ctrl_cols).to_numpy()
    ok = (
        np.isfinite(ctrl).all(axis=1)
        & np.isfinite(sf["resid"].to_numpy())
        & np.isfinite(sf[col].to_numpy())
    )
    return G.clustered_slope(
        sf["resid"].to_numpy()[ok],
        sf[col].to_numpy()[ok],
        ctrl[ok],
        sf["game_id"].to_numpy()[ok],
    )


def gbm_arms(
    train: pl.DataFrame, test: pl.DataFrame, stat: str, col: str
) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    """Fit a GBM on the production feature set (residual vs the recency mean, rows of ``train``) and score squared error on
    ``test`` rows for four arms: base, base + struct column, base + the closest existing product (rate x vac_min), base +
    the struct column permuted within the frame (noise). Same parameters and seed for every arm."""
    import lightgbm as lgb

    names = [c for c in stat_feature_names(stat) if c in train.columns]
    rng = np.random.default_rng(0)

    def prep(df: pl.DataFrame) -> pl.DataFrame:
        sf = stat_frame(df, stat).with_columns(
            (pl.col(f"rate_{stat}") * pl.col("vac_min")).alias("_prod")
        )
        return sf.with_columns(pl.Series("_noise", rng.permutation(sf[col].to_numpy())))

    tr, te = prep(train), prep(test)
    errs: dict[str, np.ndarray] = {}
    for arm, extra in (("base", []), ("struct", [col]), ("prod", ["_prod"]), ("noise", ["_noise"])):
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


def gbm_summary(
    te: pl.DataFrame, errs: dict[str, np.ndarray], mask: np.ndarray, n_boot: int = 400
) -> dict[str, Any]:
    """Relative squared-error gain of each arm over base on the masked rows, with a game-clustered bootstrap interval."""
    ok = mask & np.all([np.isfinite(e) for e in errs.values()], axis=0)
    gid = te["game_id"].to_numpy()[ok]
    _, inv = np.unique(gid, return_inverse=True)
    g = int(inv.max()) + 1
    sums = {k: np.bincount(inv, weights=e[ok], minlength=g) for k, e in errs.items()}
    rng = np.random.default_rng(0)
    draws = rng.integers(0, g, size=(n_boot, g))
    out: dict[str, Any] = {
        "n": int(ok.sum()),
        "games": g,
        "mse_base": float(errs["base"][ok].mean()),
    }
    for arm in ("struct", "prod", "noise"):
        rel = 1.0 - sums[arm][draws].sum(axis=1) / sums["base"][draws].sum(axis=1)
        out[arm] = {
            "relative_gain": float(1.0 - sums[arm].sum() / sums["base"].sum()),
            "ci": [float(np.quantile(rel, 0.025)), float(np.quantile(rel, 0.975))],
        }
    rel_sp = 1.0 - sums["struct"][draws].sum(axis=1) / sums["prod"][draws].sum(axis=1)
    out["struct_vs_prod"] = {
        "relative_gain": float(1.0 - sums["struct"].sum() / sums["prod"].sum()),
        "ci": [float(np.quantile(rel_sp, 0.025)), float(np.quantile(rel_sp, 0.975))],
    }
    return out


def partial_r2(sf: pl.DataFrame, stat: str, col: str) -> dict[str, float]:
    """OLS of the residual on the production features (linear, cheap) and the increment from ``col``."""
    names = [c for c in stat_feature_names(stat) if c in sf.columns]
    x = sf.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()
    x = np.where(np.isfinite(x), x, 0.0)
    keep = x.std(axis=0) > 0
    x = (x[:, keep] - x[:, keep].mean(axis=0)) / x[:, keep].std(axis=0)
    y = sf["resid"].to_numpy()
    y = y - y.mean()
    z = sf[col].fill_null(0.0).to_numpy()
    z = (z - z.mean()) / (z.std() or 1.0)

    def r2(m: np.ndarray) -> float:
        beta, *_ = np.linalg.lstsq(m, y, rcond=None)
        e = y - m @ beta
        return 1.0 - float((e**2).sum() / (y**2).sum())

    base = r2(x)
    full = r2(np.column_stack([x, z]))
    alone = r2(z[:, None])
    return {
        "r2_base_production_linear": base,
        "r2_with_struct": full,
        "partial_r2": (full - base) / (1 - base),
        "r2_struct_alone": alone,
        "n": float(len(y)),
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
    box_m, box_h = load_box_state(db), load_box_state(hdb)
    full = pl.concat([box_h, box_m], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = pl.concat(
        [
            games.select("game_id", pl.col("game_date").cast(pl.Date)),
            hgames.select("game_id", pl.col("game_date").cast(pl.Date)),
        ]
    )
    pos = pl.concat([load_positions(hdb), load_positions(db)]).unique("player_id")
    print(
        f"[{time.time() - t0:5.1f}s] inputs ready: states {states.height}, pool {len(pool)}",
        flush=True,
    )

    def attach(fe: pl.DataFrame, op: pl.DataFrame, bx: pl.DataFrame) -> pl.DataFrame:
        rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
        st = SA.build_struct(rows, op, pool, states, dates, pos)
        j = fe.join(st, on=["game_id", "team_id", "player_id"], how="left")
        zero = [c for c in st.columns if c.startswith(("struct_", "leader_out_"))]
        j = j.with_columns([pl.col(c).fill_null(0.0) for c in zero])
        rm = bx.select("game_id", "player_id", pl.col("minutes").alias("real_min"))
        return j.join(rm, on=["game_id", "player_id"], how="left")

    fr = attach(feats, outp, box_m)
    hfr = attach(hfeats, houtp, box_h)
    print(f"[{time.time() - t0:5.1f}s] struct built", flush=True)
    out: dict[str, Any] = {"measured_at": C.now_cdt()}
    # parity of V with production vac on the real rows, by season (2022 differs: production's states start cold in 2022)
    par: dict[str, Any] = {}
    for sea in (2022, 2023, 2024):
        cc = fr.filter(
            (pl.col("season") == sea) & (pl.col("n_out_rot") > 0) & (pl.col("has_report") == 1)
        )
        par[str(sea)] = {
            f"{s_}": {
                "mean_abs": _f((cc[f"V_{s_}"] - cc[f"vac_{s_}"]).abs().mean()),
                "share_gt_0.05": _f(((cc[f"V_{s_}"] - cc[f"vac_{s_}"]).abs() > 0.05).mean()),
                "max_abs": _f((cc[f"V_{s_}"] - cc[f"vac_{s_}"]).abs().max()),
            }
            for s_ in ("pts", "ast", "min")
        }
    out["parity_V_vs_production_vac"] = par

    frames = {
        "2020": hfr.filter(pl.col("season") == 2020),
        "2021": hfr.filter(pl.col("season") == 2021),
        "2022": fr.filter(pl.col("season") == 2022),
    }

    # ---- version choice on 2020-22 pooled VAC24
    def vac_rows(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.filter(
            (pl.col("vac_min") >= 24.0) & (pl.col("has_report") == 1) & (pl.col("n_out_rot") > 0)
        )

    pooled = pl.concat([vac_rows(f_) for f_ in frames.values()], how="vertical_relaxed")
    version: dict[str, str] = {}
    vsel: dict[str, Any] = {}
    for stat in PROP_STATS:
        cands = [f"struct_d_{stat}"] + ([f"struct_u_{stat}"] if stat in SA.USAGE_STATS else [])
        res = {c: slope_on(pooled, stat, c) for c in cands}
        vsel[stat] = res

        def tstat(c: str, res: dict[str, dict[str, float] | None] = res) -> float:
            r = res[c]
            return r["coef"] / r["se"] if r else -9.0

        version[stat] = max(cands, key=tstat)
    mres = {}
    for c in ("struct_dmin_role", "struct_dmin_pos"):
        sf = pooled.filter(
            (pl.col("n_prior") >= 5)
            & pl.col("real_min").is_not_null()
            & pl.col("min10").is_not_nan()
        ).with_columns((pl.col("real_min") - pl.col("min10")).alias("resid"))
        mres[c] = G.clustered_slope(
            sf["resid"].to_numpy(),
            sf[c].to_numpy(),
            sf.select("vac_min").to_numpy(),
            sf["game_id"].to_numpy(),
        )
    out["version_choice_2020_22"] = {
        "stat": vsel,
        "chosen": version,
        "minutes": mres,
        "minutes_chosen": max(mres, key=lambda c: mres[c]["coef"] / mres[c]["se"]),
    }
    print(f"[{time.time() - t0:5.1f}s] versions: {version}", flush=True)

    # ---- kappa by slice, per season and pooled (2020-22 outcomes)
    kap: dict[str, Any] = {}
    parts: dict[str, dict[str, list[pl.DataFrame]]] = {s: {} for s in PROP_STATS}
    for fname, frame in frames.items():
        kap[fname] = {}
        for stat in PROP_STATS:
            col = chosen_col(stat, version)
            msd = slice_masks_struct(frame, col, f"leader_out_{stat}")
            kap[fname][stat] = {}
            for sn, m in msd.items():
                sub = frame.filter(pl.Series(m))
                parts[stat].setdefault(sn, []).append(sub)
                r = slope_on(sub, stat, col)
                kap[fname][stat][sn] = {"rows": sub.height, **(r or {})}
    kap["pooled 2020-22"] = {}
    for stat in PROP_STATS:
        col = chosen_col(stat, version)
        kap["pooled 2020-22"][stat] = {}
        for sn, lst in parts[stat].items():
            sub = pl.concat(lst, how="vertical_relaxed")
            r = slope_on(sub, stat, col)
            kap["pooled 2020-22"][stat][sn] = {"rows": sub.height, **(r or {})}
    # same coefficients with production's own ingredients as extra controls (own as-of stat, rate x vac_min)
    kap2: dict[str, Any] = {"pooled 2020-22": {}, "2022": {}}
    for stat in PROP_STATS:
        col = chosen_col(stat, version)
        kap2["pooled 2020-22"][stat] = {}
        kap2["2022"][stat] = {}
        for sn, lst in parts[stat].items():
            sub = pl.concat(lst, how="vertical_relaxed")
            kap2["pooled 2020-22"][stat][sn] = {
                "rows": sub.height,
                **(slope_on(sub, stat, col, extra=True) or {}),
            }
            kap2["2022"][stat][sn] = {
                "rows": lst[-1].height,
                **(slope_on(lst[-1], stat, col, extra=True) or {}),
            }
    out["kappa_with_production_controls"] = kap2
    # GBM on the production feature set: does the column add held-out accuracy? train 2020-21 rows, test 2022 rows
    gb: dict[str, Any] = {}
    train_all = pl.concat(
        [f_.filter(pl.col("has_report") == 1) for f_ in (frames["2020"], frames["2021"])],
        how="vertical_relaxed",
    )
    test_all = frames["2022"].filter(pl.col("has_report") == 1)
    for stat in PROP_STATS:
        col = chosen_col(stat, version)
        te, errs = gbm_arms(train_all, test_all, stat, col)
        msd = slice_masks_struct(te, col, f"leader_out_{stat}")
        gb[stat] = {sn: gbm_summary(te, errs, m_) for sn, m_ in msd.items()}
    out["gbm_increment_train2020_21_test2022"] = gb
    print(f"[{time.time() - t0:5.1f}s] controls and gbm done", flush=True)
    # minutes-version coefficient (descriptive)
    mcol = out["version_choice_2020_22"]["minutes_chosen"]
    mk: dict[str, Any] = {}
    for fname, frame in {**frames, "pooled 2020-22": pooled}.items():
        base = vac_rows(frame) if fname != "pooled 2020-22" else frame
        sf = base.filter(
            (pl.col("n_prior") >= 5)
            & pl.col("real_min").is_not_null()
            & pl.col("min10").is_not_nan()
        ).with_columns((pl.col("real_min") - pl.col("min10")).alias("resid"))
        mk[fname] = G.clustered_slope(
            sf["resid"].to_numpy(),
            sf[mcol].to_numpy(),
            sf.select("vac_min").to_numpy(),
            sf["game_id"].to_numpy(),
        )
        top = sf.filter(pl.col(mcol).abs() >= np.quantile(sf[mcol].abs().to_numpy(), 0.75))
        mk[fname + " top25"] = G.clustered_slope(
            top["resid"].to_numpy(),
            top[mcol].to_numpy(),
            top.select("vac_min").to_numpy(),
            top["game_id"].to_numpy(),
        )
    out["kappa_inside_slices"] = kap
    out["minutes_version_kappa"] = {"feature": mcol, **mk}
    print(f"[{time.time() - t0:5.1f}s] kappas done", flush=True)

    # ---- overlap with what production already has (pooled 2020-22 VAC24 rows)
    ov: dict[str, Any] = {}
    for stat in PROP_STATS:
        col = chosen_col(stat, version)
        sf = stat_frame(pooled, stat).with_columns(
            (pl.col(f"rate_{stat}") * pl.col("vac_min")).alias("_rate_vac")
        )
        z = sf[col].to_numpy()
        ov[stat] = {
            "corr_with_vac_stat": float(np.corrcoef(z, sf[f"vac_{stat}"].to_numpy())[0, 1]),
            "corr_with_vac_min": float(np.corrcoef(z, sf["vac_min"].to_numpy())[0, 1]),
            "corr_with_rate_x_vac_min": float(np.corrcoef(z, sf["_rate_vac"].to_numpy())[0, 1]),
            "corr_with_own_m10_stat": float(np.corrcoef(z, sf[f"m10_{stat}"].to_numpy())[0, 1]),
            **partial_r2(sf, stat, col),
        }
    out["overlap_with_production_pooled_2020_22_VAC24"] = ov
    print(f"[{time.time() - t0:5.1f}s] overlap done", flush=True)

    # ---- sizing on 2023/2024 stored A0 grids
    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    lt = G.REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    full_pi = np.load(G.REAL / "data/ops/recent_role_full_cache/pi_R.npz", allow_pickle=True)["pi"]
    sizing: dict[str, Any] = {}
    for stat in PROP_STATS:
        col = chosen_col(stat, version)
        cs = load_components(stat, lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        gid = cs["row_gid"].astype(str)
        season = cs["row_season"]
        y = cs["row_y"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        jf = keys.join(
            fr.select(
                "game_id",
                "player_id",
                "vac_min",
                "n_out_rot",
                "has_report",
                col,
                f"leader_out_{stat}",
            ),
            on=["game_id", "player_id"],
            how="left",
        ).with_columns(
            pl.col("vac_min").fill_null(0.0),
            pl.col("n_out_rot").fill_null(0),
            pl.col("has_report").fill_null(0),
            pl.col(col).fill_null(0.0),
            pl.col(f"leader_out_{stat}").fill_null(0),
        )
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        hd = dict(
            np.load(
                G.REAL / f"data/ops/recent_role_full_cache/heads_{stat}_R.npz", allow_pickle=True
            ).items()
        )
        q1 = RF.arm_qi(stat, hd, pic, full_pi)
        d = PR.crps_int(q1, y) - PR.crps_int(q0, y)
        grid = arm_grid("mixture", cs, pic, stat)
        shift = jf[col].to_numpy()
        k_pool = max(kap["pooled 2020-22"][stat]["VAC24"].get("coef", 0.0), 0.0)
        k_22 = max(kap["2022"][stat]["VAC24"].get("coef", 0.0), 0.0)
        sizing[stat] = {"feature": col, "kappa_VAC24": {"pooled_2020_22": k_pool, "2022": k_22}}
        for s in G.SEASONS:
            in_s = season == s
            sub = jf.filter(pl.Series(in_s))
            masks_sub = slice_masks_struct(sub, col, f"leader_out_{stat}")
            masks = {k: np.zeros(len(y), dtype=bool) for k in masks_sub}
            for k, v in masks_sub.items():
                masks[k][np.flatnonzero(in_s)] = v
            vac = masks["VAC24"]
            seas: dict[str, Any] = {}
            gains: dict[str, tuple[np.ndarray, np.ndarray]] = {}

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
                        q0[sel].astype(float), to_integer_support(grid[sel] + kv * shift[sel, None])
                    )
                return gm, gv

            union = vac | masks["leader_out"]
            for kn, kv in (("pooled_2020_22", k_pool), ("2022", k_22)):
                gains[kn] = gain(kv, union)
            tot = {kn: _f(gains[kn][0][vac].sum()) for kn in gains}
            for sn, ms in masks.items():
                if not ms.any():
                    continue
                cg = C.cluster_stats(d[ms], gid[ms])
                entry: dict[str, Any] = {
                    "rows": int(ms.sum()),
                    "games": int(cg["clusters"]),
                    "sd_row_a": cg["sd_row"],
                    "deff_a": cg["deff"],
                }
                kin = max(kap["pooled 2020-22"][stat][sn].get("coef", 0.0), 0.0)
                scen = {"pooled_2020_22": gains["pooled_2020_22"], "2022": gains["2022"]}
                kin2 = max(kap2["pooled 2020-22"][stat][sn].get("coef", 0.0), 0.0)
                if sn != "VAC24":
                    scen["in_slice_pooled"] = gain(kin, ms)
                scen["in_slice_pooled_prod_controls"] = gain(kin2, ms)
                for kn, (gm, gv) in scen.items():
                    e = -_f(gm[ms].mean())
                    sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var()))
                    ra = C.sizing_row("a", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e)
                    rb = C.sizing_row("b", max(sd_free, 1e-9), SL.DEFF_FREE, ms.sum(), FLOOR, e)
                    entry[kn] = {
                        "kappa": (
                            kin
                            if kn == "in_slice_pooled"
                            else (k_pool if kn == "pooled_2020_22" else k_22)
                        ),
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
            sizing[stat][str(s)] = seas
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    out["sizing"] = sizing
    best: list[tuple[float, str]] = []
    for stat, v in sizing.items():
        for ss in ("2023", "2024"):
            for sn, e in v[ss].items():
                for kn in ("pooled_2020_22",):
                    p = e[kn]["power_b_outcome_free"]
                    if p is not None:
                        best.append((p, f"{stat} {ss} {sn} {kn} B"))
                    best.append((e[kn]["power_a"], f"{stat} {ss} {sn} {kn} A"))
    best.sort(reverse=True)
    out["top_powers_at_pooled_kappa"] = best[:12]
    out["any_struct_slice_power_ge_0.5_at_pooled_kappa"] = bool(best and best[0][0] >= 0.5)
    gate = json.loads(G.OUT.read_text())
    gate["struct"] = out
    G.OUT.write_text(json.dumps(gate, indent=1, default=float))
    print(
        json.dumps(
            {
                "versions": version,
                "top_pooled": out["top_powers_at_pooled_kappa"][:8],
                "ge05": out["any_struct_slice_power_ge_0.5_at_pooled_kappa"],
            },
            indent=1,
            default=float,
        )
    )
    return out


if __name__ == "__main__":
    main()
