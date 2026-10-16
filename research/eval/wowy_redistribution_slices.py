# ruff: noqa: E501
"""WOWY_REDISTRIBUTION follow-up gate: slices defined by the SIZE of the as-of predicted shift (DRAFT rule).

    cd /Users/devin/Downloads/nba-prediction && .venv/bin/python -W ignore -m research.eval.wowy_redistribution_slices

Inside VAC24 (``vac_min >= 24``), per season, rows whose ``|wo_d_<stat>|`` (the shrunk, as-of WOWY shift of the
stat, i.e. kappa x the column up to the constant kappa) is in the top quartile / top decile, and the same by
``|wo_d_min|`` (the minutes version). Quantiles are taken within the season's VAC24 rows on pre-tip features only
(a live rule needs fixed cut-offs; those are not set here). Sizing is outcome-light:

* the expected gain per row is outcome-free (``prereg_gates_common.expected_crps_gain`` on the stored A0 quantile
  grids, which hold forecasts only) at kappa = the 2022 estimate (history window) and the pooled 2020-22 estimate;
* kappa is also re-estimated INSIDE each slice on 2020, 2021 and 2022 outcomes only (a shift selected on its size
  may carry a different coefficient: ABSORB's points coefficient was 0.003);
* half-width, two versions: (a) sd and design effect from the stored FULL-minus-A0 per-row deltas of 2023/24
  (second moments of arms already scored under T467; the same method as the first gate); (b) fully outcome-free:
  the sd of the per-row CRPS difference under the hypothesised truth (excludes fit noise, so optimistic), with a
  design effect of 1.1. No 2023, 2024 or 2025 outcome is related to any WOWY column.

Adds the key ``feature_defined_slices`` to ``reports/prereg_wowy_redistribution/gate.json``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np
import polars as pl

from nba.eval.lower_tail_eval import arm_grid, load_components
from nba.props import wowy as W
from nba.props.context_residual import CRPS_TAUS, PROP_STATS, stat_frame, to_integer_support
from research.eval import prereg_gates_common as C
from research.eval import wowy_redistribution_gate as G

QUANT = {"top25": 0.75, "top10": 0.90}
FLOOR = -0.005
DEFF_FREE = 1.1


def _f(x: Any) -> float:
    return float(x)


def expected_gain_var(
    q_old: np.ndarray, q_new: np.ndarray, chunk: int = 400
) -> tuple[np.ndarray, np.ndarray]:
    """Per row: mean and variance (over G's own atoms) of CRPS(F, Y) - CRPS(G, Y), Y ~ G. Outcome-free."""
    taus = CRPS_TAUS
    mean = np.empty(len(q_old))
    var = np.empty(len(q_old))
    for a in range(0, len(q_old), chunk):
        f = q_old[a : a + chunk].astype(float)
        g = q_new[a : a + chunk].astype(float)
        y = g[:, :, None]

        def pin(q: np.ndarray, y: np.ndarray = y) -> np.ndarray:
            d = y - q[:, None, :]
            return np.asarray(np.where(d >= 0, taus * d, (taus - 1.0) * d).mean(axis=2) * 2.0)

        diff = pin(f) - pin(g)
        mean[a : a + chunk] = diff.mean(axis=1)
        var[a : a + chunk] = diff.var(axis=1)
    return mean, var


def slice_masks(df_cols: dict[str, np.ndarray], base: np.ndarray) -> dict[str, np.ndarray]:
    """Masks inside ``base`` (VAC24 rows of one season): top quartile / decile of |shift| for each column."""
    out: dict[str, np.ndarray] = {"VAC24": base}
    for col in ("wo_d_pts", "wo_d_reb", "wo_d_ast", "wo_d_fg3m", "wo_d_min"):
        a = np.abs(df_cols[col])
        pos = a[base]
        for name, q in QUANT.items():
            cut = np.quantile(pos, q) if len(pos) else np.inf
            out[f"{name}|{col}"] = base & (a >= cut) & (a > 0)
    return out


def kappas(frames: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """Slice-specific coefficients on outcome seasons 2020-22: stat residual vs recency mean on the slice's own-stat shift."""
    res: dict[str, Any] = {}
    parts: dict[str, list[pl.DataFrame]] = {}
    for fname, fr in frames.items():
        base = fr.filter(
            (pl.col("vac_min") >= 24.0) & (pl.col("wo_conf") > 0) & (pl.col("has_report") == 1)
        )
        cols = {
            c: base[c].to_numpy()
            for c in ("wo_d_pts", "wo_d_reb", "wo_d_ast", "wo_d_fg3m", "wo_d_min")
        }
        masks = slice_masks(cols, np.ones(base.height, dtype=bool))
        res[fname] = {}
        for sn, m in masks.items():
            sub = base.filter(pl.Series(m))
            parts.setdefault(sn, []).append(sub)
            res[fname][sn] = {"rows": sub.height}
            for stat in PROP_STATS:
                sf = stat_frame(sub, stat)
                if sf.height > 50:
                    res[fname][sn][stat] = G.clustered_slope(
                        sf["resid"].to_numpy(),
                        sf[f"wo_d_{stat}"].to_numpy(),
                        sf.select(f"vac_{stat}", "vac_min").to_numpy(),
                        sf["game_id"].to_numpy(),
                    )
    res["pooled 2020-22"] = {}
    for sn, lst in parts.items():
        pooled = pl.concat(lst, how="vertical_relaxed")
        res["pooled 2020-22"][sn] = {"rows": pooled.height}
        for stat in PROP_STATS:
            sf = stat_frame(pooled, stat)
            if sf.height > 50:
                res["pooled 2020-22"][sn][stat] = G.clustered_slope(
                    sf["resid"].to_numpy(),
                    sf[f"wo_d_{stat}"].to_numpy(),
                    sf.select(f"vac_{stat}", "vac_min").to_numpy(),
                    sf["game_id"].to_numpy(),
                )
    return res


def main() -> dict[str, Any]:
    t0 = time.time()
    db = str(G.REAL / "nba.duckdb")
    feats, sets, _o, _g = G.frame_and_out_sets(db)
    box = G.load_box_full(db)
    rows = feats.filter(pl.col("n_out_rot") > 0).select("game_id", "player_id")
    # history frames (2020, 2021) and 2022 with the history-box window; real tips from data/history/schedule
    import nba.features.game_tipoff as GT

    old = GT.SCHEDULE_DIR
    GT.SCHEDULE_DIR = G.REAL / "data/history/schedule"
    try:
        hfeats, hsets, _ho, _hg = G.frame_and_out_sets(
            str(G.REAL / "data/history/nba_history.duckdb")
        )
    finally:
        GT.SCHEDULE_DIR = old
    hbox = G.load_box_full(str(G.REAL / "data/history/nba_history.duckdb"))
    hrows = hfeats.filter(pl.col("n_out_rot") > 0).select("game_id", "player_id")
    hfr = W.attach_wowy(hfeats, W.build_wowy(hbox, hsets, target_rows=hrows))
    full_box = pl.concat([hbox, box], how="vertical_relaxed")
    fr_full = W.attach_wowy(feats, W.build_wowy(full_box, sets, target_rows=rows))
    fr = W.attach_wowy(feats, W.build_wowy(box, sets, target_rows=rows))
    print(f"[{time.time() - t0:5.1f}s] frames built", flush=True)

    out: dict[str, Any] = {"measured_at": C.now_cdt()}
    frames = {
        "2020": hfr.filter(pl.col("season") == 2020),
        "2021": hfr.filter(pl.col("season") == 2021),
        "2022 (history window)": fr_full.filter(pl.col("season") == 2022),
    }
    out["kappa_inside_slices"] = kappas(frames)
    # ANY-slice kappas (the coordinator's 0.072 / 0.037 for points) per stat
    anyk: dict[str, Any] = {"2022_hist_window": {}, "pooled_2020_22": {}}
    pool_parts = []
    for name, frame in frames.items():
        b = frame.filter(
            (pl.col("n_out_rot") > 0) & (pl.col("wo_conf") > 0) & (pl.col("has_report") == 1)
        )
        pool_parts.append(b)
        if name.startswith("2022"):
            for stat in PROP_STATS:
                sf = stat_frame(b, stat)
                anyk["2022_hist_window"][stat] = G.clustered_slope(
                    sf["resid"].to_numpy(),
                    sf[f"wo_d_{stat}"].to_numpy(),
                    sf.select(f"vac_{stat}", "vac_min").to_numpy(),
                    sf["game_id"].to_numpy(),
                )["coef"]
    pooled = pl.concat(pool_parts, how="vertical_relaxed")
    for stat in PROP_STATS:
        sf = stat_frame(pooled, stat)
        anyk["pooled_2020_22"][stat] = G.clustered_slope(
            sf["resid"].to_numpy(),
            sf[f"wo_d_{stat}"].to_numpy(),
            sf.select(f"vac_{stat}", "vac_min").to_numpy(),
            sf["game_id"].to_numpy(),
        )["coef"]
    out["kappa_any_slice"] = anyk
    print(f"[{time.time() - t0:5.1f}s] kappas done: {anyk}", flush=True)

    from research.eval import pi_recent_role_eval as PR
    from research.eval import recent_role_full_eval as RF

    lt = G.REAL / "reports/lower_tail"
    pic = dict(np.load(lt / "components_pi.npz", allow_pickle=True))
    full_pi = np.load(G.REAL / "data/ops/recent_role_full_cache/pi_R.npz", allow_pickle=True)["pi"]
    sizing: dict[str, Any] = {}
    for stat in PROP_STATS:
        cs = load_components(stat, lt)
        if int(cs["row_season"].max()) > C.MAX_SEASON:
            raise RuntimeError("season > 2024 in stored components")
        gid = cs["row_gid"].astype(str)
        season = cs["row_season"]
        y = cs["row_y"]
        keys = pl.DataFrame({"game_id": gid, "player_id": cs["row_pid"].astype(np.int64)})
        jf = keys.join(
            fr.select("game_id", "player_id", "vac_min", *W.WOWY_COLS),
            on=["game_id", "player_id"],
            how="left",
        ).with_columns(
            pl.col("vac_min").fill_null(0.0), *[pl.col(c).fill_null(0.0) for c in W.WOWY_COLS]
        )
        cols = {
            c: jf[c].to_numpy()
            for c in ("wo_d_pts", "wo_d_reb", "wo_d_ast", "wo_d_fg3m", "wo_d_min")
        }
        q0 = RF.arm_qi(stat, cs, pic, pic["row_pi"])
        hd = dict(
            np.load(
                G.REAL / f"data/ops/recent_role_full_cache/heads_{stat}_R.npz", allow_pickle=True
            ).items()
        )
        q1 = RF.arm_qi(stat, hd, pic, full_pi)
        d = PR.crps_int(q1, y) - PR.crps_int(q0, y)  # stored FULL - A0 (T467): second moments only
        grid = arm_grid("mixture", cs, pic, stat)
        shift = jf[f"wo_d_{stat}"].to_numpy()
        kap_sets = {
            "2022_hist_window": anyk["2022_hist_window"][stat],
            "pooled_2020_22": anyk["pooled_2020_22"][stat],
        }
        sizing[stat] = {"kappa": {k: max(v, 0.0) for k, v in kap_sets.items()}}
        for s in G.SEASONS:
            m = season == s
            base = m & (jf["vac_min"].to_numpy() >= 24.0)
            masks = slice_masks(cols, base)
            seas: dict[str, Any] = {}
            gains: dict[str, tuple[np.ndarray, np.ndarray]] = {}
            for kn, kv in kap_sets.items():
                kv = max(kv, 0.0)
                gm = np.zeros(len(y))
                gv = np.zeros(len(y))
                sel = base & (shift != 0) & (kv > 0)
                if sel.any():
                    gm[sel], gv[sel] = expected_gain_var(
                        q0[sel].astype(float), to_integer_support(grid[sel] + kv * shift[sel, None])
                    )
                gains[kn] = (gm, gv)
            tot = {kn: _f(gains[kn][0][base].sum()) for kn in kap_sets}
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
                for kn in kap_sets:
                    gm, gv = gains[kn]
                    e = -_f(gm[ms].mean())
                    sd_free = float(np.sqrt(gv[ms].mean() + gm[ms].var())) if ms.any() else 0.0
                    ra = C.sizing_row(f"{stat} {sn}", cg["sd_row"], cg["deff"], ms.sum(), FLOOR, e)
                    rb = C.sizing_row(
                        f"{stat} {sn} free", max(sd_free, 1e-9), DEFF_FREE, ms.sum(), FLOOR, e
                    )
                    entry[kn] = {
                        "expected_delta": e,
                        "share_of_vac24_gain": _f(gm[ms].sum()) / tot[kn] if tot[kn] > 0 else None,
                        "halfwidth_a": ra["halfwidth95"],
                        "power_a": ra.get("power_one_season"),
                        "halfwidth_b_outcome_free": rb["halfwidth95"],
                        "power_b_outcome_free": rb.get("power_one_season"),
                    }
                seas[sn] = entry
            sizing[stat][str(s)] = seas
        print(f"[{time.time() - t0:5.1f}s] sized {stat}", flush=True)
    out["sizing"] = sizing

    best: list[tuple[float, str]] = []
    for stat, v in sizing.items():
        for ss in ("2023", "2024"):
            for sn, e in v[ss].items():
                for kn in ("pooled_2020_22", "2022_hist_window"):
                    for ver in ("power_a", "power_b_outcome_free"):
                        p = e[kn][ver]
                        if p is not None:
                            best.append((p, f"{stat} {ss} {sn} {kn} {ver}"))
    best.sort(reverse=True)
    out["top_powers"] = best[:25]
    out["any_slice_power_ge_0.5_at_pooled_kappa"] = any(p >= 0.5 and "pooled" in n for p, n in best)
    out["any_slice_power_ge_0.5_at_pooled_kappa_version_a"] = any(
        p >= 0.5 and "pooled" in n and n.endswith("power_a") for p, n in best
    )

    gate = json.loads(G.OUT.read_text())
    gate["feature_defined_slices"] = out
    G.OUT.write_text(json.dumps(gate, indent=1, default=float))
    print(
        json.dumps(
            {
                "top": out["top_powers"][:12],
                "ge05_pooled": out["any_slice_power_ge_0.5_at_pooled_kappa"],
                "ge05_pooled_a": out["any_slice_power_ge_0.5_at_pooled_kappa_version_a"],
            },
            indent=1,
            default=float,
        )
    )
    return out


if __name__ == "__main__":
    main()
