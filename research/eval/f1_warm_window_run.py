# ruff: noqa: E501
# mypy: disable-error-code="no-any-return,no-untyped-call,attr-defined,index,arg-type,assignment,operator,union-attr,misc,call-overload,type-var"
"""F1_WARM_WINDOW harness (rule: docs/prereg/F1_WARM_WINDOW.md; DRAFT until the main session commits `prereg(F1_WARM_WINDOW): <sha256>`).

Three arms of production's props pipeline (stat heads AND the lower-tail mixture `pi` / `w_q`, refit inside every month block on the arm's own
frame and window): A = cold frame (nba.duckdb alone), rows from 2022-10-01 (production); B = warm frame (history + nba.duckdb in one
`build_features`), rows from 2022-10-01; C = warm frame, rows from 2019-10-01. Confirmatory: C minus A, integer-support CRPS on production's
(cold) evaluation keys, four stats, 2023 and 2024.

    R="-m research.eval.f1_warm_window_run"; D=/Users/devin/Downloads/nba-prediction
    .venv/bin/python -W ignore $R check    --data-root $D [--smoke]   # label-free preflight (no score of any kind); --smoke fits ONE block of pts
    .venv/bin/python -W ignore $R rehearse --world helps|null|harm|one_season|guard   # synthetic scores, no data, no fit
    .venv/bin/python -W ignore $R run      --data-root $D --sha <64 hex>   # frozen + pinned only; heavy.lock; checkpoints; verdict

``run`` refuses unless the rule is frozen AND the harness files equal the `harness(F1_WARM_WINDOW): <pin>` commit
(`f1_warm_window_rule.require_ready`), then runs the preflight (library versions, input hashes pinned in the rule, A reproduces production)
BEFORE any 2023-24 outcome is scored. `nba.duckdb` and the history DB are opened read-only with `season <= 2024` in SQL; season 2025 is never
loaded (the schedule reader gets ``max_season=2024``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import yaml

from nba.eval.context_residual_eval import MAX_SEASON, crps_from_quantiles, month_blocks
from nba.eval.lower_tail_eval import (
    ROW_COVARIATES,
    arm_grid,
    attach_realised,
    collect_pi,
    load_components,
)
from nba.props.context_residual import (
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    split_calibration,
    stat_feature_names,
    stat_frame,
    to_integer_support,
)
from nba.props.lower_tail import short_flag
from nba.props.metrics import _clustered_boot_means
from research.eval import f1_warm_window_gate as gate
from research.eval import f1_warm_window_rule as rule

STATS = tuple(PROP_STATS)
SEASONS = (2023, 2024)
ARMS = ("A", "B", "C")
WINDOW = {
    "A": dt.date(2022, 10, 1),
    "B": dt.date(2022, 10, 1),
    "C": dt.date(2019, 10, 1),
}  # A's window is a no-op on the cold frame (it starts 2022-10); applied anyway so the three arms share one code path
FRAME_OF = {"A": "cold", "B": "warm", "C": "warm"}
FLOOR = {
    "pts": 0.005,
    "reb": 0.0025,
    "ast": 0.0025,
    "fg3m": 0.0025,
}  # improvement required (C minus A <= -floor)
ALPHA = 0.05
N_BOOT = 2000
SEED = 0
MIN_TRAIN = 1500
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01  # G1: |cov_C - tau| <= |cov_A - tau| + COV_TOL
ECE_TOL = 0.005  # G2
SLICE_MULT = 2.0  # G3: slice delta <= SLICE_MULT * floor
MIN_SLICE_N = 300
REPRO_TOL = {
    2023: 1e-4,
    2024: 2e-4,
}  # R1: A refit vs stored production integer CRPS (stored 2024 arrays pre-date the neutral-site restoration)
HEADS_TOL = 0.0  # label-free check: A first block heads / pi equal the stored components exactly
PINNED_VERSIONS = {
    "lightgbm": "4.7.0",
    "numpy": "2.5.3",
    "polars": "1.44.2",
    "duckdb": "1.5.6",
    "scipy": "1.18.1",
    "pyyaml": "6.0.3",
    "scikit-learn": "1.9.1",
    "pyarrow": "25.0.1",
}
PINNED_PYTHON = "3.12.13"
WORDS = ("PASS", "FAIL_NO_GAIN", "FAIL_ONE_SEASON", "FAIL_GUARD", "FAIL_HARM")
SLICE_NAMES = (
    "all",
    "starter",
    "bench",
    "n_out_rot>=1",
    "n_out_rot>=2",
    "has_report=0",
    "cold_start",
    "first15",
    "playoffs",
    "unexposed",
)
OUT_DIR = Path("reports/prereg_f1_warm_window")
CACHE_REL = "data/ops/f1_warm_window_cache"
LT_REL = "reports/lower_tail"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def versions() -> dict[str, str]:
    return {k: importlib.metadata.version(k) for k in PINNED_VERSIONS}


def require_versions() -> dict[str, str]:
    if platform.python_version() != PINNED_PYTHON:
        raise RuntimeError(
            f"python {platform.python_version()} differs from the pinned {PINNED_PYTHON}"
        )
    got = versions()
    if got != PINNED_VERSIONS:
        raise RuntimeError(
            f"library versions differ from the pinned set: {got} vs {PINNED_VERSIONS}"
        )
    return got


# ------------------------------------------------------------------------------------ inference (house code path)
def delta_stats(
    delta: np.ndarray, cluster: np.ndarray, n_boot: int = N_BOOT, seed: int = SEED
) -> dict[str, float]:
    """Mean paired delta, 95% percentile CI and two-sided bootstrap p, clustered by ``cluster``.

    The draws are ``nba.props.metrics._clustered_boot_means`` with ``default_rng(seed)`` (the draws behind ``paired_score_delta_ci``, whose CI this
    equals: tested). p = min(1, 2 * (1 + min(#{draws >= 0}, #{draws <= 0})) / (n_boot + 1))."""
    d = np.asarray(delta, dtype=float)
    draws = _clustered_boot_means(d, np.asarray(cluster), n_boot, np.random.default_rng(seed))
    k = min(int((draws >= 0).sum()), int((draws <= 0).sum()))
    return {
        "delta": float(d.mean()),
        "ci_lo": float(np.quantile(draws, ALPHA / 2)),
        "ci_hi": float(np.quantile(draws, 1 - ALPHA / 2)),
        "p": float(min(1.0, 2.0 * (1 + k) / (n_boot + 1))),
        "n_rows": int(len(d)),
        "n_clusters": int(len(np.unique(cluster))),
    }


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    tot = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            tot += m.mean() * abs(float(p[m].mean()) - float(y[m].mean()))
    return tot


def pit_from_qi(qi: np.ndarray, y: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Randomised PIT of an integer-grid forecast with externally supplied uniforms ``u`` (shared by the three arms)."""
    q, yy = qi.astype(float), y.astype(float)
    f_lo = (q < (yy - 0.5)[:, None]).mean(axis=1)
    f_hi = (q <= (yy + 0.5)[:, None]).mean(axis=1)
    return f_lo + u * (f_hi - f_lo)


# ------------------------------------------------------------------------------------ the decision (pure; the rehearsal drives this)
def coverage(pit: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    return {f"{t:.2f}": float((pit[mask] < t).mean()) for t in COV_TAUS}


def guard_g1(cov_a: dict[str, float], cov_c: dict[str, float]) -> bool:
    return all(abs(cov_c[f"{t:.2f}"] - t) <= abs(cov_a[f"{t:.2f}"] - t) + COV_TOL for t in COV_TAUS)


def guard_g2(ece_a: float, ece_c: float) -> bool:
    return ece_c <= ece_a + ECE_TOL


def guard_g3(slice_deltas: dict[str, tuple[float, int]], floor: float) -> tuple[bool, list[str]]:
    """Binding slices (n >= MIN_SLICE_N): C minus A must not exceed SLICE_MULT * floor."""
    bad = [k for k, (d, n) in slice_deltas.items() if n >= MIN_SLICE_N and d > SLICE_MULT * floor]
    return not bad, bad


def evaluate(
    bundle: dict[str, Any], gates_ok: bool = True, repro_ok: bool = True
) -> dict[str, Any]:
    """The frozen decision. ``bundle`` carries, per stat, aligned arrays on production's evaluation keys:
    ``gid, date, season`` (cluster ids and season), ``score[arm]`` (per-row integer CRPS), ``pit[arm]`` (randomised PIT), ``slices[name]`` (bool),
    and ``pi[arm]`` = {p, short, season} (stat-independent; the pts rows). Words, in this order:
    FAIL_HARM (any stat-season HARM) > PASS (>= 1 stat confirmed) > FAIL_GUARD (a stat clears on numbers in both seasons but a guard G1, G2, G3 or G9 fails; G9 = the floor and CI upper < 0 also hold on games NOT stamp-exposed, BH not re-applied)
    > FAIL_ONE_SEASON (a stat clears in exactly one season, none in both) > FAIL_NO_GAIN.
    Gate (G4) and reproduction (R1) failures are STOPS, never words."""
    if not (gates_ok and repro_ok):
        raise RuntimeError(
            "STOP: a gate item or a reproduction check failed; no verdict is printed"
        )
    rows: list[dict[str, Any]] = []
    pi = bundle["pi"]
    for s in SEASONS:
        recs = []
        for st in STATS:
            b = bundle["stats"][st]
            m = b["season"] == s
            d = b["score"]["C"][m] - b["score"]["A"][m]
            r = {"stat": st, "season": s, **delta_stats(d, b["gid"][m])}
            r["floor"] = FLOOR[st]
            r["ci_date"] = {k: delta_stats(d, b["date"][m])[k] for k in ("ci_lo", "ci_hi")}
            for name, (x, y) in {"B_minus_A": ("B", "A"), "C_minus_B": ("C", "B")}.items():
                r[name] = delta_stats(b["score"][x][m] - b["score"][y][m], b["gid"][m])
            r["coverage"] = {a: coverage(b["pit"][a], m) for a in ("A", "C")}
            r["g1"] = guard_g1(r["coverage"]["A"], r["coverage"]["C"])
            sl: dict[str, tuple[float, int]] = {}
            for name, mk in b["slices"].items():
                mm = m & mk
                sl[name] = (
                    float((b["score"]["C"][mm] - b["score"]["A"][mm]).mean()) if mm.any() else 0.0,
                    int(mm.sum()),
                )
            r["slices"] = {k: {"delta": v[0], "n": v[1]} for k, v in sl.items()}
            r["g3"], r["g3_bad"] = guard_g3(sl, FLOOR[st])
            pm = pi["C"]["season"] == s
            r["ece"] = {a: ece(pi[a]["p"][pm], pi[a]["short"][pm]) for a in ("A", "C")}
            r["g2"] = guard_g2(r["ece"]["A"], r["ece"]["C"])
            mu = m & b["slices"]["unexposed"]
            du = b["score"]["C"][mu] - b["score"]["A"][mu]
            r["unexposed"] = delta_stats(du, b["gid"][mu])
            r["g9"] = bool(r["unexposed"]["delta"] <= -FLOOR[st] and r["unexposed"]["ci_hi"] < 0)
            r["guards_ok"] = bool(r["g1"] and r["g2"] and r["g3"] and r["g9"])
            recs.append(r)
        adj = bh_adjust(np.array([r["p"] for r in recs]))
        for r, a in zip(recs, adj, strict=True):
            r["p_bh"] = float(a)
            r["clears"] = bool(r["delta"] <= -r["floor"] and r["ci_hi"] < 0 and r["p_bh"] <= ALPHA)
            r["harm"] = bool(r["delta"] >= r["floor"] and r["ci_lo"] > 0)
        rows += recs
    by = {(r["stat"], r["season"]): r for r in rows}
    stat_out: dict[str, Any] = {}
    for st in STATS:
        r23, r24 = by[(st, 2023)], by[(st, 2024)]
        clears_both = r23["clears"] and r24["clears"]
        stat_out[st] = {
            "clears_2023": r23["clears"],
            "clears_2024": r24["clears"],
            "clears_both": clears_both,
            "guards_ok_both": r23["guards_ok"] and r24["guards_ok"],
            "confirmed": bool(clears_both and r23["guards_ok"] and r24["guards_ok"]),
            "harm": bool(r23["harm"] or r24["harm"]),
        }
    if any(v["harm"] for v in stat_out.values()):
        word = "FAIL_HARM"
    elif any(v["confirmed"] for v in stat_out.values()):
        word = "PASS"
    elif any(v["clears_both"] for v in stat_out.values()):
        word = "FAIL_GUARD"
    elif any(v["clears_2023"] or v["clears_2024"] for v in stat_out.values()):
        word = "FAIL_ONE_SEASON"
    else:
        word = "FAIL_NO_GAIN"
    return {
        "word": word,
        "verdict": "PASS" if word == "PASS" else "FAIL",
        "stats": stat_out,
        "rows": rows,
    }


# ------------------------------------------------------------------------------------ data and arms
def _frame(root: Path, dbs: list[str]) -> pl.DataFrame:
    feats, _games, _flagged = gate.build(root, dbs)
    parts = [gate.load_inputs(str(root / d), None) for d in dbs]
    games = pl.concat([p[0] for p in parts], how="vertical_relaxed")
    pgs = pl.concat([p[1] for p in parts], how="vertical_relaxed")
    return attach_realised(feats, games, pgs)


def load_frames(root: Path) -> tuple[dict[str, pl.DataFrame], ContextResidualConfig]:
    cfg = ContextResidualConfig(
        **yaml.safe_load((rule.ROOT / "configs/context_residual.yaml").read_text()).get("model", {})
    )
    if cfg.report.tip_source != "real":
        raise RuntimeError("tip_source must be 'real' (no proxy tips)")
    frames = {
        "cold": _frame(root, ["nba.duckdb"]),
        "warm": _frame(root, ["data/history/nba_history.duckdb", "nba.duckdb"]),
    }
    for f in frames.values():
        if int(f["season"].max()) > MAX_SEASON:
            raise RuntimeError("season > 2024 loaded: frozen holdout")
    return frames, cfg


def windowed(frame: pl.DataFrame, arm: str) -> pl.DataFrame:
    return frame.filter(pl.col("game_date") >= WINDOW[arm])


def collect_heads(
    frame: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = SEASONS,
    log: bool = True,
) -> dict[str, np.ndarray]:
    """Production walk-forward of one stat (month blocks, production model, calibration split, covariates): `lower_tail_eval.collect_stat`
    with the window already applied to ``frame`` and a progress line per block."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    names = stat_feature_names(stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk", *ROW_COVARIATES)
    }
    cal: dict[str, list[np.ndarray]] = {
        k: [] for k in ("y", "c", "s", "z", "blk", "minutes", "min10", "m")
    }
    blocks = month_blocks(test_all)
    for b, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < MIN_TRAIN:
            continue
        t0 = time.time()
        model = ContextResidualModel(stat, cfg, names).fit(train)
        c, s = model.predict_components(block)
        n = block.height
        rows["gid"].append(block["game_id"].to_numpy().astype(str))
        rows["pid"].append(block["player_id"].to_numpy().astype(np.int64))
        rows["season"].append(block["season"].to_numpy())
        rows["y"].append(block["y"].to_numpy())
        rows["c"].append(c)
        rows["s"].append(s)
        rows["blk"].append(np.full(n, b))
        for k in ROW_COVARIATES:
            rows[k].append(block[k].cast(pl.Float64).to_numpy())
        _, cal_df = split_calibration(train, cfg)
        xc = cal_df.select([pl.col(k).cast(pl.Float64) for k in names]).to_numpy()
        mu = np.asarray(model.mean_head.predict(xc))
        sc = model._scale(xc)
        cal["y"].append(cal_df["y"].to_numpy())
        cal["c"].append(cal_df["m"].to_numpy() + mu)
        cal["s"].append(sc)
        cal["z"].append((cal_df["resid"].to_numpy() - mu) / sc)
        cal["blk"].append(np.full(cal_df.height, b))
        for k in ("minutes", "min10", "m"):
            cal[k].append(cal_df[k].cast(pl.Float64).to_numpy())
        if log:
            _log(
                f"{stat} block {b + 1}/{len(blocks)} {start} train {train.height} test {n} {time.time() - t0:.0f}s"
            )
    if not rows["c"]:
        raise ValueError("no month block had enough training rows")
    out = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    return out


def collect_arm_pi(
    frame: pl.DataFrame, cfg: ContextResidualConfig, test_seasons: tuple[int, ...] = SEASONS
) -> dict[str, np.ndarray]:
    pic = collect_pi(frame, cfg, stats=STATS, test_seasons=test_seasons)
    pic["row_gid"] = pic["row_gid"].astype(str)
    pic["row_pid"] = pic["row_pid"].astype(np.int64)
    return pic


def qi_of(comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], stat: str) -> np.ndarray:
    """Integer-support quantiles of the production lower-tail mixture (this arm's own heads, `pi`, `w_q`)."""
    if not (
        np.array_equal(comp["row_gid"].astype(str), pic["row_gid"])
        and np.array_equal(comp["row_pid"].astype(np.int64), pic["row_pid"])
    ):
        raise RuntimeError("heads rows are not aligned with the mixture rows")
    return to_integer_support(arm_grid("mixture", comp, pic, stat)).astype(np.int16)


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    return crps_from_quantiles(qi.astype(float), y)


def align_index(
    ref_gid: np.ndarray, ref_pid: np.ndarray, gid: np.ndarray, pid: np.ndarray
) -> np.ndarray:
    """Index into an arm's rows that lists the reference keys in the reference order; raises if any reference key is missing from the arm."""
    arm = pl.DataFrame(
        {"game_id": gid.astype(str), "player_id": pid.astype(np.int64), "_i": np.arange(len(gid))}
    )
    ref = pl.DataFrame({"game_id": ref_gid.astype(str), "player_id": ref_pid.astype(np.int64)})
    j = ref.join(arm, on=["game_id", "player_id"], how="left")
    if j.height != ref.height or j["_i"].null_count() > 0:
        raise RuntimeError("an evaluation key is missing from an arm")
    return j["_i"].to_numpy()


def slice_masks(
    cold_frame: pl.DataFrame,
    stat: str,
    gid: np.ndarray,
    pid: np.ndarray,
    exposed: frozenset[str] = frozenset(),
) -> dict[str, np.ndarray]:
    """Rule slices on the production (cold) frame's values at the evaluation keys. starter = starter10 >= 0.5; cold start = n_prior < 20 under
    A's count; first 15 team games = team_game_no < 15; playoffs = game_id prefix 004."""
    sf = stat_frame(cold_frame, stat).select(
        "game_id", "player_id", "starter10", "n_out_rot", "has_report", "n_prior", "team_game_no"
    )
    ref = pl.DataFrame({"game_id": gid.astype(str), "player_id": pid.astype(np.int64)}).join(
        sf, on=["game_id", "player_id"], how="left"
    )
    if ref["n_prior"].null_count() > 0:
        raise RuntimeError("slice join lost rows")
    st = ref["starter10"].to_numpy()
    return {
        "all": np.ones(len(gid), dtype=bool),
        "starter": st >= 0.5,
        "bench": st < 0.5,
        "n_out_rot>=1": ref["n_out_rot"].to_numpy() >= 1,
        "n_out_rot>=2": ref["n_out_rot"].to_numpy() >= 2,
        "has_report=0": ref["has_report"].to_numpy() == 0,
        "cold_start": ref["n_prior"].to_numpy() < 20,
        "first15": ref["team_game_no"].to_numpy() < 15,
        "playoffs": np.char.startswith(gid.astype(str), "004"),
        "unexposed": ~np.isin(gid.astype(str), list(exposed)),
    }


def build_bundle(
    arms: dict[str, dict[str, Any]],
    cold_frame: pl.DataFrame,
    game_dates: dict[str, str],
    exposed: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Per-row scores / PIT / slices of the three arms on A's rows (production's evaluation keys), from fitted arms
    ``arms[arm] = {"pic": ..., "comp": {stat: ...}}``. Integer grid for every arm; B and C are subset to A's keys."""
    out: dict[str, Any] = {"stats": {}, "pi": {}}
    for st in STATS:
        ca = arms["A"]["comp"][st]
        gid, pid = ca["row_gid"].astype(str), ca["row_pid"].astype(np.int64)
        y = ca["row_y"]
        u = np.random.default_rng(SEED).random(len(y))
        rec: dict[str, Any] = {
            "gid": gid,
            "pid": pid,
            "season": ca["row_season"],
            "date": np.array([game_dates[g] for g in gid]),
            "score": {},
            "pit": {},
            "slices": slice_masks(cold_frame, st, gid, pid, exposed),
        }
        for arm in ARMS:
            c = arms[arm]["comp"][st]
            idx = align_index(gid, pid, c["row_gid"], c["row_pid"])
            if not np.array_equal(c["row_y"][idx], y):
                raise RuntimeError(f"{st} arm {arm}: outcomes differ from A on the common keys")
            qi = qi_of(c, arms[arm]["pic"], st)[idx]
            rec["score"][arm] = crps_int(qi, y)
            rec["pit"][arm] = pit_from_qi(qi, y, u)
        out["stats"][st] = rec
    ca = arms["A"]["comp"]["pts"]
    gid, pid = ca["row_gid"].astype(str), ca["row_pid"].astype(np.int64)
    for arm in ARMS:
        pic = arms[arm]["pic"]
        c = arms[arm]["comp"]["pts"]
        idx_c = align_index(gid, pid, c["row_gid"], c["row_pid"])
        idx_p = align_index(gid, pid, pic["row_gid"], pic["row_pid"])
        short = short_flag(c["row_minutes"][idx_c], c["row_min10"][idx_c]).astype(float)
        out["pi"][arm] = {
            "p": pic["row_pi"][idx_p],
            "short": short,
            "season": c["row_season"][idx_c],
        }
    return out


# ------------------------------------------------------------------------------------ reproduction (R1) and label-free checks
def stored_dir(root: Path) -> Path:
    return root / LT_REL


def r1_reproduction(bundle_a: dict[str, Any], arms_a: dict[str, Any], root: Path) -> dict[str, Any]:
    """R1: A refit equals production's stored integer CRPS (per stat and season, means over the common keys): 2023 within 1e-4, 2024 within 2e-4."""
    pic_s = dict(np.load(stored_dir(root) / "components_pi.npz", allow_pickle=True))
    pic_s["row_gid"], pic_s["row_pid"] = (
        pic_s["row_gid"].astype(str),
        pic_s["row_pid"].astype(np.int64),
    )
    out: dict[str, Any] = {}
    ok = True
    for st in STATS:
        cs = load_components(st, stored_dir(root))
        cs = {k: (v.astype(str) if k == "row_gid" else v) for k, v in cs.items()}
        sc_s = crps_int(qi_of(cs, pic_s, st), cs["row_y"])
        b = bundle_a["stats"][st]
        idx = align_index(b["gid"], b["pid"], cs["row_gid"], cs["row_pid"])
        for s in SEASONS:
            m = b["season"] == s
            d = abs(float(b["score"]["A"][m].mean()) - float(sc_s[idx][m].mean()))
            good = d <= REPRO_TOL[s]
            ok &= good
            out[f"{st}_{s}"] = {"abs_diff": d, "tol": REPRO_TOL[s], "n": int(m.sum()), "pass": good}
    out["pass"] = bool(ok)
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _df_sha(df: pl.DataFrame) -> str:
    return hashlib.sha256(df.sort(df.columns).write_csv().encode()).hexdigest()


def content_fp(db: Path) -> str:
    """sha256 over the season <= 2024 games, box rows and availability rows of one database (read-only). Static rows are pinned separately
    (``static_fp``), merged the way the warm frame merges them."""
    games, pgs, _static, avail = gate.load_inputs(str(db), None)
    h = hashlib.sha256()
    for df in (games, pgs, avail):
        h.update(_df_sha(df).encode())
    return h.hexdigest()


def static_fp(root: Path) -> str:
    """sha256 of ``players_static`` as the warm frame uses it: both databases concatenated, ``unique(player_id, keep="last")``, semi-joined to the
    union of box player ids (a history-only player's row can be overridden by ``nba.duckdb``, which neither per-database hash sees)."""
    parts = [
        gate.load_inputs(str(root / d), None)
        for d in ("data/history/nba_history.duckdb", "nba.duckdb")
    ]
    static = pl.concat([p[2] for p in parts], how="vertical_relaxed").unique(
        "player_id", keep="last"
    )
    pids = pl.concat([p[1].select("player_id") for p in parts]).unique()
    return _df_sha(static.join(pids, on="player_id", how="semi"))


def schedule_fp(root: Path) -> str:
    h = hashlib.sha256()
    for d in ("data/schedule", "data/history/schedule"):
        for f in sorted((root / d).glob("raw_*.parquet")):
            m = re.search(r"(\d{4})-\d{2}$", f.stem)
            if m and int(m.group(1)) <= MAX_SEASON:
                h.update(f"{d}/{f.name}:{sha256_file(f)}\n".encode())
    return h.hexdigest()


def input_hashes(root: Path) -> dict[str, str]:
    out = {
        "HISTORY_DB_SHA256": sha256_file(root / "data/history/nba_history.duckdb"),
        "FP_NBA_LE2024": content_fp(root / "nba.duckdb"),
        "FP_HISTORY_LE2024": content_fp(root / "data/history/nba_history.duckdb"),
        "FP_STATIC_MERGED": static_fp(root),
        "FP_SCHEDULE_LE2024": schedule_fp(root),
        "GATE_SHA256": sha256_file(rule.ROOT / "reports/prereg_f1_warm_window/gate.json"),
    }
    for st in STATS:
        out[f"COMPONENTS_SHA256_{st.upper()}"] = sha256_file(
            stored_dir(root) / f"components_{st}.npz"
        )
    out["COMPONENTS_SHA256_PI"] = sha256_file(stored_dir(root) / "components_pi.npz")
    return out


def preflight(
    root: Path, declared: dict[str, str], allow_undeclared: bool = False
) -> dict[str, Any]:
    """Label-free: library versions equal the pinned set; every input hash equals the line pinned in the rule. No outcome array is read."""
    ver = require_versions()
    got = input_hashes(root)
    bad = {
        k: (v[:12], declared.get(k, "missing")[:12]) for k, v in got.items() if declared.get(k) != v
    }
    if bad and not allow_undeclared:
        raise RuntimeError(f"input hashes differ from the rule's pinned lines (got, pinned): {bad}")
    return {"versions": ver, "hashes": got, "mismatch_vs_rule": bad}


def _nan_max_abs(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or not np.array_equal(np.isnan(a), np.isnan(b)):
        return float("inf")
    f = ~np.isnan(a)
    return float(np.abs(a[f] - b[f]).max()) if f.any() else 0.0


def smoke_block0(
    root: Path, frames: dict[str, pl.DataFrame], cfg: ContextResidualConfig, stat: str = "pts"
) -> dict[str, Any]:
    """Label-free A reproduction on the first month block (2023-10): refit A on rows before 2023-10-01 and compare the heads' centre / scale and
    `pi` / `w_q` with the stored production components. No score, no delta, no statistic of any outcome is computed (``row_y`` is dropped)."""
    fr = windowed(frames["cold"], "A").filter(pl.col("game_date") < dt.date(2023, 11, 1))
    t0 = time.time()
    comp = collect_heads(fr, stat, cfg, test_seasons=(2023,))
    pic = collect_arm_pi(fr, cfg, test_seasons=(2023,))
    comp.pop("row_y")
    cs = load_components(stat, stored_dir(root))
    pic_s = dict(np.load(stored_dir(root) / "components_pi.npz", allow_pickle=True))
    m = cs["row_blk"] == 0
    gid_s, pid_s = cs["row_gid"][m].astype(str), cs["row_pid"][m].astype(np.int64)
    idx = align_index(gid_s, pid_s, comp["row_gid"], comp["row_pid"])
    d_c = float(np.abs(comp["row_c"][idx] - cs["row_c"][m]).max())
    d_s = float(np.abs(comp["row_s"][idx] - cs["row_s"][m]).max())
    d_pi = float(
        np.abs(
            pic["row_pi"][align_index(gid_s, pid_s, pic["row_gid"], pic["row_pid"])]
            - pic_s["row_pi"][m]
        ).max()
    )
    d_wq = _nan_max_abs(pic[f"wq_{stat}"][0], pic_s[f"wq_{stat}"][0])
    return {
        "stat": stat,
        "rows": int(m.sum()),
        "max_abs_diff": {"row_c": d_c, "row_s": d_s, "pi": d_pi, "w_q": d_wq},
        "pass": bool(max(d_c, d_s, d_pi, d_wq) <= HEADS_TOL),
        "seconds_one_arm_one_stat_one_block_plus_pi": round(time.time() - t0, 1),
    }


# ------------------------------------------------------------------------------------ synthetic rehearsal (no data, no fit)
WORLD_EFFECT: dict[
    str, dict[tuple[str, int], float]
] = {}  # (stat, season) -> true C minus A mean (negative = C better)


def _world_effect(world: str, stat: str, season: int) -> float:
    big = -0.012 if stat == "pts" else -0.005
    if world in ("helps", "guard", "stamp"):
        return big
    if world == "one_season":
        return big if season == 2023 else 0.0
    if world == "harm":
        return 0.008 if (stat == "pts" and season == 2024) else 0.0
    if world == "null":
        return 0.0
    raise ValueError(world)


def synthetic_bundle(world: str, seed: int = 0) -> dict[str, Any]:
    """Per-row synthetic scores with game-clustered noise at the widths the gate sized (pts SE ~0.002, counts ~0.0008) and a known true effect,
    to drive ``evaluate``. B sits halfway (descriptive). Not a fit and not data: it rehearses the decision pipeline only."""
    rng = np.random.default_rng(seed)
    n_games, per = 1300, 21
    out: dict[str, Any] = {"stats": {}, "pi": {}}
    parts = {s: (np.repeat(np.arange(n_games), per)) for s in SEASONS}
    seasons = np.concatenate([np.full(n_games * per, s) for s in SEASONS])
    gid = np.concatenate(
        [np.char.add(f"{s}", np.char.zfill(parts[s].astype(str), 5)) for s in SEASONS]
    )
    date = np.concatenate(
        [np.char.add(f"{s}-", np.char.zfill((parts[s] // 8 % 200).astype(str), 3)) for s in SEASONS]
    )
    n = len(gid)
    for st in STATS:
        scale = 1.0 if st == "pts" else 0.4
        base = 1.5 + 0.2 * rng.standard_normal(n)
        eff = np.array([_world_effect(world, st, int(s)) for s in seasons])
        exposed_row = (np.concatenate([parts[s] for s in SEASONS]) % 20) == 0  # 5% of games
        if world == "stamp":  # the whole gain sits in the exposed games (same overall mean)
            eff = np.where(exposed_row, eff / 0.05, 0.0)
        game_eff = (
            np.concatenate([rng.standard_normal(n_games)[parts[s]] for s in SEASONS]) * 0.07 * scale
        )
        d = eff + game_eff + 0.1 * scale * rng.standard_normal(n)
        pit_a = rng.random(n)
        pit_c = rng.random(n)
        if world == "guard":
            pit_c = pit_c**1.5  # miscalibrated tails: coverage at tau 0.20 / 0.80 moves by ~0.06
        score_b = base + 0.5 * d + 0.1 * scale * rng.standard_normal(n)
        out["stats"][st] = {
            "gid": gid,
            "pid": np.arange(n),
            "season": seasons,
            "date": date,
            "score": {"A": base, "B": score_b, "C": base + d},
            "pit": {"A": pit_a, "B": pit_a, "C": pit_c},
            "slices": {
                **{
                    k: rng.random(n) < p
                    for k, p in zip(
                        SLICE_NAMES[:-1],
                        (1.0, 0.4, 0.6, 0.8, 0.5, 0.02, 0.2, 0.15, 0.06),
                        strict=True,
                    )
                },
                "unexposed": ~exposed_row,
            },
        }
    pa = rng.beta(1, 8, n)
    short = (rng.random(n) < pa).astype(float)
    out["pi"] = {a: {"p": pa, "short": short, "season": seasons} for a in ARMS}
    return out


# ------------------------------------------------------------------------------------ the run
def heavy_lock(root: Path) -> Path:
    return root / "data/ops/heavy.lock"


def acquire_lock(root: Path, wait_s: int = 120) -> Path:
    p = heavy_lock(root)
    t0 = time.time()
    while True:
        try:
            p.mkdir()
            return p
        except FileExistsError:
            if time.time() - t0 > wait_s:
                raise RuntimeError(f"{p} is held: another heavy job is running") from None
            time.sleep(10)


def _ckpt(root: Path, arm: str, name: str) -> Path:
    return root / CACHE_REL / f"{arm}_{name}.npz"


def _save(path: Path, tag: str, d: dict[str, np.ndarray]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, _tag=np.array(tag), **d)


def _load(path: Path, tag: str) -> dict[str, np.ndarray] | None:
    if not path.exists():
        return None
    z = np.load(path, allow_pickle=True)
    if str(z["_tag"]) != tag:
        return None
    return {k: z[k] for k in z.files if k != "_tag"}


def fit_arms(
    root: Path,
    frames: dict[str, pl.DataFrame],
    cfg: ContextResidualConfig,
    tag: str,
    arms: tuple[str, ...] = ARMS,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for arm in arms:
        fr = windowed(frames[FRAME_OF[arm]], arm)
        _log(f"arm {arm}: frame {FRAME_OF[arm]} from {WINDOW[arm]}, {fr.height} rows")
        pic = _load(_ckpt(root, arm, "pic"), tag)
        if pic is None:
            pic = collect_arm_pi(fr, cfg)
            _save(_ckpt(root, arm, "pic"), tag, pic)
        comps = {}
        for st in STATS:
            c = _load(_ckpt(root, arm, st), tag)
            if c is None:
                c = collect_heads(fr, st, cfg)
                _save(_ckpt(root, arm, st), tag, c)
            else:
                _log(f"arm {arm} {st}: from checkpoint")
            comps[st] = c
        out[arm] = {"pic": pic, "comp": comps}
    return out


def exposed_games(root: Path) -> frozenset[str]:
    """Stamp-exposed 2023-24 game ids from the committed gate JSON (its sha256 is a pinned line, checked by the preflight)."""
    g = json.loads((rule.ROOT / "reports/prereg_f1_warm_window/gate.json").read_text())
    return frozenset(
        i for s in ("2023", "2024") for i in g["stamp_exposure"][s]["exposed_game_ids"]
    )


def run_real(root: Path, sha: str) -> dict[str, Any]:
    if Path.cwd().resolve() != rule.ROOT:
        raise RuntimeError(f"cwd must be the pinned checkout {rule.ROOT}")
    blobs = rule.require_ready(sha)
    pin = rule.pin_of(blobs)
    _log(f"rule frozen and harness pinned: {pin[:12]}")
    pre = preflight(root, rule.declared())
    _log("preflight: library versions and every pinned input hash equal; no outcome scored")
    lock = acquire_lock(root)
    try:
        frames, cfg = load_frames(root)
        chk = smoke_block0(root, frames, cfg)
        if not chk["pass"]:
            raise RuntimeError(
                f"STOP before any score: A first block does not equal the stored production components: {chk}"
            )
        _log("A first block equals the stored production heads, pi and w_q (max abs diff 0.0)")
        planted = gate.run(root, root / "data/ops/f1_gate_rerun.json")["planted_future"]
        if planted["max_abs_diff"] != 0.0 or planted["columns_differing"]:
            raise RuntimeError(
                f"STOP before any arm is fitted: planted-future test failed (R4/G4): {planted}"
            )
        _log("planted-future re-run (R4/G4) holds; fitting the arms")
        arms = fit_arms(root, frames, cfg, tag=f"{sha}:{pin}")
        games = frames["cold"].select("game_id", "game_date").unique()
        gd = {g: str(d) for g, d in zip(games["game_id"], games["game_date"], strict=True)}
        bundle = build_bundle(arms, frames["cold"], gd, exposed_games(root))
        repro = r1_reproduction(bundle, arms["A"], root)
        if not repro["pass"]:
            raise RuntimeError(f"STOP: R1 failed, the comparator is not production: {repro}")
        res = evaluate(bundle, gates_ok=True, repro_ok=True)
        res.update(
            {
                "rule_sha": sha,
                "pin": pin,
                "preflight": pre,
                "smoke_block0": chk,
                "r1": repro,
                "planted_future": planted,
                "versions": versions(),
            }
        )
        return res
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--data-root", type=Path, default=Path(os.environ.get("F1_ROOT", rule.ROOT)))
    ap.add_argument("--sha", default=None)
    ap.add_argument("--world", default="helps")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "rehearse":
        res = evaluate(synthetic_bundle(a.world))
        print(
            json.dumps(
                {"world": a.world, "word": res["word"], "stats": res["stats"]},
                indent=1,
                default=str,
            )
        )
        return 0
    if a.cmd == "check":
        pre = preflight(a.data_root, rule.declared(), allow_undeclared=True)
        print(json.dumps(pre, indent=1))
        if a.smoke:
            frames, cfg = load_frames(a.data_root)
            print(json.dumps(smoke_block0(a.data_root, frames, cfg), indent=1))
        return 0
    res = run_real(a.data_root, a.sha)
    out = a.out or (rule.ROOT / OUT_DIR / "results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out}: {res['word']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
