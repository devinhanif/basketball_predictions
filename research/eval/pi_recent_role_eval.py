# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""PI_RECENT_ROLE harness (rule: docs/prereg/PI_RECENT_ROLE.md; DRAFT until the main session commits `prereg(PI_RECENT_ROLE): <sha256>`).

Candidate = the lower-tail mixture with the short-minutes classifier `pi` refit on COMMON_FEATURES + the 5 RECENT_ROLE columns.
Nothing else moves: the stat heads, calibration windows and short-ratio quantiles `w_q` are the stored A0 components (`w_q` and the heads do
not depend on the classifier), the blocks are the production month blocks, the learner is production's binary LightGBM, seed 0.

    M="-m research.eval.pi_recent_role_eval"
    .venv/bin/python -W ignore $M gate --allow-draft         # minimum-data gates before the freeze (control pi refit only is scored)
    .venv/bin/python -W ignore $M gate  --sha <64 hex>       # the same, frozen (writes gate.json)
    .venv/bin/python -W ignore $M arms  --sha <64 hex>       # pi walks: CAND, PL, shuffled CTRL/CAND (heavy.lock)
    .venv/bin/python -W ignore $M score --sha <64 hex>       # integer CRPS vs A0, guards, attacks, verdict, report (light)
    .venv/bin/python -W ignore $M run   --sha <64 hex>       # gate -> arms -> score under ONE data/ops/heavy.lock
    .venv/bin/python -W ignore $M holdout --sha <64 hex>     # Stage 2: refuses unless a committed, confirmed log row exists; no 2025 loader exists

Every stage except `gate --allow-draft` refuses unless the text above `=== RESULTS BELOW ===` hashes to `--sha` AND a commit whose subject is
`prereg(PI_RECENT_ROLE): <sha>` is reachable from HEAD. `nba.duckdb` is read-only; `season <= 2024` is asserted; season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import (
    MAX_SEASON,
    TEST_SEASONS,
    bh_adjust,
    crps_from_quantiles,
    month_blocks,
)
from nba.eval.lower_tail_eval import load_components
from nba.props.context_residual import (
    COMMON_FEATURES,
    CRPS_TAUS,
    PROP_STATS,
    ContextResidualConfig,
    _matrix,
    _params,
    stat_frame,
)
from nba.props.lower_tail import fit_short_state, predict_pi, short_flag
from research.eval import workload_eval as WE
from research.recent_role import placebo as PB
from research.workload import asof as W

ID = "PI_RECENT_ROLE"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/PI_RECENT_ROLE.md"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
OUT_DIR = ROOT / "reports/prereg_pi_recent_role"
REPORT_MD = ROOT / "reports/prereg_pi_recent_role.md"
CACHE = ROOT / "data/ops/pi_recent_role_cache"
LT_DIR = ROOT / "reports/lower_tail"
PAIRS_DIR = ROOT / "data/ops/model_clv_pairs"
DB = str(ROOT / "nba.duckdb")
KEYS = ["game_id", "player_id"]
STATS = PROP_STATS
SEASONS = (2023, 2024)
SEED = 0
MIN_TRAIN = 1500
PL_PREFIX = "pl_"

R_COLS: tuple[str, ...] = (
    "wl_min_last",
    "wl_mean_min_l3",
    "wl_min_3d",
    "wl_min_7d",
    "wl_mean_min_l5",
)
CAND_COLS: tuple[str, ...] = (*COMMON_FEATURES, *R_COLS)
PLACEBO_COLS: tuple[str, ...] = (*COMMON_FEATURES, *[PL_PREFIX + c for c in R_COLS])

FLOOR = -0.005  # integer CRPS, candidate minus A0 (house props floor)
PLACEBO_MARGIN = -0.003  # CAND must beat its past-only placebo by this much, per stat and season
SHUF_TOL = -0.001  # shuffled-label check FAILS if CAND_shuf - CTRL_shuf <= this with CI upper < 0 (one fifth of the floor)
ECE_TOL = 0.005
SLICE_TOL = 0.01
MIN_SLICE_N = 300
REPRO_TOL = 1e-6  # A0 reproduction from stored components (CRPS)
CTRL_PI_TOL = 1e-6  # control refit of pi vs the stored pi, max abs diff
GUARD_SLICES: tuple[str, ...] = ("min<5", "bench", "playoffs", "cold_start")
A0_REF = {"pts": {2023: 3.140236, 2024: 3.176840}}  # the numbers the rule quotes


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze and holdout refusal
def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path = DOC, git: Callable[..., str] = _git) -> str:
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = WE.frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; no arm may be fitted"
        )
    return sha


def require_holdout_row(
    sha: str, log: Path = HOLDOUT_LOG, git: Callable[..., str] = _git, doc: Path | None = None
) -> str:
    """Stage 2 gate: a committed, unmodified HOLDOUT_ACCESS_LOG row naming this id and sha8 with PRE-REGISTERED and DEVIN CONFIRMED."""
    require_frozen(sha, doc or DOC, git)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            f"no committed holdout row for {ID} (PRE-REGISTERED, DEVIN CONFIRMED, sha8): Stage 2 refuses"
        )
    if git("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(
            "HOLDOUT_ACCESS_LOG.md has uncommitted changes: commit the row BEFORE the run"
        )
    if not git("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is not in any commit: Stage 2 refuses")
    return rows[0]


# --------------------------------------------------------------------------- frame
def build_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    """Production feature rows (played rows, `build_features`) + the 5 RECENT_ROLE columns + the past-only placebo of them."""
    from research.eval import four_factors_eval as F

    study = F.load_study(DB)
    feats = study["feats"].with_columns(pl.col("player_id").cast(pl.Int64))
    WE.assert_seasons(feats)
    src = WE.load_sources(DB)
    load = W.player_load_features(src["played"], feats.select("game_id", "player_id", "game_date"))
    load = load.rename({c: W.PREFIX + c for c in load.columns if c in W.ALL_COLS}).select(
        *KEYS, *R_COLS
    )
    fr = feats.join(load, on=KEYS, how="left")
    if (
        fr.height != feats.height
        or fr.select(KEYS).unique().height != feats.select(KEYS).unique().height
    ):
        raise ValueError("attaching the R columns changed the rows")
    dup = sorted(set(R_COLS) & set(feats.columns))
    if dup:
        raise ValueError(f"R columns already in the feature frame: {dup}")
    fr = PB.permute_past_only(fr, R_COLS, SEED, PL_PREFIX)
    WE.assert_seasons(fr)
    return fr, study["cfg"], {"src": src, "feats": feats}


# --------------------------------------------------------------------------- the pi walk
def walk_pi(
    frame: pl.DataFrame,
    xcols: list[str] | tuple[str, ...],
    cfg: ContextResidualConfig,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    shuffle: bool = False,
    permute: bool = True,
    seed: int = SEED,
    n_estimators: int | None = None,
    min_train: int = MIN_TRAIN,
    log: bool = True,
) -> dict[str, Any]:
    """Month-block walk-forward of the short-minutes classifier, exactly as `lower_tail_eval.collect_pi` (production's `fit_short_state`,
    `predict_pi`, the `pts` stat frame, blocks that start with < ``min_train`` earlier rows are skipped) with an explicit feature list.

    ``shuffle``: the classifier is trained on ONE permutation of the training short labels per block, drawn from ``default_rng([seed, block])``
    (global over the training rows) so two arms with different feature lists see the SAME permuted labels; ``permute=False`` is the old
    defect (labels not permuted) and exists only so the harness can show its own check can fail. The real labels are never used to fit
    when ``shuffle``. Returns row arrays plus a per-block hash of the labels each fit used."""
    WE.assert_seasons(frame)
    sf0 = stat_frame(frame, "pts")
    test_all = sf0.filter(pl.col("season").is_in(list(test_seasons)))
    params = _params(cfg, "binary")
    if n_estimators is not None:
        params["n_estimators"] = n_estimators
    cols = list(xcols)
    out: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "blk", "pi", "short", "minutes", "min10")
    }
    hashes: list[str] = []
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        sel = (pl.col("game_date") >= start) & (pl.col("game_date") < end)
        block = sf0.filter(sel).filter(pl.col("season").is_in(list(test_seasons)))
        train0 = sf0.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train0.height < min_train:
            continue
        short = short_flag(train0["minutes"].to_numpy(), train0["min10"].to_numpy())
        y_fit = short
        if shuffle and permute:
            y_fit = short[np.random.default_rng([seed, bi]).permutation(len(short))]
        hashes.append(
            hashlib.sha256(np.ascontiguousarray(y_fit.astype(np.int8)).tobytes()).hexdigest()[:16]
        )
        ones = np.ones(train0.height)
        state = fit_short_state(_matrix(train0, cols), y_fit, ones, ones, CRPS_TAUS, params)
        pi = predict_pi(state, _matrix(block, cols))
        n = block.height
        out["gid"].append(block["game_id"].to_numpy().astype(str))
        out["pid"].append(block["player_id"].to_numpy().astype(np.int64))
        out["season"].append(block["season"].to_numpy())
        out["blk"].append(np.full(n, bi))
        out["pi"].append(pi)
        out["short"].append(short_flag(block["minutes"].to_numpy(), block["min10"].to_numpy()))
        out["minutes"].append(block["minutes"].cast(pl.Float64).to_numpy())
        out["min10"].append(block["min10"].cast(pl.Float64).to_numpy())
        if log:
            _log(f"pi block {bi + 1}/{len(blocks)} {start} n_test={n} train={train0.height}")
    if not out["pi"]:
        raise ValueError("no month block had enough training rows")
    res: dict[str, Any] = {k: np.concatenate(v) for k, v in out.items()}
    res["label_hashes"] = hashes
    return res


def align_to_stored(
    res: dict[str, Any], stored_gid: np.ndarray, stored_pid: np.ndarray
) -> np.ndarray:
    """`pi` of a walk in the stored component row order (the stored `components_pi.npz`); raises on any missing key."""
    mine = pl.DataFrame({"game_id": res["gid"], "player_id": res["pid"], "_pi": res["pi"]})
    want = pl.DataFrame(
        {"game_id": stored_gid.astype(str), "player_id": stored_pid.astype(np.int64)}
    )
    j = want.join(mine, on=KEYS, how="left")
    if j.height != want.height or j["_pi"].null_count() > 0:
        raise RuntimeError("the walk's rows do not cover the stored component rows")
    return j["_pi"].to_numpy()


def candidate_pic(stored: dict[str, np.ndarray], pi: np.ndarray) -> dict[str, np.ndarray]:
    """Stored `pic` with only `row_pi` replaced (w_q per block is unchanged: it depends on the training short flag and the ratios, not on the classifier)."""
    out = dict(stored)
    out["row_pi"] = pi
    return out


# --------------------------------------------------------------------------- scoring
def ece_bins(p: np.ndarray, y: np.ndarray) -> float:
    return float(WE.ece(p, y.astype(float)))


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """Rank AUC with tie-averaged ranks (no sklearn)."""
    order = np.argsort(p, kind="stable")
    ranks = np.empty(len(p))
    ps = p[order]
    i = 0
    r = np.arange(1, len(p) + 1, dtype=float)
    while i < len(p):
        j = i
        while j + 1 < len(p) and ps[j + 1] == ps[i]:
            j += 1
        r[i : j + 1] = r[i : j + 1].mean()
        i = j + 1
    ranks[order] = r
    n1 = float(y.sum())
    n0 = len(y) - n1
    return float((ranks[y.astype(bool)].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def crps_int(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    return crps_from_quantiles(qi.astype(float), y)


def mixture_qi_pi(
    comp: dict[str, np.ndarray], pic: dict[str, np.ndarray], pi: np.ndarray, stat: str
) -> np.ndarray:
    return WE.mixture_qi(comp, candidate_pic(pic, pi), stat)


def slice_masks(comp: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Declared slices on the played rows of a stat component file. `bench` is as-of (starter10 < 0.5); `min<5` is realised minutes (the rows
    the lower tail exists for); `cold_start` is n_prior < 10 (rows enter at n_prior >= 5)."""
    mins, m10 = comp["row_minutes"], comp["row_min10"]
    gid = comp["row_gid"].astype(str)
    short = short_flag(mins, m10)
    return {
        "all": np.ones(len(mins), dtype=bool),
        "min<5": mins < 5,
        "bench": comp["row_starter10"] < 0.5,
        "playoffs": np.char.startswith(gid, "004"),
        "cold_start": comp["row_n_prior"] < 10,
        "starter": comp["row_starter10"] >= 0.5,
        "short(<0.5*proj)": short,
        "not short": ~short,
        "min<10": mins < 10,
    }


def shuffle_check(row: dict[str, Any]) -> dict[str, Any]:
    """FAILS when a model trained on permuted labels still shows the candidate beating the control by <= SHUF_TOL with CI upper < 0."""
    failed = bool(row["delta"] <= SHUF_TOL and row["ci_hi"] < 0)
    return {
        "stat": row.get("stat"),
        "season": row["season"],
        "delta": row["delta"],
        "ci_hi": row["ci_hi"],
        "ok": not failed,
    }


def pi_component_rows(
    pis: dict[str, np.ndarray], short: np.ndarray, gid: np.ndarray, season: np.ndarray
) -> list[dict[str, Any]]:
    """Descriptive: short-flag log loss, ECE and AUC per arm and season; paired differences vs A0 with game-clustered CIs."""
    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        m = season == s
        y = short[m].astype(float)
        for arm, p in pis.items():
            rows.append(
                {
                    "season": s,
                    "arm": arm,
                    "n": int(m.sum()),
                    "logloss": float(WE._ll(p[m], y).mean()),
                    "ece": ece_bins(p[m], y),
                    "auc": auc(p[m], y),
                    "mean_pi": float(p[m].mean()),
                    "observed": float(y.mean()),
                }
            )
        for arm in pis:
            if arm in ("A0", "ORACLE"):
                continue
            d = WE._ll(pis[arm][m], y) - WE._ll(pis["A0"][m], y)
            rows.append(
                {
                    "season": s,
                    "arm": f"{arm}_minus_A0",
                    "n": int(m.sum()),
                    **WE.delta_stats(d, gid[m]),
                    "ece_delta": ece_bins(pis[arm][m], y) - ece_bins(pis["A0"][m], y),
                }
            )
    return rows


def score_stat(
    stat: str, arms_pi: dict[str, np.ndarray], pic: dict[str, np.ndarray]
) -> dict[str, Any]:
    """Per-row integer CRPS of every arm on the stored components of one stat, plus deltas vs A0 (overall, slices, placebo, shuffle).
    ``arms_pi``: A0 (stored pi), CAND, PL, SHUF_CTRL, SHUF_CAND and optionally ORACLE (pi = realised short flag; descriptive)."""
    comp = load_components(stat, LT_DIR)
    y, gid, season = comp["row_y"], comp["row_gid"].astype(str), comp["row_season"]
    scores = {arm: crps_int(mixture_qi_pi(comp, pic, pi, stat), y) for arm, pi in arms_pi.items()}
    masks = slice_masks(comp)
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        n_games = int(len(np.unique(gid[sm])))
        for arm, sc in scores.items():
            rows.append(
                {
                    "stat": stat,
                    "season": s,
                    "arm": arm,
                    "n_rows": int(sm.sum()),
                    "n_games": n_games,
                    "crps_int": float(sc[sm].mean()),
                }
            )
        for a, b in (
            ("CAND", "A0"),
            ("PL", "A0"),
            ("CAND", "PL"),
            ("SHUF_CAND", "SHUF_CTRL"),
            ("ORACLE", "A0"),
        ):
            if a in scores and b in scores:
                rows.append(
                    {
                        "stat": stat,
                        "season": s,
                        "arm": f"{a}_minus_{b}",
                        "n_rows": int(sm.sum()),
                        "n_games": n_games,
                        **WE.delta_stats(scores[a][sm] - scores[b][sm], gid[sm]),
                    }
                )
        d = scores["CAND"] - scores["A0"]
        for name, mk in masks.items():
            m = mk & sm
            rec: dict[str, Any] = {"stat": stat, "season": s, "slice": name, "n": int(m.sum())}
            if m.sum() >= MIN_SLICE_N or name == "all":
                ds = WE.delta_stats(d[m], gid[m])
                rec.update({k: ds[k] for k in ("delta", "ci_lo", "ci_hi")})
            slices.append(rec)
    return {
        "rows": rows,
        "slices": slices,
        "crps": {k: v.astype(np.float32) for k, v in scores.items()},
    }


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the m = 4 stats within each season for the primary contrast CAND - A0 (adds `p_bh`)."""
    for s in SEASONS:
        tgt = [r for r in rows if r["season"] == s and r["arm"] == "CAND_minus_A0"]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


def decide(res: dict[str, Any], gate_ok: bool) -> dict[str, Any]:
    """The frozen pass rule. Per stat: 2023 delta <= FLOOR with CI upper < 0 and BH p <= 0.05; 2024 the same; pi ECE no worse by > ECE_TOL in
    both seasons; no guard slice (n >= 300) worse by more than SLICE_TOL; CAND beats the past-only placebo by <= PLACEBO_MARGIN in both seasons;
    the shuffled-label check holds for EVERY stat (the classifier is shared); gate and plants clean."""
    r = {(x["stat"], x["season"], x["arm"]): x for x in res["rows"] if "stat" in x}
    comp = {(x["season"], x["arm"]): x for x in res["pi_rows"]}
    ece_ok = all(comp[(s, "CAND_minus_A0")]["ece_delta"] <= ECE_TOL for s in SEASONS)
    shuf = [
        shuffle_check({**r[(st, s, "SHUF_CAND_minus_SHUF_CTRL")], "stat": st})
        for st in STATS
        for s in SEASONS
    ]
    shuf_ok = all(x["ok"] for x in shuf)
    per: dict[str, Any] = {}
    for st in STATS:
        a, b = r[(st, 2023, "CAND_minus_A0")], r[(st, 2024, "CAND_minus_A0")]
        c23, c24 = (
            a["delta"] <= FLOOR and a["ci_hi"] < 0 and a.get("p_bh", 1.0) <= 0.05,
            b["delta"] <= FLOOR and b["ci_hi"] < 0 and b.get("p_bh", 1.0) <= 0.05,
        )
        worse = [
            (x["season"], x["slice"], x["n"], x["delta"])
            for x in res["slices"]
            if x["stat"] == st
            and x["slice"] in GUARD_SLICES
            and x.get("n", 0) >= MIN_SLICE_N
            and x.get("delta", -1.0) > SLICE_TOL
        ]
        plc = all(r[(st, s, "CAND_minus_PL")]["delta"] <= PLACEBO_MARGIN for s in SEASONS)
        per[st] = {
            "pass": bool(c23 and c24 and ece_ok and not worse and plc and shuf_ok and gate_ok),
            "select_2023": bool(c23), "report_2024": bool(c24), "ece_ok": bool(ece_ok), "slices_ok": not worse, "slices_worse": worse,
            "placebo_ok": bool(plc), "shuffle_ok": bool(shuf_ok), "gate_ok": bool(gate_ok),
            "d2023": a["delta"], "d2024": b["delta"], "mde2024": b["mde"],
        }  # fmt: skip
    return {
        "per_stat": per,
        "any_pass": any(v["pass"] for v in per.values()),
        "shuffle": shuf,
        "ece_ok": ece_ok,
    }


# --------------------------------------------------------------------------- secondary: DraftKings T-60 decision score (descriptive)
def decision_scores(qi_by_arm: dict[str, dict[str, dict[str, np.ndarray]]]) -> dict[str, Any]:
    """D2-style: DraftKings T-60 legs re-priced with each arm's integer grid. Cannot rescue or fail the rule."""
    try:
        from nba.markets import decision_scoring as DS
        from research.eval.team_budget_eval import legs_with_p, p_over_from_grid

        legs = DS.load_legs(PAIRS_DIR)
        out: dict[str, Any] = {}
        for armname, by_stat in qi_by_arm.items():
            p = np.full(len(legs["line"]), np.nan)
            for stat, a in by_stat.items():
                idx = pl.DataFrame(
                    {"game_id": a["gid"], "player_id": a["pid"].astype(np.int64)}
                ).with_row_index("_i")
                m = np.flatnonzero(legs["stat"] == stat)
                lk = pl.DataFrame(
                    {
                        "game_id": legs["game_id"][m],
                        "player_id": legs["player_id"][m].astype(np.int64),
                        "_p": np.arange(len(m)),
                    }
                )
                j = lk.join(idx, on=KEYS, how="left")
                ok = j["_i"].is_not_null().to_numpy()
                pp = p_over_from_grid(a["qi"][j["_i"].fill_null(0).to_numpy()], legs["line"][m])
                p[m[ok]] = pp[ok]
            keep = ~np.isnan(p)
            lg = {
                k: (v[keep] if isinstance(v, np.ndarray) and v.shape[:1] == keep.shape else v)
                for k, v in legs.items()
            }
            gm = {g: i for i, g in enumerate(np.unique(lg["game_id"]))}
            lg["gi"], lg["G"] = np.array([gm[g] for g in lg["game_id"]]), len(gm)
            r = DS.run_all(legs_with_p(lg, p[keep]))
            out[armname] = {
                "n_legs": int(keep.sum()),
                "cells": [
                    {
                        "season": c["season"],
                        "stat": c["stat"],
                        "by_threshold": [
                            {"threshold": t["threshold"], "model": t["model"]}
                            for t in c["by_threshold"]
                        ],
                    }
                    for c in r["cells"]
                ],
            }
        return out
    except Exception as exc:  # secondary; cannot rescue or fail the rule
        return {"error": repr(exc)}


# --------------------------------------------------------------------------- gate
def planted_walk(fr: pl.DataFrame, cfg: ContextResidualConfig) -> list[dict[str, Any]]:
    """As-of plant on the real frame: overwrite every label (`minutes`) and R column of rows dated on/after a mid-2024 cutoff; the candidate pi of
    every row dated before the cutoff must be bit-identical, and rows after the cutoff must change (non-inert). Nothing is scored."""
    chk = WE._check
    d24 = fr.filter(pl.col("season") == 2024).sort("game_date")["game_date"]
    cut = d24[d24.len() // 2]
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
        *[pl.when(late).then(48.0).otherwise(pl.col(c)).alias(c) for c in R_COLS],
    )
    a = walk_pi(fr, CAND_COLS, cfg, test_seasons=(2024,), log=False)
    b = walk_pi(edited, CAND_COLS, cfg, test_seasons=(2024,), log=False)
    ja = pl.DataFrame({"game_id": a["gid"], "player_id": a["pid"], "pa": a["pi"]})
    jb = pl.DataFrame({"game_id": b["gid"], "player_id": b["pid"], "pb": b["pi"]})
    date = fr.select("game_id", "player_id", "game_date").with_columns(
        pl.col("player_id").cast(pl.Int64)
    )
    j = ja.join(jb, on=KEYS).join(date, on=KEYS)
    pre, post = j.filter(pl.col("game_date") < cut), j.filter(pl.col("game_date") >= cut)
    # rows in the cutoff's own month dated after the cutoff see edited features, so the control uses blocks that START after the cutoff
    nxt = post.filter(
        pl.col("game_date")
        >= (cut.replace(day=1).replace(month=cut.month % 12 + 1, year=cut.year + (cut.month == 12)))
    )
    same = float(np.abs(pre["pa"].to_numpy() - pre["pb"].to_numpy()).max()) if pre.height else 0.0
    moved = float(np.abs(nxt["pa"].to_numpy() - nxt["pb"].to_numpy()).max()) if nxt.height else 0.0
    return [
        chk(
            "planted future (walk): candidate pi of rows dated before the cutoff identical",
            {"cutoff": str(cut), "n_rows": pre.height, "max_abs_diff": same},
            "== 0",
            same == 0.0,
        ),
        chk(
            "control (walk): pi of rows in later blocks change",
            {"n_rows": nxt.height, "max_abs_diff": moved},
            "> 0",
            moved > 0.0,
        ),
    ]


def run_gate(sha: str | None, draft: bool = False) -> dict[str, Any]:
    """Minimum-data gates. The only arm SCORED is the control refit (COMMON_FEATURES only), which must equal the stored A0 pi; the planted-walk
    fits candidate-feature classifiers on edited data and compares them with each other, never with a label. No candidate score is computed."""
    if not draft:
        require_frozen(sha)
    t0 = time.time()
    chk = WE._check
    tests: list[dict[str, Any]] = []
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    refs = json.loads((LT_DIR / "candidates.json").read_text())
    cells = []
    for st in STATS:
        comp = load_components(st, LT_DIR)
        if int(comp["row_season"].max()) > MAX_SEASON:
            raise RuntimeError("season > 2024 in components")
        if not (
            np.array_equal(comp["row_gid"], pic["row_gid"])
            and np.array_equal(comp["row_pid"], pic["row_pid"])
        ):
            raise RuntimeError("pi components not row-aligned")
        qi = WE.mixture_qi(comp, pic, st)
        c = crps_int(qi, comp["row_y"])
        for s in SEASONS:
            m = comp["row_season"] == s
            cells.append(
                {
                    "stat": st,
                    "season": s,
                    "crps_int": float(c[m].mean()),
                    "reference": float(refs[st][str(s)]["mixture"]["crps_int"]),
                    "n": int(m.sum()),
                    "n_ref": int(refs[st][str(s)]["off"]["n"]),
                }
            )
        _log(f"A0 {st} reproduced")
    for x in cells:
        x["abs_diff"] = abs(x["crps_int"] - x["reference"])
    tests.append(
        chk(
            "A0 reproduction from stored components (mixture integer CRPS vs candidates.json)",
            {
                "max_abs_diff": max(x["abs_diff"] for x in cells),
                "pts": {x["season"]: x["crps_int"] for x in cells if x["stat"] == "pts"},
            },
            f"<= {REPRO_TOL}, n equal",
            all(x["abs_diff"] <= REPRO_TOL and x["n"] == x["n_ref"] for x in cells),
        )
    )
    tests.append(
        chk(
            "A0 pts equals the quoted 3.140236 / 3.176840",
            {x["season"]: round(x["crps_int"], 6) for x in cells if x["stat"] == "pts"},
            "to 1e-6",
            all(
                abs(x["crps_int"] - A0_REF["pts"][x["season"]]) <= 5e-7
                for x in cells
                if x["stat"] == "pts"
            ),
        )
    )
    fr, cfg, aux = build_frame()
    # the 5 columns attach to every A0 row
    keys = pl.DataFrame(
        {"game_id": pic["row_gid"].astype(str), "player_id": pic["row_pid"].astype(np.int64)}
    )
    a0rows = keys.join(
        fr.select(
            *KEYS, "season", "game_date", "minutes", *R_COLS, *[PL_PREFIX + c for c in R_COLS]
        ),
        on=KEYS,
        how="left",
    )
    nulls = {c: int(a0rows[c].is_null().sum() + a0rows[c].is_nan().sum()) for c in R_COLS}
    tests.append(
        chk(
            "the 5 R columns attach to every A0 row",
            {
                "n_a0_rows": a0rows.height,
                "frame_rows": fr.height,
                "null_or_nan": nulls,
                "minutes_null": int(a0rows["minutes"].null_count()),
            },
            "all A0 rows found in the frame; null share < 2%",
            a0rows["minutes"].null_count() == 0
            and a0rows.height == len(pic["row_gid"])
            and all(v / a0rows.height < 0.02 for v in nulls.values()),
        )
    )
    # control refit of pi == stored pi
    ctrl = walk_pi(fr, COMMON_FEATURES, cfg)
    pi_ctrl = align_to_stored(ctrl, pic["row_gid"], pic["row_pid"])
    dpi = np.abs(pi_ctrl - pic["row_pi"])
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE / "pi_CTRL.npz", pi=pi_ctrl)
    tests.append(
        chk(
            "control refit of pi (COMMON_FEATURES only) equals the stored A0 pi",
            {
                "max_abs_diff": float(dpi.max()),
                "mean_abs_diff": float(dpi.mean()),
                "n": int(len(dpi)),
            },
            f"max abs diff <= {CTRL_PI_TOL}",
            dpi.max() <= CTRL_PI_TOL,
        )
    )
    # missingness by realised-minutes bucket: the 5 candidate columns carry the pass criterion; the placebo's own NaN rows (first row of a
    # player-season has no earlier row) are reported with their gap (they can only make the placebo stronger, which is conservative for CAND)
    sub = a0rows.filter(pl.col("season").is_in(list(SEASONS)))
    aud = WE.audit_nulls(sub, list(R_COLS), WE.minutes_bucket("minutes"))
    tests.append(
        chk(
            "missingness of the 5 R columns by realised-minutes bucket (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp")},
            f"<= {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )
    pcols = [PL_PREFIX + c for c in R_COLS]
    aud_p = WE.audit_nulls(sub, pcols, WE.minutes_bucket("minutes"))
    nan_share = {c: float(sub[c].is_null().mean() + sub[c].is_nan().mean()) for c in pcols}
    tests.append(
        chk(
            "past-only placebo columns: NaN share and its gap by realised-minutes bucket (reported; conservative for CAND)",
            {
                "max_gap_pp": aud_p["max_gap_pp"],
                "worst_col": aud_p["worst_col"],
                "permuted_gap_pp": aud_p["placebo_max_gap_pp"],
                "nan_share": nan_share,
            },
            "NaN share < 5%; gap reported",
            max(nan_share.values()) < 0.05,
        )
    )
    # plants on the real tables, and the walk plant
    tests += WE.planted_tests(aux["src"], fr.select("game_id", "player_id", "season", "game_date"))
    tests += planted_walk(fr, cfg)
    # slice sizes per stat and season
    sizes: dict[str, Any] = {}
    for st in STATS:
        comp = load_components(st, LT_DIR)
        masks = slice_masks(comp)
        sizes[st] = {
            str(s): {k: int((mk & (comp["row_season"] == s)).sum()) for k, mk in masks.items()}
            for s in SEASONS
        }
        del comp
    tests.append(
        chk(
            "slice sizes (n rows) per stat and season",
            sizes["pts"],
            f"every declared guard slice >= {MIN_SLICE_N} in both seasons, all stats",
            all(
                sizes[st][str(s)][k] >= MIN_SLICE_N
                for st in STATS
                for s in SEASONS
                for k in GUARD_SLICES
            ),
        )
    )
    comp0 = load_components("pts", LT_DIR)
    sh0 = short_flag(comp0["row_minutes"], comp0["row_min10"])
    base = {
        str(s): {
            "observed_short": float(sh0[comp0["row_season"] == s].mean()),
            "mean_stored_pi": float(pic["row_pi"][comp0["row_season"] == s].mean()),
            "n": int((comp0["row_season"] == s).sum()),
        }
        for s in SEASONS
    }
    tests.append(
        chk("short-flag base rate and mean stored pi (descriptive)", base, "reported", True)
    )
    gate = {
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "sha256": sha,
        "draft": draft,
        "seconds": time.time() - t0,
        "slice_sizes": sizes,
    }
    WE.write_json(OUT_DIR / ("gate_draft.json" if draft else "gate.json"), gate)
    for t in tests:
        print(
            ("PASS " if t["ok"] else "FAIL ") + f"{t['test']}: {t['value']} ({t['threshold']})",
            flush=True,
        )
    return gate


def require_gate(sha: str) -> dict[str, Any]:
    p = OUT_DIR / "gate.json"
    if not p.exists():
        raise RuntimeError("no gate.json: run the gate stage first")
    g = json.loads(p.read_text())
    if not g.get("gate_ok") or g.get("sha256") != sha:
        raise RuntimeError("the frozen gate has not passed for this rule: stop (kill criterion)")
    return g


# --------------------------------------------------------------------------- arms and score
def run_arms(sha: str) -> None:
    require_frozen(sha)
    require_gate(sha)
    fr, cfg, _ = build_frame()
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    plan = {
        "CAND": dict(xcols=CAND_COLS, shuffle=False),
        "PL": dict(xcols=PLACEBO_COLS, shuffle=False),
        "SHUF_CTRL": dict(xcols=COMMON_FEATURES, shuffle=True),
        "SHUF_CAND": dict(xcols=CAND_COLS, shuffle=True),
    }
    audit: dict[str, Any] = {}
    for arm, kw in plan.items():
        path = CACHE / f"pi_{arm}.npz"
        if path.exists():
            _log(f"{arm} from checkpoint")
            continue
        _log(f"pi walk {arm}")
        res = walk_pi(fr, kw["xcols"], cfg, shuffle=kw["shuffle"])
        np.savez_compressed(path, pi=align_to_stored(res, pic["row_gid"], pic["row_pid"]))
        audit[arm] = res["label_hashes"]
    if (
        audit.get("SHUF_CTRL")
        and audit.get("SHUF_CAND")
        and audit["SHUF_CTRL"] != audit["SHUF_CAND"]
    ):
        raise RuntimeError(
            "the shuffled CTRL and CAND arms were not fitted on the same permuted labels"
        )
    if audit:
        WE.write_json(CACHE / "audit.json", audit)


def run_score(sha: str | None, smoke: bool = False) -> dict[str, Any]:
    """``smoke``: plumbing and runtime check with NO candidate information: every non-A0 pi arm is the stored A0 pi (all deltas exactly 0), the
    realised-flag ORACLE arm is real; nothing frozen is required and everything goes to OUT_DIR/smoke."""
    if smoke:
        gate: dict[str, Any] = {"gate_ok": True, "tests": []}
        sha = "smoke"
    else:
        sha = require_frozen(sha)
        gate = require_gate(sha)
    out_dir = OUT_DIR / "smoke" if smoke else OUT_DIR
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    arms_pi: dict[str, np.ndarray] = {"A0": pic["row_pi"]}
    for arm in ("CAND", "PL", "SHUF_CTRL", "SHUF_CAND"):
        arms_pi[arm] = pic["row_pi"].copy() if smoke else np.load(CACHE / f"pi_{arm}.npz")["pi"]
    comp0 = load_components("pts", LT_DIR)
    short = short_flag(comp0["row_minutes"], comp0["row_min10"])
    gid0, season0 = comp0["row_gid"].astype(str), comp0["row_season"]
    arms_pi["ORACLE"] = short.astype(float)
    pi_rows = pi_component_rows(
        {k: v for k, v in arms_pi.items() if k != "ORACLE"}, short, gid0, season0
    )
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    qi_arms: dict[str, dict[str, dict[str, np.ndarray]]] = {"A0": {}, "CAND": {}}
    for st in STATS:
        t0 = time.time()
        sc = score_stat(st, arms_pi, pic)
        rows += sc["rows"]
        slices += sc["slices"]
        comp = load_components(st, LT_DIR)
        for a in qi_arms:
            qi_arms[a][st] = {
                "qi": mixture_qi_pi(comp, pic, arms_pi[a], st),
                "gid": comp["row_gid"].astype(str),
                "pid": comp["row_pid"].astype(np.int64),
            }
        del comp
        _log(f"scored {st} in {time.time() - t0:.0f}s")
    add_bh(rows)
    res: dict[str, Any] = {"rows": rows, "slices": slices, "pi_rows": pi_rows}
    verdict = decide(res, bool(gate["gate_ok"]))
    out = {
        "sha256": sha,
        "scores": res,
        "verdict": verdict,
        "gate": gate["tests"],
        "decision_score_dk_t60": decision_scores(qi_arms),
        "stage2": "never run here; see `holdout`",
    }
    WE.write_json(out_dir / "results.json", out)
    if not smoke:
        REPORT_MD.write_text(render(out))
    return out


def _f(x: Any, nd: int = 4) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def render(out: dict[str, Any]) -> str:
    v = out["verdict"]
    lines = [f"# {ID} results", "", f"Rule sha256 {out['sha256'][:8]}. Season 2025 never loaded. Any stat passes: **{v['any_pass']}**. Per stat: " + ", ".join(f"{k} {'PASS' if x['pass'] else 'no'}" for k, x in v["per_stat"].items()), "",
             "| stat | season | arm | n rows | n games | integer CRPS / delta | CI | p | p BH | MDE |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in out["scores"]["rows"]:
        if "crps_int" in r:
            lines.append(
                f"| {r['stat']} | {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {_f(r['crps_int'], 6)} | | | | |"
            )
        else:
            lines.append(
                f"| {r['stat']} | {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {_f(r['delta'], 5)} | [{_f(r['ci_lo'], 5)}, {_f(r['ci_hi'], 5)}] | {_f(r['p'])} | {_f(r.get('p_bh'))} | {_f(r['mde'], 5)} |"
            )
    lines += [
        "",
        "| season | pi arm | n | log loss / delta | CI | ECE | AUC | mean pi (obs) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in out["scores"]["pi_rows"]:
        if "logloss" in r:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n']} | {_f(r['logloss'], 5)} | | {_f(r['ece'])} | {_f(r['auc'])} | {_f(r['mean_pi'])} ({_f(r['observed'])}) |"
            )
        else:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n']} | {_f(r['delta'], 5)} | [{_f(r['ci_lo'], 5)}, {_f(r['ci_hi'], 5)}] | ECE delta {_f(r['ece_delta'])} | | |"
            )
    lines += ["", "| stat | season | slice | n | CAND - A0 | CI |", "|---|---|---|---|---|---|"]
    for s in out["scores"]["slices"]:
        ci = "" if "ci_lo" not in s else f"[{_f(s['ci_lo'], 5)}, {_f(s['ci_hi'], 5)}]"
        lines.append(
            f"| {s['stat']} | {s['season']} | {s['slice']} | {s['n']} | {_f(s.get('delta'), 5)} | {ci} |"
        )
    lines += ["", "```json", json.dumps(v, indent=1, default=float), "```", ""]
    return "\n".join(lines)


def holdout(sha: str) -> None:
    """Stage 2. Refuses unless the pre-registered, Devin-confirmed row is committed and Stage 1 passed for >= 1 stat. Performs NO touch: the 2025
    loader (stat-head components and pi walk on season 2025 under a season cap, as research/eval/recent_role_holdout.py) is written in the commit
    that follows the row; the decision rule does not change."""
    require_holdout_row(sha)
    prev = json.loads((OUT_DIR / "results.json").read_text())
    if not prev["verdict"]["any_pass"]:
        raise RuntimeError("Stage 1 passed for no stat: Stage 2 refuses")
    raise NotImplementedError("the 2025 loader is added in the commit that follows the holdout row")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "arms", "score", "run", "holdout"])
    ap.add_argument("--sha", default=None)
    ap.add_argument(
        "--allow-draft", action="store_true", help="gate only: measure before the freeze"
    )
    ap.add_argument(
        "--smoke", action="store_true", help="score only: plumbing check, A0 pi in every arm"
    )
    a = ap.parse_args(argv)
    if a.cmd == "gate" and a.allow_draft:
        with WE.heavy():
            return 0 if run_gate(None, draft=True)["gate_ok"] else 1
    if a.cmd == "score" and a.smoke:
        run_score(None, smoke=True)
        return 0
    sha = require_frozen(a.sha)
    if a.cmd == "holdout":
        holdout(sha)
        return 0
    with WE.heavy():
        if a.cmd == "gate":
            return 0 if run_gate(sha)["gate_ok"] else 1
        if a.cmd == "arms":
            run_arms(sha)
            return 0
        if a.cmd == "score":
            run_score(sha)
            return 0
        if not run_gate(sha)["gate_ok"]:
            return 1
        run_arms(sha)
        run_score(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
