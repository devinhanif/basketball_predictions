"""Three small backlog items, evaluated under pre-registered rules (seasons < 2025 only).

The decision rules are in the module docstrings of ``nba.props.rookie_minutes`` (item 1) and
``nba.parlay.totals_poisson`` (item 2) and in docs/BACKLOG_SMALL_2026-10-08.md, all written before
the first run. Item 3 is analysis only (no keep/reject) on the persisted ctxres_v2 exp-2 OOF.

Entrypoint::

    uv run python -W ignore -m nba.eval.backlog_small_eval --db nba.duckdb --item all \
        --out-dir data/backlog_small
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy import stats

from nba.eval.ctxres_v2_eval import cdf_from_q19, ece_equal_mass
from nba.props.metrics import paired_score_delta_ci

N_BOOT = 2000
SEED = 0
OOF_RUN = "data/colab/runs/ctxres_v2_sweep/20261008_171446"
CANDIDATE = "xgb_v12_poisson_nb"
THR = {
    "pts": [10, 15, 20, 25, 30],
    "reb": [4, 6, 8, 10],
    "ast": [2, 4, 6, 8],
    "fg3m": [1, 2, 3, 4],
}
STATS = ("pts", "reb", "ast", "fg3m")


def _ci(a: np.ndarray, b: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT) -> list[float]:
    ci = paired_score_delta_ci(a, b, n_boot=n_boot, cluster_ids=cluster)
    return [float(ci.point), float(ci.lo), float(ci.hi)]


# --------------------------------------------------------------------------- item 1


def run_item1(con: duckdb.DuckDBPyConnection, n_boot: int = N_BOOT) -> dict[str, Any]:
    from nba.eval.context_screen_players import load_static
    from nba.features.player_pedigree import load_college_map, static_pedigree
    from nba.props.config import MinutesModelConfig
    from nba.props.minutes import build_minutes_features, fetch_actual_minutes
    from nba.props.rookie_minutes import MAX_SEASON, evaluate_rookie_minutes

    cfg = MinutesModelConfig()
    feats = build_minutes_features(
        con, lookback_games=cfg.lookback_games, use_game_context=cfg.use_game_context
    )
    feats = feats.filter(pl.col("season") <= MAX_SEASON)
    actual = fetch_actual_minutes(con)
    played = con.execute(
        "SELECT s.player_id, g.season, s.minutes FROM player_game_stats s "
        "JOIN games g USING (game_id) WHERE s.minutes > 0 AND g.season <= ?",
        [MAX_SEASON],
    ).pl()
    static = static_pedigree(load_static(con), load_college_map())
    res = evaluate_rookie_minutes(feats, actual, played, static, cfg, n_boot)
    res["minutes_config"] = {"k_mu": cfg.k_mu, "default_mu": cfg.default_mu}
    return res


# --------------------------------------------------------------------------- item 2


def run_item2(con: duckdb.DuckDBPyConnection, n_boot: int = N_BOOT) -> dict[str, Any]:
    from nba.eval.winprob_family_eval import (
        arm_makers,
        build_family_frame,
        game_model_wf,
        month_blocks,
        run_arm_wf,
    )
    from nba.models.winprob_family import crps_normal
    from nba.parlay.totals_poisson import NormalTotal, PoissonTotal, bvn_cdf

    df, _ = build_family_frame(con, "data/injury_elo/oof_predictions.parquet")
    assert int(df["season"].max()) <= 2024  # type: ignore[arg-type]
    teams = sorted(set(df["home_team"].to_list()) | set(df["away_team"].to_list()))
    wf = run_arm_wf(df, arm_makers(teams)["poisson_dc"])
    gm = game_model_wf(df)
    lam = wf["lam"]
    ok = ~np.isnan(lam[:, 0]) & ~np.isnan(gm["mu_m"]) & ~np.isnan(gm["mu_t"])
    rows = np.where(ok)[0]
    total = (df["home_pts"] + df["away_pts"]).to_numpy().astype(float)
    margin = (df["home_pts"] - df["away_pts"]).to_numpy().astype(float)
    gids = df["game_id"].to_numpy()
    rng = np.random.default_rng(SEED)

    pois = {int(i): PoissonTotal(lam[i, 0], lam[i, 1], lam[i, 2], lam[i, 3]) for i in rows}
    norm = {int(i): NormalTotal(float(gm["mu_t"][i]), float(gm["sd_t"][i])) for i in rows}
    cp = np.array([pois[int(i)].crps(total[i]) for i in rows])
    cn = np.array([norm[int(i)].crps(total[i]) for i in rows])
    mean_sd = float(np.nanmean(gm["sd_t"][rows]))
    cn_doc = crps_normal(gm["mu_t"][rows], mean_sd, total[rows])
    out: dict[str, Any] = {
        "n_games": int(len(rows)),
        "crps_poisson": float(cp.mean()),
        "crps_normal_rowsd": float(cn.mean()),
        "crps_normal_documented_meansd": float(cn_doc.mean()),
        "crps_delta_vs_rowsd": _ci(cp, cn, gids[rows], n_boot),
        "crps_delta_vs_documented": _ci(cp, cn_doc, gids[rows], n_boot),
        "poisson_k_range": [float(np.nanmin(lam[rows, 2])), float(np.nanmax(lam[rows, 2]))],
        "mean_bias_poisson": float(np.mean([pois[int(i)].mean() - total[i] for i in rows])),
        "mean_bias_normal": float(np.mean([norm[int(i)].mu - total[i] for i in rows])),
    }

    # total-leg calibration at synthetic over/under thresholds
    g_l, y_l, pn_l, pp_l = [], [], [], []
    for i in rows:
        h = norm[int(i)]
        base = round(h.mu)
        for d in range(-8, 9):
            thr = float(base + d) + 0.5
            p_n = h.p_over(thr)
            if not 0.15 <= p_n <= 0.85:
                continue
            g_l.append(gids[i])
            y_l.append(float(total[i] > thr))
            pn_l.append(p_n)
            pp_l.append(pois[int(i)].p_over(thr))
    g_a, y_a = np.array(g_l), np.array(y_l)
    pn_a = np.clip(np.array(pn_l), 1e-4, 1 - 1e-4)
    pp_a = np.clip(np.array(pp_l), 1e-4, 1 - 1e-4)

    def ll(p: np.ndarray) -> np.ndarray:
        return np.asarray(-(y_a * np.log(p) + (1 - y_a) * np.log(1 - p)))

    e_n, e_p = ece_equal_mass(pn_a, y_a), ece_equal_mass(pp_a, y_a)
    out["legs"] = {
        "n_legs": int(len(y_a)),
        "n_games": int(len(set(g_a.tolist()))),
        "ll_normal": float(ll(pn_a).mean()),
        "ll_poisson": float(ll(pp_a).mean()),
        "ll_delta": _ci(ll(pp_a), ll(pn_a), g_a, n_boot),
        "brier_delta": _ci((pp_a - y_a) ** 2, (pn_a - y_a) ** 2, g_a, n_boot),
        "ece_normal": e_n,
        "ece_poisson": e_p,
        "mean_p_normal": float(pn_a.mean()),
        "mean_p_poisson": float(pp_a.mean()),
        "mean_y": float(y_a.mean()),
    }

    # joint total x home-win under a Gaussian copula (descriptive)
    zm = (margin - gm["mu_m"]) / gm["sd_m"]
    zt_n = np.array([norm[int(i)].z_of(total[i]) for i in rows])
    zt_p = np.array([pois[int(i)].z_of(total[i], float(rng.random())) for i in rows])
    zpos = {int(i): j for j, i in enumerate(rows)}
    jg, jy, jpn, jpp = [], [], [], []
    for idx in month_blocks(df["game_date"].to_list()):
        s = int(idx[0])
        prior = rows[rows < s]
        if len(prior) < 300:
            continue
        pj = np.array([zpos[int(i)] for i in prior])
        rho_n = float(np.corrcoef(zt_n[pj], zm[prior])[0, 1])
        rho_p = float(np.corrcoef(zt_p[pj], zm[prior])[0, 1])
        for i in (int(r) for r in idx if ok[r]):
            c_m = -float(gm["mu_m"][i] / gm["sd_m"][i])  # home win <=> Z2 > c_m
            p_win = 1.0 - float(stats.norm.cdf(c_m))
            for d in range(-8, 9, 4):
                thr = float(round(norm[i].mu) + d) + 0.5
                if not 0.15 <= norm[i].p_over(thr) <= 0.85:
                    continue
                f_n, f_p = norm[i].cdf(thr), pois[i].cdf(thr)
                a_n = float(stats.norm.ppf(min(max(f_n, 1e-9), 1 - 1e-9)))
                a_p = float(stats.norm.ppf(min(max(f_p, 1e-9), 1 - 1e-9)))
                for over in (True, False):
                    ev = float((total[i] > thr) == over and margin[i] > 0)
                    ps = []
                    for a, f, rho in ((a_n, f_n, rho_n), (a_p, f_p, rho_p)):
                        both_le = float(bvn_cdf(np.array([a]), np.array([c_m]), rho)[0])
                        # P(T<=thr, M<=0 side c_m): joint of (under, home loss)
                        p_under_loss = both_le
                        p_under_win = f - p_under_loss
                        p_over_win = p_win - p_under_win
                        ps.append(p_over_win if over else p_under_win)
                    jg.append(gids[i])
                    jy.append(ev)
                    jpn.append(ps[0])
                    jpp.append(ps[1])
    jy_a = np.array(jy)
    jn = np.clip(np.array(jpn), 1e-4, 1 - 1e-4)
    jp = np.clip(np.array(jpp), 1e-4, 1 - 1e-4)

    def jll(p: np.ndarray) -> np.ndarray:
        return np.asarray(-(jy_a * np.log(p) + (1 - jy_a) * np.log(1 - p)))

    out["joint_total_x_win"] = {
        "n_parlays": int(len(jy_a)),
        "ll_normal": float(jll(jn).mean()),
        "ll_poisson": float(jll(jp).mean()),
        "ll_delta": _ci(jll(jp), jll(jn), np.array(jg), n_boot),
    }
    # decomposition (descriptive): is the gain the Poisson MEAN model or the discrete SHAPE?
    # A Normal with the Poisson head's own mean and sd isolates the shape.
    nm = {int(i): NormalTotal(pois[int(i)].mean(), pois[int(i)].sd()) for i in rows}
    cm = np.array([nm[int(i)].crps(total[i]) for i in rows])
    pm_l = []
    for i in rows:
        h = norm[int(i)]
        for d in range(-8, 9):
            thr = float(round(h.mu) + d) + 0.5
            if 0.15 <= h.p_over(thr) <= 0.85:
                pm_l.append(nm[int(i)].p_over(thr))
    pm_a = np.clip(np.array(pm_l), 1e-4, 1 - 1e-4)
    ll_m = -(y_a * np.log(pm_a) + (1 - y_a) * np.log(1 - pm_a))
    out["shape_vs_mean"] = {
        "crps_normal_poisson_moments": float(cm.mean()),
        "crps_poisson_minus_normal_poisson_moments": _ci(cp, cm, gids[rows], n_boot),
        "crps_normal_poisson_moments_minus_normal_rowsd": _ci(cm, cn, gids[rows], n_boot),
        "leg_ll_poisson_minus_normal_poisson_moments": _ci(ll(pp_a), ll_m, g_a, n_boot),
        "leg_ll_normal_poisson_moments_minus_normal_rowsd": _ci(ll_m, ll(pn_a), g_a, n_boot),
    }
    r = out["crps_delta_vs_rowsd"]
    lg = out["legs"]
    ll_ok = lg["ll_delta"][0] <= 0 or (
        lg["ll_delta"][0] <= 0.0005 and lg["ll_delta"][1] <= 0 <= lg["ll_delta"][2]
    )
    ece_ok = (lg["ece_poisson"] - lg["ece_normal"]) <= 0.005
    out["checks"] = {
        "primary_crps_ci_below_0": bool(r[2] < 0),
        "leg_logloss_not_worse": bool(ll_ok),
        "leg_ece_not_worse": bool(ece_ok),
    }
    out["keep"] = bool(all(out["checks"].values()))
    return out


# --------------------------------------------------------------------------- item 3


def _mat(col: pl.Series) -> np.ndarray:
    return np.array(col.to_list(), dtype=float)


def _noise_floor_ece(p: np.ndarray, n_sim: int, rng: np.random.Generator) -> float:
    """Expected equal-mass ECE of a PERFECTLY calibrated forecast with these probabilities and
    nested (comonotone) threshold events ``u <= p_j``."""
    vals = []
    for _ in range(n_sim):
        u = rng.random(len(p))[:, None]
        vals.append(ece_equal_mass(p, (u <= p).astype(float)))
    return float(np.mean(vals))


def diagnose_calibrators(run_dir: str = OOF_RUN, n_sim: int = 30) -> dict[str, Any]:
    """Descriptive diagnosis of the exp-2 calibrator results on the persisted 2024 OOF."""
    from sklearn.linear_model import LogisticRegression

    base = Path(run_dir) / "oof"
    names = {"raw": CANDIDATE}
    names.update({k: f"{CANDIDATE}__{k}" for k in ("platt", "iso", "conf", "pit")})
    oof = {
        k: pl.read_parquet(base / f"{v}.parquet").filter(pl.col("stat").is_not_null())
        for k, v in names.items()
    }
    rng = np.random.default_rng(SEED)
    out: dict[str, Any] = {"n_rows_per_stat": {}}
    for st in STATS:
        thr = np.array(THR[st])
        d = {
            k: v.filter(pl.col("stat") == st).sort(["game_id", "player_id"]) for k, v in oof.items()
        }
        raw = d["raw"]
        y = raw["y"].to_numpy().astype(float)
        gid = raw["game_id"].to_numpy()
        fold = raw["fold_id"].to_numpy()
        ev = (y[:, None] >= thr[None, :]).astype(float)
        res: dict[str, Any] = {"n": int(len(y))}
        # (a) ECE vs noise floor
        ece_rows = {}
        for k in ("raw", "platt", "iso", "conf", "pit"):
            p = _mat(d[k]["p_ge"])
            ece_rows[k] = ece_equal_mass(p, ev)
        res["ece"] = ece_rows
        res["ece_noise_floor_raw"] = _noise_floor_ece(_mat(raw["p_ge"]), n_sim, rng)
        res["ece_noise_floor_iso"] = _noise_floor_ece(_mat(d["iso"]["p_ge"]), n_sim, rng)
        # (b) per-threshold gaps (binomial SE; rows within a game are correlated, so optimistic)
        p_raw = _mat(raw["p_ge"])
        p_platt = _mat(d["platt"]["p_ge"])
        p_iso = _mat(d["iso"]["p_ge"])
        per_thr = []
        for j, t in enumerate(thr):
            m, f = p_raw[:, j].mean(), ev[:, j].mean()
            se = float(np.sqrt(max(f * (1 - f), 1e-12) / len(y)))
            per_thr.append(
                {
                    "thr": int(t),
                    "events": int(ev[:, j].sum()),
                    "mean_p_raw": float(m),
                    "freq": float(f),
                    "gap_raw": float(m - f),
                    "gap_z": float((m - f) / se),
                    "gap_platt": float(p_platt[:, j].mean() - f),
                    "gap_iso": float(p_iso[:, j].mean() - f),
                }
            )
        res["per_threshold"] = per_thr
        # logistic recalibration slope / intercept of raw p (pooled over thresholds)
        lp = np.log(np.clip(p_raw, 1e-4, 1 - 1e-4) / (1 - np.clip(p_raw, 1e-4, 1 - 1e-4))).ravel()
        lr = LogisticRegression(C=1e6, max_iter=500).fit(lp[:, None], ev.ravel().astype(int))
        res["recal_slope"] = float(lr.coef_[0, 0])
        res["recal_intercept"] = float(lr.intercept_[0])
        # (c) month-to-month persistence of the per-threshold gap
        folds = sorted(set(fold.tolist()))
        gap = np.full((len(folds), len(thr)), np.nan)
        for a, f_id in enumerate(folds):
            m = fold == f_id
            if m.sum() >= 300:
                gap[a] = p_raw[m].mean(axis=0) - ev[m].mean(axis=0)
        pairs = [
            (gap[a], gap[a + 1])
            for a in range(len(folds) - 1)
            if not np.isnan(gap[a : a + 2]).any()
        ]
        if len(pairs) >= 3:
            x = np.concatenate([p[0] for p in pairs])
            z = np.concatenate([p[1] for p in pairs])
            res["gap_persistence_corr"] = float(np.corrcoef(x, z)[0, 1])
            res["gap_sign_agree"] = float(np.mean(np.sign(x) == np.sign(z)))
        trail = []
        for a in range(1, len(folds)):
            if np.isnan(gap[a]).any():
                continue
            hist = gap[max(0, a - 4) : a]
            hist = hist[~np.isnan(hist).any(axis=1)]
            if len(hist):
                trail.append((gap[a], hist.mean(axis=0)))
        if trail:
            g = np.concatenate([t[0] for t in trail])
            pr = np.concatenate([t[1] for t in trail])
            res["trailing4_gap_r2"] = float(1.0 - np.sum((g - pr) ** 2) / np.sum(g**2))
        res["monthly_gap_mean"] = {str(f): float(np.nanmean(gap[a])) for a, f in enumerate(folds)}
        # (d) integer-grid damage
        q19 = _mat(raw["q19"])
        iqr = q19[:, 14] - q19[:, 4]
        res["frac_iqr_le_floor"] = float(np.mean(iqr <= 0.25 * 1.349))
        res["mean_distinct_knots"] = float(np.mean([len(set(r)) for r in q19.tolist()]))
        p_grid = np.column_stack([1.0 - cdf_from_q19(q19, np.full(len(y), t - 0.5)) for t in thr])
        p_grid = np.clip(p_grid, 1e-4, 1 - 1e-4)
        p_ex = np.clip(p_raw, 1e-4, 1 - 1e-4)

        def tll(p: np.ndarray, ev: np.ndarray = ev) -> np.ndarray:
            return np.asarray(-(ev * np.log(p) + (1 - ev) * np.log(1 - p)).mean(axis=1))

        res["grid_vs_exact_tll"] = _ci(tll(p_grid), tll(p_ex), gid)
        res["ece_grid_derived"] = ece_equal_mass(p_grid, ev)
        # (e) PIT uniformity of the raw forecast (non-randomized, continuity-corrected)
        hi = cdf_from_q19(q19, y + 0.5)
        lo = cdf_from_q19(q19, y - 0.5)
        edges = np.linspace(0, 1, 11)
        mass = np.zeros(10)
        for b in range(10):
            w = np.maximum(hi - lo, 1e-12)
            mass[b] = np.mean(
                np.clip((edges[b + 1] - lo) / w, 0, 1) - np.clip((edges[b] - lo) / w, 0, 1)
            )
        res["pit_decile_mass"] = [float(v) for v in mass]
        res["pit_max_dev"] = float(np.max(np.abs(mass - 0.1)))
        # calibrator effect per stat (clustered CI of thr-LL vs raw), for context
        for k in ("platt", "iso", "conf", "pit"):
            pk = np.clip(_mat(d[k]["p_ge"]), 1e-4, 1 - 1e-4)
            res[f"tll_delta_{k}"] = _ci(tll(pk), tll(p_ex), gid)
        out[st] = res
        out["n_rows_per_stat"][st] = int(len(y))
    return out


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Small backlog items 1-3")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--item", default="all", choices=["1", "2", "3", "all"])
    ap.add_argument("--out-dir", default="data/backlog_small")
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(args.db, read_only=True)
    if args.item in ("1", "all"):
        r1 = run_item1(con, args.n_boot)
        (out / "item1_rookie_minutes.json").write_text(json.dumps(r1, indent=1, default=str))
        print("ITEM1", json.dumps(r1, indent=1, default=str))
    if args.item in ("2", "all"):
        r2 = run_item2(con, args.n_boot)
        (out / "item2_totals_poisson.json").write_text(json.dumps(r2, indent=1, default=str))
        print("ITEM2", json.dumps(r2, indent=1, default=str))
    if args.item in ("3", "all"):
        r3 = diagnose_calibrators()
        (out / "item3_calibrator_diagnosis.json").write_text(json.dumps(r3, indent=1, default=str))
        print("ITEM3 written", out / "item3_calibrator_diagnosis.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
