# ruff: noqa: E501
# mypy: ignore-errors
"""ROLE_CHANGEPOINT minimum-data gate (rule: docs/prereg/ROLE_CHANGEPOINT.md, DRAFT). No arm is fitted; no scored outcome is read.

    NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction .venv/bin/python -W ignore -m research.eval.role_changepoint_gate [--out reports/prereg_role_changepoint/gate.json] [--seeds 10] [--no-power]

Read-only; ``season <= 2024`` in every SQL; 2025 never queried. Box-score minutes are read ONLY to build as-of states (a row's features see strictly earlier
games; planted-future proof below). The target game's own minutes and stats are never used: stored A0 files are read for ids, ``min10``/``starter10``/``n_prior``
and the served forecast grid (``c, s, p_ge``); ``minutes_scores.npz`` only for ``row_gid, row_pid, row_season`` and ``M1_mean, M1_q10, M1_q90``.
Power is measured on a label-free noise arm (truth drawn from A0's own grid), so no realised outcome enters a power number.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import polars as pl
from scipy.stats import norm

from nba.props import context_residual as cr
from research.archive.preseason_role_prior import preseason_role_prior as arch
from research.eval import archetype_matchup_all_gate as amg
from research.eval import archetype_matchup_gate as g6
from research.eval import role_changepoint_rule as R
from research.eval import role_prior_salary_eval as ev

ROOT = Path(os.environ.get("NBA_DATA_ROOT", "."))
OUT = Path("reports/prereg_role_changepoint/gate.json")
P_COLS = [f"p{k}" for k in R.KS]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def sha8(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:8]


Q = (
    "select b.game_id, b.player_id, b.team_id, b.minutes, b.pts, b.reb, b.ast, b.fg3m, coalesce(b.starter, false) as starter, g.game_date, g.season "
    "from player_game_stats_played b join games g using (game_id) where g.season between 2022 and 2024 and b.minutes > 0"
)


def load() -> pd.DataFrame:
    c = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
    db = c.execute(Q).df()
    assert db.season.max() <= 2024
    db["game_date"] = pd.to_datetime(db.game_date)
    db = db[~db.game_id.isin(R.BACKFILLED_AFTER_A0)]
    return db.sort_values(["player_id", "game_date", "game_id"]).reset_index(drop=True)


def build_states(db: pd.DataFrame, cfg: R.BocpdConfig | None = None) -> pd.DataFrame:
    """One row per played game: production states (n_prior, min10, starter10, gap_days, since_flag, flag_dir) + the BOCPD and comparator columns."""
    pl_df = pl.from_pandas(db).with_columns(pl.col("game_date").cast(pl.Date))
    st = cr.player_states(pl_df)
    out = db[["game_id", "player_id", "team_id", "game_date", "season", "minutes"]].copy()
    out["n_prior"] = st["n_prior"]
    out["n_season"] = st["n_season"]
    out["gap_days"] = st["gap_days"]
    out["since_flag"] = st["since_flag"]
    out["flag_dir"] = st["flag_dir"]
    out["min10"] = cr._sel(st["pre"], 10.0, "min")
    out["min5"] = cr._sel(st["pre"], 5.0, "min")
    out["min40"] = cr._sel(st["pre"], 40.0, "min")
    out["starter10"] = cr._sel(st["pre"], 10.0, "starter")
    cols: dict[str, list[np.ndarray]] = {}
    pid = out.player_id.to_numpy()
    cuts = np.flatnonzero(np.r_[True, pid[1:] != pid[:-1], True])
    mins = out.minutes.to_numpy(float)
    for a, b in zip(cuts[:-1], cuts[1:], strict=True):
        x = mins[a:b]
        o = R.bocpd_asof(x, cfg)
        for k, v in o.items():
            cols.setdefault(f"bo_{k}", []).append(v)
        cols.setdefault("tw_z", []).append(R.two_window_z(x))
        cu = R.cusum_post(x)
        for k, v in cu.items():
            cols.setdefault(f"cu_{k}", []).append(v)
    for k, v in cols.items():
        out[k] = np.concatenate(v)
    return out


def gate_keys() -> dict[int, pd.DataFrame]:
    out = {}
    for S in (R.SELECT, R.REPORT):
        sets = []
        for st in R.STATS:
            o = arch.read_rows(st, S, ["game_id", "player_id"])
            o = o[o.game_id.str.startswith("002")]
            sets.append(set(zip(o.game_id, o.player_id, strict=True)))
        out[S] = pd.DataFrame(sorted(set.intersection(*sets)), columns=["game_id", "player_id"])
    return out


def cohort_mask(df: pd.DataFrame, k: int, thr: float) -> np.ndarray:
    return (df[f"bo_p{k}"].to_numpy() >= thr) & (df.n_prior.to_numpy() >= R.MIN_PRIOR)


def top_share(df: pd.DataFrame, mask: np.ndarray, n: int = 5) -> float:
    ps = (df.season.astype(str) + "-" + df.player_id.astype(str)).to_numpy()[mask]
    if len(ps) == 0:
        return float("nan")
    cnt = pd.Series(ps).value_counts()
    return float(cnt.iloc[:n].sum() / cnt.sum())


def counts(df: pd.DataFrame, S: int, keys: pd.DataFrame) -> dict[str, Any]:
    sub = df[df.season == S].merge(keys, on=["game_id", "player_id"], how="inner")
    out: dict[str, Any] = {
        "scored_set_rows": int(len(sub)),
        "player_seasons": int(sub.player_id.nunique()),
        "games": int(sub.game_id.nunique()),
        "weeks": int(len(np.unique(g6.weeks(sub, S)))),
        "rows_n_prior_ge_10": int((sub.n_prior >= R.MIN_PRIOR).sum()),
        "grid": {},
    }
    for k in R.KS:
        for thr in R.THRS:
            m = cohort_mask(sub, k, thr)
            out["grid"][f"K{k}_thr{thr}"] = {
                "rows": int(m.sum()),
                "share": round(float(m.mean()), 4),
                "player_seasons": int(sub[m].player_id.nunique()),
                "games": int(sub[m].game_id.nunique()),
                "top5_player_season_share": round(top_share(sub, m), 3),
            }
    m = cohort_mask(sub, R.K_PRIMARY, R.THR_PRIMARY)
    c = sub[m]
    up = (c.bo_mean - c.min10).to_numpy()
    out["primary"] = {
        "k": R.K_PRIMARY,
        "thr": R.THR_PRIMARY,
        "rows": int(m.sum()),
        "weeks": int(len(np.unique(g6.weeks(c, S)))) if len(c) else 0,
        "direction_up_rows": int((up > 0).sum()),
        "direction_down_rows": int((up < 0).sum()),
        "abs_bocpd_minus_min10_mean_min": round(float(np.abs(up).mean()), 3),
        "starter10_ge_0.5_rows": int((c.starter10 >= 0.5).sum()),
        "bench_rows": int((c.starter10 < 0.5).sum()),
        "gap_days_ge_7_rows (absence returns)": int((c.gap_days >= 7).sum()),
        "n_season_lt_10_rows (season-start; SEASON_BOUNDARY territory)": int(
            (c.n_season < 10).sum()
        ),
        "n_season_ge_10_rows (in-season; the primary slice)": int((c.n_season >= 10).sum()),
        "n_season_ge_10_player_seasons": int(c[c.n_season >= 10].player_id.nunique()),
        "n_season_ge_10_games": int(c[c.n_season >= 10].game_id.nunique()),
        "n_season_ge_10_top5_player_season_share": round(
            top_share(c[c.n_season >= 10], np.ones(int((c.n_season >= 10).sum()), dtype=bool)), 3
        ),
        "production_since_flag_le_K_rows (overlap with the CUSUM already in A0)": int(
            (c.since_flag <= R.K_PRIMARY).sum()
        ),
        "production_flag_dir_agrees_rows": int(
            ((c.since_flag <= R.K_PRIMARY) & (np.sign(c.flag_dir) == np.sign(up))).sum()
        ),
        "abs_bocpd_minus_min10_ge_3_rows": int((np.abs(up) >= 3).sum()),
        "abs_bocpd_minus_min10_ge_3_share_of_all_scored_rows": round(
            float((np.abs((sub.bo_mean - sub.min10).to_numpy()) >= 3).mean()), 4
        ),
        "rows_in_cohort_by_min10_bucket": {
            lab: int(((c.min10 >= lo) & (c.min10 < hi)).sum())
            for lab, lo, hi in (
                ("<10", 0, 10),
                ("10-20", 10, 20),
                ("20-28", 20, 28),
                (">=28", 28, 99),
            )
        },
    }
    # redundancy with columns A0 already has (information versus re-expression): as-of, label-free
    ok = (sub.n_prior >= R.MIN_PRIOR).to_numpy()
    y1 = (sub.bo_mean - sub.min10).to_numpy()[ok]
    X = np.column_stack(
        [
            (sub.min5 - sub.min10).to_numpy()[ok],
            (sub.min40 - sub.min10).to_numpy()[ok],
            (sub.since_flag.to_numpy()[ok] <= R.K_PRIMARY) * sub.flag_dir.to_numpy()[ok],
            np.ones(ok.sum()),
        ]
    )
    beta = np.linalg.lstsq(X, y1, rcond=None)[0]
    res_ = y1 - X @ beta
    out["redundancy_with_A0_columns"] = {
        "corr(bocpd_mean - min10, min5 - min10)": round(float(np.corrcoef(y1, X[:, 0])[0, 1]), 4),
        "R2 of (bocpd_mean - min10) on [min5-min10, min40-min10, flag_dir if since_flag<=8]": round(
            float(1 - res_.var() / y1.var()), 4
        ),
        "sd(bocpd_mean - min10) min": round(float(y1.std()), 3),
        "sd of the part not explained by those A0 columns (min)": round(float(res_.std()), 3),
    }
    # production CUSUM alone, same question, for the attribution baseline
    pm = (sub.since_flag.to_numpy() <= R.K_PRIMARY) & (sub.n_prior.to_numpy() >= R.MIN_PRIOR)
    out["production_cusum_cohort_rows"] = int(pm.sum())
    out["nan_pattern"] = {
        "bocpd_columns_nan_rows": int(sub[["bo_p5", "bo_mean", "bo_sd"]].isna().any(axis=1).sum()),
        "nan_only_if_n_prior_lt_1": bool(
            sub.loc[sub[["bo_p5", "bo_mean"]].isna().any(axis=1), "n_prior"].lt(1).all()
        )
        if sub[["bo_p5"]].isna().any().any()
        else True,
    }
    return out


def sensitivity(db: pd.DataFrame, keys: dict[int, pd.DataFrame]) -> dict[str, Any]:
    """Cohort size (K = 8, thr 0.5) under other detector constants; label-free, descriptive (the grids are searched on 2019-2022 after the freeze)."""
    out = {}
    for name, cfg in (
        ("sigma7", R.BocpdConfig(sigma=7.0)),
        ("sigma7_hazard1/60", R.BocpdConfig(sigma=7.0, hazard=1 / 60)),
        ("sigma5_tau8", R.BocpdConfig(tau=8.0)),
        ("sigma5_hazard1/60", R.BocpdConfig(hazard=1 / 60)),
        ("sigma4_hazard1/15", R.BocpdConfig(sigma=4.0, hazard=1 / 15)),
    ):
        d = build_states(db, cfg)
        row = {}
        for S in (R.SELECT, R.REPORT):
            sub = d[d.season == S].merge(keys[S], on=["game_id", "player_id"], how="inner")
            m = cohort_mask(sub, R.K_PRIMARY, R.THR_PRIMARY)
            row[str(S)] = {"rows": int(m.sum()), "share": round(float(m.mean()), 4)}
        out[name] = row
    return out


def a0_identity(df: pd.DataFrame, keys: dict[int, pd.DataFrame]) -> dict[str, Any]:
    """Our re-built production states vs the min10 / starter10 / n_prior stored with A0 on the scored rows (the run-time identity the fit repeats).
    A row mismatches when |min10 diff| > 1e-6 or n_prior differs; the 2024 stored rows are one data commit older than the current db (T493-T496 note)."""
    res = {}
    for S in (R.SELECT, R.REPORT):
        a = arch.read_rows("pts", S, ["game_id", "player_id", "n_prior", "min10", "starter10"])
        m = a.merge(
            df[["game_id", "player_id", "n_prior", "min10", "starter10"]],
            on=["game_id", "player_id"],
            suffixes=("_a0", "_gate"),
            how="inner",
        )
        bad = (np.abs(m.min10_a0 - m.min10_gate) > 1e-6) | (
            m.n_prior_a0.astype(float) != m.n_prior_gate.astype(float)
        )
        res[S] = {
            "rows": int(len(m)),
            "rows_mismatching": int(bad.sum()),
            "players_mismatching": int(m.loc[bad, "player_id"].nunique()),
            "max_abs_diff_min10": float(np.nanmax(np.abs(m.min10_a0 - m.min10_gate))),
            "max_abs_diff_starter10": float(np.nanmax(np.abs(m.starter10_a0 - m.starter10_gate))),
            "n_prior_equal_all": bool(
                (m.n_prior_a0.astype(float) == m.n_prior_gate.astype(float)).all()
            ),
        }
    return res


def planted_future(db: pd.DataFrame, base: pd.DataFrame, cut: str = "2023-12-01") -> dict[str, Any]:
    """Edit minutes of every game dated >= cut by x50; every column for rows strictly before cut must be identical; rows after must differ (control)."""
    d2 = db.copy()
    m = d2.game_date >= pd.Timestamp(cut)
    d2.loc[m, "minutes"] = np.minimum(d2.loc[m, "minutes"] * 50.0, 2000.0)
    new = build_states(d2)
    cols = [c for c in base.columns if c.startswith(("bo_", "tw_", "cu_"))] + [
        "min10",
        "min5",
        "min40",
        "since_flag",
        "starter10",
    ]
    before = (base.game_date < pd.Timestamp(cut)).to_numpy()
    same = bool(
        np.allclose(
            base.loc[before, cols].to_numpy(float),
            new.loc[before, cols].to_numpy(float),
            equal_nan=True,
            atol=0,
            rtol=0,
        )
    )
    after = ~before
    changed = float(
        np.mean(
            np.any(
                ~np.isclose(
                    base.loc[after, cols].to_numpy(float),
                    new.loc[after, cols].to_numpy(float),
                    equal_nan=True,
                ),
                axis=1,
            )
        )
    )
    return {
        "cut": cut,
        "rows_before": int(before.sum()),
        "rows_after": int(after.sum()),
        "identical_before": same,
        "control_share_of_after_rows_changed": round(changed, 4),
    }


def synthetic_detection(seeds: int = 60) -> dict[str, Any]:
    """Label-free positive/negative control for the DETECTOR (synthetic series, sigma 5, level 20): delay and coverage versus the comparator flags.
    Comparator thresholds are set on stationary series to match BOCPD's per-row false-flag rate (the fair comparison)."""
    n, ch = 140, 80
    k = R.K_PRIMARY

    def run(delta: float, seed0: int):
        rows = []
        for s in range(seeds):
            x = R.synth_step_series(n, ch, delta, 20.0, 5.0, seed0 + s)
            o = R.bocpd_asof(x)
            tw = R.two_window_z(x)
            cu = R.cusum_post(x)
            rows.append((x, o, tw, cu))
        return rows

    stat = run(0.0, 1000)
    fa_bo = float(np.mean([(o[f"p{k}"][15:ch] >= R.THR_PRIMARY).mean() for _, o, _, _ in stat]))
    # comparator thresholds to match fa_bo
    tw_all = np.concatenate([np.abs(tw[15:ch]) for _, _, tw, _ in stat])
    tw_thr = float(np.quantile(tw_all, 1 - max(fa_bo, 1e-4)))
    cu_rate = float(np.mean([(cu["since"][15:ch] <= k).mean() for _, _, _, cu in stat]))
    res: dict[str, Any] = {
        "bocpd_false_flag_row_rate": round(fa_bo, 4),
        "two_window_abs_z_thr_matched": round(tw_thr, 3),
        "cusum_since_le_K_false_row_rate_unmatched": round(cu_rate, 4),
        "by_delta": {},
    }
    for delta in (4.0, -4.0, 8.0, -8.0, 12.0):
        rows = run(delta, 2000)
        d_bo, d_tw, d_cu, cov_bo, cov_tw, cov_cu = [], [], [], [], [], []
        mean_err_bo, mean_err_ew10, mean_err_ew4 = [], [], []
        for x, o, tw, cu in rows:
            d_bo.append(R.detection_delay(o[f"p{k}"], ch, R.THR_PRIMARY))
            d_tw.append(R.detection_delay(np.abs(tw), ch, tw_thr))
            d_cu.append(R.detection_delay(15.0 - cu["since"], ch, 15.0 - k))
            cov_bo.append((o[f"p{k}"][ch + 1 : ch + 16] >= R.THR_PRIMARY).mean())
            cov_tw.append((np.abs(tw[ch + 1 : ch + 16]) >= tw_thr).mean())
            cov_cu.append((cu["since"][ch + 1 : ch + 16] <= k).mean())
            tgt = 20.0 + delta
            mean_err_bo.append(np.abs(o["mean"][ch + 1 : ch + 6] - tgt).mean())
            mean_err_ew10.append(np.abs(R.ew_mean_before(x, 10.0)[ch + 1 : ch + 6] - tgt).mean())
            mean_err_ew4.append(np.abs(R.ew_mean_before(x, 4.0)[ch + 1 : ch + 6] - tgt).mean())
        res["by_delta"][str(delta)] = {
            "bocpd_median_delay_games (of detected)": float(np.nanmedian(d_bo))
            if not np.all(np.isnan(d_bo))
            else None,
            "bocpd_never_detected_in_15": round(float(np.isnan(d_bo).mean()), 3),
            "two_window_median_delay": float(np.nanmedian(d_tw))
            if not np.all(np.isnan(d_tw))
            else None,
            "two_window_never": round(float(np.isnan(d_tw).mean()), 3),
            "cusum_median_delay": float(np.nanmedian(d_cu)) if not np.all(np.isnan(d_cu)) else None,
            "cusum_never": round(float(np.isnan(d_cu).mean()), 3),
            "share_of_15_post_rows_flagged": {
                "bocpd": round(float(np.mean(cov_bo)), 3),
                "two_window": round(float(np.mean(cov_tw)), 3),
                "cusum": round(float(np.mean(cov_cu)), 3),
            },
            "abs_error_of_level_estimate_games_1_to_5_after_change (minutes)": {
                "bocpd": round(float(np.mean(mean_err_bo)), 3),
                "ew_hl10": round(float(np.mean(mean_err_ew10)), 3),
                "ew_hl4": round(float(np.mean(mean_err_ew4)), 3),
            },
        }
    # order control (detector level only; NOT a scoring placebo, see the rule): mean over series of the max of p8 in the 15 rows after the change
    mx_real, mx_shuf = [], []
    for i, (x, o, _, _) in enumerate(run(8.0, 3000)):
        mx_real.append(float(np.nanmax(o["p8"][ch + 1 : ch + 16])))
        mx_shuf.append(
            float(
                np.nanmax(
                    R.bocpd_asof(np.random.default_rng(i).permutation(x))["p8"][ch + 1 : ch + 16]
                )
            )
        )
    res["shuffled_order_control_mean_max_p8_in_15_rows_after_change"] = {
        "real": round(float(np.mean(mx_real)), 3),
        "shuffled": round(float(np.mean(mx_shuf)), 3),
        "series": len(mx_real),
    }
    return res


def crps_cont(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    tau = (np.arange(1, 200) - 0.5) / 199
    return 2.0 * np.mean(((y[:, None] < q) - tau[None, :]) * (q - y[:, None]), axis=1)


def minutes_inputs(S: int, sub: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    ms = np.load(
        ROOT / "reports/minutes_v2/minutes_scores.npz", allow_pickle=True
    )  # own artifact; only the five listed keys are indexed
    sel = ms["row_season"] == S
    d = pd.DataFrame(
        {
            "game_id": ms["row_gid"][sel],
            "player_id": ms["row_pid"][sel],
            "mu": ms["M1_mean"][sel],
            "q10": ms["M1_q10"][sel],
            "q90": ms["M1_q90"][sel],
        }
    )
    d = (
        sub[["game_id", "player_id"]]
        .reset_index(drop=True)
        .merge(d, on=["game_id", "player_id"], how="left")
    )
    ok = d.mu.notna().to_numpy()
    sd = np.maximum((d.q90 - d.q10).to_numpy() / 2.563, 0.5)
    tau = (np.arange(1, 200) - 0.5) / 199
    q0 = np.clip(d.mu.to_numpy()[:, None] + sd[:, None] * norm.ppf(tau)[None, :], 0, 48)
    return q0, d.assign(_ok=ok)


def world_deltas_minutes(q0: np.ndarray, bias: np.ndarray, recover: float, seed: int) -> np.ndarray:
    """Hypothesised world: truth = A0 grid draw + bias_row (A0 lags the new role by bias_row minutes); the arm closes ``recover`` of it."""
    rng = np.random.default_rng(seed)
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = np.clip(q0[np.arange(len(q0)), u] + bias, 0, 48)
    return crps_cont(np.clip(q0 + recover * bias[:, None], 0, 48), y) - crps_cont(q0, y)


def noise_deltas_minutes(q0: np.ndarray, ps: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ids, inv = np.unique(ps, return_inverse=True)
    z = rng.standard_normal(len(ids))[inv]
    u = rng.integers(0, q0.shape[1], size=len(q0))
    y = q0[np.arange(len(q0)), u]
    return crps_cont(np.clip(q0 * np.exp(sigma * z)[:, None], 0, 48), y) - crps_cont(q0, y)


def power(df: pd.DataFrame, keys: dict[int, pd.DataFrame], seeds: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for S in (R.SELECT, R.REPORT):
        sub = (
            df[df.season == S]
            .merge(keys[S], on=["game_id", "player_id"], how="inner")
            .sort_values(["game_id", "player_id"])
            .reset_index(drop=True)
        )
        mask = cohort_mask(sub, R.K_PRIMARY, R.THR_PRIMARY) & (
            sub.n_season.to_numpy() >= R.MIN_SEASON_GAMES
        )
        coh = sub[mask].reset_index(drop=True)
        sign = np.sign((coh.bo_mean - coh.min10).to_numpy())
        ps = np.array([f"{S}-{p}" for p in coh.player_id])
        gid = coh.game_id.to_numpy()
        wk = g6.weeks(coh, S)
        cell: dict[str, Any] = {"cohort_rows": int(len(coh)), "clusters": g6.cluster_counts(coh, S)}
        # ---- minutes (approximate Normal grid from M1; the fitted grid is used in the real run)
        q0m, dm = minutes_inputs(S, coh)
        ok = dm._ok.to_numpy()
        cell["minutes_rows_with_M1"] = int(ok.sum())
        q0m, psm, gm, wm, sg = q0m[ok], ps[ok], gid[ok], wk[ok], sign[ok]
        hw = [
            ev.three_way(noise_deltas_minutes(q0m, psm, 0.05, 900 + s), gm, psm, wm)["binding"]
            for s in range(seeds)
        ]
        cell["minutes_noise_sigma0.05_halfwidth_min"] = {
            "mean": float(np.mean([h["half_width"] for h in hw])),
            "binding": hw[0]["clustering"],
        }
        worlds = {}
        for bias_min in (2.0, 4.0):
            for rec in (0.5, 1.0):
                d = world_deltas_minutes(q0m, sg * bias_min, rec, 77)
                w = ev.three_way(d, gm, psm, wm)["binding"]
                worlds[f"A0_lags_by_{bias_min}min_arm_closes_{rec}"] = {
                    "point": round(w["point"], 4),
                    "half_width": round(w["half_width"], 4),
                    "hi": round(w["hi"], 4),
                    "clears_floor_0.10_and_ci": bool(
                        w["hi"] < 0 and w["point"] <= -R.MINUTES_FLOOR_SLICE
                    ),
                }
        cell["minutes_hypothesised_worlds_cohort_delta_min"] = worlds
        # ---- props (integer grid); noise arm sigma 0.05 on the level, composite in floor units
        q0 = {}
        subp = None
        for st in R.STATS:
            q0[st], subp = amg.stat_inputs(st, S, coh[["game_id", "player_id"]])
        hwp = []
        perstat: dict[str, list[float]] = {s: [] for s in R.STATS}
        for s in range(seeds):
            d = {
                st: amg.noise_deltas(q0[st], ps, 0.05, 1000 + 7 * s + i)
                for i, st in enumerate(R.STATS)
            }
            jc = amg.joint(d, gid, ps, wk)
            hwp.append(jc["binding"]["half_width"])
            for st in R.STATS:
                perstat[st].append(
                    ev.three_way(d[st], gid, ps, wk)["binding"]["half_width"] / R.FLOORS[st]
                )
        cell["props_noise_sigma0.05_composite_halfwidth_floor_units"] = float(np.mean(hwp))
        cell["props_noise_per_stat_halfwidth_floor_units"] = {
            s: round(float(np.mean(v)), 3) for s, v in perstat.items()
        }
        # hypothesised world for props (pts only): minutes bias b -> pts scales by (min10 + b)/min10
        out[S] = cell
        log(f"power {S} done")
    return out


R.MINUTES_FLOOR_SLICE = 0.10  # minutes floor on the cohort slice (rule constant; see the draft)


def run(out: Path, do_power: bool, seeds: int) -> dict[str, Any]:
    t0 = time.time()
    db = load()
    log(f"loaded {len(db)} played rows 2022-24, {db.player_id.nunique()} players")
    df = build_states(db)
    log("states + detectors built")
    keys = gate_keys()
    res: dict[str, Any] = {
        "rule": R.ID,
        "constants": {
            k: getattr(R, k)
            for k in (
                "SIGMA",
                "TAU",
                "HAZARD",
                "RMAX",
                "LONG_HL",
                "K_PRIMARY",
                "THR_PRIMARY",
                "MIN_PRIOR",
            )
        },
    }
    res["inputs"] = {
        "nba_duckdb_mtime": time.strftime(
            "%Y-%m-%d %H:%M %Z", time.localtime((ROOT / "nba.duckdb").stat().st_mtime)
        ),
        "played_rows_2022_24": int(len(db)),
        "model_rows_sha8": {
            f"{S}_{st}": sha8(ROOT / f"reports/odds_history/model_rows_{S}_{st}.parquet")
            for S in (2023, 2024)
            for st in R.STATS
        },
        "minutes_scores_sha8": sha8(ROOT / "reports/minutes_v2/minutes_scores.npz"),
    }
    res["counts"] = {str(S): counts(df, S, keys[S]) for S in (R.SELECT, R.REPORT)}
    res["a0_identity"] = {str(k): v for k, v in a0_identity(df, keys).items()}
    res["sensitivity"] = sensitivity(db, keys)
    res["planted_future"] = planted_future(db, df)
    log("planted future done")
    res["synthetic_detection"] = synthetic_detection()
    log("synthetic done")
    if do_power:
        res["power"] = {str(S): v for S, v in power(df, keys, seeds).items()}
    res["excluded_backfilled_games"] = list(R.BACKFILLED_AFTER_A0)
    res["season_2025_loaded"] = False
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--no-power", action="store_true")
    a = ap.parse_args(argv)
    run(Path(a.out), not a.no_power, a.seeds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
