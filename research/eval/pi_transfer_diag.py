# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""Q1 descriptive diagnostic (no claim): why does a better short-minutes classifier pi move integer pts CRPS so little?

Reads only stored components (reports/lower_tail) and the PI_RECENT_ROLE pi caches. Seasons 2023/2024 only (asserted). Read-only.
    .venv/bin/python -W ignore -m research.eval.pi_transfer_diag
Writes reports/pi_transfer_diagnostic_2026-10-12.results.json (results.json) ; the .md is hand-written from it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from nba.eval.lower_tail_eval import load_components
from nba.props.context_residual import CRPS_TAUS, to_integer_support
from nba.props.lower_tail import PI_CLIP, SHORT_RATIO, mixture_quantiles, short_flag

ROOT = Path(__file__).resolve().parents[2]
LT = ROOT / "reports/lower_tail"
CACHE = ROOT / "data/ops/pi_recent_role_cache"
OUT = ROOT / "reports/pi_transfer_diagnostic_2026-10-12.results.json"
EPS = 1e-6
TAUS = CRPS_TAUS


def crps_q(qi: Any, y: Any) -> Any:
    d = y[:, None] - qi
    pin = np.where(d >= 0, TAUS[None, :] * d, (TAUS[None, :] - 1.0) * d)
    return 2.0 * pin.mean(axis=1)


def grid(comp: Any, wq_all: Any, pi: Any, stat_wq: Any = None, wq_rows: Any = None) -> Any:
    """Integer-support mixture quantiles with row pi; optional per-row short component quantiles (wq_rows [n,199]) replacing w_q."""
    n = len(comp["row_y"])
    out = np.empty((n, len(TAUS)), dtype=np.int16)
    for i, b in enumerate(np.unique(comp["row_blk"])):
        r = np.where(comp["row_blk"] == b)[0]
        cm = comp["cal_blk"] == b
        shortc = short_flag(comp["cal_minutes"][cm], comp["cal_min10"][cm])
        z_norm = comp["cal_z"][cm][~shortc]
        w = wq_all[i]
        if wq_rows is None:
            q = mixture_quantiles(
                comp["row_c"][r],
                comp["row_s"][r],
                comp["row_m"][r],
                pi[r],
                z_norm,
                None if np.isnan(w).any() else w,
                TAUS,
            )
        else:
            q = mix_rows(comp, r, pi[r], z_norm, wq_rows[r])
        out[r] = to_integer_support(q).astype(np.int16)
    return out


def mix_rows(comp: Any, r: Any, pi: Any, z_norm: Any, wq: Any) -> Any:
    k = wq.shape[1]
    g = (np.arange(k) + 0.5) / k
    zq = np.quantile(z_norm, g)
    qn = np.clip(comp["row_c"][r][:, None] + comp["row_s"][r][:, None] * zq[None, :], 0, None)
    qs = np.clip(np.maximum(comp["row_m"][r], 0.5)[:, None] * wq, 0, None)
    vals = np.concatenate([qn, qs], 1)
    wts = np.concatenate(
        [np.repeat(((1 - pi) / k)[:, None], k, 1), np.repeat((pi / k)[:, None], k, 1)], 1
    )
    o = np.argsort(vals, 1, kind="stable")
    vals = np.take_along_axis(vals, o, 1)
    wts = np.take_along_axis(wts, o, 1)
    cum = np.cumsum(wts, 1) - 0.5 * wts
    out = np.empty((len(r), len(TAUS)))
    for i in range(len(r)):
        out[i] = np.interp(TAUS, cum[i], vals[i])
    return np.maximum.accumulate(out, 1)


def boot(delta: Any, gid: Any, n_boot: int = 1000, seed: int = 0) -> list[float]:
    u, inv = np.unique(gid, return_inverse=True)
    s = np.bincount(inv, weights=delta, minlength=len(u))
    c = np.bincount(inv, minlength=len(u)).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(u), (n_boot, len(u)))
    est = s[idx].sum(1) / c[idx].sum(1)
    return [float(delta.mean()), float(np.quantile(est, 0.025)), float(np.quantile(est, 0.975))]


def ll(p: Any, y: Any) -> Any:
    p = np.clip(p, EPS, 1 - EPS)
    return -np.where(y > 0.5, np.log(p), np.log(1 - p))


def auc(p: Any, y: Any) -> float:
    from scipy.stats import rankdata

    r = rankdata(p)
    n1 = y.sum()
    return float((r[y > 0.5].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))


def per_stat(stat: Any, pic: Any, pis: Any, with_extras: Any) -> Any:
    comp = load_components(stat, LT)
    assert int(comp["row_season"].max()) <= 2024
    y, gid, season = comp["row_y"], comp["row_gid"].astype(str), comp["row_season"]
    short = short_flag(comp["row_minutes"], comp["row_min10"])
    wq = pic[f"wq_{stat}"]
    res = {"n": int(len(y)), "short_rate": float(short.mean())}
    sh = short.astype(float)
    qa = {}
    cr = {}
    for name, p in pis.items():
        qa[name] = grid(comp, wq, p)
        cr[name] = crps_q(qa[name].astype(float), y)
    res["mean_crps"] = {k: float(v.mean()) for k, v in cr.items()}
    res["delta_vs_A0"] = {}
    for k in cr:
        if k == "A0":
            continue
        res["delta_vs_A0"][k] = {
            "all": boot(cr[k] - cr["A0"], gid),
            **{
                str(s): boot((cr[k] - cr["A0"])[season == s], gid[season == s])
                for s in (2023, 2024)
            },
        }
    # (2) oracle gain by flag
    d = cr["ORACLE"] - cr["A0"]
    n = len(y)
    res["oracle_split"] = {
        "gain_total": float(d.mean()),
        "from_short_rows": float(d[short].sum() / n),
        "from_nonshort_rows": float(d[~short].sum() / n),
        "mean_gain_per_short_row": float(d[short].mean()),
        "mean_gain_per_nonshort_row": float(d[~short].mean()),
        "A0_crps_short_rows": float(cr["A0"][short].mean()),
        "A0_crps_nonshort_rows": float(cr["A0"][~short].mean()),
        "ORACLE_crps_short_rows": float(cr["ORACLE"][short].mean()),
        "ORACLE_crps_nonshort_rows": float(cr["ORACLE"][~short].mean()),
    }
    cand_d = cr["CAND"] - cr["A0"]
    res["cand_split"] = {
        "gain_total": float(cand_d.mean()),
        "from_short_rows": float(cand_d[short].sum() / n),
        "from_nonshort_rows": float(cand_d[~short].sum() / n),
    }
    if not with_extras:
        return res, comp, short
    # one-sided oracle arms
    p = pis["A0"]
    arms2 = {
        "ORACLE_on_short_only": np.where(short, 1.0, p),
        "ORACLE_on_nonshort_only": np.where(short, p, 0.0),
        "PI0_everywhere": np.zeros(n),
        "BASERATE_const": np.full(
            n, float(short[season <= 2023].mean() if (season <= 2023).any() else short.mean())
        ),
    }
    for name, pp in arms2.items():
        q = grid(comp, wq, pp)
        c = crps_q(q.astype(float), y)
        res["mean_crps"][name] = float(c.mean())
        res["delta_vs_A0"][name] = boot(c - cr["A0"], gid)
    # interpolation A0 -> oracle: realistic accuracy
    interp = {}
    for t in (0.1, 0.25, 0.5, 0.75, 1.0):
        pp = (1 - t) * p + t * sh
        pp = np.where(t < 1.0, np.clip(pp, 1e-6, 1 - 1e-6), pp)
        q = grid(comp, wq, pp)
        c = crps_q(q.astype(float), y)
        interp[str(t)] = {
            "crps_delta_vs_A0": float((c - cr["A0"]).mean()),
            "logloss": float(ll(pp, sh).mean()),
            "auc": auc(pp, sh),
        }
    res["interp_A0_to_oracle"] = interp
    # (3) sensitivity by A0 pi bucket: CRPS vs pi on grid
    edges = np.quantile(p, [0, 0.5, 0.8, 0.95, 1.0])
    bk = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, 3)
    pg = [0.0, 0.01, 0.03, 0.06, 0.1, 0.2, 0.35, 0.5, 1.0]
    curves = {str(v): crps_q(grid(comp, wq, np.full(n, v)).astype(float), y) for v in pg}
    sens = []
    for b in range(4):
        m = bk == b
        e = {
            "bucket": b,
            "n": int(m.sum()),
            "pi_range": [float(p[m].min()), float(p[m].max())],
            "mean_A0_pi": float(p[m].mean()),
            "observed_short_rate": float(short[m].mean()),
            "mean_CRPS_vs_pi": {k: float(v[m].mean()) for k, v in curves.items()},
            "CRPS_vs_pi_on_short_rows": {
                k: float(v[m & short].mean()) if (m & short).any() else None
                for k, v in curves.items()
            },
            "CRPS_vs_pi_on_nonshort_rows": {
                k: float(v[m & ~short].mean()) for k, v in curves.items()
            },
        }
        best = min(pg, key=lambda v: e["mean_CRPS_vs_pi"][str(v)])
        e["best_const_pi_in_bucket"] = best
        e["flatness_range_0_to_0.2"] = float(
            max(e["mean_CRPS_vs_pi"][str(v)] for v in pg if v <= 0.2)
            - min(e["mean_CRPS_vs_pi"][str(v)] for v in pg if v <= 0.2)
        )
        sens.append(e)
    res["sensitivity_by_A0_pi_bucket"] = sens
    res["pi_clip"] = list(PI_CLIP)
    res["pi_clip_active_A0"] = {
        "at_floor": float((p <= PI_CLIP[0] + 1e-12).mean()),
        "at_cap": float((p >= PI_CLIP[1] - 1e-12).mean()),
    }
    # (4) short-component calibration: realised y on short rows vs m_row*w_q (block wq)
    cov: dict[str, list[Any]] = {"10": [], "50": [], "90": []}
    for i, b in enumerate(np.unique(comp["row_blk"])):
        r = (comp["row_blk"] == b) & short
        w = wq[i]
        if np.isnan(w).any() or not r.any():
            continue
        q = np.maximum(comp["row_m"][r], 0.5)[:, None] * w[None, :]
        for k, t in (("10", 0.1), ("50", 0.5), ("90", 0.9)):
            qt = q[:, int(round(t * 199)) - 1]
            cov[k].append(y[r] <= qt)
    res["short_component_coverage_P(y<=q_tau)"] = {
        k: float(np.concatenate(v).mean()) for k, v in cov.items()
    }
    # same, within realised-minutes strata
    mins = comp["row_minutes"]
    strata = {"min<5": mins < 5, "5<=min<10": (mins >= 5) & (mins < 10), "10<=min": mins >= 10}
    res["short_component_coverage_by_realised_minutes"] = {}
    for sn, sm in strata.items():
        cc: dict[str, list[Any]] = {"10": [], "50": [], "90": []}
        for i, b in enumerate(np.unique(comp["row_blk"])):
            r = (comp["row_blk"] == b) & short & sm
            w = wq[i]
            if np.isnan(w).any() or not r.any():
                continue
            q = np.maximum(comp["row_m"][r], 0.5)[:, None] * w[None, :]
            for k, t in (("10", 0.1), ("50", 0.5), ("90", 0.9)):
                cc[k].append(y[r] <= q[:, int(round(t * 199)) - 1])
        res["short_component_coverage_by_realised_minutes"][sn] = {
            "n": int((short & sm).sum()),
            **{k: float(np.concatenate(v).mean()) for k, v in cc.items()},
        }
    # short comp width vs realised short y: mean/median
    res["short_rows_y"] = {
        "mean": float(y[short].mean()),
        "median": float(np.median(y[short])),
        "p90": float(np.quantile(y[short], 0.9)),
        "share_y0": float((y[short] == 0).mean()),
        "mean_normal_centre_c_on_short_rows": float(comp["row_c"][short].mean()),
        "mean_m_row_on_short_rows": float(comp["row_m"][short].mean()),
    }
    # (2b) alternative short components with oracle pi
    # E1: empirical short distribution by role, as-of (short rows from EARLIER blocks only), ratio y/max(m,.5), role = starter10 bucket x min10 tercile
    blks = np.unique(comp["row_blk"])
    role = (comp["row_starter10"] >= 0.5).astype(int) * 3 + np.searchsorted(
        np.quantile(comp["row_min10"], [1 / 3, 2 / 3]), comp["row_min10"]
    )
    ratio = y / np.maximum(comp["row_m"], 0.5)
    wq_rows = np.empty((n, 199))
    kgrid = (np.arange(199) + 0.5) / 199
    for i, b in enumerate(blks):
        r = np.where(comp["row_blk"] == b)[0]
        prior = np.isin(comp["row_blk"], blks[:i]) & short
        for ro in np.unique(role[r]):
            pool = ratio[prior & (role == ro)]
            if len(pool) < 100:
                pool = (
                    ratio[prior]
                    if prior.sum() >= 100
                    else np.array([x for x in wq[i] if not np.isnan(x)])
                    if not np.isnan(wq[i]).any()
                    else ratio[short]
                )
            wq_rows[r[role[r] == ro]] = np.quantile(pool, kgrid)[None, :]
    e1 = grid(comp, wq, sh, wq_rows=wq_rows)
    c1 = crps_q(e1.astype(float), y)
    res["oracle_with_role_asof_short_component"] = {
        "mean_crps": float(c1.mean()),
        "delta_vs_ORACLE_A0wq": boot(c1 - cr["ORACLE"], gid),
        "delta_vs_A0": boot(c1 - cr["A0"], gid),
        "fraction_of_A0_to_ORACLE_gain": float(
            (c1 - cr["A0"]).mean() / (cr["ORACLE"] - cr["A0"]).mean()
        ),
    }
    # E2: the short component quality ceiling: replace ratio quantile by realised-y-informed? use the realised minutes: short comp = per-minute rate * realised minutes (leak) -> upper bound for short rows
    rate = np.quantile(y[short] / np.maximum(mins[short], 0.5), kgrid)
    wq_rows2 = (
        np.maximum(mins, 0.5)[:, None] / np.maximum(comp["row_m"], 0.5)[:, None] * rate[None, :] * 0
        + (np.maximum(mins, 0.5)[:, None] * rate[None, :]) / np.maximum(comp["row_m"], 0.5)[:, None]
    )
    e2 = grid(comp, wq, sh, wq_rows=wq_rows2)
    c2 = crps_q(e2.astype(float), y)
    res["oracle_with_realised_minutes_short_component_LEAK_ceiling"] = {
        "mean_crps": float(c2.mean()),
        "delta_vs_ORACLE_A0wq": boot(c2 - cr["ORACLE"], gid),
        "delta_vs_A0": boot(c2 - cr["A0"], gid),
    }
    return res, comp, short


def main() -> None:
    pic = dict(np.load(LT / "components_pi.npz", allow_pickle=True))
    a0 = pic["row_pi"]
    cand = np.load(CACHE / "pi_CAND.npz")["pi"]
    ctrl = np.load(CACHE / "pi_CTRL.npz")["pi"]
    comp0 = load_components("pts", LT)
    short = short_flag(comp0["row_minutes"], comp0["row_min10"])
    sh = short.astype(float)
    season, gid = comp0["row_season"], comp0["row_gid"].astype(str)
    out = {
        "seasons": sorted(set(season.tolist())),
        "SHORT_RATIO": SHORT_RATIO,
        "PI_CLIP": list(PI_CLIP),
    }
    pim = {}
    for name, p in (
        ("A0", a0),
        ("CTRL", ctrl),
        ("CAND", cand),
        ("BASERATE", np.full(len(sh), sh.mean())),
    ):
        pim[name] = {
            "all": {
                "logloss": float(ll(p, sh).mean()),
                "brier": float(((p - sh) ** 2).mean()),
                "auc": auc(p, sh),
                "mean_pi": float(p.mean()),
                "min": float(p.min()),
                "max": float(p.max()),
            }
        }
        for s in (2023, 2024):
            m = season == s
            pim[name][str(s)] = {
                "logloss": float(ll(p[m], sh[m]).mean()),
                "brier": float(((p[m] - sh[m]) ** 2).mean()),
                "auc": auc(p[m], sh[m]),
                "observed": float(sh[m].mean()),
            }
    pim["ORACLE"] = {"all": {"logloss": 0.0, "brier": 0.0, "auc": 1.0}}
    pim["CAND_minus_A0_logloss_ci_game_clustered"] = boot(ll(cand, sh) - ll(a0, sh), gid)
    pim["frac_short_rows_with_A0_pi_gt_0.2"] = float((a0[short] > 0.2).mean())
    pim["pi_A0_quantiles"] = {
        str(q): float(np.quantile(a0, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.9, 0.99)
    }
    out["pi_metrics"] = pim
    pis = {"A0": a0, "CTRL": ctrl, "CAND": cand, "ORACLE": sh}
    out["stats"] = {}
    for st in ("pts", "reb", "ast", "fg3m"):
        print("stat", st, flush=True)
        r, _, _ = per_stat(st, pic, pis, with_extras=(st == "pts"))
        out["stats"][st] = r
        OUT.write_text(json.dumps(out, indent=1))
    OUT.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
