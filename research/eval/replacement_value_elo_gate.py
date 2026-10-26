"""Label-free minimum-data gate for REPLACEMENT_VALUE_ELO.

DRAFT rule: docs/prereg/REPLACEMENT_VALUE_ELO.md.

Reads NO game outcome: games are loaded without points, the comparator's probabilities come from
the stored production OOF file (``data/injury_elo_v2/oof_predictions.parquet``, predictions only),
and every power number is computed from those probabilities and the as-of features. Season 2025 is
never loaded (``season <= 2024`` in every SQL; schedule files of later seasons are not read).
The one same-game quantity read is box MINUTES of season 2022 (the warm-up season, never selected or
reported on), for the absorption-validity descriptor only.

Maintainer command (read-only, about 2-4 min on the Mac; prints progress per stage)::

    uv run python -m research.eval.replacement_value_elo_gate \\
        --db nba.duckdb --history-db data/history/nba_history.duckdb \\
        --schedule-dir data/schedule --history-schedule-dir data/history/schedule \\
        --oof data/injury_elo_v2/oof_predictions.parquet \\
        --out reports/prereg_replacement_value_elo/gate.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from scipy.stats import norm

import nba.features.game_tipoff as game_tipoff
from nba.models.injury_elo import build_injury_features, load_stats_frame
from research.models.replacement_value_elo import (
    ARMS,
    FAMILY_ARMS,
    ReplacementConfig,
    build_replacement_features,
)

MAX_SEASON = 2024
SELECT_SEASON = 2023
REPORT_SEASON = 2024
FLOOR_LL = -0.002
FLOOR_BRIER = -0.0009
Z_TWO_SIDED = 1.959964
Z_BH_RANK1_OF4 = float(norm.ppf(1 - 0.05 / 4))  # one-sided BH, a single passer in a family of 4
MIN_GAMES = 1200
MIN_SLICE = 300
MIN_SIGNAL = 150
MAX_FALLBACK = 0.02
MIN_POWER = 0.80
CLIP = (0.02, 0.98)
REPORT_SQL = (
    "SELECT a.game_id, a.player_id, lower(a.status) AS status, "
    "CAST(a.as_of AS TIMESTAMP) AS as_of, "
    "g.game_date FROM player_availability a JOIN games g ON g.game_id = a.game_id "
    "WHERE a.source = 'nba_official_report' AND a.player_id IS NOT NULL AND g.season <= ?"
)
GAMES_SQL = (
    "SELECT game_id, game_date, season, home_team, away_team FROM games "
    "WHERE season <= ? AND season >= ? AND home_pts IS NOT NULL ORDER BY game_date, game_id"
)


def _f(x: Any) -> float:
    return float(x)


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


# --------------------------------------------------------------------------
# Power and oracle math (label-free)
# --------------------------------------------------------------------------


def floor_power(
    floor: float, n: int, z_crit: float = Z_TWO_SIDED, kind: str = "ll", pq: float = 0.22
) -> dict[str, float]:
    """Self-consistent power when the candidate IS the truth and its expected gain equals ``floor``.

    Log loss: per-game gain mean = -KL, variance = p(1-p) delta^2 ~= 2 KL, so SE = sqrt(2|F|/n).
    Brier: mean = -E[(dp)^2], variance ~= 4 p(1-p) (dp)^2, so SE = sqrt(4 pq |F| / n).
    Pass = point <= floor AND z >= z_crit; power = P(point <= min(floor, -z_crit * SE))."""
    f = abs(floor)
    se = math.sqrt(2.0 * f / n) if kind == "ll" else math.sqrt(4.0 * pq * f / n)
    thr = min(-f, -z_crit * se)
    power = float(norm.cdf((thr + f) / se))
    return {"floor": floor, "n": n, "se": se, "half_width": Z_TWO_SIDED * se, "power": power}


def oracle_gain(p_a: np.ndarray, p_b: np.ndarray) -> dict[str, float]:
    """Expected paired gain (B minus A) if B were the true probability, with its SE.

    Log loss: mean of -KL(B||A); per-game variance p_b(1-p_b)(logit b - logit a)^2.
    Brier: mean of -(p_b - p_a)^2; per-game variance 4 p_b(1-p_b)(p_b - p_a)^2."""
    pa = np.clip(np.asarray(p_a, float), 1e-9, 1 - 1e-9)
    pb = np.clip(np.asarray(p_b, float), 1e-9, 1 - 1e-9)
    n = len(pa)
    kl = pb * np.log(pb / pa) + (1 - pb) * np.log((1 - pb) / (1 - pa))
    dl = np.log(pb / (1 - pb)) - np.log(pa / (1 - pa))
    v_ll = pb * (1 - pb) * dl**2
    d2 = (pb - pa) ** 2
    v_b = 4 * pb * (1 - pb) * d2
    se_ll = float(math.sqrt(v_ll.sum()) / n) if n else float("nan")
    se_b = float(math.sqrt(v_b.sum()) / n) if n else float("nan")
    m_ll = -float(kl.mean()) if n else float("nan")
    m_b = -float(d2.mean()) if n else float("nan")
    return {
        "n": n,
        "mean_gain_ll": m_ll,
        "se_ll": se_ll,
        "z_ll": (-m_ll / se_ll) if se_ll > 0 else 0.0,
        "mean_gain_brier": m_b,
        "se_brier": se_b,
        "z_brier": (-m_b / se_b) if se_b > 0 else 0.0,
        "rms_logit_change": float(math.sqrt((dl**2).mean())) if n else float("nan"),
    }


def power_at(mean: float, se: float, floor: float, z_crit: float) -> float:
    if not se or se <= 0 or not math.isfinite(se):
        return 0.0
    thr = min(floor, -z_crit * se)
    return float(norm.cdf((thr - mean) / se))


def recover_block_coefs(s: np.ndarray, x: np.ndarray, blocks: np.ndarray) -> dict[str, Any]:
    """Least squares per month block of the stored injury shift on the recomputed features.

    Exact recovery (residual ~ 0) proves the recomputed production features equal the ones the
    stored OOF was made with. Uses predictions only, never an outcome."""
    coefs: dict[str, np.ndarray] = {}
    resid = np.zeros(len(s))
    for b in dict.fromkeys(blocks.tolist()):
        m = blocks == b
        c, *_ = np.linalg.lstsq(x[m], s[m], rcond=None)
        coefs[str(b)] = c
        resid[m] = s[m] - x[m] @ c
    return {
        "coefs": coefs,
        "max_abs_resid": float(np.abs(resid).max()) if len(resid) else 0.0,
        "rms_resid": float(math.sqrt((resid**2).mean())) if len(resid) else 0.0,
        "resid": resid,
    }


def _logit(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(p / (1 - p)), dtype=float)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return np.asarray(1 / (1 + np.exp(-z)), dtype=float)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def _filtered_schedule_dir(src: Path, max_season: int) -> Path:
    """Temp dir with symlinks to schedule files of seasons <= max_season only."""
    tmp = Path(tempfile.mkdtemp(prefix="rve_sched_"))
    for f in sorted(src.glob("raw_*.parquet")):
        fs = game_tipoff._file_season(f)
        if fs is not None and fs <= max_season:
            (tmp / f.name).symlink_to(f.resolve())
    return tmp


def _load(
    con: duckdb.DuckDBPyConnection, sched: Path, min_season: int
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    games = con.execute(GAMES_SQL, [MAX_SEASON, min_season]).pl()
    stats = load_stats_frame(con, MAX_SEASON)
    rows = con.execute(REPORT_SQL, [MAX_SEASON]).pl()
    rows = rows.with_columns(pl.col("as_of").cast(pl.Datetime("us")))
    rows = rows.filter(pl.col("game_id").is_in(games["game_id"].to_list()))
    tips = game_tipoff.build_game_tipoff(sched, max_season=MAX_SEASON)
    rows = game_tipoff.attach_real_tips(rows, tips=tips)
    return games, stats, rows


def _fingerprint(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    q = {
        "games": "SELECT count(*), bit_xor(hash(game_id, game_date, season, home_team, away_team)) "
        "FROM games WHERE season <= 2024",
        "player_game_stats": "SELECT count(*), bit_xor(hash(s.game_id, s.player_id, s.team_id, "
        "s.minutes)) FROM player_game_stats s JOIN games g USING (game_id) WHERE g.season <= 2024",
        "player_availability": "SELECT count(*), bit_xor(hash(a.game_id, a.player_id, a.status, "
        "a.as_of)) FROM player_availability a JOIN games g USING (game_id) WHERE g.season <= 2024",
    }
    out: dict[str, Any] = {}
    for k, sql in q.items():
        n, h = con.execute(sql).fetchone() or (0, 0)
        out[k] = {"rows": int(n), "xor_hash": str(h)}
    out["sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    return out


def _counts(frame: pl.DataFrame) -> dict[str, Any]:
    return {
        "games": frame.height,
        "with_report": int(frame["has_report"].sum()),
        "rot_out_ge1_game": int((frame["n_rot_out"] >= 1).sum()),
        "rot_out_ge2_game": int((frame["n_rot_out"] >= 2).sum()),
        "rot_out_ge2_one_team": int(
            ((frame["n_rot_out_home"] >= 2) | (frame["n_rot_out_away"] >= 2)).sum()
        ),
        "rot_out_ge3_game": int((frame["n_rot_out"] >= 3).sum()),
        "affected_any_flag": int(((frame["d_out_a0"].abs() + frame["d_doubt_a0"].abs()) > 0).sum()),
    }


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------


def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    t0 = time.time()
    cfg = ReplacementConfig()
    res: dict[str, Any] = {"rule": "REPLACEMENT_VALUE_ELO (DRAFT)", "max_season": MAX_SEASON}
    sched = _filtered_schedule_dir(Path(args.schedule_dir), MAX_SEASON)
    game_tipoff.SCHEDULE_DIR = sched  # production parity path re-attaches tips from here
    con = duckdb.connect(args.db, read_only=True)
    try:
        res["data_fingerprint"] = _fingerprint(con)
        mf = sorted(Path(args.manifest_dir).glob("*.json")) if args.manifest_dir else []
        res["latest_manifest"] = mf[-1].name if mf else None
        games, stats, rows = _load(con, sched, 2022)
        assert int(games["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
        _log(f"loaded {games.height} games, {stats.height} box rows, {rows.height} report rows", t0)
        pos = dict(
            con.execute("SELECT player_id, position FROM players_static").fetchall()  # noqa: S608
        )
        absorb: list[tuple[str, int, int, float, float, float]] = []
        feats, diag = build_replacement_features(games, stats, rows, cfg, pos, absorb)
        _log("features built", t0)
        prod = build_injury_features(
            con, games, stats, cfg.base, report_rows=rows.drop("tip_et"), with_oracle=False
        )
        _log("production features built (parity)", t0)
        stats22 = stats.filter(
            pl.col("game_id").is_in(games.filter(pl.col("season") == 2022)["game_id"].to_list())
        )
    finally:
        con.close()

    j = prod.select(["game_id", "d_out", "d_doubt", "n_rot_out"]).join(
        feats, on="game_id", suffix="_rve"
    )
    res["parity_a0_vs_production"] = {
        "n": j.height,
        "max_abs_d_out": _f((j["d_out"] - j["d_out_a0"]).abs().max() or 0.0),
        "max_abs_d_doubt": _f((j["d_doubt"] - j["d_doubt_a0"]).abs().max() or 0.0),
        "n_rot_out_equal": bool((j["n_rot_out"] == j["n_rot_out_rve"]).all()),
    }

    frame = games.join(feats, on="game_id").sort(["game_date", "game_id"])
    dg = diag.join(games.select(["game_id", "season"]), on="game_id")

    # ---- counts, coverage, signal
    res["seasons"] = {}
    first_2023 = frame.filter(pl.col("season") == SELECT_SEASON)["game_date"].min()
    for s in (2022, 2023, 2024):
        fs = frame.filter(pl.col("season") == s)
        d_s = dg.filter((pl.col("season") == s) & (pl.col("n_flagged") > 0))
        cov = {
            "flagged_team_games": d_s.height,
            "fallback_share": _f(d_s["fallback"].mean()) if d_s.height else 0.0,
            "median_avail": _f(d_s["n_avail"].median() or 0) if d_s.height else 0.0,
            "ladder_any_capped_share": _f((d_s["n_capped"] > 0).mean()) if d_s.height else 0.0,
            "ladder_overflow_share": _f((d_s["overflow_min"] > 1e-9).mean()) if d_s.height else 0.0,
            "mean_r_pool_over_league": _f((d_s["r_pool"] / d_s["r_league"]).mean())
            if d_s.height
            else 0.0,
            "mean_r_ladder_over_league": _f((d_s["r_ladder"] / d_s["r_league"]).mean())
            if d_s.height
            else 0.0,
        }
        res["seasons"][str(s)] = {"counts": _counts(fs), "pool": cov}
    signal = {}
    pre = frame.filter(pl.col("game_date") < first_2023)
    for arm in ARMS:
        signal[arm] = int(((pre[f"d_out_{arm}"].abs() + pre[f"d_doubt_{arm}"].abs()) > 0).sum())
    res["signal_games_before_first_select_block"] = signal

    # ---- feature divergence
    div: dict[str, Any] = {}
    for s in (2023, 2024):
        fs = frame.filter(pl.col("season") == s)
        a = fs["d_out_a0"].to_numpy()
        div[str(s)] = {}
        for arm in ARMS[1:]:
            b = fs[f"d_out_{arm}"].to_numpy()
            ok = (np.abs(a) + np.abs(b)) > 0
            corr = float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 2 else float("nan")
            div[str(s)][arm] = {
                "corr_d_out_with_a0": corr,
                "orthogonal_share": float(1 - corr**2) if math.isfinite(corr) else float("nan"),
                "sd_ratio": float(b[ok].std() / a[ok].std()) if ok.sum() > 2 else float("nan"),
            }
    res["divergence"] = div
    _log("counts and divergence done", t0)

    # ---- stored production OOF: reproduction and oracle
    oof = pl.read_parquet(args.oof).filter(pl.col("season") <= MAX_SEASON)
    pi = oof.filter(pl.col("model") == "rung0_injury_elo").select(
        ["game_id", pl.col("p").alias("p_inj")]
    )
    pm = oof.filter(pl.col("model") == "rung0_mov_elo").select(
        ["game_id", pl.col("p").alias("p_mov")]
    )
    fo = frame.join(pi, on="game_id").join(pm, on="game_id").sort(["game_date", "game_id"])
    p_inj = fo["p_inj"].to_numpy()
    p_mov = fo["p_mov"].to_numpy()
    unclipped = (p_inj > CLIP[0] + 1e-12) & (p_inj < CLIP[1] - 1e-12)
    s_a = _logit(np.clip(p_inj, 1e-9, 1 - 1e-9)) - _logit(np.clip(p_mov, 1e-9, 1 - 1e-9))
    xa = fo.select(["d_out_a0", "d_doubt_a0"]).to_numpy()
    blocks = np.array([f"{d.year}-{d.month:02d}" for d in fo["game_date"].to_list()])
    rec = recover_block_coefs(s_a[unclipped], xa[unclipped], blocks[unclipped])
    res["a0_reproduction"] = {
        "n_scored_oof": fo.height,
        "n_unclipped": int(unclipped.sum()),
        "max_abs_resid_logit": rec["max_abs_resid"],
        "rms_resid_logit": rec["rms_resid"],
        "share_resid_gt_1e-6": float((np.abs(rec["resid"]) > 1e-6).mean()),
        "coefs_by_block": {k: [float(c) for c in v] for k, v in rec["coefs"].items()},
    }
    _log("A0 reproduction done", t0)

    pq_mean = {}
    res["oracle"] = {}
    for s in (2023, 2024):
        m_s = (fo["season"] == s).to_numpy()
        pa = p_inj[m_s]
        pq_mean[s] = float((pa * (1 - pa)).mean())
        u = m_s & unclipped
        bg, *_ = np.linalg.lstsq(xa[u], s_a[u], rcond=None)
        res["oracle"][str(s)] = {"b_g_season": [float(c) for c in bg]}
        slices = {
            "all": np.ones(m_s.sum(), bool),
            "rot_out_ge2_game": (fo["n_rot_out"].to_numpy()[m_s] >= 2),
            "rot_out_ge2_one_team": (
                (fo["n_rot_out_home"].to_numpy()[m_s] >= 2)
                | (fo["n_rot_out_away"].to_numpy()[m_s] >= 2)
            ),
        }
        for arm in ARMS[1:]:
            xb = fo.select([f"d_out_{arm}", f"d_doubt_{arm}"]).to_numpy()
            mmat, *_ = np.linalg.lstsq(xa[u], xb[u], rcond=None)  # A-fit of X_b theta = M theta
            try:
                theta = np.linalg.solve(mmat, bg)
            except np.linalg.LinAlgError:
                theta = np.full(2, np.nan)
            s_b = xb[m_s] @ theta
            p_b = np.clip(_sigmoid(_logit(np.clip(pa, 1e-9, 1 - 1e-9)) - s_a[m_s] + s_b), *CLIP)
            arm_res: dict[str, Any] = {"theta": [float(t) for t in theta]}
            for name, msk in slices.items():
                g = oracle_gain(pa[msk], p_b[msk])
                g["power_ll_two_sided"] = power_at(
                    g["mean_gain_ll"], g["se_ll"], FLOOR_LL, Z_TWO_SIDED
                )
                g["power_brier_two_sided"] = power_at(
                    g["mean_gain_brier"], g["se_brier"], FLOOR_BRIER, Z_TWO_SIDED
                )
                g["reaches_floor_ll"] = bool(g["mean_gain_ll"] <= FLOOR_LL)
                arm_res[name] = g
            res["oracle"][str(s)][arm] = arm_res
    _log("oracle done", t0)

    # ---- power at the floor (self-consistent, model-free)
    fp: dict[str, Any] = {}
    n23 = res["seasons"]["2023"]["counts"]["games"]
    n24 = res["seasons"]["2024"]["counts"]["games"]
    for label, n, pq in (
        ("2023", n23, pq_mean[2023]),
        ("2024", n24, pq_mean[2024]),
        ("pooled_2023_2024", n23 + n24, (pq_mean[2023] + pq_mean[2024]) / 2),
    ):
        fp[label] = {
            "ll_two_sided": floor_power(FLOOR_LL, n, Z_TWO_SIDED, "ll"),
            "ll_bh_rank1_of4": floor_power(FLOOR_LL, n, Z_BH_RANK1_OF4, "ll"),
            "brier_two_sided": floor_power(FLOOR_BRIER, n, Z_TWO_SIDED, "brier", pq),
        }
    p_both = fp["2023"]["ll_bh_rank1_of4"]["power"] * fp["2024"]["ll_two_sided"]["power"]
    fp["select_then_report_ll"] = p_both
    kl_needed = 2 * (Z_TWO_SIDED + norm.ppf(MIN_POWER)) ** 2
    fp["ll_gain_needed_for_80pct_one_season"] = {
        "2023": -kl_needed / n23,
        "2024": -kl_needed / n24,
        "pooled_2023_2024": -kl_needed / (n23 + n24),
    }
    res["floor_power"] = fp

    # ---- absorption validity (2022 minutes only; descriptive)
    ab = pl.DataFrame(
        absorb,
        schema={
            "game_id": pl.Utf8,
            "team_id": pl.Int64,
            "player_id": pl.Int64,
            "mins_asof": pl.Float64,
            "pred_pool": pl.Float64,
            "pred_ladder": pl.Float64,
        },
        orient="row",
    ).join(games.select(["game_id", "season"]), on="game_id")
    ab22 = (
        ab.filter(pl.col("season") == 2022)
        .join(
            stats22.select(["game_id", "player_id", pl.col("minutes").alias("actual")]),
            on=["game_id", "player_id"],
            how="left",
        )
        .with_columns(pl.col("actual").fill_null(0.0))
    )
    ab22 = ab22.with_columns((pl.col("actual") - pl.col("mins_asof")).alias("realized"))
    absv: dict[str, Any] = {"n_player_rows": ab22.height}
    if ab22.height > 2:
        r = ab22["realized"].to_numpy()
        for k in ("pred_pool", "pred_ladder"):
            absv[f"corr_{k}"] = float(np.corrcoef(ab22[k].to_numpy(), r)[0, 1])
        ab22 = ab22.with_columns(
            pl.col("mins_asof")
            .rank("ordinal", descending=True)
            .over(["game_id", "team_id"])
            .alias("rk")
        )
        top = ab22.filter(pl.col("rk") <= 5)
        tot_r = _f(ab22["realized"].clip(lower_bound=0).sum())
        absv["share_realized_gain_top5"] = (
            _f(top["realized"].clip(lower_bound=0).sum()) / tot_r if tot_r else float("nan")
        )
        absv["share_pred_pool_top5"] = _f(top["pred_pool"].sum()) / _f(ab22["pred_pool"].sum())
        absv["share_pred_ladder_top5"] = _f(top["pred_ladder"].sum()) / _f(
            ab22["pred_ladder"].sum()
        )
    res["absorption_validity_2022"] = absv

    # ---- history capture audit (counts only)
    if args.history_db:
        hres: dict[str, Any] = {}
        hsched = _filtered_schedule_dir(Path(args.history_schedule_dir), 2021)
        hcon = duckdb.connect(args.history_db, read_only=True)
        try:
            hres["seasons_in_db"] = dict(
                hcon.execute("SELECT season, count(*) FROM games GROUP BY 1 ORDER BY 1").fetchall()
            )
            hg, hs, hr = _load(hcon, hsched, 2019)
            _log(f"history loaded {hg.height} games, {hr.height} report rows", t0)
            hf, hd = build_replacement_features(hg, hs, hr, cfg, None)
            hf = hg.join(hf, on="game_id")
            for s in sorted(set(hf["season"].to_list())):
                c = _counts(hf.filter(pl.col("season") == s))
                hds = hd.join(hg.select(["game_id", "season"]), on="game_id").filter(
                    (pl.col("season") == s) & (pl.col("n_flagged") > 0)
                )
                c["fallback_share"] = _f(hds["fallback"].mean()) if hds.height else 0.0
                hres[str(s)] = c
            hres["real_tip_coverage"] = {
                "games_with_tip": int(
                    hg.join(game_tipoff.build_game_tipoff(hsched), on="game_id").height
                ),
                "games": hg.height,
            }
        finally:
            hcon.close()
        res["history_audit"] = hres
        _log("history audit done", t0)

    res["gate"] = _decide(res)
    return res


def _decide(res: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in ("2023", "2024"):
        c = res["seasons"][s]["counts"]
        out.append(
            {
                "check": f"G1 games {s} >= {MIN_GAMES}",
                "value": c["games"],
                "pass": c["games"] >= MIN_GAMES,
            }
        )
        out.append(
            {"check": f"G1 slice rot_out>=2 (game) {s} >= {MIN_SLICE}",
             "value": c["rot_out_ge2_game"],
             "pass": c["rot_out_ge2_game"] >= MIN_SLICE}
        )  # fmt: skip
        fb = res["seasons"][s]["pool"]["fallback_share"]
        out.append(
            {
                "check": f"G3 pool fallback {s} <= {MAX_FALLBACK}",
                "value": fb,
                "pass": fb <= MAX_FALLBACK,
            }
        )
    sig = res["signal_games_before_first_select_block"]
    out.append(
        {"check": f"G2 signal games before first 2023 block >= {MIN_SIGNAL} (every arm)",
         "value": min(sig.values()), "pass": min(sig.values()) >= MIN_SIGNAL}
    )  # fmt: skip
    par = res["parity_a0_vs_production"]
    ok = par["max_abs_d_out"] < 1e-9 and par["max_abs_d_doubt"] < 1e-9 and par["n_rot_out_equal"]
    out.append({"check": "G4a A0 recomputation == production features", "value": par, "pass": ok})
    rep = res["a0_reproduction"]
    out.append(
        {"check": "G4b stored OOF reproduced (max |resid| <= 1e-6 logit)",
         "value": rep["max_abs_resid_logit"],
         "pass": rep["max_abs_resid_logit"] <= 1e-6}
    )  # fmt: skip
    fp = res["floor_power"]
    for s in ("2023", "2024"):
        pw = fp[s]["ll_two_sided"]["power"]
        out.append(
            {
                "check": f"G5 power at the log-loss floor {s} >= {MIN_POWER}",
                "value": pw,
                "pass": pw >= MIN_POWER,
            }
        )
    for s in ("2023", "2024"):
        # a perfectly true candidate: is it even detectable? (a distance bound, not evidence:
        # the placebo's bound is as large, because it measures how far the forecasts move)
        best = max(res["oracle"][s][a]["all"]["power_ll_two_sided"] for a in FAMILY_ARMS)
        out.append(
            {"check": f"G6 power if the best family arm were exactly true {s} >= {MIN_POWER}",
             "value": best, "pass": best >= MIN_POWER}
        )  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="REPLACEMENT_VALUE_ELO label-free gate")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--history-db", default="")
    ap.add_argument("--schedule-dir", default="data/schedule")
    ap.add_argument("--history-schedule-dir", default="data/history/schedule")
    ap.add_argument("--oof", default="data/injury_elo_v2/oof_predictions.parquet")
    ap.add_argument("--manifest-dir", default="data/manifests")
    ap.add_argument("--out", default="reports/prereg_replacement_value_elo/gate.json")
    args = ap.parse_args(argv)
    res = run_gate(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, default=str))
    for g in res["gate"]:
        print(("PASS " if g["pass"] else "FAIL ") + g["check"] + f": {g['value']}")
    print("sha256", hashlib.sha256(out.read_bytes()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
