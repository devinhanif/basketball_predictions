# ruff: noqa: E501
"""COMBO_STOCKS_SHADOW rehearsal, stage B: SELECT on 2023, then REPORT on 2024 (DESCRIPTIVE, not evidence).

Reads stage A's walk-forward grids (``stage_a_oof.py``); the database is read only for the played-game history that RECENT
needs and for player positions (seasons <= 2024 only). Two phases, run in this order, so that nothing in the draft rule is
chosen after the report season is read:

  --phase select   season 2023 regular season only: marginal scores, the equivalence of my pts/reb/ast walk-forward with the
                   stored production OOF, the correlation-scheme choice (pooled / position / starter, time split-half,
                   frozen criterion), the final latent matrices from all of 2023, the 2023 contrasts that set the floors and
                   the gate counts. Writes ``select.json`` and ``R_chosen.npz``.
  --phase report   season 2024 regular season only: loads ``R_chosen.npz`` as written, scores COPULA / INDEP / RECENT
                   (/ DIRECT / RECENT_CAL if stage A2 ran), calibration, slices, sampler check, gate counts.

    python -W ignore reports/prereg_combo_stocks_shadow/stage_b.py --phase select --a <stage_a> --a2 <stage_a2|none> --db <db> --out <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from nba.eval.avg_shadow_live import calibration  # noqa: E402
from nba.eval.context_residual_eval import crps_from_quantiles  # noqa: E402
from nba.eval.full_shadow_live import one_sided  # noqa: E402
from nba.eval.lower_tail_eval import randomized_pit  # noqa: E402
from nba.props import combo_copula as C  # noqa: E402

STATS = C.STAT_ORDER
COMBO_NAMES = tuple(C.COMBOS)
SELECT, REPORT = 2023, 2024
SEL_FLOOR = 0.005  # by-role replaces pooled only if the held-out mean CRPS gain over the five combos is at least this
MIN_GROUP_ROWS = 3000
PROD_OOF = Path(
    "/Users/devin/Downloads/nba-prediction/reports/context_residual_realtip/oof_context_residual.parquet"
)


def load_oof(d: Path) -> tuple[pl.DataFrame, dict[str, np.ndarray]]:
    tags = sorted(p.stem.removeprefix("keys_") for p in d.glob("keys_*.parquet"))
    keys = pl.concat([pl.read_parquet(d / f"keys_{t}.parquet") for t in tags])
    arrs: dict[str, list[np.ndarray]] = {}
    for t in tags:
        z = np.load(d / f"oof_{t}.npz")
        for k in z.files:
            arrs.setdefault(k, []).append(z[k])
    return keys, {k: np.concatenate(v) for k, v in arrs.items()}


def crps(q: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Exact integer CRPS (RPS) of equal-mass 199-point integer grids (the midpoint rule is exact for them)."""
    return np.asarray(crps_from_quantiles(q.astype(float), y.astype(float)))


def contrast(a: np.ndarray, b: np.ndarray, gid: np.ndarray, day: np.ndarray) -> dict[str, Any]:
    """Paired delta a - b with game- and date-clustered one-sided bounds (percentile bootstrap, 2000 draws, seed 0)."""
    d = a - b
    g, t = one_sided(d, gid), one_sided(d, day)
    se_row = float(d.std(ddof=1) / np.sqrt(len(d)))
    return {
        "n": int(len(d)),
        "delta": float(d.mean()),
        "game_upper95": g.get("upper95"),
        "game_lower95": g.get("lower95"),
        "date_upper95": t.get("upper95"),
        "sd_row": float(d.std(ddof=1)),
        "deff_game": float((g["se"] / se_row) ** 2) if g.get("se") else None,
        "p_less_game": g.get("p_less"),
    }


def cal(q: np.ndarray, y: np.ndarray) -> dict[str, Any]:
    c = calibration(q.astype(float), y.astype(float))
    pit = randomized_pit(q.astype(float), y.astype(float), 0)
    return {
        "bias": c["bias"],
        "cov80": c["cov80"],
        "cover": {k: round(v, 4) for k, v in c["guard"]["cover"].items()},
        "g1_ok": c["ok"],
        "pit_mean": float(pit.mean()),
    }


def history_grids(db: str, keys: pl.DataFrame) -> dict[str, np.ndarray]:
    """RECENT: recency-weighted empirical 199-grid of the last 40 played values, per target, aligned to ``keys``."""
    con = duckdb.connect(db, read_only=True)
    try:
        pg = con.execute(
            "SELECT s.game_id, s.player_id, s.minutes, s.pts, s.reb, s.ast, s.fg3m, s.stl, s.blk, g.game_date FROM player_game_stats s "
            "JOIN games g USING (game_id) WHERE g.season <= 2024 AND s.minutes > 0"
        ).pl()
    finally:
        con.close()
    pg = pg.with_columns(
        (pl.col("pts") + pl.col("reb") + pl.col("ast")).alias("pra"),
        (pl.col("pts") + pl.col("reb")).alias("pr"),
        (pl.col("pts") + pl.col("ast")).alias("pa"),
        (pl.col("reb") + pl.col("ast")).alias("ra"),
        (pl.col("blk") + pl.col("stl")).alias("bs"),
    ).sort(["player_id", "game_date", "game_id"])
    idx = pg.select("game_id", "player_id").with_row_index("_i")
    pos = (
        keys.select("game_id", "player_id")
        .join(idx, on=["game_id", "player_id"], how="left")["_i"]
        .to_numpy()
    )
    assert not np.isnan(pos.astype(float)).any()
    return {t: C.empirical_grid(C.recent_windows(pg, t)[pos]) for t in (*STATS, *COMBO_NAMES)}


class World:
    """Everything both phases read: keys, grids, outcomes, RECENT, positions."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.keys, arr = load_oof(Path(args.a))
        if getattr(args, "a5", "none") != "none":  # fg3m joins the joint (Devin, 2026-10-14)
            k5, a5 = load_oof(Path(args.a5))
            assert k5.select("game_id", "player_id").equals(
                self.keys.select("game_id", "player_id")
            ), "stage A5 rows differ"
            arr.update({k_: v for k_, v in a5.items() if k_.endswith("_fg3m")})
        k = self.keys
        self.reg = k["game_id"].str.starts_with("002").to_numpy()
        self.season = k["season"].to_numpy()
        self.gid = k["game_id"].to_numpy()
        self.day = k["game_date"].cast(pl.Int32).to_numpy()
        self.y = {s: arr[f"y_{s}"].astype(np.int64) for s in STATS}
        self.y.update(
            pra=self.y["pts"] + self.y["reb"] + self.y["ast"],
            pr=self.y["pts"] + self.y["reb"],
            pa=self.y["pts"] + self.y["ast"],
            ra=self.y["reb"] + self.y["ast"],
            bs=self.y["blk"] + self.y["stl"],
        )
        self.q = {s: arr[f"q_{s}"] for s in STATS}
        self.q_nolt = self.q["pts"]
        if (
            getattr(args, "a4", "none") != "none"
        ):  # pts = production lower-tail mixture (the A0 of the other shadows)
            k4, a4 = load_oof(Path(args.a4))
            assert k4.select("game_id", "player_id").equals(k.select("game_id", "player_id")), (
                "stage A4 rows differ"
            )
            self.q["pts"] = a4["q_pts"]
        self.q_cr = dict(self.q)
        self.q_nb: dict[str, np.ndarray] = {}
        if getattr(args, "a3", "none") != "none":
            k3, a3 = load_oof(Path(args.a3))
            assert k3.select("game_id", "player_id").equals(k.select("game_id", "player_id")), (
                "stage A3 rows differ"
            )
            self.q_nb = {s_: a3[f"q_{s_}"] for s_ in ("blk", "stl")}
        self.qb = {s: arr[f"qb_{s}"] for s in STATS}
        self.q19raw = {s: arr[f"q19raw_{s}"] for s in ("pts", "reb", "ast")}
        self.rec = history_grids(args.db, k)
        self.direct = self.qcal = None
        if args.a2 != "none":
            k2, a2 = load_oof(Path(args.a2))
            assert k2.select("game_id", "player_id").equals(k.select("game_id", "player_id")), (
                "stage A2 rows differ from stage A"
            )
            self.direct = {c: a2[f"q_{c}"] for c in COMBO_NAMES}
            self.qcal = {c: a2[f"qb_{c}"] for c in COMBO_NAMES}
        con = duckdb.connect(args.db, read_only=True)
        try:
            st = con.execute("SELECT player_id, position FROM players_static").pl()
        finally:
            con.close()
        code = (
            st.with_columns(
                pl.col("position")
                .cast(pl.Utf8)
                .str.slice(0, 1)
                .replace_strict({"G": 0, "F": 1, "C": 2}, default=-1)
                .alias("pos")
            )
            .select("player_id", "pos")
            .unique("player_id")
        )
        self.pos = k.join(code, on="player_id", how="left")["pos"].fill_null(-1).to_numpy()
        self.starter = (k["starter10"].fill_null(0.0).to_numpy() >= 0.5).astype(int)
        self.choice = {s: "q" for s in STATS}
        self.cdfs = {s: C.int_cdf(self.q[s], C.KMAX[s]) for s in STATS}
        self.base = C.base_normals()

    def set_head(self, head: dict[str, str]) -> None:
        """Which head is the model grid of blk / stl: ``cr`` (production recipe) or ``nb`` (NBCountModel)."""
        for s_, h in head.items():
            self.q[s_] = self.q_nb[s_] if h == "nb" else self.q_cr[s_]

    def set_choice(self, choice: dict[str, str]) -> None:
        """Which marginal grid enters the copula per stat: ``q`` (the head) or ``qb`` (recency-conformal)."""
        self.choice = dict(choice)
        self.cdfs = {
            s: C.int_cdf((self.q if choice[s] == "q" else self.qb)[s], C.KMAX[s]) for s in STATS
        }

    def schemes(self) -> dict[str, np.ndarray]:
        return {
            "pooled": np.zeros(self.keys.height, dtype=int),
            "position": self.pos,
            "starter": self.starter,
        }

    def sub(self, idx: np.ndarray) -> dict[str, np.ndarray]:
        return {s: self.cdfs[s][idx] for s in STATS}


def marginals(w: World, m: np.ndarray) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in STATS:
        y = w.y[s][m]
        r: dict[str, Any] = {
            "n": int(m.sum()),
            "games": int(len(np.unique(w.gid[m]))),
            "mean_y": float(y.mean()),
            "p0": float((y == 0).mean()),
        }
        c_mod, c_cal, c_rec = crps(w.q[s][m], y), crps(w.qb[s][m], y), crps(w.rec[s][m], y)
        r["crps"] = {
            "model": float(c_mod.mean()),
            "recent_cal": float(c_cal.mean()),
            "recent": float(c_rec.mean()),
        }
        r["model_vs_recent"] = contrast(c_mod, c_rec, w.gid[m], w.day[m])
        r["model_vs_recent_cal"] = contrast(c_mod, c_cal, w.gid[m], w.day[m])
        r["cal_model"], r["cal_recent_cal"], r["cal_recent"] = (
            cal(w.q[s][m], y),
            cal(w.qb[s][m], y),
            cal(w.rec[s][m], y),
        )
        cdf0 = (w.q[s][m] == 0).mean(axis=1)  # P(Y = 0) of the 199-point grid
        bins = np.minimum((cdf0 * 10).astype(int), 9)
        r["p0_reliability"] = [
            {
                "bin": int(b),
                "n": int((bins == b).sum()),
                "pred": float(cdf0[bins == b].mean()),
                "obs": float((y[bins == b] == 0).mean()),
            }
            for b in range(10)
            if (bins == b).sum() >= 30
        ]
        if s in w.q_nb:
            r["head_arms"] = {}
            for nm, grid in (("cr", w.q_cr[s][m]), ("nb", w.q_nb[s][m])):
                c_h = crps(grid, y)
                p0h = (grid == 0).mean(axis=1)
                bh = np.minimum((p0h * 10).astype(int), 9)
                r["head_arms"][nm] = {
                    "crps": float(c_h.mean()),
                    "vs_recent": contrast(c_h, c_rec, w.gid[m], w.day[m]),
                    "vs_recent_cal": contrast(c_h, c_cal, w.gid[m], w.day[m]),
                    "cal": cal(grid, y),
                    "p0_reliability": [
                        {
                            "bin": int(b),
                            "n": int((bh == b).sum()),
                            "pred": float(p0h[bh == b].mean()),
                            "obs": float((y[bh == b] == 0).mean()),
                        }
                        for b in range(10)
                        if (bh == b).sum() >= 30
                    ],
                }
        out[s] = r
        print(
            s,
            json.dumps({k: r[k] for k in ("crps", "model_vs_recent_cal")}, default=float),
            flush=True,
        )
    return out


def equivalence(w: World) -> dict[str, Any]:
    """My walk-forward q19 against the stored production OOF (written 2026-10-09), by season: where the data are
    unchanged the two are bit-identical; the remaining differences are data changes after 2026-10-09."""
    if not PROD_OOF.exists():
        return {}
    prod = pl.read_parquet(PROD_OOF)
    eq: dict[str, Any] = {}
    for s in ("pts", "reb", "ast"):
        mine = w.keys.select("game_id", "player_id", "season").with_columns(
            pl.Series("row", np.arange(w.keys.height))
        )
        j = mine.join(
            prod.filter(pl.col("target") == s).select("game_id", "player_id", "q_grid"),
            on=["game_id", "player_id"],
        )
        a = w.q19raw[s][j["row"].to_numpy()]
        b = np.asarray(j["q_grid"].to_list(), dtype=float)
        dmax = np.abs(a - b).max(axis=1)
        for sv in (SELECT, REPORT):
            mk = j["season"].to_numpy() == sv
            eq[f"{s}_{sv}"] = {
                "n_joined": int(mk.sum()),
                "frac_rows_equal_1e-3": float((dmax[mk] < 1e-3).mean()),
                "max_abs_diff_q19": float(dmax[mk].max()),
            }
    return eq


def estimate_R(w: World, idx: np.ndarray, method: str = "rpit") -> np.ndarray:
    r, _, _ = C.estimate_latent_corr(
        [w.cdfs[s][idx] for s in STATS], [w.y[s][idx] for s in STATS], seed=0, method=method
    )
    return r


def grids_by_group(
    w: World,
    rows: np.ndarray,
    grp_rows: np.ndarray,
    mats: dict[int, np.ndarray],
    pooled: np.ndarray,
) -> dict[str, np.ndarray]:
    out = {c: np.zeros((len(rows), 199), dtype=np.int16) for c in COMBO_NAMES}
    for gval in np.unique(grp_rows):
        ei = np.flatnonzero(grp_rows == gval)
        g = C.joint_grids(w.sub(rows[ei]), mats.get(int(gval), pooled), w.base)
        for c in COMBO_NAMES:
            out[c][ei] = g[c]
    return out


FLOOR_REL, FLOOR_MIN = 0.01, 0.005  # floor_t = max(0.005, 1% of RECENT's 2023 regular-season CRPS)
# a combo uses the copula (not the independent sum) iff COPULA - INDEP < 0 with game-clustered one-sided upper bound < 0 (no floor:
# the two cost the same, so the choice between them is significance only)
Z_ALPHA, Z_BETA = 1.6448536, 0.8416212  # one-sided 95% test, 80% power


def n_required(sd_row: float, deff: float, delta: float) -> int:
    """Rows for 80% power of the one-sided 95% clustered test at a true effect ``delta`` (positive number)."""
    return int(np.ceil(((Z_ALPHA + Z_BETA) * sd_row * np.sqrt(deff) / delta) ** 2))


def fit_matrices(
    w: World, idx: np.ndarray, grp: np.ndarray, method: str, scheme: str
) -> dict[int, np.ndarray]:
    pooled = estimate_R(w, idx, method)
    mats: dict[int, np.ndarray] = {}
    for gval in np.unique(grp[idx]):
        gi = idx[grp[idx] == gval]
        mats[int(gval)] = (
            pooled
            if (scheme == "pooled" or len(gi) < MIN_GROUP_ROWS)
            else estimate_R(w, gi, method)
        )
    return mats


def phase_select(w: World, out: Path) -> None:
    t0 = time.time()
    sel = w.reg & (w.season == SELECT)
    mar = marginals(w, sel)
    head = {s_: "cr" for s_ in ("blk", "stl")}
    for s_ in ("blk", "stl"):
        if s_ in w.q_nb and mar[s_]["head_arms"]["nb"]["crps"] < mar[s_]["head_arms"]["cr"]["crps"]:
            head[s_] = "nb"
    w.set_head(head)
    mar = marginals(w, sel)  # again with the chosen head as "model"
    res: dict[str, Any] = {
        "phase": "select",
        "season": SELECT,
        "n_2023_regular": int(sel.sum()),
        "games": int(len(np.unique(w.gid[sel]))),
        "marginals_2023": mar,
        "equivalence_prod_oof": equivalence(w),
    }
    # which marginal enters the copula: the head unless its 2023 CRPS is worse than the recency-conformal baseline
    choice = {s: "q" for s in STATS}
    for s_ in ("blk", "stl"):
        choice[s_] = "q" if mar[s_]["crps"]["model"] <= mar[s_]["crps"]["recent_cal"] else "qb"
    w.set_choice(choice)
    res["marginal_choice"] = choice
    res["head_choice"] = head
    print("marginal choice:", choice, flush=True)
    med = float(np.median(w.day[sel]))
    fit_idx, eval_idx = np.flatnonzero(sel & (w.day < med)), np.flatnonzero(sel & (w.day >= med))
    res["split"] = {"split_day": med, "n_fit": int(len(fit_idx)), "n_eval": int(len(eval_idx))}
    schemes = w.schemes()

    def eval_scheme(method: str, scheme: str) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
        t1 = time.time()
        grp = schemes[scheme]
        mats = fit_matrices(w, fit_idx, grp, method, scheme)
        g = grids_by_group(w, eval_idx, grp[eval_idx], mats, mats.get(0, next(iter(mats.values()))))
        per = {c: float(crps(g[c], w.y[c][eval_idx]).mean()) for c in COMBO_NAMES}
        sizes = {str(int(v)): int((grp[fit_idx] == v).sum()) for v in np.unique(grp[fit_idx])}
        rec = {
            "crps": per,
            "mean_crps": float(np.mean(list(per.values()))),
            "group_fit_rows": sizes,
        }
        print(
            f"select {method}/{scheme}: {rec['mean_crps']:.5f} ({time.time() - t1:.0f}s)",
            flush=True,
        )
        return rec, g

    methods: dict[str, Any] = {}
    stored: dict[tuple[str, str], dict[str, np.ndarray]] = {}
    for method in C.METHODS:
        methods[method], stored[(method, "pooled")] = eval_scheme(method, "pooled")
    best_method = "rpit"
    if methods["cov"]["mean_crps"] < methods["rpit"]["mean_crps"] - 0.0005:
        best_method = "cov"
    res["methods"], res["chosen_method"] = methods, best_method
    sc: dict[str, Any] = {"pooled": methods[best_method]}
    for scheme in ("position", "starter"):
        sc[scheme], stored[(best_method, scheme)] = eval_scheme(best_method, scheme)
    pooled = sc["pooled"]["mean_crps"]
    best, best_gain = "pooled", 0.0
    for scheme in ("position", "starter"):
        gain = pooled - sc[scheme]["mean_crps"]
        small = any(v < MIN_GROUP_ROWS for v in sc[scheme]["group_fit_rows"].values())
        sc[scheme].update(gain_vs_pooled=gain, has_small_group=small)
        if gain >= SEL_FLOOR and not small and gain > best_gain:
            best, best_gain = scheme, gain
    res["schemes"], res["chosen_scheme"] = sc, best
    print(f"chosen: method {best_method}, scheme {best}", flush=True)
    # final matrices from all of 2023
    all_idx = np.flatnonzero(sel)
    grp = schemes[best]
    pooled_R, obs, pinfo = C.estimate_latent_corr(
        [w.cdfs[s][all_idx] for s in STATS],
        [w.y[s][all_idx] for s in STATS],
        seed=0,
        method=best_method,
    )
    mats_final = fit_matrices(w, all_idx, grp, best_method, best)
    info: dict[str, Any] = {
        "stat_order": list(STATS),
        "method": best_method,
        "pooled_latent": np.round(pooled_R, 4).tolist(),
        "observed_statistic": np.round(obs, 4).tolist(),
        "pooled_info": pinfo,
    }
    for gval, mat in mats_final.items():
        info[f"group_{gval}"] = {
            "n": int((grp[all_idx] == gval).sum()),
            "latent": np.round(mat, 4).tolist(),
        }
    res["matrices_2023"] = info
    # the other estimator's pooled matrix on all 2023, for the record
    other = "rpit" if best_method == "cov" else "cov"
    other_R, other_obs, _ = C.estimate_latent_corr(
        [w.cdfs[s][all_idx] for s in STATS], [w.y[s][all_idx] for s in STATS], seed=0, method=other
    )
    res["matrices_2023_other_method"] = {
        "method": other,
        "latent": np.round(other_R, 4).tolist(),
        "observed_statistic": np.round(other_obs, 4).tolist(),
    }
    # 2023 eval-half contrasts: which combos use the copula, and the floors
    g = stored[(best_method, best)]
    ind = C.joint_grids(w.sub(eval_idx), np.eye(len(STATS)), w.base)
    c23: dict[str, Any] = {}
    use_copula: dict[str, bool] = {}
    for c in COMBO_NAMES:
        yy = w.y[c][eval_idx]
        scs = {
            "copula": crps(g[c], yy),
            "indep": crps(ind[c], yy),
            "recent": crps(w.rec[c][eval_idx], yy),
        }
        if w.direct is not None and w.qcal is not None:
            scs["direct"], scs["recent_cal"] = (
                crps(w.direct[c][eval_idx], yy),
                crps(w.qcal[c][eval_idx], yy),
            )
        r: dict[str, Any] = {
            "n": int(len(eval_idx)),
            "crps": {k: float(v.mean()) for k, v in scs.items()},
        }
        for other_arm in scs:
            if other_arm != "copula":
                r[f"copula_vs_{other_arm}"] = contrast(
                    scs["copula"], scs[other_arm], w.gid[eval_idx], w.day[eval_idx]
                )
        use_copula[c] = (
            True  # ONE joint for every combo (Devin, 2026-10-14); copula_vs_indep is reported, not a switch
        )
        s_arm = scs["copula"] if use_copula[c] else scs["indep"]
        r["shadow_arm"] = "copula" if use_copula[c] else "indep"
        r["shadow_vs_recent"] = contrast(s_arm, scs["recent"], w.gid[eval_idx], w.day[eval_idx])
        r["cal_copula"], r["cal_indep"] = cal(g[c], yy), cal(ind[c], yy)
        c23[c] = r
        print(
            c,
            r["shadow_arm"],
            json.dumps({k: r[k] for k in ("crps", "copula_vs_indep")}, default=float),
            flush=True,
        )
    res["combos_2023_eval_half"], res["use_copula"] = c23, use_copula
    # floors and required rows, 2023
    fl: dict[str, Any] = {}
    for t in ("blk", "stl", *COMBO_NAMES):
        yy_all = w.y[t][sel]
        rec_crps = float(crps(w.rec[t][sel], yy_all).mean())
        floor = max(FLOOR_MIN, FLOOR_REL * rec_crps)
        ct = mar[t]["model_vs_recent"] if t in ("blk", "stl") else c23[t]["shadow_vs_recent"]
        eff = max(-ct["delta"], 0.0)
        eff_used = max(floor, 0.5 * eff)
        fl[t] = {
            "recent_crps_2023": rec_crps,
            "floor": floor,
            "delta_2023": ct["delta"],
            "sd_row": ct["sd_row"],
            "deff": ct["deff_game"],
            "effect_used_for_power": eff_used,
            "n_required": n_required(ct["sd_row"], max(ct["deff_game"], 1.0), eff_used),
        }
    res["floors_and_n_required"] = fl
    first = int(w.day[sel].min())
    res["gate_counts_2023"] = {
        "rows_regular": int(sel.sum()),
        "rows_first_12_weeks": int((sel & (w.day < first + 84)).sum()),
        "games_first_12_weeks": int(len(np.unique(w.gid[sel & (w.day < first + 84)]))),
        "first_day_epoch": first,
    }
    np.savez(
        out / "R_chosen.npz",
        method=np.array(best_method),
        scheme=np.array(best),
        pooled=pooled_R,
        groups=np.array(sorted(mats_final)),
        use_copula=np.array([use_copula[c] for c in COMBO_NAMES]),
        choice=np.array([choice[s] for s in STATS]),
        head=np.array([head["blk"], head["stl"]]),
        **{f"g{k}": v for k, v in mats_final.items()},
    )
    res["seconds"] = time.time() - t0
    (out / "select.json").write_text(json.dumps(res, indent=1, default=float))
    print("select phase done", flush=True)


def phase_report(w: World, out: Path) -> None:
    t0 = time.time()
    z = np.load(out / "R_chosen.npz")
    best = str(z["scheme"])
    mats = {int(k): z[f"g{int(k)}"] for k in z["groups"]}
    pooled_R = z["pooled"]
    use_copula = {c: bool(v) for c, v in zip(COMBO_NAMES, z["use_copula"], strict=True)}
    w.set_head({"blk": str(z["head"][0]), "stl": str(z["head"][1])})
    w.set_choice({s: str(v) for s, v in zip(STATS, z["choice"], strict=True)})
    sel_json = json.loads((out / "select.json").read_text())
    m = w.reg & (w.season == REPORT)
    rep = np.flatnonzero(m)
    res: dict[str, Any] = {
        "phase": "report",
        "season": REPORT,
        "scheme": best,
        "method": str(z["method"]),
        "use_copula": use_copula,
        "marginal_choice": w.choice,
        "marginals_2024": marginals(w, m),
        "equivalence_prod_oof": equivalence(w),
    }
    grp = w.schemes()[best][rep]
    t1 = time.time()
    cop = grids_by_group(w, rep, grp, mats, pooled_R)
    print(f"COPULA grids {time.time() - t1:.0f}s for {len(rep)} rows", flush=True)
    t2 = time.time()
    ind = C.joint_grids(w.sub(rep), np.eye(len(STATS)), w.base)
    print(f"INDEP grids {time.time() - t2:.0f}s", flush=True)
    res["inference_seconds_per_1000_rows"] = {
        "copula": (t2 - t1) / len(rep) * 1000,
        "indep": (time.time() - t2) / len(rep) * 1000,
    }
    np.savez_compressed(
        out / "grids_2024.npz",
        rep=rep,
        **{f"cop_{c}": cop[c] for c in COMBO_NAMES},
        **{f"ind_{c}": ind[c] for c in COMBO_NAMES},
    )
    gid_r, day_r = w.gid[rep], w.day[rep]
    fl = sel_json["floors_and_n_required"]
    res["combos_2024"] = {}
    for c in COMBO_NAMES:
        yy = w.y[c][rep]
        shadow = cop[c] if use_copula[c] else ind[c]
        scs = {
            "shadow": crps(shadow, yy),
            "copula": crps(cop[c], yy),
            "indep": crps(ind[c], yy),
            "recent": crps(w.rec[c][rep], yy),
        }
        if w.direct is not None and w.qcal is not None:
            scs["direct"], scs["recent_cal"] = crps(w.direct[c][rep], yy), crps(w.qcal[c][rep], yy)
        r: dict[str, Any] = {
            "n": int(len(rep)),
            "games": int(len(np.unique(gid_r))),
            "mean_y": float(yy.mean()),
            "p0": float((yy == 0).mean()),
            "shadow_arm": "copula" if use_copula[c] else "indep",
            "floor": fl[c]["floor"],
            "crps": {k: float(v.mean()) for k, v in scs.items()},
        }
        for other_arm in ("recent", "indep", "direct", "recent_cal"):
            if other_arm in scs:
                r[f"shadow_vs_{other_arm}"] = contrast(scs["shadow"], scs[other_arm], gid_r, day_r)
        r["copula_vs_indep"] = contrast(scs["copula"], scs["indep"], gid_r, day_r)
        r["cal_shadow"], r["cal_indep"], r["cal_recent"] = (
            cal(shadow, yy),
            cal(ind[c], yy),
            cal(w.rec[c][rep], yy),
        )
        if w.direct is not None:
            r["cal_direct"] = cal(w.direct[c][rep], yy)
        sl = {"starter": w.starter[rep] == 1, "bench": w.starter[rep] == 0}
        r["slices"] = {
            nm: {
                "n": int(mk.sum()),
                "shadow_vs_recent": float((scs["shadow"] - scs["recent"])[mk].mean()),
                "shadow_vs_indep": float((scs["shadow"] - scs["indep"])[mk].mean()),
            }
            for nm, mk in sl.items()
        }
        res["combos_2024"][c] = r
        print(
            c,
            r["shadow_arm"],
            json.dumps(
                {k: r[k] for k in ("crps", "shadow_vs_recent", "copula_vs_indep")}, default=float
            ),
            flush=True,
        )
    # sampler check: INDEP through the sampler vs the exact convolution, first 1500 report rows
    ex = rep[:1500]
    chk: dict[str, Any] = {}
    for c, parts in C.COMBOS.items():
        cdf = np.cumsum(C.convolve_exact(w.sub(ex), parts), axis=1)
        exact = (cdf[:, :, None] < (C.CRPS_TAUS - 1e-12)[None, None, :]).sum(axis=1)
        a, b = crps(exact, w.y[c][ex]), crps(ind[c][:1500], w.y[c][ex])
        chk[c] = {
            "crps_exact_convolution": float(a.mean()),
            "crps_sampled_indep": float(b.mean()),
            "diff": float((b - a).mean()),
        }
    res["sampler_check"] = chk
    first = int(w.day[m].min())
    res["gate_counts_2024"] = {
        "rows_regular": int(m.sum()),
        "games": int(len(np.unique(w.gid[m]))),
        "rows_first_12_weeks": int((m & (w.day < first + 84)).sum()),
        "games_first_12_weeks": int(len(np.unique(w.gid[m & (w.day < first + 84)]))),
        "first_day_epoch": first,
    }
    res["seconds"] = time.time() - t0
    (out / "report.json").write_text(json.dumps(res, indent=1, default=float))
    print("report phase done", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("select", "report"), required=True)
    ap.add_argument("--a", required=True)
    ap.add_argument("--a2", default="none")
    ap.add_argument("--a5", default="none", help="stage A output of fg3m")
    ap.add_argument(
        "--a4", default="none", help="stage A output of pts with the lower-tail mixture"
    )
    ap.add_argument("--a3", default="none", help="stage A output of the NB head for blk, stl")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    import contextlib
    import os

    from research.eval import workload_eval as WE

    lock = (
        contextlib.nullcontext()
        if os.environ.get("COMBO_NO_LOCK")
        else WE.WorkloadLock(Path("/Users/devin/Downloads/nba-prediction/data/ops/heavy.lock"))
    )
    with lock:
        w = World(args)
        print(f"world built: {w.keys.height} oof rows ({time.time() - t0:.0f}s)", flush=True)
        (phase_select if args.phase == "select" else phase_report)(w, out)


if __name__ == "__main__":
    main()
