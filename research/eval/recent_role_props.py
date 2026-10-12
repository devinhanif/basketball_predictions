# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc"
"""RECENT_ROLE Stage 2 (docs/prereg/RECENT_ROLE.md): does the improved P(play) help the props, and does it enter injury-Elo.

2a  Unconditional integer CRPS of the production props on EVERY Cell-P row (played and DNP, not OUT at T-60). The conditional
    distribution is A0 (production + lower-tail mixture) refit live, identical across arms; only p = P(play) differs:
        F_u(x) = (1 - p) * 1[x >= 0] + p * F_c(x),   y_u = stat if played else 0,
    scored as the CRPS of that weighted point set (``weighted_crps``). The played-only A0 CRPS (pts 3.140236 / 3.176840) is
    conditional on playing and CANNOT move with p: ``p_play`` never enters the mixture (its pi is P(short | plays)); it enters
    only ``mean_uncond`` / ``p_ge_uncond`` (nba/props/forward.py `_ctx_row`). DNP rows get their features from
    ``prod_state_rows`` (gated equal to the forward placeholder path) plus the game-level columns of a teammate's row.
2b  injury-Elo: does NOT apply. It uses report flags only (OUT weight 1, doubtful weight 0.5 = a constant); no availability
    probability for a not-OUT player enters (nba/models/injury_elo.py `_side_sums`).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, TEST_SEASONS, bh_adjust, month_blocks
from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualModel,
    _matrix,
    _params,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import fit_short_state, lower_quantiles, predict_pi, short_flag
from research.eval import recent_role_eval as RR
from research.eval import workload_eval as WE
from research.recent_role import prod_state as PS

FLOOR = -0.005
LIVE_TOL = 0.01  # live A0 refit vs the stored A0, played-row CRPS (info gate; data clean-up drift)
GAME_LEVEL = (
    "rest_days", "b2b", "team_game_no", "exp_margin", "abs_margin", "r20_pts_for", "r20_pts_against",
    "opp_rest_days", "opp_b2b", "opp_r20_pts_for", "opp_r20_pts_against", "n_out_rot", "vac_min", "has_report", "is_home_f",
    *[f"opp_allow_{s}" for s in PROP_STATS], *[f"vac_{s}" for s in PROP_STATS],
)  # fmt: skip
P_ARMS = (
    "prod",
    "C",
    "Cp",
    "R",
)  # prod = production raw hurdle p_play; the rest are Stage 1's LightGBM P(play) arms


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def weighted_crps(x: np.ndarray, w: np.ndarray, y: np.ndarray) -> np.ndarray:
    """CRPS of the discrete distribution (points ``x[n,k]``, weights ``w[n,k]`` summing to 1) at ``y[n]``:
    E|X - y| - E|X - X'|/2, the second term from sorted points and cumulative weights."""
    o = np.argsort(x, axis=1, kind="stable")
    xs, ws = np.take_along_axis(x, o, axis=1), np.take_along_axis(w, o, axis=1)
    c = np.cumsum(ws, axis=1)
    t1 = (ws * np.abs(xs - y[:, None])).sum(axis=1)
    t2 = (xs * ws * (2.0 * c - ws - 1.0)).sum(axis=1)
    return np.asarray(t1 - t2)


def uncond_crps(qi: np.ndarray, p: np.ndarray, y_u: np.ndarray) -> np.ndarray:
    """Unconditional CRPS: a point mass at 0 with weight 1-p plus the conditional integer quantile points with weight p/k each."""
    n, k = qi.shape
    x = np.concatenate([np.zeros((n, 1)), qi.astype(float)], axis=1)
    w = np.concatenate([(1.0 - p)[:, None], np.repeat((p / k)[:, None], k, axis=1)], axis=1)
    return weighted_crps(x, w, y_u)


def dnp_query_frame(q: pl.DataFrame, feats: pl.DataFrame, box: pl.DataFrame) -> pl.DataFrame:
    """Feature rows for arbitrary P-universe rows: production state at (player, date) + the game-level columns of any teammate row of
    the same game-team + the player's position code. Rows with no teammate row in ``feats`` are dropped (counted by the caller)."""
    st = PS.prod_state_rows(box, q.select("game_id", "player_id", "team_id", "season", "game_date"))
    tmpl = feats.unique(["game_id", "team_id"]).select("game_id", "team_id", *GAME_LEVEL)
    pos = (
        feats.sort(["game_date", "game_id"])
        .group_by("player_id", maintain_order=True)
        .last()
        .select("player_id", "pos_code")
    )
    out = q.select("game_id", "player_id", "team_id", "season", "game_date").join(
        st, on=["game_id", "player_id"], how="left"
    )
    out = out.join(tmpl, on=["game_id", "team_id"], how="inner").join(
        pos, on="player_id", how="left"
    )
    need = (
        set(COMMON_FEATURES)
        | set(stat_feature_names("pts"))
        | {n for s in PROP_STATS for n in stat_feature_names(s)}
    )
    missing = need - set(out.columns)
    if missing:
        raise RuntimeError(f"DNP feature frame lacks columns: {sorted(missing)}")
    return out


def run_2a(sha: str, smoke: bool = False) -> dict[str, Any]:
    """Live A0 per stat on played AND DNP P-rows, unconditional CRPS for each P(play) arm. Needs a Stage 1 PASS (checked by the caller)."""
    from research.eval import four_factors_eval as F

    t0 = time.time()
    fr, cfg, aux = RR.build_frame()
    if (
        smoke
    ):  # code-path check only: p arms are the production p, two blocks, no verdict, nothing written
        sc = {"gid": fr["game_id"].to_numpy().astype(str), "pid": fr["player_id"].to_numpy()}
        for a in ("base", "Cp", "R"):
            sc[f"p_{a}"] = fr["p_raw"].to_numpy()
    else:
        sc = dict(np.load(RR.CACHE / "scores.npz", allow_pickle=True))
    study = F.load_study(RR.DB)
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    q = fr.filter(pl.col("season").is_in(list(TEST_SEASONS))).select(
        "game_id", "player_id", "team_id", "season", "game_date", "played", "p_raw"
    )
    ps = pl.DataFrame(
        {
            "game_id": sc["gid"],
            "player_id": sc["pid"].astype(np.int64),
            **{f"p_{a}": sc[f"p_{a}"] for a in ("base", "Cp", "R")},
        }
    )
    q = q.join(ps, on=RR.KEYS, how="inner")
    qf = dnp_query_frame(q, feats, aux["box"])
    lab = aux["box"].select(*RR.KEYS, *PROP_STATS).with_columns(pl.col("player_id").cast(pl.Int64))
    qf = (
        qf.join(q.select(*RR.KEYS, "played", "p_raw", "p_base", "p_Cp", "p_R"), on=RR.KEYS)
        .join(lab, on=RR.KEYS, how="left")
        .sort(["game_date", "game_id", "player_id"])
    )
    dropped = q.height - qf.height
    _log(f"2a rows {qf.height} (dropped {dropped} without a teammate template)")
    xcols = list(COMMON_FEATURES)
    frames = {s: stat_frame(feats, s) for s in PROP_STATS}
    sf0 = frames["pts"]
    res: dict[str, dict[str, list[np.ndarray]]] = {
        s: {k: [] for k in ("qi", "gid", "pid", "season", "played", "y")} for s in PROP_STATS
    }
    for bi, (start, end) in enumerate(month_blocks(qf)):
        if smoke and bi >= 2:
            break
        blk = qf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("n_prior") >= 5
        )
        if blk.is_empty():
            continue
        train0 = sf0.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if train0.height < 1500:
            continue
        short = short_flag(train0["minutes"].to_numpy(), train0["min10"].to_numpy())
        x_tr = _matrix(train0, xcols)
        clf = None
        params = _params(cfg, "binary")
        for s in PROP_STATS:
            names = stat_feature_names(s)
            train = (
                frames[s]
                .filter(pl.col("game_date") < start)
                .sort(["game_date", "game_id", "player_id"])
            )
            model = ContextResidualModel(s, cfg, names).fit(train)
            state = fit_short_state(
                x_tr,
                short,
                train["y"].to_numpy(),
                train["m"].to_numpy(),
                CRPS_TAUS,
                params,
                clf=clf,
            )
            clf = state["pi_model"]
            b = blk.with_columns(pl.col(f"m10_{s}").alias("m"))
            c, sc_ = model.predict_components(b)
            _, cal_df = split_calibration(train, cfg)
            xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
            mu = np.asarray(model.mean_head.predict(xc))
            scl = model._scale(xc)
            z_cal = (cal_df["resid"].to_numpy() - mu) / scl
            pi = predict_pi({"pi_model": clf}, _matrix(b, xcols))
            qq = lower_quantiles(
                "mixture", z_cal=z_cal, c_cal=cal_df["m"].to_numpy() + mu, min10_cal=cal_df["min10"].to_numpy(),
                short_cal=short_flag(cal_df["minutes"].to_numpy(), cal_df["min10"].to_numpy()), c=c, s=sc_,
                m_row=b["m"].to_numpy(), min10_row=b["min10"].to_numpy(), pi=pi, w_q=state["w_q"], taus=CRPS_TAUS,
            )  # fmt: skip
            acc = res[s]
            acc["qi"].append(to_integer_support(qq).astype(np.int16))
            acc["gid"].append(b["game_id"].to_numpy().astype(str))
            acc["pid"].append(b["player_id"].to_numpy().astype(np.int64))
            acc["season"].append(b["season"].to_numpy())
            acc["played"].append(b["played"].to_numpy())
            acc["y"].append(b[s].cast(pl.Float64).to_numpy())
        _log(f"2a block {bi + 1} {start} n={blk.height}")
    out: dict[str, Any] = {"rows": [], "live_vs_stored": [], "dropped_rows": dropped, "applies_2b": False,
                           "note_2b": "injury-Elo uses report OUT / doubtful flags with constant weights (1.0 / 0.5); no P(play) for a not-OUT player enters, so Stage 2b does not apply"}  # fmt: skip
    pic = WE.load_pic()
    for s in PROP_STATS:
        r: dict[str, Any] = {k: np.concatenate(v) for k, v in res[s].items()}
        pmap = qf.select(*RR.KEYS, "p_raw", "p_base", "p_Cp", "p_R")
        pj = pl.DataFrame({"game_id": r["gid"], "player_id": r["pid"]}).join(
            pmap, on=RR.KEYS, how="left"
        )
        parms = {
            "prod": pj["p_raw"].to_numpy(),
            "C": pj["p_base"].to_numpy(),
            "Cp": pj["p_Cp"].to_numpy(),
            "R": pj["p_R"].to_numpy(),
        }
        y_u = np.where(r["played"] > 0.5, r["y"], 0.0)
        crps = {a: uncond_crps(r["qi"], np.clip(p, 1e-6, 1 - 1e-6), y_u) for a, p in parms.items()}
        a0 = WE.a0_arm(s, pic)
        for season in RR.SEASONS:
            m = r["season"] == season
            if not m.any():
                continue
            g = r["gid"][m]
            for a in P_ARMS:
                out["rows"].append(
                    {
                        "stat": s,
                        "season": season,
                        "arm": a,
                        "n_rows": int(m.sum()),
                        "n_games": int(len(np.unique(g))),
                        "crps_uncond": float(crps[a][m].mean()),
                    }
                )
            for a, b in (("R", "Cp"), ("R", "prod"), ("Cp", "prod"), ("Cp", "C")):
                out["rows"].append(
                    {
                        "stat": s,
                        "season": season,
                        "arm": f"{a}_minus_{b}",
                        "n_rows": int(m.sum()),
                        "n_games": int(len(np.unique(g))),
                        **WE.delta_stats(crps[a][m] - crps[b][m], g),
                    }
                )
            pl_m = m & (r["played"] > 0.5)
            sm = a0["season"] == season
            k_live = set(zip(r["gid"][pl_m].tolist(), r["pid"][pl_m].tolist(), strict=True))
            k_st = set(zip(a0["gid"][sm].tolist(), a0["pid"][sm].tolist(), strict=True))
            both = k_live & k_st  # compare on the rows both have
            ml = (
                np.array(
                    [
                        (g_, p_) in both
                        for g_, p_ in zip(r["gid"].tolist(), r["pid"].tolist(), strict=True)
                    ]
                )
                & pl_m
            )
            ms = (
                np.array(
                    [
                        (g_, p_) in both
                        for g_, p_ in zip(a0["gid"].tolist(), a0["pid"].tolist(), strict=True)
                    ]
                )
                & sm
            )
            live = float(F.row_scores(r["qi"][ml].astype(float), r["y"][ml], s)["crps"].mean())
            stored = float(F.row_scores(a0["qi"][ms].astype(float), a0["y"][ms], s)["crps"].mean())
            out["live_vs_stored"].append(
                {
                    "stat": s,
                    "season": season,
                    "live": live,
                    "stored": stored,
                    "abs_diff": abs(live - stored),
                    "n_live": int(k_live.__len__()),
                    "n_stored": int(len(k_st)),
                    "n_both": int(len(both)),
                    "ok": abs(live - stored) <= LIVE_TOL,
                }
            )
    if smoke:
        out["seconds"] = time.time() - t0
        return out
    for season in RR.SEASONS:
        tgt = [x for x in out["rows"] if x["season"] == season and x["arm"] == "R_minus_Cp"]
        adj = bh_adjust(np.array([x["p"] for x in tgt]))
        for x, a in zip(tgt, adj, strict=True):
            x["p_bh"] = float(a)
    out["verdict"] = decide_2a(out)
    out["seconds"] = time.time() - t0
    out["sha256"] = sha
    return out


def decide_2a(out: dict[str, Any]) -> dict[str, Any]:
    """Per stat: 2023 point <= floor with CI upper < 0; 2024 same plus BH p <= 0.05 (m = 4). The live-vs-stored gate must hold."""
    r = {(x["stat"], x["season"], x["arm"]): x for x in out["rows"]}
    gate = all(x["ok"] for x in out["live_vs_stored"])
    per: dict[str, Any] = {}
    for s in PROP_STATS:
        a, b = r[(s, 2023, "R_minus_Cp")], r[(s, 2024, "R_minus_Cp")]
        per[s] = {
            "pass": bool(
                gate
                and a["delta"] <= FLOOR
                and a["ci_hi"] < 0
                and b["delta"] <= FLOOR
                and b["ci_hi"] < 0
                and b["p_bh"] <= 0.05
            ),
            "d2023": a["delta"],
            "d2024": b["delta"],
            "mde2024": b["mde"],
        }
    return {
        "live_gate_ok": gate,
        "per_stat": per,
        "any_pass": any(v["pass"] for v in per.values()),
        "max_season": MAX_SEASON,
    }
