# ruff: noqa: E501
"""WOWY_STRUCT G6 power re-measure for the E7 design (DRAFT rule; sizing only, nothing is fitted, no contrast is computed).

    cd <worktree> && NBA_ROOT=/Users/devin/Downloads/nba-prediction nice -n 10 \\
        /Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.archive.wowy_struct.wowy_struct_power_e7

What it does: the SAME sizing method as ``wowy_struct_gate.py::main`` (version B: outcome-free sd of the per-row CRPS delta
between the stored A0 forecast and the same forecast shifted by kappa x struct, design effect 1.1, pass line min(-0.005,
-halfwidth)), with kappa and the fixed cut-offs READ from the stored ``struct_warm`` block of ``gate.json`` (2020-21
outcomes already stored; no outcome is read here; ``row_y`` is dropped on load). It fits no arm: the E7 A0_warm (2019-10
window, mixture refit per block) is a shared arm with F1_WARM_WINDOW and may not be fitted before F1 freezes, so the
forecast grids are the STORED A0 grids (production, window 2022-10 on), the only forecasts that exist. The effect of the
window on forecast widths is bracketed by a spread-scaling sensitivity (grid and A0 quantiles scaled about the row median
by f) and by the break-even halfwidth multiplier. No season-2025 data is loaded (``warm_frame`` asserts season <= 2024).
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import struct_alloc as SA
from nba.props import wowy as W
from nba.props.context_residual import to_integer_support
from research.archive.wowy_struct import wowy_struct_gate as SG
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G
from research.eval import wowy_redistribution_slices as SL

FLOOR = -0.005
CELLS = (("pts", "leader_out"), ("pts", "top25_fixed"), ("ast", "leader_out"))
CHOSEN = {"pts": "struct_u_pts", "ast": "struct_d_ast"}
STAR_CUT = {"pts": 15.1289, "ast": 3.4512}  # draft rule line 6 (G5), fixed
SPREADS = (0.90, 0.95, 1.00, 1.05, 1.10)
OUT = G.HERE / "reports/prereg_wowy_struct/power_e7.json"


def scale_about_median(q: np.ndarray, f: float) -> np.ndarray:
    med = q[:, q.shape[1] // 2][:, None]
    return np.asarray(med + f * (q - med))


def main() -> dict[str, Any]:
    t0 = time.time()
    stored = json.loads(G.OUT.read_text())["struct_warm"]
    kap = stored["kappa"]
    cuts = {
        s: tuple(stored["fixed_cutoffs_2020_21(|struct| quantiles 0.75 / 0.90)"][s])
        for s in ("pts", "ast")
    }
    db, hdb = str(G.REAL / "nba.duckdb"), str(G.REAL / "data/history/nba_history.duckdb")
    feats, outp, games = SG.warm_frame(db, hdb)
    full = pl.concat([SG.load_box_state(hdb), SG.load_box_state(db)], how="vertical_relaxed")
    states, pool = SA.decayed_states(full), W.asof_rotation_pool(full)
    dates = games.select("game_id", "game_date")
    pos = pl.concat([SG.load_positions(hdb), SG.load_positions(db)]).unique("player_id")
    fe = feats.filter(pl.col("season").is_in([2023, 2024]))
    rows = fe.filter(pl.col("n_out_rot") > 0).select("game_id", "team_id", "player_id")
    op = outp.join(rows.select("game_id").unique(), on="game_id", how="inner")
    fr = SA.attach_struct(fe, SA.build_struct(rows, op, pool, states, dates, pos))
    print(f"[{time.time() - t0:5.1f}s] warm frame {feats.height} rows, struct attached", flush=True)

    from research.eval import pi_recent_role_eval as PR  # noqa: F401
    from research.eval import recent_role_full_eval as RF

    lt = G.REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    res: dict[str, Any] = {
        "measured_at": C.now_cdt(),
        "method": "wowy_struct_gate.py main() version B, stored A0 grids, kappa and cut-offs from stored gate.json",
        "forecast_source": "STORED production A0 grids (window 2022-10 on); the E7 2019+ A0_warm is not fitted (shared with F1_WARM_WINDOW)",
        "kappa_source": "gate.json struct_warm.kappa pooled_2020_21 plain",
        "star_cut": STAR_CUT,
        "cells": {},
        "warm_rows_feature_only": {},
    }
    for stat in ("pts", "ast"):
        col = CHOSEN[stat]
        cs = load_components(stat, lt)
        cs["row_y"] = np.full(
            len(cs["row_y"]), np.nan
        )  # outcomes are not needed by version B (length only); NaN so none can reach a statistic
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        gid, season = cs["row_gid"].astype(str), cs["row_season"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        sel_cols = [
            "game_id",
            "player_id",
            "vac_min",
            "n_out_rot",
            "has_report",
            col,
            f"leader_out_{stat}",
            f"Vmax_{stat}",
        ]
        jf = keys.join(fr.select(sel_cols), on=["game_id", "player_id"], how="left").with_columns(
            pl.col("vac_min").fill_null(0.0),
            pl.col("n_out_rot").fill_null(0),
            pl.col("has_report").fill_null(0),
            pl.col(col).fill_null(0.0),
            pl.col(f"leader_out_{stat}").fill_null(0),
            pl.col(f"Vmax_{stat}").fill_null(0.0),
        )
        # warm-frame row counts of the same cells on the E7 A0 row set (n_prior >= 5), features only
        for s in G.SEASONS:
            fw = (
                fr.filter((pl.col("season") == s) & (pl.col("n_prior") >= 5))
                if "n_prior" in fr.columns
                else None
            )
            if fw is not None:
                m_ = SG.cell_masks(fw, col, f"leader_out_{stat}", cuts[stat])
                res["warm_rows_feature_only"][f"{stat}|{s}"] = {
                    k: int(v.sum()) for k, v in m_.items() if k in ("leader_out", "top25_fixed")
                }
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"]).astype(float)
        grid = arm_grid("mixture", cs, pic, stat)
        shift = jf[col].to_numpy()
        vmax = jf[f"Vmax_{stat}"].to_numpy()
        masks: dict[tuple[str, int], np.ndarray] = {}
        for s in G.SEASONS:
            in_s = season == s
            msub = SG.cell_masks(jf.filter(pl.Series(in_s)), col, f"leader_out_{stat}", cuts[stat])
            for sn, v in msub.items():
                m = np.zeros(len(shift), dtype=bool)
                m[np.flatnonzero(in_s)] = v
                masks[(sn, s)] = m
        for sn in {c[1] for c in CELLS if c[0] == stat}:
            kv = max(kap[stat]["pooled_2020_21"][sn]["plain"]["coef"], 0.0)
            union = np.zeros(len(shift), dtype=bool)
            for s in G.SEASONS:
                union |= masks[(sn, s)]
            union &= shift != 0
            idx = np.flatnonzero(union)
            for f in SPREADS:
                q0f = to_integer_support(scale_about_median(q0[idx], f)) if f != 1.0 else q0[idx]
                gr = scale_about_median(grid[idx], f) if f != 1.0 else grid[idx]
                gm_i, gv_i = SL.expected_gain_var(
                    q0f, to_integer_support(gr + kv * shift[idx, None])
                )
                gm, gv = np.zeros(len(shift)), np.zeros(len(shift))
                gm[idx], gv[idx] = gm_i, gv_i
                for s in G.SEASONS:
                    for variant, extra in (("cell", None), ("star_out", vmax >= STAR_CUT[stat])):
                        ms = masks[(sn, s)] if extra is None else masks[(sn, s)] & extra
                        if not ms.any():
                            continue
                        e = -float(gm[ms].mean())
                        sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var()))
                        rb = C.sizing_row(
                            "b", max(sd_free, 1e-9), SL.DEFF_FREE, float(ms.sum()), FLOOR, e
                        )
                        hw = rb["halfwidth95"]
                        res["cells"].setdefault(
                            f"{stat}|{sn}|{variant}|{s}",
                            {
                                "kappa": kv,
                                "rows": int(ms.sum()),
                                "games": int(len(np.unique(gid[ms]))),
                            },
                        )[f"f={f:.2f}"] = {
                            "expected_delta": e,
                            "halfwidth_b": hw,
                            "power_b": rb["power_one_season"],
                            "breakeven_halfwidth_multiplier": (abs(e) / hw) if e <= FLOOR else 0.0,
                        }
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res, indent=1, default=float))
    return res


if __name__ == "__main__":
    main()
