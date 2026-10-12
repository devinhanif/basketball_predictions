# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""DESCRIPTIVE EDA (no claim, no rule): is the forecast spread calibrated by slice, and would an as-of scale multiplier help? (Q7.3, Devin 2026-10-12).

Seasons 2023/2024 only; 2025 never loaded; read-only (stored forecasts + one scratch parquet of as-of frame columns).
Stages:  dump  -> build the frame columns the slices need (read-only DB, ~5 s)   [heavy.lock held by the caller]
         cache -> per-stat CRPS tables over a scale grid for A0 and FULL, PIT, quantiles (checkpointed npz)
         report-> slices, coverage (game-clustered CI), 2023-fit / 2024-apply shrunk multipliers, markdown + results.json
Inflation (declared before any number): symmetric S: q' = med + k (q - med); lower-only L: q' = med - kL (med - q) for q < med.
k on a 0.05 grid in [0.70, 1.60]; per-slice k fitted on 2023 (argmin mean CRPS), shrunk k_s = 1 + n/(n+500) (k - 1), snapped to the grid, applied to 2024.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import CRPS_TAUS, crps_from_quantiles
from nba.eval.lower_tail_eval import arm_grid, load_components, randomized_pit
from nba.props.context_residual import PROP_STATS, to_integer_support
from nba.props.lower_tail import short_flag

ROOT = Path(__file__).resolve().parents[2]
LT = ROOT / "reports/lower_tail"
MV2 = ROOT / "reports/minutes_v2"
RRF = ROOT / "data/ops/recent_role_full_cache"
SCR = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data/ops/minutes_spread_eda_cache"
KGRID = np.round(np.arange(0.70, 1.601, 0.05), 2)
K1 = int(np.argmin(np.abs(KGRID - 1.0)))
N0 = 500.0
MED = 99
I10, I20, I80, I90 = (int(np.argmin(np.abs(CRPS_TAUS - t))) for t in (0.1, 0.2, 0.8, 0.9))
TAUS = (0.10, 0.20, 0.80, 0.90)
B = 1000
RK: dict[tuple[str, str, str, str], np.ndarray] = {}
SEASONS = (2023, 2024)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def scale(grid: np.ndarray, k: float, mode: str, lo: float = 0.0, hi: float = np.inf) -> np.ndarray:
    med = grid[:, MED : MED + 1]
    if mode == "S":
        g = med + k * (grid - med)
    else:
        g = np.where(grid < med, med - k * (med - grid), grid)
    return np.clip(g, lo, hi)


# --------------------------------------------------------------------------- stage dump
def dump(out: Path) -> None:
    from research.eval import pi_recent_role_eval as PR

    fr, _, _ = PR.build_frame()
    keep = [
        "game_id",
        "player_id",
        "season",
        "n_prior",
        "min10",
        "starter10",
        "is_home",
        "b2b",
        "team_game_no",
        "exp_margin",
        "wl_min_last",
    ]
    fr.select(keep).write_parquet(out)
    log(f"dumped {fr.height} rows")


# --------------------------------------------------------------------------- stage cache
def trunc_solve(
    q10: np.ndarray, q90: np.ndarray, upper: float = 48.0
) -> tuple[np.ndarray, np.ndarray]:
    """Recover (mu, sigma) of production's truncated normal from its stored q10/q90 (vectorised Newton, numeric Jacobian)."""
    from scipy import stats

    t10, t90 = CRPS_TAUS[I10], CRPS_TAUS[I90]

    def ppf(mu: np.ndarray, sg: np.ndarray, t: float) -> np.ndarray:
        sg = np.maximum(sg, 1e-3)
        return stats.truncnorm.ppf(t, (0 - mu) / sg, (upper - mu) / sg, loc=mu, scale=sg)

    mu = (q10 + q90) / 2
    sg = np.maximum((q90 - q10) / 2.563, 0.5)
    for _ in range(25):
        f1, f2 = ppf(mu, sg, t10) - q10, ppf(mu, sg, t90) - q90
        h = 1e-4
        a1 = (ppf(mu + h, sg, t10) - ppf(mu, sg, t10)) / h
        b1 = (ppf(mu, sg + h, t10) - ppf(mu, sg, t10)) / h
        a2 = (ppf(mu + h, sg, t90) - ppf(mu, sg, t90)) / h
        b2 = (ppf(mu, sg + h, t90) - ppf(mu, sg, t90)) / h
        det = a1 * b2 - a2 * b1
        det = np.where(np.abs(det) < 1e-9, 1e-9, det)
        dmu, dsg = (b2 * f1 - b1 * f2) / det, (a1 * f2 - a2 * f1) / det
        mu = mu - np.clip(dmu, -5, 5)
        sg = np.maximum(sg - np.clip(dsg, -5, 5), 0.05)
    return mu, sg


def crps_table(grid: np.ndarray, y: np.ndarray, integer: bool, hi: float) -> dict[str, np.ndarray]:
    out = {}
    for mode in ("S", "L"):
        tab = np.empty((len(y), len(KGRID)), dtype=np.float32)
        for j, k in enumerate(KGRID):
            g = scale(grid, float(k), mode, 0.0, hi)
            if integer:
                g = to_integer_support(g)
            tab[:, j] = crps_from_quantiles(g, y)
        out[mode] = tab
    return out


def cache() -> None:
    SCR.mkdir(parents=True, exist_ok=True)
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    piR = np.load(RRF / "pi_R.npz")["pi"]
    for stat in PROP_STATS:
        path = SCR / f"props_{stat}.npz"
        if path.exists():
            log(f"{stat} cached")
            continue
        c0 = load_components(stat, LT)
        cR = {k: v for k, v in np.load(RRF / f"heads_{stat}_R.npz", allow_pickle=True).items()}
        for k in ("row_gid", "row_pid", "row_y", "row_blk"):
            assert np.array_equal(
                np.asarray(c0[k]).astype(str), np.asarray(cR[k]).astype(str)
            ) or np.array_equal(c0[k], cR[k]), k
        cR["row_gid"] = c0["row_gid"]
        y = c0["row_y"]
        res: dict[str, np.ndarray] = {
            "gid": c0["row_gid"].astype(str),
            "pid": c0["row_pid"],
            "season": c0["row_season"],
            "y": y,
            "minutes": c0["row_minutes"],
            "min10": c0["row_min10"],
            "starter10": c0["row_starter10"],
            "n_prior": c0["row_n_prior"],
            "b2b": c0["row_b2b"],
        }
        for arm, comp, pi in (("A0", c0, pic["row_pi"]), ("FULL", cR, piR)):
            pp = dict(pic)
            pp["row_pi"] = pi
            g = arm_grid("mixture", comp, pp, stat)
            qi = to_integer_support(g)
            res[f"{arm}_pit"] = randomized_pit(qi, y)
            res[f"{arm}_q10"], res[f"{arm}_q50"], res[f"{arm}_q90"] = (
                qi[:, I10],
                qi[:, MED],
                qi[:, I90],
            )
            for mode, tab in crps_table(g, y, True, np.inf).items():
                res[f"{arm}_crps_{mode}"] = tab
            log(
                f"{stat} {arm}: int CRPS 2023 {res[f'{arm}_crps_S'][c0['row_season'] == 2023, K1].mean():.6f} 2024 {res[f'{arm}_crps_S'][c0['row_season'] == 2024, K1].mean():.6f}"
            )
            del g, qi
        np.savez_compressed(path, **res)
    # minutes
    path = SCR / "minutes.npz"
    if not path.exists():
        z = dict(np.load(MV2 / "minutes_scores.npz", allow_pickle=True))
        mu, sg = trunc_solve(z["P_q10"], z["P_q90"])
        from scipy import stats

        a, b_ = (0 - mu) / sg, (48.0 - mu) / sg
        g = stats.truncnorm.ppf(
            CRPS_TAUS[None, :], a[:, None], b_[:, None], loc=mu[:, None], scale=sg[:, None]
        )
        y = z["row_y"]
        cr = crps_from_quantiles(g, y)
        diff = float(np.abs(cr - z["P_crps"]).max())
        log(
            f"minutes P reconstruction: max |CRPS - stored| = {diff:.2e}, mean diff {np.abs(cr - z['P_crps']).mean():.2e}; q10 err {np.abs(g[:, I10] - z['P_q10']).max():.2e}"
        )
        res = {
            "gid": z["row_gid"].astype(str),
            "pid": z["row_pid"],
            "season": z["row_season"],
            "y": y,
            "recon_max_diff": np.array(diff),
            "min10": z["row_min10"],
            "starter10": z["row_starter10"],
            "n_prior": z["row_n_prior"],
            "b2b": z["row_b2b"],
            "team_game_no": z["row_team_game_no"],
        }
        res["P_pit"] = z["P_pit"]
        res["P_q10"], res["P_q50"], res["P_q90"] = g[:, I10], g[:, MED], g[:, I90]
        for mode, tab in crps_table(g, y, False, 48.0).items():
            res[f"P_crps_{mode}"] = tab
        for arm in ("M2", "R"):
            res[f"{arm}_pit"], res[f"{arm}_q10"], res[f"{arm}_q90"], res[f"{arm}_crps"] = (
                z[f"{arm}_pit"],
                z[f"{arm}_q10"],
                z[f"{arm}_q90"],
                z[f"{arm}_crps"],
            )
        np.savez_compressed(path, **res)


# --------------------------------------------------------------------------- stage report
class Boot:
    """Game-clustered bootstrap with a common weight matrix (multinomial over games)."""

    def __init__(self, gid: np.ndarray, seed: int = 0) -> None:
        self.codes = np.unique(gid, return_inverse=True)[1]
        self.g = int(self.codes.max()) + 1
        rng = np.random.default_rng(seed)
        self.w = rng.multinomial(self.g, np.full(self.g, 1.0 / self.g), size=B).astype(np.float32)

    def ratio(
        self, num: np.ndarray, mask: np.ndarray, den: np.ndarray | None = None
    ) -> tuple[float, float, float, float]:
        """mean of ``num`` over ``mask`` with a 95% game-clustered CI and the bootstrap SD."""
        n = np.bincount(self.codes[mask], weights=num[mask].astype(float), minlength=self.g)
        c = np.bincount(
            self.codes[mask],
            weights=(np.ones(mask.sum()) if den is None else den[mask].astype(float)),
            minlength=self.g,
        )
        pt = n.sum() / max(c.sum(), 1e-12)
        bn, bc = self.w @ n, self.w @ c
        ok = bc > 0
        bs = bn[ok] / bc[ok]
        return (
            float(pt),
            float(np.percentile(bs, 2.5)),
            float(np.percentile(bs, 97.5)),
            float(bs.std()),
        )


def best_k(tab: np.ndarray, mask: np.ndarray) -> float:
    return float(KGRID[int(np.argmin(tab[mask].mean(axis=0)))])


def shrunk_idx(k: float, n: int) -> int:
    ks = 1.0 + n / (n + N0) * (k - 1.0)
    return int(np.argmin(np.abs(KGRID - ks)))


def load_frame() -> pl.DataFrame:
    return (
        pl.read_parquet(SCR.parent / "frame_cols.parquet")
        .with_columns(pl.col("player_id").cast(pl.Int64))
        .select("game_id", "player_id", "exp_margin", "is_home", "wl_min_last", "team_game_no")
    )


def dims_of(
    d: dict[str, np.ndarray], fr: pl.DataFrame, season: np.ndarray
) -> dict[str, tuple[list[str], np.ndarray]]:
    j = pl.DataFrame({"game_id": d["gid"], "player_id": d["pid"].astype(np.int64)}).join(
        fr, on=["game_id", "player_id"], how="left"
    )
    em, home, wl, tg = (
        j[c].cast(pl.Float64).to_numpy()
        for c in ("exp_margin", "is_home", "wl_min_last", "team_game_no")
    )
    m10 = d["min10"]
    out: dict[str, tuple[list[str], np.ndarray]] = {}
    out["prior10_min"] = (
        ["<15", "15-24", "24-30", "30-34", "34+"],
        np.digitize(m10, [15, 24, 30, 34]),
    )
    cuts = np.nanquantile(em[season == 2023], [1 / 3, 2 / 3])
    lv = np.where(np.isnan(em), -1, np.digitize(em, cuts))
    out["elo_tercile(margin)"] = (["underdog T1", "middle T2", "favourite T3"], lv)
    tgn = d.get("team_game_no", tg)
    out["games_into_season"] = (
        ["first 15", "rest"],
        np.where(np.isnan(tgn), -1, (tgn >= 15).astype(int)),
    )
    out["role(as-of starter10)"] = (["bench", "starter"], (d["starter10"] >= 0.5).astype(int))
    out["back_to_back"] = (["no", "yes"], (d["b2b"] > 0.5).astype(int))
    out["venue"] = (["away", "home"], np.where(np.isnan(home), -1, home.astype(int)))
    out["wl_min_last"] = (
        ["<12", "12-24", "24-32", "32+", "missing"],
        np.where(np.isnan(wl), 4, np.digitize(wl, [12, 24, 32])),
    )
    return out


def analyse_source(
    name: str,
    d: dict[str, np.ndarray],
    arm: str,
    fr: pl.DataFrame,
    bt: Boot,
    lo_hi: tuple[float, float],
    realised_min: np.ndarray | None,
    with_crps: bool = True,
) -> dict[str, Any]:
    season, y = d["season"], d["y"]
    pit = d[f"{arm}_pit"]
    q10, q50, q90 = (
        d[f"{arm}_q10"].astype(float),
        d[f"{arm}_q50"].astype(float),
        d[f"{arm}_q90"].astype(float),
    )
    err2 = (y - q50) ** 2
    wid = q90 - q10
    s23, s24 = season == 2023, season == 2024
    dims = dims_of(d, fr, season)
    short = None if realised_min is None else realised_min
    crs = {m: d[f"{arm}_crps_{m}"] for m in ("S", "L")} if with_crps else {}
    gk = {m: float(KGRID[int(np.argmin(crs[m][s23].mean(axis=0)))]) for m in crs}
    allmask = np.ones(len(y), dtype=bool)
    out: dict[str, Any] = {
        "source": name,
        "arm": arm,
        "n": int(len(y)),
        "global_k_2023": gk,
        "dims": {},
    }
    ind = {t: (pit < t).astype(float) for t in TAUS}
    central = ((pit >= 0.1) & (pit < 0.9)).astype(float)
    for dn, (labels, lv) in dims.items():
        levels = []
        rowk = {m: np.full(len(y), K1) for m in crs}
        rowrev = {m: np.full(len(y), K1) for m in crs}
        for li, lab in enumerate(labels):
            mk = lv == li
            if mk.sum() < 50:
                continue
            rec: dict[str, Any] = {
                "level": lab,
                "n": int(mk.sum()),
                "games": int(len(np.unique(d["gid"][mk]))),
                "n_2023": int((mk & s23).sum()),
                "n_2024": int((mk & s24).sum()),
            }
            for t in TAUS:
                p, a, b, _ = bt.ratio(ind[t], mk)
                rec[f"cov{int(t * 100)}"] = [p, a, b]
            p, a, b, _ = bt.ratio(central, mk)
            rec["central80"] = [p, a, b]
            rec["central80_2023"] = float(central[mk & s23].mean()) if (mk & s23).any() else None
            rec["central80_2024"] = float(central[mk & s24].mean()) if (mk & s24).any() else None
            rec["verdict"] = "too narrow" if b < 0.80 else ("too wide" if a > 0.80 else "ok")
            lo_p = rec["cov10"]
            hi_p = [1 - rec["cov90"][0], 1 - rec["cov90"][2], 1 - rec["cov90"][1]]
            rec["lower_miss"] = lo_p  # P(PIT<0.10): nominal 0.10
            rec["upper_miss"] = hi_p  # P(PIT>=0.90): nominal 0.10
            rec["mean_width80"] = float(wid[mk].mean())
            rec["rmse"] = float(np.sqrt(err2[mk].mean()))
            rec["spread_ratio"] = float(np.sqrt(err2[mk].mean()) / (wid[mk].mean() / 2.5631))
            if short is not None:
                sh = short[mk]
                mm = (1 - central[mk]).astype(bool)
                rec["short_rate"] = float(sh.mean())
                rec["miss_share_from_short"] = float((mm & sh).sum() / max(mm.sum(), 1))
                rec["central80_excl_short"] = float(central[mk][~sh].mean())
                rec["lower_miss_excl_short"] = float((pit[mk][~sh] < 0.1).mean())
                rec["upper_miss_excl_short"] = float((pit[mk][~sh] >= 0.9).mean())
            for m in crs:
                tab = crs[m]
                k23 = best_k(tab, mk & s23) if (mk & s23).sum() >= 30 else 1.0
                k24 = best_k(tab, mk & s24) if (mk & s24).sum() >= 30 else 1.0
                idx = shrunk_idx(k23, int((mk & s23).sum()))
                rowk[m][mk] = idx
                rowrev[m][mk] = shrunk_idx(k24, int((mk & s24).sum()))
                dl = tab[:, idx].astype(float) - tab[:, K1].astype(float)
                p, a, b, _ = bt.ratio(dl, mk & s24)
                rec[f"k23_{m}"], rec[f"k24_{m}"], rec[f"kshrunk_{m}"] = k23, k24, float(KGRID[idx])
                rec[f"d2024_{m}"] = [p, a, b]
            levels.append(rec)
        dd: dict[str, Any] = {"levels": levels}
        for m in crs:
            tab = crs[m]
            dl = tab[np.arange(len(y)), rowk[m]].astype(float) - tab[:, K1].astype(float)
            p, a, b, sd = bt.ratio(dl, s24)
            dd[f"overall_d2024_{m}"] = {"delta": p, "lo": a, "hi": b, "sd": sd}
            RK[(name, arm, dn, m)] = rowk[m]
            dr = tab[np.arange(len(y)), rowrev[m]].astype(float) - tab[:, K1].astype(float)
            p, a, b, sd = bt.ratio(dr, s23)
            dd[f"overall_rev2023_{m}"] = {"delta": p, "lo": a, "hi": b, "sd": sd}
        out["dims"][dn] = dd
    for m in crs:
        tab = crs[m]
        gi = int(np.argmin(np.abs(KGRID - gk[m])))
        dl = tab[:, gi].astype(float) - tab[:, K1].astype(float)
        p, a, b, sd = bt.ratio(dl, s24)
        out[f"global_d2024_{m}"] = {"k": gk[m], "delta": p, "lo": a, "hi": b, "sd": sd}
    p, a, b, _ = bt.ratio(central, allmask)
    out["overall"] = {f"cov{int(t * 100)}": bt.ratio(ind[t], allmask)[:3] for t in TAUS}
    out["overall"]["central80"] = [p, a, b]
    return out


def walk(srcs: dict[str, dict[str, np.ndarray]], fr: pl.DataFrame) -> dict[str, Any]:
    """As-of version of the lower-tail multiplier: for each month block the k of each group is fitted on EARLIER blocks only (test-season rows), shrunk by n/(n+500), applied to that block. Descriptive."""
    out: dict[str, Any] = {}
    for stat in PROP_STATS:
        d = srcs[stat]
        blk = load_components(stat, LT)["row_blk"]
        assert len(blk) == len(d["y"])
        bt = Boot(d["gid"])
        dims = dims_of(d, fr, d["season"])
        out[stat] = {}
        for dn in ("role(as-of starter10)", "prior10_min"):
            _, lv = dims[dn]
            for arm in ("A0", "FULL"):
                tab = d[f"{arm}_crps_L"]
                rk = np.full(len(blk), K1)
                for b in np.unique(blk):
                    cur = blk == b
                    past = blk < b
                    for g in np.unique(lv[cur]):
                        pm = past & (lv == g)
                        if pm.sum() < 30:
                            continue
                        rk[cur & (lv == g)] = shrunk_idx(best_k(tab, pm), int(pm.sum()))
                dl = tab[np.arange(len(blk)), rk].astype(float) - tab[:, K1].astype(float)
                row: dict[str, Any] = {}
                for sea in SEASONS:
                    p, a, b_, sd = bt.ratio(dl, d["season"] == sea)
                    row[str(sea)] = {
                        "delta": p,
                        "lo": a,
                        "hi": b_,
                        "sd": sd,
                        "n": int((d["season"] == sea).sum()),
                    }
                kk = {
                    str(g): float(np.mean(KGRID[rk[(lv == g) & (d["season"] == 2024)]]))
                    for g in np.unique(lv)
                    if g >= 0
                }
                row["mean_k_2024_by_level"] = kk
                out[stat][f"{dn}|{arm}"] = row
    return out


def report() -> None:
    fr = load_frame()
    res: dict[str, Any] = {
        "note": "DESCRIPTIVE EDA, seasons 2023/2024 only, no claim",
        "kgrid": KGRID.tolist(),
        "n0_shrink": N0,
        "boot": B,
    }
    srcs: dict[str, dict[str, np.ndarray]] = {}
    for stat in PROP_STATS:
        z = {k: v for k, v in np.load(SCR / f"props_{stat}.npz", allow_pickle=True).items()}
        z["gid"] = z["gid"].astype(str)
        srcs[stat] = z
    zm = {k: v for k, v in np.load(SCR / "minutes.npz", allow_pickle=True).items()}
    zm["gid"] = zm["gid"].astype(str)
    srcs["minutes"] = zm
    res["minutes_P_recon_max_abs_crps_diff"] = float(zm["recon_max_diff"])
    for name, d in srcs.items():
        bt = Boot(d["gid"])
        arms = ("A0", "FULL") if name != "minutes" else ("P",)
        rm = None
        if name != "minutes":
            rm = short_flag(d["minutes"], d["min10"]).astype(bool)
        else:
            # minutes: the "short night" is the outcome itself
            rm = short_flag(d["y"], d["min10"]).astype(bool)
        res[name] = {}
        for arm in arms:
            log(f"{name} {arm}")
            res[name][arm] = analyse_source(name, d, arm, fr, bt, (0.0, np.inf), rm)
        if name == "minutes":
            for arm in ("M2", "R"):
                dd = dict(d)
                dd[f"{arm}_q50"] = (d[f"{arm}_q10"] + d[f"{arm}_q90"]) / 2
                res[name][arm] = analyse_source(
                    name, dd, arm, fr, bt, (0.0, np.inf), rm, with_crps=False
                )
    # overlap with RECENT_ROLE_FULL: FULL + its own 2023-fit slice multiplier vs A0 at k=1, and vs FULL at k=1 (2024, game-clustered)
    res["combined"] = {}
    for stat in PROP_STATS:
        d = srcs[stat]
        bt = Boot(d["gid"])
        s24 = d["season"] == 2024
        n_ = np.arange(len(d["y"]))
        res["combined"][stat] = {}
        for dn in res[stat]["FULL"]["dims"]:
            for m in ("S", "L"):
                idx = RK[(stat, "FULL", dn, m)]
                full_k = d[f"FULL_crps_{m}"][n_, idx].astype(float)
                full_1 = d["FULL_crps_S"][:, K1].astype(float)
                a0_1 = d["A0_crps_S"][:, K1].astype(float)
                row: dict[str, Any] = {}
                for lab, dl in (
                    ("FULLk_minus_A0", full_k - a0_1),
                    ("FULLk_minus_FULL", full_k - full_1),
                    ("FULL_minus_A0", full_1 - a0_1),
                ):
                    p, a, b, sd = bt.ratio(dl, s24)
                    row[lab] = {"delta": p, "lo": a, "hi": b, "sd": sd}
                res["combined"][stat][f"{dn}|{m}"] = row
    res["walk"] = walk(srcs, fr)
    (SCR.parent / "results_raw.json").write_text(json.dumps(res, default=float))
    Path(sys.argv[3]).write_text(json.dumps(res, default=float, indent=1))
    log("report json written")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dump":
        dump(Path(sys.argv[2]))
    elif cmd == "cache":
        cache()
    elif cmd == "report":
        report()
