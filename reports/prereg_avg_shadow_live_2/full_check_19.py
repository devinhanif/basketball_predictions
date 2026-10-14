# ruff: noqa: E501, E402
"""FULL_SHADOW_LIVE_2026_27 checks on the stored 19-quantile form vs the 199-point form (DESCRIPTIVE, season 2024 only; the FULL rule is frozen and untouched).

The FULL rule has NO calibration guard. This measures every check it does have (a, b, c, d, e) on the exact live read-out: the 19 quantiles at taus
0.05..0.95 of the mixture, integer-mapped (as ``nba/daily/full_shadow/run.py``), against the 199-point form of the offline rehearsal. Regular season and
play-in rows (the rule's population: prefixes 002, 005) of 2024. Slices are proxies from the stored components (bench = starter10 < 0.5, second night =
row_b2b); production proj_minutes / p_play do not exist for history, so the low-availability slice cannot be rebuilt.

    .venv/bin/python -W ignore reports/prereg_avg_shadow_live_2/full_check_19.py     (about 30 s; read-only db for game dates; writes full_check_19.json beside it)
"""

import json
import sys
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nba.eval import full_shadow_live as L
from nba.eval.lower_tail_eval import guard, load_components, lower_quantiles, randomized_pit
from nba.props.context_residual import to_integer_support
from nba.props.lower_tail import short_flag
from research.eval import pi_recent_role_eval as PR
from research.eval import recent_role_full_eval as RF

T19 = np.asarray(L.TAUS)


def grid(kind, comp, pic, stat, taus):
    out = np.empty((len(comp["row_y"]), len(taus)))
    wq_all = pic[f"wq_{stat}"]
    for i, b in enumerate(np.unique(comp["row_blk"])):
        r = comp["row_blk"] == b
        cm = comp["cal_blk"] == b
        w = wq_all[i]
        out[r] = lower_quantiles(
            kind,
            z_cal=comp["cal_z"][cm],
            c_cal=comp["cal_c"][cm],
            min10_cal=comp["cal_min10"][cm],
            short_cal=short_flag(comp["cal_minutes"][cm], comp["cal_min10"][cm]),
            c=comp["row_c"][r],
            s=comp["row_s"][r],
            m_row=comp["row_m"][r],
            min10_row=comp["row_min10"][r],
            pi=pic["row_pi"][r],
            w_q=None if np.isnan(w).any() else w,
            taus=taus,
        )
    return out


def pin(q, y):  # 19-tau scorer
    return L.crps_rows(q, y)


pic = dict(np.load(RF.LT_DIR / "components_pi.npz", allow_pickle=True))
pi_r = np.load(RF.pi_path("R"), allow_pickle=True)["pi"]
con = duckdb.connect(str(ROOT / "nba.duckdb"), read_only=True)
dates = dict(con.execute("SELECT game_id, game_date FROM games WHERE season <= 2024").fetchall())
from nba.props.context_residual import CRPS_TAUS

res = {}
for stat in L.STATS:
    c0 = load_components(stat, RF.LT_DIR)
    assert int(c0["row_season"].max()) <= 2024
    ch = RF.load_heads(stat, "R")
    y = c0["row_y"]
    gid = c0["row_gid"].astype(str)
    pid = c0["row_pid"].astype(np.int64)
    pic0 = pic
    picR = PR.candidate_pic(pic, pi_r)
    q0_19 = to_integer_support(grid("mixture", c0, pic0, stat, T19)).astype(np.int16)
    q1_19 = to_integer_support(grid("mixture", ch, picR, stat, T19)).astype(np.int16)
    qp_19 = to_integer_support(grid("off", ch, picR, stat, T19)).astype(
        np.int16
    )  # production (no mixture) with FULL heads? see note
    q0_199 = to_integer_support(grid("mixture", c0, pic0, stat, CRPS_TAUS)).astype(np.int16)
    q1_199 = to_integer_support(grid("mixture", ch, picR, stat, CRPS_TAUS)).astype(np.int16)
    qprod_19 = to_integer_support(grid("off", c0, pic0, stat, T19)).astype(np.int16)
    qprod_199 = to_integer_support(grid("off", c0, pic0, stat, CRPS_TAUS)).astype(np.int16)
    pref = np.array([g[:3] for g in gid])
    m = (c0["row_season"] == 2024) & np.isin(pref, ["002", "005"])
    mall = c0["row_season"] == 2024
    day = np.array([str(dates[g]) for g in gid])
    d199 = PR.crps_int(q1_199, y) - PR.crps_int(q0_199, y)
    d19 = pin(q1_19, y) - pin(q0_19, y)
    rec = {
        "n_reg_playin": int(m.sum()),
        "n_all_2024": int(mall.sum()),
        "games_reg_playin": int(len(np.unique(gid[m]))),
    }
    sig = L.NULL_SD_PTS if stat == "pts" else 0.0
    rec["full_minus_a0"] = {
        "all_2024_199": float(d199[mall].mean()),
        "all_2024_19": float(d19[mall].mean()),
        "reg_199": float(d199[m].mean()),
        "reg_19": float(d19[m].mean()),
        "reg_19_game": L.one_sided(d19[m], gid[m], sig),
        "reg_19_date": L.one_sided(d19[m], day[m], sig),
        "reg_199_game": L.one_sided(d199[m], gid[m], sig),
    }
    dp199 = PR.crps_int(q1_199, y) - PR.crps_int(qprod_199, y)
    dp19 = pin(q1_19, y) - pin(qprod_19, y)
    rec["full_minus_prod_own_grid"] = {
        "reg_199": float(dp199[m].mean()),
        "reg_19": float(dp19[m].mean()),
        "reg_19_game": L.one_sided(dp19[m], gid[m]),
    }
    if stat == "pts":
        tipv = np.array(
            [np.datetime64(dates[g]).astype("datetime64[s]").astype("int64") for g in gid[m]]
        )
        dayd = np.array([np.datetime64(dates[g]) for g in gid[m]])
        for tag, (qa, qb, crf) in {
            "19": (q1_19, q0_19, pin),
            "199": (q1_199, q0_199, PR.crps_int),
        }.items():
            pl = L.stale_adjust_placebo(pid[m], tipv, qa[m], qb[m])
            sh = L.shuffle_grids_within_day(qa[m], dayd)
            sc0 = crf(qb[m], y[m])
            rec[f"guards_{tag}"] = {
                "full_minus_placebo": L.one_sided(crf(qa[m], y[m]) - crf(pl, y[m]), gid[m]),
                "placebo_minus_a0": L.one_sided(crf(pl, y[m]) - sc0, gid[m]),
                "shuffled_minus_a0": L.one_sided(crf(sh, y[m]) - sc0, gid[m]),
            }
            # slices (proxies from components): second night = row_b2b ; bench proxy = starter10 < .5 ; played<5 min
            dd = crf(qa, y) - crf(qb, y)
            sl = {
                "second_night(b2b)": c0["row_b2b"] > 0.5,
                "bench_proxy(starter10<.5)": c0["row_starter10"] < 0.5,
                "starter10": c0["row_starter10"] >= 0.5,
                "cold(n_prior<10)": c0["row_n_prior"] < 10,
                "min<5": c0["row_minutes"] < 5,
            }
            rec[f"slices_{tag}"] = {
                k: {"n": int((m & v).sum()), "delta": float(dd[m & v].mean())}
                for k, v in sl.items()
            }
        # descriptive calibration on the stored form (FULL has no G1)
        for nm, q199, q19 in (("FULL", q1_199, q1_19), ("A0", q0_199, q0_19)):
            rec[f"calib_{nm}"] = {}
            for tag, q in (("199", q199), ("19", q19)):
                pit = randomized_pit(q[m].astype(float), y[m], 0)
                rec[f"calib_{nm}"][tag] = {
                    "bias": float((y[m] - q[m].mean(axis=1)).mean()),
                    "cov80": float(((pit > 0.1) & (pit <= 0.9)).mean()),
                    **guard(pit),
                }
    res[stat] = rec
    print(stat, json.dumps(rec, default=float)[:3000], flush=True)
out = Path(__file__).resolve().parent / "full_check_19.json"
out.write_text(json.dumps(res, indent=1, default=float))
