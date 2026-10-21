# ruff: noqa: E501
"""WOWY_STRUCT permutation-null sizing for the player-season-permuted guard G1 (DRAFT rule; gate only).

    cd /Users/devin/Downloads/nba-prediction && nice -n 10 .venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_struct_null

Question: how much of the gain "ROLE + STRUCT minus ROLE" can a STRUCT column with the right marginal but the wrong
row (permuted within player-season, train and test separately) produce by chance and by player-level information?
K permutation draws per design and stat; the arms are the GBM of ``wowy_struct_gate.gbm_eval`` (ROLE and S+R fitted
once, P+R fitted K times); metric = relative squared-error gain over the ROLE arm on each surviving cell's rows,
game-clustered point estimate. Seasons 2020, 2021, 2022 only (D1 train 2020-21 / test 2022; D2a 2020 -> 2021;
D2b 2021 -> 2020); no 2023, 2024 or 2025 outcome. The squared-error null sd is converted to CRPS units with the
Gaussian rule (CRPS ~ 0.564 x RMSE, relative CRPS gain ~ half the relative squared-error gain); that conversion is
unverified. Adds the key ``struct_null`` to ``reports/prereg_wowy_redistribution/gate.json``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import polars as pl

from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import stat_feature_names, stat_frame
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G

K_DRAWS = 30
CHOSEN = {"pts": "struct_u_pts", "ast": "struct_d_ast"}
CELLS = (("pts", "leader_out"), ("pts", "top25_fixed"), ("ast", "leader_out"))
CUT_Q = (0.75, 0.90)


def _f(x: Any) -> float:
    return float(x)


def main() -> dict[str, Any]:
    import lightgbm as lgb

    t0 = time.time()
    db = str(G.REAL / "nba.duckdb")
    hdb = str(G.REAL / "data/history/nba_history.duckdb")
    feats, outp, games = SG.warm_frame(db, hdb)
    full = pl.concat([SG.load_box_state(hdb), SG.load_box_state(db)], how="vertical_relaxed")
    states = SA.decayed_states(full)
    pool = W.asof_rotation_pool(full)
    dates = games.select("game_id", "game_date")
    pos = pl.concat([SG.load_positions(hdb), SG.load_positions(db)]).unique("player_id")
    fe = feats.filter(
        pl.col("season").is_in([2020, 2021, 2022])
    )  # outcomes of 2023-24 never enter this job
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    fr = SA.attach_struct(fe, SA.build_struct(rows, op, pool, states, dates, pos))
    f20, f21, f22 = (fr.filter(pl.col("season") == y) for y in (2020, 2021, 2022))
    sel = pl.concat(
        [
            f.filter(
                (pl.col("vac_min") >= 24.0)
                & (pl.col("has_report") == 1)
                & (pl.col("n_out_rot") > 0)
            )
            for f in (f20, f21)
        ],
        how="vertical_relaxed",
    )
    cuts: dict[str, tuple[float, float]] = {}
    for stat, col in CHOSEN.items():
        a = np.abs(sel[col].to_numpy())
        a = a[a > 0]
        cuts[stat] = (_f(np.quantile(a, CUT_Q[0])), _f(np.quantile(a, CUT_Q[1])))
    print(f"[{time.time() - t0:5.1f}s] frames ready; cuts {cuts}", flush=True)

    designs = {
        "D1_train2020-21_test2022": ([f20, f21], f22),
        "D2a_train2020_test2021": ([f20], f21),
        "D2b_train2021_test2020": ([f21], f20),
    }
    params = dict(
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
    out: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "K": K_DRAWS,
        "seasons_read": [2020, 2021, 2022],
        "cells": {},
    }

    def perm_ps(df: pl.DataFrame, col: str, rng: np.random.Generator) -> np.ndarray:
        v = df[col].to_numpy().copy()
        key = (df["player_id"].cast(pl.Utf8) + "_" + df["season"].cast(pl.Utf8)).to_numpy()
        for k in np.unique(key):
            idx = np.flatnonzero(key == k)
            v[idx] = v[rng.permutation(idx)]
        return v

    for stat in ("pts", "ast"):
        col, role = CHOSEN[stat], f"role_share_{stat}"
        names = [c for c in stat_feature_names(stat) if c in fr.columns]
        for dn, (trn, tst) in designs.items():
            tr = stat_frame(
                pl.concat(trn, how="vertical_relaxed").filter(pl.col("has_report") == 1), stat
            )
            te = stat_frame(tst.filter(pl.col("has_report") == 1), stat)
            ok = np.isfinite(tr["resid"].to_numpy())

            def fit_err(
                tr_x: np.ndarray,
                te_x: np.ndarray,
                tr: pl.DataFrame = tr,
                te: pl.DataFrame = te,
                ok: np.ndarray = ok,
            ) -> np.ndarray:
                m = lgb.LGBMRegressor(**params)  # type: ignore[arg-type]
                m.fit(tr_x[ok], tr["resid"].to_numpy()[ok])
                return np.asarray((te["resid"].to_numpy() - m.predict(te_x)) ** 2)

            def mat(
                df: pl.DataFrame, extra: list[np.ndarray], names: list[str] = names
            ) -> np.ndarray:
                base = df.select([pl.col(c).cast(pl.Float64) for c in names]).to_numpy()
                return np.column_stack([base, *extra]) if extra else base

            r_tr, r_te = tr[role].to_numpy(), te[role].to_numpy()
            s_tr, s_te = tr[col].to_numpy(), te[col].to_numpy()
            e_role = fit_err(mat(tr, [r_tr]), mat(te, [r_te]))
            e_sr = fit_err(mat(tr, [r_tr, s_tr]), mat(te, [r_te, s_te]))
            masks = SG.cell_masks(te, col, f"leader_out_{stat}", cuts[stat])
            rng = np.random.default_rng(1234)
            perm_gain: dict[str, list[float]] = {sn: [] for st_, sn in CELLS if st_ == stat}
            for _k in range(K_DRAWS):
                ptr, pte = perm_ps(tr, col, rng), perm_ps(te, col, rng)
                e_p = fit_err(mat(tr, [r_tr, ptr]), mat(te, [r_te, pte]))
                for sn in perm_gain:
                    mk = masks[sn] & np.isfinite(e_role) & np.isfinite(e_p)
                    perm_gain[sn].append(100 * _f(1.0 - e_p[mk].sum() / e_role[mk].sum()))
            for sn in perm_gain:
                mk = masks[sn] & np.isfinite(e_role) & np.isfinite(e_sr)
                obs = 100 * _f(1.0 - e_sr[mk].sum() / e_role[mk].sum())
                g = np.array(perm_gain[sn])
                rmse = float(np.sqrt(e_role[mk].mean()))
                crps_cell = 0.564 * rmse
                sd = _f(g.std(ddof=1))
                out["cells"].setdefault(f"{stat}|{sn}", {})[dn] = {
                    "n": int(mk.sum()),
                    "observed_struct_beyond_role_pct": obs,
                    "null_mean_pct": _f(g.mean()),
                    "null_sd_pct": sd,
                    "null_max_pct": _f(g.max()),
                    "z_vs_null": (obs - _f(g.mean())) / sd if sd > 0 else None,
                    "p_empirical": (1 + int((g >= obs).sum())) / (K_DRAWS + 1),
                    "cell_crps_level_gaussian_rule": crps_cell,
                    "null_sd_crps_equiv(unverified)": 0.5 * sd / 100 * crps_cell,
                    "two_sd_crps_equiv(unverified)": 2 * 0.5 * sd / 100 * crps_cell,
                }
            print(f"[{time.time() - t0:5.1f}s] {stat} {dn} done", flush=True)

    # G1 threshold per cell = max(0.003, 2 sd) with sd the largest null sd across the three designs (CRPS-equivalent)
    thr: dict[str, Any] = {}
    for cell, per in out["cells"].items():
        two_sd = max(v["two_sd_crps_equiv(unverified)"] for v in per.values())
        thr[cell] = {
            "two_sd_crps_equiv_max_over_designs": two_sd,
            "G1_threshold": max(0.003, two_sd),
        }
    out["G1_thresholds"] = thr
    gate = json.loads(G.OUT.read_text())
    gate["struct_null"] = out
    G.OUT.write_text(json.dumps(gate, indent=1, default=float))
    print(json.dumps({"thresholds": thr}, indent=1, default=float), flush=True)
    return out


if __name__ == "__main__":
    main()
