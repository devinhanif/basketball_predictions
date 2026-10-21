# ruff: noqa: E501
"""MARKET_PRIOR_RESIDUAL minimum-data gate (label-free; rule: docs/prereg/MARKET_PRIOR_RESIDUAL.md).

    .venv/bin/python -W ignore -m research.eval.market_prior_residual_gate [--no-rehearsal] [--k-null 10] [--out reports/prereg_market_prior_residual/gate.json]

Read-only: ``data/odds/odds_history.duckdb`` and ``nba.duckdb`` are opened ``read_only=True`` with ``season <= 2024`` in every SQL. The
label columns (``y``, ``over``) are never selected, with ONE disclosed exception: G6 recomputes ODDS_HISTORY's published A0 and market
log-loss cells (results_{season}.json h1) to prove the comparator reproduces; that function selects nothing but ``over``, ``p_model``,
``p_over_mult`` and the leg keys. The rehearsal (G8) replaces outcomes by a fixed draw from the de-vigged market, so R, PL and the
bootstrap run end to end on real rows and real features with the true effect equal to zero.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import market_prior_residual_eval as ev
from research.eval import market_prior_residual_rule as rule

OUT = Path("reports/prereg_market_prior_residual/gate.json")
SEEDS = {"synthetic_labels": 0, "bootstrap": rule.SEED}
SYN_SEED = 20261020


def _norm_cdf(x: float) -> float:
    from math import erf, sqrt

    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def chk(name: str, value: Any, bar: str, ok: bool) -> dict[str, Any]:
    return {"check": name, "value": value, "bar": bar, "pass": bool(ok)}


def pctl(v: np.ndarray) -> dict[str, float]:
    return {
        "n": int(len(v)),
        "min": float(np.min(v)),
        "p10": float(np.percentile(v, 10)),
        "median": float(np.median(v)),
        "p90": float(np.percentile(v, 90)),
        "max": float(np.max(v)),
        "mean": float(np.mean(v)),
    }


# ---------------------------------------------------------------- inputs and sha pins


def input_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        pins[f"pairs_{s}"] = rule.file_sha256(ev.resolve(ev.PAIRS_REL.format(season=s)))
        pins[f"model_rows_{s}"] = rule.file_sha256(
            ev.resolve(f"reports/odds_history/model_rows_{s}.parquet")
        )
        pins[f"market_rows_{s}"] = rule.file_sha256(
            ev.resolve(f"reports/odds_history/market_rows_{s}.parquet")
        )
    return pins


# ---------------------------------------------------------------- G1-G3 (counts)


def g1_g2_g3(legs: pl.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out: dict[str, Any] = {"g1_coverage": {}, "g2_legs": {}, "g3_clusters": {}}
    checks: list[dict[str, Any]] = []
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        mr = pl.read_parquet(
            ev.resolve(f"reports/odds_history/model_rows_{s}.parquet"),
            columns=["game_id", "player_id", "stat", "minutes", "season"],
        ).filter(pl.col("season") <= rule.MAX_SEASON)
        mk = pl.read_parquet(
            ev.resolve(f"reports/odds_history/market_rows_{s}.parquet"),
            columns=["game_id", "player_id", "stat", "book", "snapshot_kind"],
        ).filter((pl.col("book") == rule.BOOK) & (pl.col("snapshot_kind") == rule.SNAPSHOT_KIND))
        lg = legs.filter(pl.col("season") == s)
        keys = ["game_id", "player_id", "stat"]
        quoted = mk.select(keys).unique().with_columns(pl.lit(True).alias("quoted"))
        leg_k = lg.select(keys).unique().with_columns(pl.lit(True).alias("leg"))
        j = (
            mr.join(quoted, on=keys, how="left")
            .join(leg_k, on=keys, how="left")
            .with_columns(pl.col("quoted").fill_null(False), pl.col("leg").fill_null(False))
        )
        cov: dict[str, Any] = {}
        for stat in rule.STATS:
            r = j.filter(pl.col("stat") == stat)
            r20 = r.filter(pl.col("minutes") >= 20)
            played = r.filter(pl.col("minutes") > 0)
            cov[stat] = {
                "model_rows": r.height,
                "played_rows": played.height,
                "rows_min20": r20.height,
                "quote_share_all": float(r["quoted"].mean()),  # type: ignore[arg-type]
                "quote_share_min20": float(r20["quoted"].mean()),  # type: ignore[arg-type]
                "leg_share_all": float(r["leg"].mean()),  # type: ignore[arg-type]
                "leg_share_played": float(played["leg"].mean()),  # type: ignore[arg-type]
                "leg_share_min20": float(r20["leg"].mean()),  # type: ignore[arg-type]
            }
        out["g1_coverage"][str(s)] = cov
        per = {
            st: {
                "legs": int((lg["stat"] == st).sum()),
                "games": int(lg.filter(pl.col("stat") == st)["game_id"].n_unique()),
            }
            for st in rule.STATS
        }
        out["g2_legs"][str(s)] = {
            "pooled_legs": lg.height,
            "pooled_games": int(lg["game_id"].n_unique()),
            "per_stat": per,
        }
        cc = {k: rule.n_clusters(rule.cluster_codes(lg, k)) for k in rule.CLUSTERINGS}
        ps = lg.group_by(["player_id", "season"]).len().sort("len", descending=True)
        out["g3_clusters"][str(s)] = {
            **cc,
            "top5_player_season_share": float(ps["len"][:5].sum() / lg.height),
            "max_player_season_share": float(ps["len"][0] / lg.height),
        }
        checks += [
            chk(
                f"G1 {s} pts leg share of played model rows",
                round(cov["pts"]["leg_share_played"], 4),
                "reported (no bar; a coverage fact)",
                True,
            ),
            chk(
                f"G2 {s} pooled legs",
                lg.height,
                f">= {rule.GATE_MIN_LEGS_POOLED}",
                lg.height >= rule.GATE_MIN_LEGS_POOLED,
            ),
            chk(
                f"G2 {s} smallest per-stat legs",
                min(p["legs"] for p in per.values()),
                f">= {rule.GATE_MIN_LEGS_STAT}",
                min(p["legs"] for p in per.values()) >= rule.GATE_MIN_LEGS_STAT,
            ),
            chk(
                f"G3 {s} game clusters",
                cc["game"],
                f">= {rule.GATE_MIN_GAMES}",
                cc["game"] >= rule.GATE_MIN_GAMES,
            ),
            chk(
                f"G3 {s} player-season clusters",
                cc["player_season"],
                f">= {rule.GATE_MIN_PLAYER_SEASONS}",
                cc["player_season"] >= rule.GATE_MIN_PLAYER_SEASONS,
            ),
            chk(
                f"G3 {s} ISO-week clusters",
                cc["week"],
                f">= {rule.MIN_WEEKS} (else week CI is reported only)",
                cc["week"] >= rule.MIN_WEEKS,
            ),
        ]
    return out, checks


# ---------------------------------------------------------------- G5 lead and known-at


def g5_lead(
    counts: dict[str, Any], legs: pl.DataFrame
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    out: dict[str, Any] = {"legs_counts": counts, "lead_minutes": {}}
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        lg = legs.filter(pl.col("season") == s)
        lead = rule.lead_minutes(lg["snapshot_ts"].to_numpy(), lg["tipoff_utc"].to_numpy())
        g = lg.with_columns(pl.Series("lead", lead)).group_by("game_id").agg(pl.col("lead").first())
        out["lead_minutes"][str(s)] = {"legs": pctl(lead), "games": pctl(g["lead"].to_numpy())}
    allv = rule.lead_minutes(legs["snapshot_ts"].to_numpy(), legs["tipoff_utc"].to_numpy())
    out["lead_minutes"]["pooled"] = {
        "legs": pctl(allv),
        "tertile_edges": [float(np.percentile(allv, 100 / 3)), float(np.percentile(allv, 200 / 3))],
    }
    dropped = sum(c["dropped_known_at"] for c in counts.values())
    checks = [
        chk(
            "G5 rows dropped by snapshot_ts <= tip - 60 (real tip)",
            dropped,
            "count reported; 0 expected",
            True,
        ),
        chk(
            "G5 every leg has a real tip and a snapshot stamp",
            int(np.isfinite(allv).all()),
            "1",
            bool(np.isfinite(allv).all()),
        ),
        chk(
            "G5 min lead over legs (minutes)",
            float(np.min(allv)),
            ">= 60",
            float(np.min(allv)) >= rule.MIN_LEAD_MIN,
        ),
    ]
    return out, checks


def raw_snapshot_audit() -> dict[str, Any]:
    """Raw odds_history (read-only, season <= 2024): how many distinct Pinnacle t60 snapshots exist per game."""
    import duckdb

    con = duckdb.connect(str(ev.resolve("data/odds/odds_history.duckdb")), read_only=True)
    try:
        rows = con.execute(
            "SELECT season, snapshot_kind, COUNT(*) games, AVG(n) mean_snaps, MAX(n) max_snaps FROM ("
            "SELECT season, snapshot_kind, game_id, COUNT(DISTINCT snapshot_ts) n FROM odds_history "
            f"WHERE season <= {rule.MAX_SEASON} AND book = 'pinnacle' GROUP BY 1,2,3) GROUP BY 1,2 ORDER BY 1,2"
        ).fetchall()
        before = con.execute(
            f"SELECT COUNT(*) FROM odds_history WHERE season < {rule.SELECT_SEASON}"
        ).fetchone()
        kinds = con.execute(
            f"SELECT DISTINCT snapshot_kind FROM odds_history WHERE season <= {rule.MAX_SEASON}"
        ).fetchall()
        raw = con.execute(
            "SELECT season, snapshot_kind, COUNT(*) FROM odds_history "
            f"WHERE season <= {rule.MAX_SEASON} AND book = 'pinnacle' GROUP BY 1,2 ORDER BY 1,2"
        ).fetchall()
    finally:
        con.close()
    return {
        "pinnacle_snapshots_per_game": [
            {"season": r[0], "kind": r[1], "games": r[2], "mean": float(r[3]), "max": r[4]}
            for r in rows
        ],
        "rows_before_2023": int(before[0]) if before else None,
        "snapshot_kinds_stored": sorted(k[0] for k in kinds),
        "pinnacle_raw_rows": [{"season": r[0], "kind": r[1], "rows": r[2]} for r in raw],
    }


# ---------------------------------------------------------------- G6 A0 reproduction


def g6_reproduction() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A0 and market log loss on Pinnacle t60 vs ODDS_HISTORY h1 (published); probabilities vs the stored OOF survival vectors.

    The only function that reads ``over``; it compares a published number and scores no R, PL or contrast."""
    from research.eval.odds_history_eval import model_p_over

    out: dict[str, Any] = {"cells": {}, "prob_level": {}, "current_frame_vs_stored": {}}
    checks: list[dict[str, Any]] = []
    worst_ll = 0.0
    worst_n = 0
    worst_p = 0.0
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        d = pl.read_parquet(
            ev.resolve(ev.PAIRS_REL.format(season=s)),
            columns=[
                "game_id",
                "player_id",
                "stat",
                "book",
                "snapshot_kind",
                "status",
                "_dnp",
                "line",
                "p_model",
                "p_over_mult",
                "over",
            ],
        ).filter((pl.col("book") == rule.BOOK) & (pl.col("snapshot_kind") == "t60"))
        pub = json.loads(ev.resolve(f"reports/odds_history/results_{s}.json").read_text())["h1"]
        mr = pl.read_parquet(
            ev.resolve(f"reports/odds_history/model_rows_{s}.parquet"),
            columns=["game_id", "player_id", "stat", "p_ge"],
        )
        for stat in rule.STATS:
            c = d.filter((pl.col("stat") == stat) & ~pl.col("_dnp") & (pl.col("status") == 0))
            ref = pub[f"t60/{stat}/pinnacle"]
            pm = np.clip(c["p_model"].to_numpy(), rule.MODEL_CLIP, 1 - rule.MODEL_CLIP)
            y = c["over"].to_numpy().astype(float)
            ll_a = float(rule.log_loss_vec(pm, y).mean())
            ll_m = float(rule.log_loss_vec(c["p_over_mult"].to_numpy(), y, eps=1e-15).mean())
            dl, dm = abs(ll_a - ref["ll_model"]), abs(ll_m - ref["ll_market"])
            out["cells"][f"{s}/{stat}"] = {
                "n": c.height,
                "n_pub": ref["n"],
                "ll_A0": ll_a,
                "ll_A0_pub": ref["ll_model"],
                "ll_M": ll_m,
                "ll_M_pub": ref["ll_market"],
                "abs_diff_A0": dl,
                "abs_diff_M": dm,
            }
            worst_ll = max(worst_ll, dl, dm)
            worst_n = max(worst_n, abs(c.height - int(ref["n"])))
            j = c.join(
                mr.filter(pl.col("stat") == stat), on=["game_id", "player_id", "stat"], how="left"
            )
            pge = np.asarray(j["p_ge"].to_list(), dtype=float)
            p_surv, _, _ = model_p_over(pge, j["line"].to_numpy(), np.zeros(j.height))
            dp = float(np.max(np.abs(p_surv - j["p_model"].to_numpy())))
            out["prob_level"][f"{s}/{stat}"] = {"n": j.height, "max_abs_diff_vs_stored_p_ge": dp}
            worst_p = max(worst_p, dp)
    out["current_frame_vs_stored"] = current_frame_vs_stored()
    checks += [
        chk("G6 n equals ODDS_HISTORY h1 n (8 cells)", worst_n, "0", worst_n == 0),
        chk(
            "G6 A0 and market log loss equal ODDS_HISTORY h1 (8 cells)",
            worst_ll,
            f"<= {rule.GATE_TOL}",
            worst_ll <= rule.GATE_TOL,
        ),
        chk(
            "G6 P(over) at the line from stored survival vectors equals p_model (8 cells)",
            worst_p,
            f"<= {rule.GATE_TOL}",
            worst_p <= rule.GATE_TOL,
        ),
    ]
    return out, checks


def current_frame_vs_stored() -> dict[str, Any]:
    """Label-free vintage check: today's A0 integer-quantile grids (GAME_MARKET_CONTEXT cache, 2026-10-11 data) vs the stored p_model."""
    res: dict[str, Any] = {}
    try:
        base = ev.resolve("data/ops/game_market_context_cache/arm_pts_A0.npz").parent
    except FileNotFoundError:
        return {"note": "cache absent"}
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        d = pl.read_parquet(
            ev.resolve(ev.PAIRS_REL.format(season=s)),
            columns=[
                "game_id",
                "player_id",
                "stat",
                "book",
                "snapshot_kind",
                "status",
                "_dnp",
                "line",
                "p_model",
            ],
        ).filter(
            (pl.col("book") == rule.BOOK)
            & (pl.col("snapshot_kind") == "t60")
            & ~pl.col("_dnp")
            & (pl.col("status") == 0)
        )
        for stat in rule.STATS:
            z = np.load(base / f"arm_{stat}_A0.npz")
            meta = (
                pl.read_parquet(base / f"arm_{stat}_A0.parquet")
                .select("game_id", "player_id", "season")
                .with_columns(pl.int_range(pl.len()).alias("_i"))
            )
            c = d.filter(pl.col("stat") == stat).join(
                meta.filter(pl.col("season") == s), on=["game_id", "player_id"], how="inner"
            )
            qi = z["qi"][c["_i"].to_numpy()]
            k = np.floor(c["line"].to_numpy()) + 1
            p_cur = (qi >= k[:, None]).mean(axis=1)
            diff = np.abs(p_cur - c["p_model"].to_numpy())
            res[f"{s}/{stat}"] = {
                "legs_matched": c.height,
                "legs": int((d["stat"] == stat).sum()),
                "share_equal_1e9": float((diff <= 1e-9).mean()),
                "share_equal_1e6": float((diff <= 1e-6).mean()),
                "max_abs": float(diff.max()),
            }
    return res


# ---------------------------------------------------------------- G7 features and G8 rehearsal


def g7_features(
    legs: pl.DataFrame, feats: pl.DataFrame
) -> tuple[pl.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    frame, fc = ev.build_feature_frame(legs, feats)
    names = ev.generic_feature_names()
    nulls = {c: float(frame[c].null_count() / frame.height) for c in names}
    per_season: dict[str, Any] = {}
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        f = frame.filter(pl.col("season") == s)
        per_season[str(s)] = {"legs": f.height, "misses": int(f["m_rec"].is_null().sum())}
    worst_null = max(nulls.values())
    drift = feature_drift_vs_cache(frame)
    miss_share = fc["misses"] / frame.height
    out = {
        "alignment": fc,
        "per_season": per_season,
        "null_rate_by_feature": dict(sorted(nulls.items(), key=lambda kv: -kv[1])[:8]),
        "max_null_rate": worst_null,
        "drift_vs_a0_cache": drift,
    }
    checks = [
        chk(
            "G7 legs without a current-frame feature row (m_rec NULL), share",
            round(miss_share, 5),
            "<= 0.005",
            miss_share <= 0.005,
        ),
        chk(
            "G7 largest NULL rate of any production feature on legs",
            round(worst_null, 5),
            "<= 0.05",
            worst_null <= 0.05,
        ),
    ]
    return frame, out, checks


def feature_drift_vs_cache(frame: pl.DataFrame) -> dict[str, Any]:
    """Legs whose today's feature values differ from the A0 cache's (2026-10-11 data): the data-version fact behind the A0 comparator."""
    try:
        base = ev.resolve("data/ops/game_market_context_cache/arm_pts_A0.npz").parent
    except FileNotFoundError:
        return {"note": "cache absent"}
    meta = pl.read_parquet(base / "arm_pts_A0.parquet").select(
        "game_id",
        "player_id",
        "season",
        "min10",
        "n_prior",
        "starter10",
        "team_game_no",
        "n_out_rot",
        "has_report",
    )
    cols = ["min10", "n_prior", "starter10", "team_game_no", "n_out_rot", "has_report"]
    j = frame.select("game_id", "player_id", "season", *cols).join(
        meta, on=["game_id", "player_id", "season"], how="inner", suffix="_c"
    )
    res: dict[str, Any] = {
        "legs_matched_pts_cache": j.height,
        "legs": frame.filter(pl.col("stat") == "pts").height,
    }
    for c in cols:
        a, b = j[c].to_numpy().astype(float), j[f"{c}_c"].to_numpy().astype(float)
        res[c] = int((~np.isclose(a, b, rtol=0, atol=1e-9, equal_nan=True)).sum())
    return res


def bootstrap_halfwidth(v: np.ndarray, d: pl.DataFrame) -> dict[str, Any]:
    cl = {k: rule.cluster_codes(d, k) for k in rule.CLUSTERINGS}
    c = ev.contrast(v, d, cl)
    b = c["binding"]
    hw = {k: (x["hi"] - x["lo"]) / 2 for k, x in c["by_clustering"].items()}
    return {
        "mean": c["binding"]["point"],
        "half_width_by_clustering": hw,
        "binding_half_width": (b["hi"] - b["lo"]) / 2,
        "clusters": {k: x["clusters"] for k, x in c["by_clustering"].items()},
        "decisive": c["decisive"],
    }


def power_row(hw: float) -> dict[str, float]:
    se = hw / 1.96
    return {f"power_at_{e}": _norm_cdf(abs(e) / se - 1.96) for e in (0.003, 0.0022, 0.0015)}


def g4_proxy(legs: pl.DataFrame) -> dict[str, Any]:
    """Label-free power proxies: outcomes drawn once from M (seed fixed); A0 minus M (an upper bound on the R minus M noise) and a pseudo-R
    (logit p_M + 0.25 x disagreement with A0) minus M."""
    out: dict[str, Any] = {}
    rng = np.random.default_rng(SYN_SEED)
    for s in (rule.SELECT_SEASON, rule.REPORT_SEASON):
        d = legs.filter(pl.col("season") == s)
        p_m = d["p_over_mult"].to_numpy().astype(float)
        y = (rng.random(len(p_m)) < p_m).astype(float)
        p_a = np.clip(d["p_model"].to_numpy().astype(float), rule.MODEL_CLIP, 1 - rule.MODEL_CLIP)
        pr = rule.sigmoid(rule.logit(p_m) + 0.25 * (rule.logit(p_a) - rule.logit(p_m)))
        a = bootstrap_halfwidth(rule.log_loss_vec(p_a, y) - rule.log_loss_vec(p_m, y), d)
        r = bootstrap_halfwidth(rule.log_loss_vec(pr, y) - rule.log_loss_vec(p_m, y), d)
        out[str(s)] = {
            "A0_minus_M": a | power_row(a["binding_half_width"]),
            "pseudoR_w025_minus_M": r | power_row(r["binding_half_width"]),
        }
    return out


def planted_future(frame: pl.DataFrame, y: np.ndarray) -> dict[str, Any]:
    """Synthetic labels flipped on/after a block's first day must not move that block's predictions; flipped inside the training
    window they must (positive control). Two report-season blocks."""
    season = frame["season"].to_numpy()
    dates = frame["game_date"].to_numpy().astype("datetime64[D]")
    probes: list[dict[str, Any]] = []
    for start in (np.datetime64("2025-01-01"), np.datetime64("2025-03-01")):
        end = (start.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
        keep = (dates < end) & np.isin(season, (rule.SELECT_SEASON, rule.REPORT_SEASON))
        sub = frame.filter(pl.Series(keep))
        ys = y[keep]
        dd = dates[keep]
        base = _predict_one_block(sub, ys, start)
        y_fut = ys.copy()
        y_fut[dd >= start] = 1.0 - y_fut[dd >= start]
        fut = _predict_one_block(sub, y_fut, start)
        y_in = ys.copy()
        win = (dd < start) & (dd >= start - np.timedelta64(60, "D"))
        y_in[win] = 1.0 - y_in[win]
        inn = _predict_one_block(sub, y_in, start)
        probes.append(
            {
                "block": str(start),
                "identical_under_future_flip": bool(np.array_equal(base, fut)),
                "moved_under_in_window_flip": bool(not np.array_equal(base, inn)),
                "n": int(len(base)),
            }
        )
    return {
        "probes": probes,
        "ok": all(
            p["identical_under_future_flip"] and p["moved_under_in_window_flip"] for p in probes
        ),
    }


def _predict_one_block(sub: pl.DataFrame, y: np.ndarray, start: np.datetime64) -> np.ndarray:
    season = sub["season"].to_numpy()
    dates = sub["game_date"].to_numpy().astype("datetime64[D]")
    end = (start.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
    X = ev.design_matrix(sub)
    off = rule.logit(sub["p_over_mult"].to_numpy().astype(float))
    tr = np.isin(season, (rule.SELECT_SEASON, rule.REPORT_SEASON)) & (dates < start)
    te = (dates >= start) & (dates < end)
    w = rule.time_weights(dates[tr], start)
    return ev.fit_predict_block(X[tr], y[tr], off[tr], w, X[te], off[te])


def g8_rehearsal(frame: pl.DataFrame, k_null: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rng = np.random.default_rng(SYN_SEED)
    p_m = frame["p_over_mult"].to_numpy().astype(float)
    y = (rng.random(len(p_m)) < p_m).astype(float)
    t0 = time.time()
    res = ev.run_stages(frame, frame, y, k_null, verbose=True, with_secondary=False)
    secs = time.time() - t0
    out: dict[str, Any] = {
        "synthetic_labels": "Bernoulli(p_M), default_rng(20261020), one fixed draw over all legs",
        "k_null": k_null,
        "seconds": secs,
        "stages": {},
    }
    checks: list[dict[str, Any]] = []
    for stage in ("select", "report"):
        r = res[stage]
        a = r["a_R_minus_M"]
        hw = {k: (x["hi"] - x["lo"]) / 2 for k, x in a["by_clustering"].items()}
        bhw = (a["binding"]["hi"] - a["binding"]["lo"]) / 2
        c = r["c_R_minus_PL"]
        out["stages"][stage] = {
            "n": r["n"],
            "clusters": r["clusters"],
            "ll": r["ll"],
            "R_minus_M": a["binding"]["point"],
            "R_minus_M_ci_binding": [a["binding"]["lo"], a["binding"]["hi"]],
            "half_width_by_clustering": hw,
            "binding_half_width": bhw,
            "power": power_row(bhw),
            "b_disagreement_coef_null": r["b_disagreement"]["binding"],
            "R_minus_PL": c["binding"]["point"],
            "placebo_null": r["placebo_null"],
            "scored_blocks": [b["start"] for b in r["walk_forward"]["blocks"] if b["scored"]],
            "skipped_blocks": [b["start"] for b in r["walk_forward"]["blocks"] if not b["scored"]],
        }
        checks.append(
            chk(
                f"G8 {stage} R minus M under an outcome drawn from M (overfit cost)",
                round(a["binding"]["point"], 5),
                f"<= {rule.GATE_MAX_NULL_BIAS}",
                a["binding"]["point"] <= rule.GATE_MAX_NULL_BIAS,
            )
        )
        if stage == "report":
            checks.append(
                chk(
                    "G4 report-season projected 95% half-width of R minus M (binding clustering, real R)",
                    round(bhw, 5),
                    f"<= {rule.GATE_MAX_HALFWIDTH}",
                    bhw <= rule.GATE_MAX_HALFWIDTH,
                )
            )
    return out, checks


def g9_positive_control(frame: pl.DataFrame) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Planted-signal worlds (synthetic outcomes; no real label): y* ~ Bernoulli(sigmoid(logit p_M + w (logit p_A0 - logit p_M))) with
    w = 0.25 (the largest ODDS_HISTORY H2 fit: reb and fg3m) and w = 0.5 (twice that). The oracle is the planted probability itself.
    Report season only (2023 as warm start). Bars were set after this measurement on synthetic labels, before any real outcome was read."""
    pm = frame["p_over_mult"].to_numpy().astype(float)
    pa = np.clip(frame["p_model"].to_numpy().astype(float), rule.MODEL_CLIP, 1 - rule.MODEL_CLIP)
    out: dict[str, Any] = {}
    checks: list[dict[str, Any]] = []
    for w_plant, seed in ((0.25, 7), (0.5, 8)):
        pt = rule.sigmoid(rule.logit(pm) + w_plant * (rule.logit(pa) - rule.logit(pm)))
        y = (np.random.default_rng(seed).random(len(pm)) < pt).astype(float)
        scored, lr, _ = ev.walk_forward(frame, y, rule.REPORT_SEASON, (rule.SELECT_SEASON,))
        d = frame.filter(pl.Series(scored))
        yy, p_r = y[scored], rule.sigmoid(lr[scored])
        ll_m = rule.log_loss_vec(pm[scored], yy)
        cl = {k: rule.cluster_codes(d, k) for k in rule.CLUSTERINGS}
        a = ev.contrast(rule.log_loss_vec(p_r, yy) - ll_m, d, cl)
        dis = {k: rule.disagreement_boot(yy, pm[scored], p_r, cl[k]) for k in rule.CLUSTERINGS}
        dec = a["decisive"]
        b_lo = min(dis[k]["lo"] for k in dec)
        oracle = float((rule.log_loss_vec(pt[scored], yy) - ll_m).mean())
        rec = float(a["binding"]["point"] / oracle) if oracle != 0 else float("nan")
        out[f"w={w_plant}"] = {
            "n": int(scored.sum()),
            "oracle_minus_M": oracle,
            "R_minus_M": a["binding"]["point"],
            "R_minus_M_ci": [a["binding"]["lo"], a["binding"]["hi"]],
            "recovered_share": rec,
            "b2_coef": dis[dec[0]]["coef"],
            "b2_ci_envelope_lo": b_lo,
            "b2_by_clustering": {k: [v["lo"], v["hi"]] for k, v in dis.items()},
            "R_minus_M_reaches_floor": bool(a["binding"]["point"] <= rule.FLOOR),
        }
        if w_plant == 0.25:
            checks.append(
                chk(
                    "G9a planted w=0.25: disagreement coefficient positive, envelope CI lower > 0",
                    round(b_lo, 4),
                    "> 0",
                    b_lo > 0 and dis[dec[0]]["coef"] > 0,
                )
            )
        else:
            checks.append(
                chk(
                    "G9b planted w=0.5: R minus M negative and recovers >= 40% of the oracle gain",
                    round(rec, 3),
                    ">= 0.40",
                    a["binding"]["point"] < 0 and rec >= 0.4,
                )
            )
    return out, checks


# ---------------------------------------------------------------- main


def run_gate(out: Path = OUT, rehearsal: bool = True, k_null: int = 10) -> dict[str, Any]:
    t0 = time.time()
    rule.assert_seasons((rule.SELECT_SEASON, rule.REPORT_SEASON))
    res: dict[str, Any] = {
        "rule": rule.ID,
        "started": time.strftime("%Y-%m-%d %H:%M %Z"),
        "seasons": [rule.SELECT_SEASON, rule.REPORT_SEASON],
        "rule_status": rule.doc_status(rule.DOC.read_text()) if rule.DOC.exists() else "n/a",
        "seeds": SEEDS,
        "git_head": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=rule.ROOT,
            check=False,
        ).stdout.strip(),
    }
    checks: list[dict[str, Any]] = []
    ev._log("pins")
    res["input_sha256"] = input_pins()
    pinned = {s: res["input_sha256"][f"pairs_{s}"] == ev.PINNED_SHA256[s] for s in ev.PINNED_SHA256}
    checks.append(
        chk(
            "inputs: pairs frames equal LINE_CALIBRATION's pinned sha256",
            pinned,
            "both True",
            all(pinned.values()),
        )
    )
    ev._log("legs")
    legs, counts = ev.load_legs(with_labels=False)
    assert "over" not in legs.columns and "y" not in legs.columns
    c123, ch = g1_g2_g3(legs)
    res |= c123
    checks += ch
    g5, ch = g5_lead(counts, legs)
    res["g5_lead"] = g5
    res["raw_odds_history_audit"] = raw_snapshot_audit()
    checks += ch
    ev._log("G6 reproduction")
    g6, ch = g6_reproduction()
    res["g6_a0_reproduction"] = g6
    checks += ch
    ev._log("G4 proxies")
    res["g4_proxies"] = g4_proxy(legs)
    ev._log("G7 features (load_features)")
    with ev.Lock("MARKET_PRIOR_RESIDUAL gate"):
        feats = ev.load_features()
        frame, g7, ch = g7_features(legs, feats)
        res["g7_features"] = g7
        checks += ch
        if rehearsal:
            yfake = (
                np.random.default_rng(SYN_SEED).random(frame.height)
                < frame["p_over_mult"].to_numpy().astype(float)
            ).astype(float)
            ev._log("planted future")
            pf = planted_future(frame, yfake)
            res["planted_future"] = pf
            checks.append(
                chk(
                    "G8 planted future: flipped on/after block start leaves the block identical; flipped in window moves it",
                    pf["probes"],
                    "2 of 2 blocks",
                    pf["ok"],
                )
            )
            ev._log("G9 positive control")
            g9, ch = g9_positive_control(frame)
            res["g9_positive_control"] = g9
            checks += ch
            ev._log("G8 rehearsal")
            g8, ch = g8_rehearsal(frame, k_null)
            res["g8_rehearsal"] = g8
            checks += ch
    res["checks"] = checks
    res["all_pass"] = all(c["pass"] for c in checks)
    res["seconds"] = time.time() - t0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    ev._log(
        f"gate {'PASS' if res['all_pass'] else 'FAIL'} ({sum(c['pass'] for c in checks)}/{len(checks)}) in {res['seconds']:.0f}s -> {out}"
    )
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--no-rehearsal", action="store_true")
    ap.add_argument("--k-null", type=int, default=10)
    a = ap.parse_args(argv)
    run_gate(a.out, rehearsal=not a.no_rehearsal, k_null=a.k_null)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
