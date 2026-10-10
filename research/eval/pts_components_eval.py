# ruff: noqa: E501
"""PTS_COMPONENTS (docs/prereg/PTS_COMPONENTS.md, frozen sha256 7e6fd8f0, commit 918e03c).

    M="-m research.eval.pts_components_eval"
    .venv/bin/python $M gate     # checks 1, 2, 3, 5 (+ nba/ untouched); writes reports/prereg_pts_components/
    .venv/bin/python $M smoke    # one month block, A0 / A1 / A2 scored, nothing written but a scratch cache
    .venv/bin/python $M arms     # A0 (+ check 4) A1 A2 A3 A4 + fg3m secondary; heavy.lock held
    .venv/bin/python $M report   # reports/prereg_pts_components.md from the JSON

Research module only: ``nba/`` is untouched.  Read-only on ``nba.duckdb``; every SQL carries
``season <= 2024`` and the loaded frames are asserted; season 2025 is never loaded.  CPU only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    boot_p_value,
    crps_from_quantiles,
    load_inputs,
    month_blocks,
)
from nba.eval.lower_tail_eval import EPS, attach_realised, randomized_pit
from nba.props.context_residual import (
    CRPS_TAUS,
    ContextResidualConfig,
    ContextResidualModel,
    build_features,
    flagged_from_availability,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import raw_quantiles
from research.eval.f11_lineup_context import (
    _bucket_minutes,
    _ci,
    _jd,
    _write_json,
    frozen_sha256,
    git_sha,
    heavy_lock,
    wait_for_replay,
)
from research.props.pts_components import (
    ATT,
    COMP_FEATURES,
    KEYS,
    MAX_PTS,
    RATE_COLS,
    ComponentModel,
    build_component_features,
    component_names,
    identity_ok,
    permute_targets,
    pmf_to_quantiles,
    points_pmf,
    summary,
)

ROOT = Path(__file__).resolve().parents[2]
DOC = Path("docs/prereg/PTS_COMPONENTS.md")
OUT_DIR = Path("reports/prereg_pts_components")
REPORT_MD = Path("reports/prereg_pts_components.md")
CACHE_DIR = Path("data/ops/pts_components_cache")
LOWER_TAIL_JSON = Path("reports/lower_tail/candidates.json")
FROZEN_PREFIX = "7e6fd8f0"
SELECT_SEASON, REPORT_SEASON = 2023, 2024
SEEDS = {"model": 0, "bootstrap": 0, "pit": 0, "placebo": 0}
N_BOOT = 2000
ARMS: tuple[str, ...] = ("A0", "A1", "A2", "A3", "A4")
TLL_THRESHOLDS: tuple[int, ...] = (20, 25, 30)

# --- frozen thresholds (the doc)
FLOOR = -0.010
MAX_BIAS = 0.5
COV_LO, COV_HI = 0.75, 0.85
PIT_TOL = 0.02
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
A4_FLOOR = -0.005
A3_FLOOR = -0.003
NONNULL_MIN = 0.999
G2_MAX_GAP_PP = 2.0
ENUM_TOL = 1e-9

UNSTATED: list[str] = [
    "no ContextResidualConfig.pts_components flag is added (nba/ is untouched; the layering test forbids "
    "nba -> research): 'flag off byte-identical' is checked as nba/props/context_residual.py unchanged vs "
    "HEAD plus the A0 reproduction to 1e-6",
    "attempt mean = recency m10 + gradient-boosted residual (production learner, hyper-parameters, seed 0) "
    "floored at 0.05; every attempt head uses the full feature list (production pts + the 27 component columns)",
    "NegBin size r_k, activity loadings beta_k and Beta-Binomial concentrations kappa_k are fit on the "
    "calibration window (the newest 15% of the training window, rows the heads never saw: production's "
    "split_calibration), not on the head-fit rows",
    "beta_k and r_k jointly by marginal likelihood with z integrated by 15-node Gauss-Hermite quadrature "
    "(L-BFGS-B, 60 iterations, start beta=0.15, r from the independent fit); the factor is mean-one, "
    "exp(beta z - beta^2/2), so the head mean stays mu_k.  CHOSEN AFTER the one-block smoke (Oct 2023, n=1062) "
    "showed the literal exp(beta z) inflates the pmf mean by exp(beta^2/2), about +0.75 points (A1 bias -1.87 "
    "there); the choice rests on that arithmetic and on the doc calling mu_k the head's mean, not on any "
    "comparison of CRPS across variants",
    "A2 (independent sum) reuses the A1 heads; r_k is its own 1-D MLE with beta = 0",
    "makes given attempts: p = the shrunk prior-40 rate itself (no learner on the make rate); kappa_k by 1-D MLE",
    "prior-40 window = the player's previous 40 PLAYED games (shifted; tonight excluded); league rate = all "
    "rows on strictly earlier dates (fixed constants only on the first date); pseudo-count 50 attempts",
    "attempt mass above the caps 40 / 25 / 30 is folded onto the cap cell (the grid sums to one exactly); "
    "fta exceeds 30 in a handful of rows, whose labels are untouched",
    "sd10 for attempts: 1.0 when degenerate (production's per-stat default std has no attempt analogue)",
    "points quantile grid = smallest k with CDF(k) >= tau on production's 199-tau grid; every arm is scored by "
    "the same CRPS-from-quantiles, randomized-PIT and mean-of-grid bias code on integer quantile grids",
    "A3 appends all 27 component columns (24 attempt columns + 3 shrunk make rates) to production's pts list",
    "A4 permutes the six component targets JOINTLY within player over the whole 2022-2024 frame (seed 0); "
    "the evaluation label y is real points",
    "slices: scoring tier by m10_pts (<10, 10-17, 17-24, 24+); three-point share = 3 m10_fg3m / max(m10_pts, 0.1) in "
    "per-season terciles; starter = starter10 >= 0.5; cold start n_prior < 20; slice CIs only for n >= 300",
    "pass rule evaluated in each season and required in BOTH (2023 selects, 2024 confirms); 'A1 beats A4 by <= -0.005' = "
    "crps(A1) - crps(A4) point <= -0.005 on all rows (likewise A3 at -0.003); the A1 vs A2 line reports "
    "shared_factor_did_nothing = crps(A2) <= crps(A1)",
    "one primary test per season (pts only): the family has size 1, so p_bh = p",
    "upper PIT = share with randomized PIT <= 0.80 and <= 0.90; cov80 = share with PIT in (0.10, 0.90]; tll = mean log "
    "loss of P(y >= n) at n = 20, 25, 30 reported separately",
    "check 2 audits the 27 component columns plus both make-rate inputs on the scored rows (n_prior >= 5) by realised-minutes "
    "bucket (<5, 5-10, 10-20, >20); max gap over buckets and columns <= 2 pp",
    "check 3 plants edits to the box columns (+9 on every shot column for one mid-season day; +5 for every game dated "
    ">= 2024-01-15) and requires earlier rows' component features to be unchanged and later rows to move",
    "check 5 at the gate uses fixed synthetic parameters on all 2023-2024 scored rows (no fitted model); the arms stage "
    "asserts the same bound for every fitted grid",
    "secondary fg3m: the fg3m marginal of A1 versus production's fg3m arm (same rows, same integer support), descriptive",
    "rows failing check 1 would be dropped from ALL arms before any fit (counted; 0 on the real data)",
]


# --------------------------------------------------------------------------- inputs


def load_box(db_path: str = "nba.duckdb") -> pl.DataFrame:
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.execute(
            "SELECT s.game_id, s.player_id, s.fgm, s.fga, s.fg3a, s.ftm, s.fta "
            "FROM player_game_stats s JOIN games g USING (game_id) "
            f"WHERE g.season <= {MAX_SEASON}"
        ).pl()
    finally:
        con.close()


def load_study(
    db_path: str = "nba.duckdb",
    elo_config: str = "configs/mov_elo_tuned.yaml",
    config_path: str = "configs/context_residual.yaml",
) -> dict[str, Any]:
    elo = {k: float(v) for k, v in yaml.safe_load(Path(elo_config).read_text()).items()}
    cfg = ContextResidualConfig(**yaml.safe_load(Path(config_path).read_text()).get("model", {}))
    games, pgs, static, avail = load_inputs(db_path, None)
    assert int(games["season"].max()) <= MAX_SEASON, "frozen holdout (2025) must not be loaded"  # type: ignore[arg-type]
    box = load_box(db_path)
    pgs_ext = pgs.join(box, on=KEYS, how="left")
    assert pgs_ext.height == pgs.height
    flagged, _ = flagged_from_availability(avail, games, cfg.report)
    feats = attach_realised(build_features(games, pgs, static, flagged, elo), games, pgs)
    assert int(feats["season"].max()) <= MAX_SEASON  # type: ignore[arg-type]
    comp = build_component_features(games, pgs_ext, flagged)
    frame = feats.join(comp, on=KEYS, how="left")
    assert frame.height == feats.height
    ok = identity_ok(pgs_ext.join(feats.select(KEYS), on=KEYS, how="semi"))
    n_bad = int((~ok).sum())
    return {
        "games": games,
        "pgs_ext": pgs_ext,
        "flagged": flagged,
        "cfg": cfg,
        "feats": feats,
        "frame": frame,
        "n_identity_fail_played": n_bad,
    }


def drop_failures(inp: dict[str, Any]) -> pl.DataFrame:
    """Rows failing check 1 are dropped from ALL arms (0 on the real data)."""
    frame: pl.DataFrame = inp["frame"]
    pe: pl.DataFrame = inp["pgs_ext"]
    bad = pe.filter(~identity_ok(pe)).select(KEYS)
    return frame.join(bad, on=KEYS, how="anti") if bad.height else frame


# --------------------------------------------------------------------------- blocks and arms


def _blocks(sf: pl.DataFrame, seasons: tuple[int, ...], max_blocks: int | None):  # type: ignore[no-untyped-def]
    if max(seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    test_all = sf.filter(pl.col("season").is_in(list(seasons)))
    for b, (start, end) in enumerate(month_blocks(test_all)):
        if max_blocks is not None and b >= max_blocks:
            break
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < 1500:
            continue
        yield start, block, train


META_COLS: tuple[str, ...] = (
    "game_id",
    "player_id",
    "team_id",
    "season",
    "n_prior",
    "starter10",
    "m10_pts",
    "m10_fg3m",
)


def _cached(path: Path) -> dict[str, np.ndarray] | None:
    if path.exists():
        z = np.load(path)
        return {k: z[k] for k in z.files}
    return None


def _save(path: Path, **arrs: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrs)  # type: ignore[arg-type]


def collect_prod(
    frame: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    tag: str,
    cache: Path,
    seasons: tuple[int, ...] = TEST_SEASONS,
    max_blocks: int | None = None,
) -> dict[str, Any]:
    """Production-style walk-forward (month blocks, 2022 warm-up), integer grid (A0, A3, fg3m)."""
    sf = stat_frame(frame, stat)
    qs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    for start, block, train in _blocks(sf, seasons, max_blocks):
        p = cache / f"{tag}_{start}.npz"
        c = _cached(p)
        if c is None:
            t0 = time.time()
            model = ContextResidualModel(stat, cfg, names).fit(train)
            cen, s = model.predict_components(block)
            _, cal_df = split_calibration(train, cfg)
            assert model.mean_head is not None and model.z_sorted is not None
            xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
            mu = np.asarray(model.mean_head.predict(xc))
            sc = model._scale(xc)
            z = (cal_df["resid"].to_numpy() - mu) / sc
            q = to_integer_support(raw_quantiles(cen, s, z, CRPS_TAUS)).astype(np.int16)
            _save(p, qi=q)
            print(
                f"  {tag} block {start} done ({time.time() - t0:.0f}s, n={block.height})",
                flush=True,
            )
            c = {"qi": q}
        qs.append(c["qi"])
        ys.append(block["y"].to_numpy())
        metas.append(block.select([c_ for c_ in META_COLS if c_ in block.columns]))
    return {"qi": np.vstack(qs), "y": np.concatenate(ys), "meta": pl.concat(metas)}


def collect_comp(
    frame: pl.DataFrame,
    cfg: ContextResidualConfig,
    cache: Path,
    placebo: bool,
    seasons: tuple[int, ...] = TEST_SEASONS,
    max_blocks: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Component arms. ``placebo=False``: A1 (shared factor) and A2 (independent) from one set of heads,
    plus the fg3m marginal of A1.  ``placebo=True``: A4 = A1 with permuted component targets."""
    names = component_names(stat_feature_names("pts"))
    base = permute_targets(frame, SEEDS["placebo"]) if placebo else frame
    sf = stat_frame(base, "pts")
    keys = ("A4",) if placebo else ("A1", "A2", "F3")
    qs: dict[str, list[np.ndarray]] = {k: [] for k in keys}
    ys: list[np.ndarray] = []
    y3: list[np.ndarray] = []
    metas: list[pl.DataFrame] = []
    devs: list[float] = []
    prm_log: list[dict[str, Any]] = []
    for start, block, train in _blocks(sf, seasons, max_blocks):
        tag = "comp4" if placebo else "comp12"
        p = cache / f"{tag}_{start}.npz"
        c = _cached(p)
        if c is None:
            t0 = time.time()
            model = ComponentModel(cfg, names).fit(train)
            out: dict[str, np.ndarray] = {}
            dev = 0.0
            plog: dict[str, Any] = {"block": str(start)}
            for arm, shared in (("A4", True),) if placebo else (("A1", True), ("A2", False)):
                prm = model.params(shared)
                pts, m3, d = model.predict(block, prm)
                dev = max(dev, d)
                if d > 1e-9:
                    raise RuntimeError(
                        f"check 5 failed: grid sum deviates by {d:.3g} ({arm}, {start})"
                    )
                out[arm] = pmf_to_quantiles(pts)
                if arm == "A1":
                    out["F3"] = pmf_to_quantiles(m3)
                plog[arm] = summary(prm)
            _save(p, dev=np.array(dev), plog=np.array(json.dumps(plog)), **out)
            print(
                f"  {tag} block {start} done ({time.time() - t0:.0f}s, n={block.height}) "
                f"max|sum-1|={dev:.2e} {json.dumps(plog)}",
                flush=True,
            )
            c = {**out, "dev": np.array(dev), "plog": np.array(json.dumps(plog))}
        for k in keys:
            qs[k].append(c[k])
        devs.append(float(c["dev"]))
        prm_log.append(json.loads(str(c["plog"])))
        ys.append(block["y"].to_numpy())
        y3.append(block["fg3m"].to_numpy().astype(float))
        metas.append(block.select([c_ for c_ in META_COLS if c_ in block.columns]))
    meta = pl.concat(metas)
    y = np.concatenate(ys)
    res: dict[str, dict[str, Any]] = {}
    for k in keys:
        res[k] = {"qi": np.vstack(qs[k]), "y": y if k != "F3" else np.concatenate(y3), "meta": meta}
    res["_info"] = {"max_dev": max(devs), "params": prm_log}
    return res


# --------------------------------------------------------------------------- scoring


def _ll(p: np.ndarray, ev: np.ndarray) -> np.ndarray:
    pc = np.clip(p, EPS, 1.0 - EPS)
    return np.asarray(-np.where(ev, np.log(pc), np.log(1.0 - pc)))


def row_scores(qi: np.ndarray, y: np.ndarray, seed: int = SEEDS["pit"]) -> dict[str, Any]:
    qf = qi.astype(float)
    return {
        "crps": crps_from_quantiles(qf, y),
        "resid": y - qf.mean(axis=1),
        "pit": randomized_pit(qf, y, seed),
        "tll": {n: _ll((qf >= n).mean(axis=1), y >= n) for n in TLL_THRESHOLDS},
    }


def _delta_stats(delta: np.ndarray, gid: np.ndarray) -> dict[str, float]:
    ci = _ci(delta, gid, N_BOOT)
    return {
        "dcrps": ci["point"],
        "ci_lo": ci["lo"],
        "ci_hi": ci["hi"],
        "p": boot_p_value(delta, gid, N_BOOT),
        "mde": (ci["hi"] - ci["lo"]) / 2.0,
    }


def tercile_masks(v: np.ndarray, season: np.ndarray) -> dict[str, np.ndarray]:
    out = {f"T{i}": np.zeros(len(v), dtype=bool) for i in (1, 2, 3)}
    for s in np.unique(season):
        m = (season == s) & ~np.isnan(v)
        if not m.any():
            continue
        lo, hi = np.quantile(v[m], [1 / 3, 2 / 3])
        out["T1"] |= m & (v <= lo)
        out["T2"] |= m & (v > lo) & (v <= hi)
        out["T3"] |= m & (v > hi)
    return out


def slice_masks(d: pl.DataFrame) -> dict[str, np.ndarray]:
    ppg = d["m10_pts"].to_numpy().astype(float)
    share = 3.0 * d["m10_fg3m"].to_numpy().astype(float) / np.maximum(ppg, 0.1)
    terc = tercile_masks(share, d["season"].to_numpy())
    st = d["starter10"].to_numpy() >= 0.5
    return {
        "all": np.ones(d.height, dtype=bool),
        "tier_<10": ppg < 10,
        "tier_10-17": (ppg >= 10) & (ppg < 17),
        "tier_17-24": (ppg >= 17) & (ppg < 24),
        "tier_24+": ppg >= 24,
        "3pt_share_T1": terc["T1"],
        "3pt_share_T2": terc["T2"],
        "3pt_share_T3": terc["T3"],
        "starter": st,
        "bench": ~st,
        "cold_start_n_prior<20": d["n_prior"].to_numpy() < 20,
    }


def score_pts(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ref = arms["A0"]["meta"]
    for a, v in arms.items():
        assert v["meta"].select(KEYS).equals(ref.select(KEYS)), (
            f"{a}: arms must score identical rows"
        )
        assert np.array_equal(v["y"], arms["A0"]["y"])
    rows: list[dict[str, Any]] = []
    sl: list[dict[str, Any]] = []
    atk: list[dict[str, Any]] = []
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        d = ref.filter(pl.Series(sm))
        gid = d["game_id"].to_numpy()
        sc = {a: row_scores(v["qi"][sm], v["y"][sm]) for a, v in arms.items()}
        for a in ARMS:
            s = sc[a]
            r: dict[str, Any] = {
                "season": season,
                "arm": a,
                "n_rows": int(d.height),
                "n_games": int(len(np.unique(gid))),
                "crps_int": float(s["crps"].mean()),
                "dcrps": None,
                "ci_lo": None,
                "ci_hi": None,
                "p": None,
                "p_bh": None,
                "mde": None,
                "bias": float(s["resid"].mean()),
                "cov80": float(((s["pit"] > 0.10) & (s["pit"] <= 0.90)).mean()),
                "pit_q80": float((s["pit"] <= 0.80).mean()),
                "pit_q90": float((s["pit"] <= 0.90).mean()),
                "tll20": float(s["tll"][20].mean()),
                "tll25": float(s["tll"][25].mean()),
                "tll30": float(s["tll"][30].mean()),
            }
            if a != "A0":
                r.update(_delta_stats(s["crps"] - sc["A0"]["crps"], gid))
                r["p_bh"] = r["p"]
            rows.append(r)
        masks = slice_masks(d)
        for name, m in masks.items():
            n = int(m.sum())
            for a in ARMS[1:]:
                rec: dict[str, Any] = {"season": season, "slice": name, "arm": a, "n": n}
                if n >= MIN_SLICE_N or name == "all":
                    rec.update(_delta_stats((sc[a]["crps"] - sc["A0"]["crps"])[m], gid[m]))
                sl.append(rec)
        ent: dict[str, Any] = {"season": season}
        for other in ("A2", "A3", "A4"):
            ent[f"A1_minus_{other}"] = _ci(sc["A1"]["crps"] - sc[other]["crps"], gid, N_BOOT)
        ent["shared_factor_did_nothing"] = bool(sc["A2"]["crps"].mean() <= sc["A1"]["crps"].mean())
        atk.append(ent)
    return {"rows": rows, "slices": sl, "attack": atk}


def score_fg3m(f3: dict[str, Any], prod: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    ref = prod["meta"]
    assert f3["meta"].select(KEYS).equals(ref.select(KEYS))
    assert np.array_equal(f3["y"], prod["y"])
    for season in TEST_SEASONS:
        sm = (ref["season"] == season).to_numpy()
        gid = ref.filter(pl.Series(sm))["game_id"].to_numpy()
        a = row_scores(f3["qi"][sm], f3["y"][sm])["crps"]
        b = row_scores(prod["qi"][sm], prod["y"][sm])["crps"]
        out.append(
            {
                "season": season,
                "n_rows": int(sm.sum()),
                "crps_int_prod": float(b.mean()),
                "crps_int_components": float(a.mean()),
                **_delta_stats(a - b, gid),
            }
        )
    return out


# --------------------------------------------------------------------------- pass rule


def evaluate_rules(res: dict[str, Any]) -> dict[str, Any]:
    tab = {(r["season"], r["arm"]): r for r in res["rows"]}
    sli = [s for s in res["slices"]]
    atk = {a["season"]: a for a in res["attack"]}
    out: dict[str, Any] = {}
    for season in TEST_SEASONS:
        a1 = tab[(season, "A1")]
        r1 = bool(a1["dcrps"] <= FLOOR and a1["ci_hi"] < 0.0)
        bad = {
            s["slice"]: s["dcrps"]
            for s in sli
            if s["season"] == season
            and s["arm"] == "A1"
            and s["slice"] != "all"
            and s["n"] >= MIN_SLICE_N
            and s.get("dcrps") is not None
            and s["dcrps"] > SLICE_WORSE
        }
        guards = {
            "abs_bias<=0.5": bool(abs(a1["bias"]) <= MAX_BIAS),
            "cov80_in_[.75,.85]": bool(COV_LO <= a1["cov80"] <= COV_HI),
            "pit_q90_within_.02": bool(abs(a1["pit_q90"] - 0.90) <= PIT_TOL),
            "no_slice_worse_than_+0.01": not bad,
        }
        e = atk[season]
        gates = {
            "A1_minus_A4<=-0.005": bool(e["A1_minus_A4"]["point"] <= A4_FLOOR),
            "A1_minus_A3<=-0.003": bool(e["A1_minus_A3"]["point"] <= A3_FLOOR),
        }
        a3 = tab[(season, "A3")]
        out[str(season)] = {
            "rule1_floor_and_ci": r1,
            "rule2_guards": guards,
            "rule2": all(guards.values()),
            "slices_worse": bad,
            "rule3_attack_gates": gates,
            "rule3": all(gates.values()),
            "A1_minus_A2_reported": e["A1_minus_A2"],
            "shared_factor_did_nothing": e["shared_factor_did_nothing"],
            "attempt_features_help_not_structure": bool(not gates["A1_minus_A3<=-0.003"]),
            "A3_dcrps": a3["dcrps"],
            "A3_rule1": bool(a3["dcrps"] <= FLOOR and a3["ci_hi"] < 0.0),
            "season_pass": bool(r1 and all(guards.values()) and all(gates.values())),
        }
    out["final_pass"] = bool(all(out[str(s)]["season_pass"] for s in TEST_SEASONS))
    out["fails_on_2023_close"] = bool(not out["2023"]["season_pass"])
    return out


# --------------------------------------------------------------------------- gate


def _chk(name: str, value: Any, threshold: str, ok: Any) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok)}


def feat_equal(a: pl.DataFrame, b: pl.DataFrame, game_ids: set[str] | None) -> bool:
    cols = list(COMP_FEATURES)
    if game_ids is not None:
        a = a.filter(pl.col("game_id").is_in(list(game_ids)))
        b = b.filter(pl.col("game_id").is_in(list(game_ids)))
    a, b = a.sort(KEYS), b.sort(KEYS)
    if a.height != b.height or not a.select(KEYS).equals(b.select(KEYS)):
        return False
    return all(np.array_equal(a[c].to_numpy(), b[c].to_numpy(), equal_nan=True) for c in cols)


BOX_COLS: tuple[str, ...] = ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta")


def bump(pgs: pl.DataFrame, game_ids: list[str], k: int) -> pl.DataFrame:
    return pgs.with_columns(
        [
            pl.when(pl.col("game_id").is_in(game_ids))
            .then(pl.col(c) + k)
            .otherwise(pl.col(c))
            .alias(c)
            for c in BOX_COLS
        ]
    )


def missingness(frame: pl.DataFrame) -> tuple[list[dict[str, Any]], float]:
    cols = [*COMP_FEATURES]
    d = frame.filter(pl.col("n_prior") >= 5).with_columns(
        _bucket_minutes(pl.col("minutes")).alias("_b")
    )
    audit: list[dict[str, Any]] = []
    gaps: list[float] = []
    for c in cols:
        rates = []
        for b in ("<5", "5-10", "10-20", ">20"):
            sub = d.filter(pl.col("_b") == b)
            r = float((sub[c].is_null() | sub[c].is_nan()).mean()) if sub.height else 0.0  # type: ignore[arg-type]
            audit.append({"col": c, "bucket": b, "null_rate": r, "n": sub.height})
            rates.append(r)
        gaps.append((max(rates) - min(rates)) * 100.0)
    return audit, max(gaps)


def nba_untouched() -> bool:
    r = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "nba/props/context_residual.py",
            "nba/props/lower_tail.py",
        ],
        capture_output=True,
    )
    return r.returncode == 0


def run_gate(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    t0 = time.time()
    sha = frozen_sha256(DOC)
    assert sha.startswith(FROZEN_PREFIX), f"frozen sha mismatch: {sha}"
    inp = load_study(db_path)
    games, pgs_ext, flagged, frame = inp["games"], inp["pgs_ext"], inp["flagged"], inp["frame"]
    print(f"inputs loaded {time.time() - t0:.0f}s", flush=True)
    tests: list[dict[str, Any]] = []
    # --- check 1 (played rows 2022-2024)
    played = pgs_ext.join(games.select("game_id", "season"), on="game_id").filter(
        (pl.col("minutes") > 0) & (pl.col("season") >= 2022)
    )
    nn = float(
        played.select(
            pl.all_horizontal(
                [pl.col(c).is_not_null() for c in ("fgm", "fga", "fg3m", "fg3a", "ftm", "fta")]
            ).mean()
        ).item()
    )
    idn = float(np.mean(identity_ok(played).to_numpy()))
    n_fail = int((~identity_ok(played)).sum())
    tests.append(
        _chk(
            "C1 six box columns non-null (played 2022-2024)",
            nn,
            f">= {NONNULL_MIN}",
            nn >= NONNULL_MIN,
        )
    )
    tests.append(
        _chk("C1 pts == 2 fg2m + 3 fg3m + ftm", idn, f">= {NONNULL_MIN}", idn >= NONNULL_MIN)
    )
    tests.append(_chk("C1 rows dropped from all arms (count)", n_fail, "reported", True))
    # --- check 2
    audit, gap = missingness(frame)
    tests.append(
        _chk(
            "C2 max NULL-rate gap across minutes buckets (pp)",
            gap,
            f"<= {G2_MAX_GAP_PP}",
            gap <= G2_MAX_GAP_PP,
        )
    )
    print(f"check 2 done {time.time() - t0:.0f}s", flush=True)
    # --- check 3 planted tests
    comp = frame.select(*KEYS, *COMP_FEATURES)
    reg24 = games.filter(
        (pl.col("season") == REPORT_SEASON) & pl.col("game_id").str.starts_with("002")
    ).sort("game_date")
    day = reg24["game_date"].to_list()[len(reg24) // 2]
    targets = games.filter(pl.col("game_date") == day)["game_id"].to_list()
    upto = set(games.filter(pl.col("game_date") <= day)["game_id"].to_list())
    all_g = set(games["game_id"].to_list())
    p3 = build_component_features(games, bump(pgs_ext, targets, 9), flagged)
    same_ok = feat_equal(comp, p3, upto)
    later_moves = not feat_equal(comp, p3, all_g - upto)
    print(f"planted same-game done {time.time() - t0:.0f}s", flush=True)
    import datetime as dt

    cutoff = dt.date(2024, 1, 15)
    late = games.filter(pl.col("game_date").cast(pl.Date) >= cutoff)["game_id"].to_list()
    p4 = build_component_features(games, bump(pgs_ext, late, 5), flagged)
    before = set(games.filter(pl.col("game_date").cast(pl.Date) <= cutoff)["game_id"].to_list())
    fut_ok = feat_equal(comp, p4, before)
    fut_moves = not feat_equal(comp, p4, all_g - before)
    tests += [
        _chk(
            "C3 PLANT same-game box edited: component features identical through the day",
            int(same_ok),
            "== 1",
            same_ok,
        ),
        _chk(
            "C3 PLANT same-game is not inert (later games change)",
            int(later_moves),
            "== 1",
            later_moves,
        ),
        _chk(
            "C3 PLANT future (>= 2024-01-15) box edited: earlier features unchanged",
            int(fut_ok),
            "== 1",
            fut_ok,
        ),
        _chk(
            "C3 PLANT future is not inert (later games change)", int(fut_moves), "== 1", fut_moves
        ),
    ]
    # --- nba untouched (stands for 'flag off byte-identical'; the A0 reproduction is check 4 in `arms`)
    tests.append(
        _chk(
            "C4a nba/props/context_residual.py and lower_tail.py unchanged vs HEAD",
            int(nba_untouched()),
            "== 1",
            nba_untouched(),
        )
    )
    # --- check 5 on fixed synthetic parameters over every scored row
    sf = stat_frame(frame, "pts").filter(pl.col("season").is_in(list(TEST_SEASONS)))
    mu = np.column_stack([sf[f"m10_{a}"].to_numpy() for a in ATT]).astype(float)
    mu = np.maximum(mu, 0.05)
    pr = np.column_stack([sf[c].to_numpy() for c in RATE_COLS]).astype(float)
    worst = 0.0
    for s in range(0, sf.height, 4000):
        pmf, _ = points_pmf(
            mu[s : s + 4000],
            pr[s : s + 4000],
            np.array([8.0, 6.0, 5.0]),
            np.array([0.3, 0.3, 0.2]),
            np.array([60.0, 30.0, 25.0]),
        )
        worst = max(worst, float(np.abs(pmf.sum(axis=1) - 1.0).max()))
        assert pmf.shape[1] == MAX_PTS + 1
    tests.append(
        _chk(
            "C5 grid sums to 1 (max abs deviation, all scored rows, fixed params)",
            worst,
            f"<= {ENUM_TOL}",
            worst <= ENUM_TOL,
        )
    )
    res: dict[str, Any] = {
        "frozen_sha256": sha,
        "frozen_prefix_ok": sha.startswith(FROZEN_PREFIX),
        "git_sha": git_sha(),
        "seeds": SEEDS,
        "audit": audit,
        "tests": tests,
        "n_identity_fail_played": n_fail,
        "gate_ok": all(t["ok"] for t in tests) and sha.startswith(FROZEN_PREFIX),
        "max_season_loaded": int(frame["season"].max()),
        "gate_seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {k: res[k] for k in ("frozen_sha256", "tests", "audit")},
        indent=2,
        default=_jd,
        sort_keys=True,
    )
    (out_dir / "gate.json").write_text(body)
    res["gate_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    _write_json(out_dir / "results.json", res)
    return res


# --------------------------------------------------------------------------- arms


def check_a0_repro(a0: dict[str, Any]) -> dict[str, Any]:
    ref = json.loads(LOWER_TAIL_JSON.read_text())
    rec: dict[str, Any] = {}
    ok = True
    for season in TEST_SEASONS:
        sm = (a0["meta"]["season"] == season).to_numpy()
        got = float(crps_from_quantiles(a0["qi"][sm].astype(float), a0["y"][sm]).mean())
        stored = float(ref["pts"][str(season)]["off"]["crps_int"])
        n_ref = int(ref["pts"][str(season)]["off"]["n"])
        good = abs(got - stored) <= 1e-6 and int(sm.sum()) == n_ref
        ok &= good
        rec[f"pts_{season}"] = {
            "got": got,
            "stored": stored,
            "delta": got - stored,
            "n": int(sm.sum()),
            "n_ref": n_ref,
            "ok": good,
        }
    return {"ok": bool(ok), "detail": rec}


def run_arms(db_path: str = "nba.duckdb", out_dir: Path = OUT_DIR) -> dict[str, Any]:
    path = out_dir / "results.json"
    cur = json.loads(path.read_text())
    stored = (out_dir / "gate.json").read_bytes()
    if hashlib.sha256(stored).hexdigest() != cur.get("gate_sha256"):
        raise RuntimeError("gate.json does not match its recorded sha256")
    if not cur.get("gate_ok"):
        raise RuntimeError("minimum-data gate did not pass: no arm may be fitted")
    sha = frozen_sha256(DOC)
    assert sha.startswith(FROZEN_PREFIX)
    t0 = time.time()
    inp = load_study(db_path)
    frame = drop_failures(inp)
    cfg = inp["cfg"]
    cache = CACHE_DIR
    arms: dict[str, dict[str, Any]] = {}
    arms["A0"] = collect_prod(frame, "pts", cfg, stat_feature_names("pts"), "A0", cache)
    repro = check_a0_repro(arms["A0"])
    print(f"A0 repro ok={repro['ok']} {json.dumps(repro['detail'])}", flush=True)
    cur["a0_repro"] = repro
    _write_json(path, cur)
    if not repro["ok"]:
        raise RuntimeError("A0 does not reproduce the stored OOF integer CRPS: stop")
    c12 = collect_comp(frame, cfg, cache, placebo=False)
    print(f"[{time.time() - t0:.0f}s] A1/A2 ready", flush=True)
    arms["A1"], arms["A2"] = c12["A1"], c12["A2"]
    arms["A3"] = collect_prod(
        frame, "pts", cfg, stat_feature_names("pts", extra=COMP_FEATURES), "A3", cache
    )
    print(f"[{time.time() - t0:.0f}s] A3 ready", flush=True)
    c4 = collect_comp(frame, cfg, cache, placebo=True)
    arms["A4"] = c4["A4"]
    print(f"[{time.time() - t0:.0f}s] A4 ready", flush=True)
    prod3 = collect_prod(frame, "fg3m", cfg, stat_feature_names("fg3m"), "P_fg3m", cache)
    tables = score_pts(arms)
    tables["fg3m"] = score_fg3m(c12["F3"], prod3)
    rules = evaluate_rules(tables)
    info = {"A1A2": c12["_info"], "A4": c4["_info"]}
    check5 = max(info["A1A2"]["max_dev"], info["A4"]["max_dev"])
    cur["tests"].append(
        _chk(
            "C4 A0 reproduces production integer CRPS to 1e-6 (n and crps_int)",
            int(repro["ok"]),
            "== 1",
            repro["ok"],
        )
    )
    cur["tests"].append(
        _chk(
            "C5 fitted grids sum to 1 (max abs deviation over scored rows)",
            check5,
            f"<= {ENUM_TOL}",
            check5 <= ENUM_TOL,
        )
    )
    cur["arms"] = {
        **tables,
        "rules": rules,
        "info": info,
        "seeds": SEEDS,
        "git_sha": git_sha(),
        "frozen_sha256": sha,
        "n_boot": N_BOOT,
        "arms_seconds": time.time() - t0,
    }
    _write_json(path, cur)
    arms_out: dict[str, Any] = cur["arms"]
    return arms_out


def run_smoke(db_path: str = "nba.duckdb") -> None:
    """One month block, A0 / A1 / A2 scored; scratch cache only."""
    t0 = time.time()
    scratch = CACHE_DIR / "smoke"
    shutil.rmtree(scratch, ignore_errors=True)
    inp = load_study(db_path)
    print(f"loaded {time.time() - t0:.0f}s", flush=True)
    frame = drop_failures(inp)
    cfg = inp["cfg"]
    a0 = collect_prod(frame, "pts", cfg, stat_feature_names("pts"), "A0", scratch, (2023,), 1)
    c12 = collect_comp(frame, cfg, scratch, placebo=False, seasons=(2023,), max_blocks=1)
    y = a0["y"]
    for name, q in (("A0", a0["qi"]), ("A1", c12["A1"]["qi"]), ("A2", c12["A2"]["qi"])):
        s = row_scores(q, y)
        print(
            f"{name} one-block crps_int {s['crps'].mean():.4f} bias {s['resid'].mean():+.3f} "
            f"cov80 {((s['pit'] > 0.1) & (s['pit'] <= 0.9)).mean():.3f} n={len(y)} [{time.time() - t0:.0f}s]",
            flush=True,
        )
    print("max grid-sum deviation", c12["_info"]["max_dev"], c12["_info"]["params"], flush=True)


# --------------------------------------------------------------------------- report


def _f(x: Any, nd: int = 4, sign: bool = False) -> str:
    if x is None:
        return ""
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def render_report(out_dir: Path = OUT_DIR) -> str:
    r = json.loads((out_dir / "results.json").read_text())
    ln = [
        "# PTS_COMPONENTS: results (docs/prereg/PTS_COMPONENTS.md)",
        "",
        f"Frozen sha256 `{r['frozen_sha256']}` (first 8 `{FROZEN_PREFIX}`, prefix ok: {r['frozen_prefix_ok']}). "
        f"git SHA at run `{r['git_sha']}`. Seeds {r['seeds']}. CPU. Seasons <= 2024 only (max loaded "
        f"{r['max_season_loaded']}); nba.duckdb read-only.",
        "",
        "## Checks `[test, ok]`",
        "",
        "| test | value | threshold | ok |",
        "|---|---|---|---|",
    ]
    for t in r["tests"]:
        v = t["value"]
        vs = f"{v:.6g}" if isinstance(v, float) else str(v)
        ln.append(f"| {t['test']} | {vs} | {t['threshold']} | {'ok' if t['ok'] else 'FAIL'} |")
    ln += [
        "",
        "Missingness audit `[col, bucket, null_rate]`:",
        "",
        "| col | bucket | null_rate | n |",
        "|---|---|---|---|",
    ]
    ln += [f"| {a['col']} | {a['bucket']} | {a['null_rate']:.5f} | {a['n']} |" for a in r["audit"]]
    arms = r.get("arms")
    if not arms:
        return "\n".join([*ln, "", "Arms not run."])
    ln += ["", "## A0 reproduction", "", "```", json.dumps(r["a0_repro"], indent=1), "```", ""]
    ln += [
        "## Results `[season, arm, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p, mde, bias, cov80, pit_q80, pit_q90, tll20, tll25, tll30]`",
        "",
        "dcrps = arm - A0, integer support, game-clustered 95% CI, 2000 resamples, seed 0. One primary test per season (pts), p_bh = p.",
        "",
        "| season | arm | n_rows | n_games | crps_int | dcrps | ci_lo | ci_hi | p | mde | bias | cov80 | pit_q80 | pit_q90 | tll20 | tll25 | tll30 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in arms["rows"]:
        ln.append(
            f"| {t['season']} | {t['arm']} | {t['n_rows']} | {t['n_games']} | {_f(t['crps_int'])} | {_f(t['dcrps'], 4, True)} | "
            f"{_f(t['ci_lo'], 4, True)} | {_f(t['ci_hi'], 4, True)} | {_f(t['p'], 3)} | {_f(t['mde'])} | {_f(t['bias'], 3, True)} | "
            f"{_f(t['cov80'], 3)} | {_f(t['pit_q80'], 3)} | {_f(t['pit_q90'], 3)} | {_f(t['tll20'])} | {_f(t['tll25'])} | {_f(t['tll30'])} |"
        )
    ln += [
        "",
        "## Slices `[season, slice, arm, n, dcrps, ci_lo, ci_hi]` (arm - A0; CI only for n >= 300; the 3pt_share terciles are the mechanism table)",
        "",
        "| season | slice | arm | n | dcrps | ci_lo | ci_hi |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in arms["slices"]:
        ln.append(
            f"| {s['season']} | {s['slice']} | {s['arm']} | {s['n']} | {_f(s.get('dcrps'), 4, True)} | {_f(s.get('ci_lo'), 4, True)} | {_f(s.get('ci_hi'), 4, True)} |"
        )

    def cell(d: dict[str, float]) -> str:
        return f"{d['point']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}]"

    ln += [
        "",
        "## Attack inputs (crps difference, all rows)",
        "",
        "| season | A1-A2 | A1-A3 | A1-A4 | shared factor did nothing |",
        "|---|---|---|---|---|",
    ]
    for a in arms["attack"]:
        ln.append(
            f"| {a['season']} | {cell(a['A1_minus_A2'])} | {cell(a['A1_minus_A3'])} | {cell(a['A1_minus_A4'])} | {a['shared_factor_did_nothing']} |"
        )
    ln += [
        "",
        "## Secondary: fg3m head alone vs production fg3m (descriptive, not tested)",
        "",
        "| season | n_rows | crps_int prod | crps_int components | dcrps | ci_lo | ci_hi |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in arms["fg3m"]:
        ln.append(
            f"| {f['season']} | {f['n_rows']} | {_f(f['crps_int_prod'])} | {_f(f['crps_int_components'])} | {_f(f['dcrps'], 4, True)} | {_f(f['ci_lo'], 4, True)} | {_f(f['ci_hi'], 4, True)} |"
        )
    ln += ["", "## Rules", "", "```", json.dumps(arms["rules"], indent=1), "```", ""]
    ln += ["## Fitted activity parameters per block (A1)", "", "```"]
    ln += [
        json.dumps(p.get("A1", {})) + f" {p.get('block')}" for p in arms["info"]["A1A2"]["params"]
    ]
    ln += ["```", "", "## Unstated details, chosen before scoring", ""] + [
        f"* {u}" for u in UNSTATED
    ]
    ln += ["", f"Seeds: {r['seeds']}; git SHA {r['git_sha']}; frozen sha256 {r['frozen_sha256']}."]
    return "\n".join(ln)


def render_ledger(out_dir: Path = OUT_DIR) -> str:
    """Ledger rows in the ledger's column format (IDs are placeholders; the main session numbers them)."""
    r = json.loads((out_dir / "results.json").read_text())
    arms = r["arms"]
    tab = {(t["season"], t["arm"]): t for t in arms["rows"]}
    rules = arms["rules"]
    ln = [
        "| ID | Date | Name | Test | Estimate | CI lo | CI hi | Unit | p | Notes | Holdout |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    k = 0
    name = f"PTS_COMPONENTS (frozen sha256 {FROZEN_PREFIX})"
    for season in TEST_SEASONS:
        a = tab[(season, "A1")]
        k += 1
        notes = (
            f"MDE {a['mde']:.4f}; bias {tab[(season, 'A0')]['bias']:+.3f} -> {a['bias']:+.3f}; "
            f"cov80 {tab[(season, 'A0')]['cov80']:.3f} -> {a['cov80']:.3f}; pit_q90 {a['pit_q90']:.3f}; "
            f"A2 {tab[(season, 'A2')]['dcrps']:+.4f}, A3 {tab[(season, 'A3')]['dcrps']:+.4f}, "
            f"A4 placebo {tab[(season, 'A4')]['dcrps']:+.4f}; tll30 A0 {tab[(season, 'A0')]['tll30']:.4f} -> {a['tll30']:.4f}; "
            f"season pass {rules[str(season)]['season_pass']}"
        )
        ln.append(
            f"| T??{k} | 2026-10-10 | {name} | pts {season} A1 vs A0 all rows, integer-support CRPS, "
            f"n={a['n_rows']} rows / {a['n_games']} games | {a['dcrps']:+.4f} | {a['ci_lo']:+.4f} | "
            f"{a['ci_hi']:+.4f} | game | {a['p']:.3f} | {notes} | not holdout (<=2024) |"
        )
    for f in arms["fg3m"]:
        k += 1
        ln.append(
            f"| T??{k} | 2026-10-10 | {name} | fg3m {f['season']} components marginal vs production fg3m (secondary, "
            f"descriptive), integer-support CRPS, n={f['n_rows']} rows | {f['dcrps']:+.4f} | {f['ci_lo']:+.4f} | "
            f"{f['ci_hi']:+.4f} | game | {f['p']:.3f} | crps {f['crps_int_prod']:.4f} -> {f['crps_int_components']:.4f}; "
            f"not tested | not holdout (<=2024) |"
        )
    return "\n".join(ln) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PTS_COMPONENTS (frozen rule)")
    ap.add_argument("stage", choices=("gate", "arms", "report", "smoke"))
    ap.add_argument("--db-path", default="nba.duckdb")
    a = ap.parse_args(argv)
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    if a.stage == "smoke":
        run_smoke(a.db_path)
    elif a.stage == "gate":
        wait_for_replay()
        with heavy_lock():
            res = run_gate(a.db_path)
        print(
            json.dumps(
                {k: res[k] for k in ("frozen_sha256", "tests", "gate_ok")}, indent=1, default=_jd
            )
        )
    elif a.stage == "arms":
        wait_for_replay()
        with heavy_lock():
            res = run_arms(a.db_path)
        print(json.dumps(res["rules"], indent=1))
    else:
        REPORT_MD.write_text(render_report())
        (OUT_DIR / "ledger_rows.md").write_text(render_ledger())
        print("wrote", REPORT_MD, OUT_DIR / "ledger_rows.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
