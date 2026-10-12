# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""RECENT_ROLE_FULL harness (rule: docs/prereg/RECENT_ROLE_FULL.md; DRAFT until the main session commits `prereg(RECENT_ROLE_FULL): <sha256>`).

FULL = production context-residual stat heads refit WITH the 5 recent-role columns + the lower-tail `pi` refit with them (PI_RECENT_ROLE's CAND pi),
scored through the stored lower-tail mixture construction (stored `w_q`, production month blocks, production LightGBM, seed 0). Nothing else moves.
Arms: FULL (the claim), HEADS (heads only, stored pi), PI (pi only = T460), PL_FULL (past-only placebo columns in both components),
SHUF_CTRL / SHUF_FULL (shared-permutation shuffled labels), ORACLE (descriptive). Comparator A0 = stored production + stored mixture.

    M="-m research.eval.recent_role_full_eval"
    .venv/bin/python -W ignore $M gate --allow-draft         # minimum-data gates before the freeze (writes gate_draft.json)
    .venv/bin/python -W ignore $M gate  --sha <64 hex>       # the same, frozen (writes gate.json)
    .venv/bin/python -W ignore $M arms  --sha <64 hex>       # heads walks + pi walks, per (stat, arm) checkpoints (heavy.lock)
    .venv/bin/python -W ignore $M score --sha <64 hex>       # integer CRPS vs A0, guards, verdict, report (light)
    .venv/bin/python -W ignore $M run   --sha <64 hex>       # gate -> arms -> score under ONE data/ops/heavy.lock
    .venv/bin/python -W ignore $M holdout --sha <64 hex>     # Stage 2: refuses unless a committed, confirmed log row exists; no 2025 loader exists

Every stage except `gate --allow-draft` refuses unless the text above `=== RESULTS BELOW ===` hashes to `--sha` AND a commit whose subject is
`prereg(RECENT_ROLE_FULL): <sha>` is reachable from HEAD. `nba.duckdb` is read-only; `season <= 2024` is asserted; season 2025 is never loaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import MAX_SEASON, TEST_SEASONS, bh_adjust, month_blocks
from nba.eval.lower_tail_eval import ROW_COVARIATES, load_components, randomized_pit
from nba.props.context_residual import (
    COMMON_FEATURES,
    PROP_STATS,
    ContextResidualConfig,
    ContextResidualModel,
    split_calibration,
    stat_feature_names,
    stat_frame,
)
from nba.props.lower_tail import short_flag
from research.eval import pi_recent_role_eval as PR
from research.eval import workload_eval as WE

ID = "RECENT_ROLE_FULL"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/RECENT_ROLE_FULL.md"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
OUT_DIR = ROOT / "reports/prereg_recent_role_full"
REPORT_MD = ROOT / "reports/prereg_recent_role_full.md"
CACHE = ROOT / "data/ops/recent_role_full_cache"
LT_DIR = PR.LT_DIR
T460_RESULTS = ROOT / "reports/prereg_pi_recent_role/results.json"
KEYS = PR.KEYS
STATS = PROP_STATS
SEASONS = PR.SEASONS
SEED = 0
MIN_TRAIN = 1500
PL_PREFIX = PR.PL_PREFIX
R_COLS = PR.R_COLS
PL_COLS = tuple(PL_PREFIX + c for c in R_COLS)

FLOOR = -0.005  # integer CRPS, FULL minus A0 (house props floor)
PLACEBO_MARGIN = -0.003  # FULL must beat PL_FULL by this much, per stat and season
SHUF_TOL = -0.001  # shuffled-label check FAILS if SHUF_FULL - SHUF_CTRL <= this with CI upper < 0
SLICE_TOL = 0.01
MIN_SLICE_N = 300
COV_TOL = (
    0.01  # |coverage - tau| of FULL may be worse than A0's by at most this at each of COV_TAUS
)
COV_TAUS: tuple[float, ...] = (0.10, 0.20, 0.80, 0.90)
REPRO_TOL = 1e-6  # A0 / heads-control reproduction
T460_TOL = 1e-6  # PI arm vs the frozen T460 deltas
GUARD_SLICES = PR.GUARD_SLICES
ARMS_HEADS: dict[
    str, tuple[str, ...]
] = {  # heads arm -> extra columns on top of stat_feature_names(stat)
    "R": R_COLS,
    "PL": PL_COLS,
    "SHUF_CTRL": (),
    "SHUF_R": R_COLS,
}
ARMS_PI: dict[str, tuple[tuple[str, ...], bool]] = {  # pi arm -> (columns, shuffled labels)
    "R": (PR.CAND_COLS, False),
    "PL": (PR.PLACEBO_COLS, False),
    "SHUF_CTRL": (tuple(COMMON_FEATURES), True),
    "SHUF_R": (PR.CAND_COLS, True),
}
CONTRASTS = (
    ("FULL", "A0"),
    ("HEADS", "A0"),
    ("PI", "A0"),
    ("FULL", "HEADS"),
    ("FULL", "PI"),
    ("FULL", "PL_FULL"),
    ("SHUF_FULL", "SHUF_CTRL"),
    ("ORACLE", "A0"),
)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze and holdout refusal
def freeze_commit(sha: str, git: Callable[..., str] = PR._git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(
    sha: str | None, doc: Path | None = None, git: Callable[..., str] = PR._git
) -> str:
    doc = doc or DOC
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
    sha: str,
    log: Path | None = None,
    git: Callable[..., str] = PR._git,
    doc: Path | None = None,
) -> str:
    """Stage 2 gate: a committed, unmodified HOLDOUT_ACCESS_LOG row naming this id and sha8 with PRE-REGISTERED and DEVIN CONFIRMED."""
    log = log or HOLDOUT_LOG
    require_frozen(sha, doc, git)
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


# --------------------------------------------------------------------------- the heads walk
def collect_heads(
    frame: pl.DataFrame,
    stat: str,
    cfg: ContextResidualConfig,
    names: list[str],
    shuffle: bool = False,
    permute: bool = True,
    seed: int = SEED,
    test_seasons: tuple[int, ...] = TEST_SEASONS,
    min_train: int = MIN_TRAIN,
    log: bool = True,
) -> dict[str, Any]:
    """`lower_tail_eval.collect_stat` with an explicit feature list (same production blocks, model, calibration split, covariates).

    ``shuffle``: the training residuals of block ``bi`` are replaced by ONE permutation drawn from ``default_rng([seed, bi])`` (global over the
    training rows), so two arms with different feature lists see the SAME permuted residuals; ``permute=False`` is the old defect (no permutation)
    and exists only so the harness can show its own check can fail. Real residuals never reach a fit when ``shuffle``. ``label_hashes`` carries a
    per-block hash of the residuals each fit used."""
    if max(test_seasons) > MAX_SEASON:
        raise ValueError("season 2025 is the frozen holdout")
    sf = stat_frame(frame, stat)
    test_all = sf.filter(pl.col("season").is_in(list(test_seasons)))
    rows: dict[str, list[np.ndarray]] = {
        k: [] for k in ("gid", "pid", "season", "y", "c", "s", "blk", *ROW_COVARIATES)
    }
    cal: dict[str, list[np.ndarray]] = {
        k: [] for k in ("y", "c", "s", "z", "blk", "minutes", "min10", "m")
    }
    hashes: list[str] = []
    blocks = month_blocks(test_all)
    for b, (start, end) in enumerate(blocks):
        block = sf.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(test_seasons))
        )
        train = sf.filter(pl.col("game_date") < start).sort(["game_date", "game_id", "player_id"])
        if block.is_empty() or train.height < min_train:
            continue
        if shuffle and permute:
            r = train["resid"].to_numpy()
            train = train.with_columns(
                pl.Series("resid", r[np.random.default_rng([seed, b]).permutation(len(r))])
            )
        hashes.append(
            hashlib.sha256(
                np.ascontiguousarray(train["resid"].to_numpy().astype(np.float64)).tobytes()
            ).hexdigest()[:16]
        )
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
        assert model.mean_head is not None and model.z_sorted is not None
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
            _log(f"{stat} heads block {b + 1}/{len(blocks)} {start} n={n} {time.time() - t0:.0f}s")
    if not rows["c"]:
        raise ValueError("no month block had enough training rows")
    out: dict[str, Any] = {f"row_{k}": np.concatenate(v) for k, v in rows.items()}
    out.update({f"cal_{k}": np.concatenate(v) for k, v in cal.items()})
    out["label_hashes"] = np.array(hashes)
    return out


def heads_names(stat: str, arm: str) -> list[str]:
    return [*stat_feature_names(stat), *ARMS_HEADS.get(arm, ())]


def heads_path(stat: str, arm: str) -> Path:
    return CACHE / f"heads_{stat}_{arm}.npz"


def pi_path(arm: str) -> Path:
    return CACHE / f"pi_{arm}.npz"


def load_heads(stat: str, arm: str) -> dict[str, np.ndarray]:
    z = np.load(heads_path(stat, arm), allow_pickle=True)
    return {k: z[k] for k in z.files}


def same_rows(a: dict[str, np.ndarray], b: dict[str, np.ndarray]) -> bool:
    return bool(
        np.array_equal(a["row_gid"].astype(str), b["row_gid"].astype(str))
        and np.array_equal(a["row_pid"].astype(np.int64), b["row_pid"].astype(np.int64))
        and np.array_equal(a["row_y"], b["row_y"])
    )


def heads_vs_stored(new: dict[str, np.ndarray], stored: dict[str, np.ndarray]) -> dict[str, Any]:
    """Max abs difference of every head output (row centre/scale, calibration z/centre/scale) between a refit and the stored components."""
    if not same_rows(new, stored):
        return {"rows_equal": False, "max_abs_diff": float("inf")}
    d = {k: float(np.abs(new[k] - stored[k]).max()) for k in ("row_c", "row_s")}
    if new["cal_z"].shape == stored["cal_z"].shape:
        d.update({k: float(np.abs(new[k] - stored[k]).max()) for k in ("cal_z", "cal_c", "cal_s")})
    else:
        d["cal_shape_equal"] = float("inf")
    return {"rows_equal": True, "max_abs_diff": max(d.values()), "by_output": d}


# --------------------------------------------------------------------------- frame
def build_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    """Production feature rows + the 5 recent-role columns + their past-only placebo (identical to PI_RECENT_ROLE's frame)."""
    return PR.build_frame()


# --------------------------------------------------------------------------- arms
def run_arms(sha: str | None, allow_draft: bool = False) -> None:
    if not allow_draft:
        require_frozen(sha)
        require_gate(sha)
    fr, cfg, _ = build_frame()
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    CACHE.mkdir(parents=True, exist_ok=True)
    audit: dict[str, Any] = {}
    for arm, (cols, shuf) in ARMS_PI.items():  # pi walks first: small, 15 s each
        path = pi_path(arm)
        if path.exists():
            _log(f"pi {arm} from checkpoint")
            continue
        _log(f"pi walk {arm}")
        res = PR.walk_pi(fr, cols, cfg, shuffle=shuf)
        np.savez_compressed(path, pi=PR.align_to_stored(res, pic["row_gid"], pic["row_pid"]))
        audit[f"pi_{arm}"] = res["label_hashes"]
    if audit.get("pi_SHUF_CTRL") and audit["pi_SHUF_CTRL"] != audit.get("pi_SHUF_R"):
        raise RuntimeError("the shuffled pi CTRL and R arms were not fitted on the same labels")
    for stat in STATS:
        for arm in ARMS_HEADS:
            path = heads_path(stat, arm)
            if path.exists():
                _log(f"{stat} heads {arm} from checkpoint")
                continue
            _log(f"{stat} heads walk {arm}")
            comp = collect_heads(
                fr, stat, cfg, heads_names(stat, arm), shuffle=arm.startswith("SHUF")
            )
            stored = load_components(stat, LT_DIR)
            if not same_rows(comp, stored):
                raise RuntimeError(f"{stat} {arm}: heads rows do not match the stored components")
            np.savez_compressed(path, **comp)
            audit[f"heads_{stat}_{arm}"] = [str(h) for h in comp["label_hashes"]]
        a, b = audit.get(f"heads_{stat}_SHUF_CTRL"), audit.get(f"heads_{stat}_SHUF_R")
        if a and a != b:
            raise RuntimeError(f"{stat}: shuffled heads arms were not fitted on the same residuals")
    if audit:
        WE.write_json(CACHE / "audit.json", audit)


# --------------------------------------------------------------------------- scoring
def arm_qi(
    stat: str,
    comp: dict[str, np.ndarray],
    pic: dict[str, np.ndarray],
    pi: np.ndarray,
) -> np.ndarray:
    return WE.mixture_qi(comp, PR.candidate_pic(pic, pi), stat)


def coverage_rows(
    qi: np.ndarray, y: np.ndarray, season: np.ndarray, taus: tuple[float, ...] = COV_TAUS
) -> dict[str, dict[str, float]]:
    """Randomised-PIT coverage P(PIT < tau) of an integer-grid forecast, per season."""
    pit = randomized_pit(qi.astype(float), y.astype(float))
    return {
        str(s): {f"{t:.2f}": float((pit[season == s] < t).mean()) for t in taus} for s in SEASONS
    }


def score_stat(
    stat: str, pic: dict[str, np.ndarray], pis: dict[str, np.ndarray], smoke: bool = False
) -> dict[str, Any]:
    """Per-row integer CRPS of every arm of one stat, deltas vs the declared comparators (overall, slices), coverage, qi of A0 and FULL."""
    c0 = load_components(stat, LT_DIR)
    y, gid, season = c0["row_y"], c0["row_gid"].astype(str), c0["row_season"]
    comps: dict[str, dict[str, np.ndarray]] = {"A0": c0, "PI": c0, "ORACLE": c0}
    pi_of: dict[str, np.ndarray] = {
        "A0": pic["row_pi"],
        "PI": pis["R"],
        "ORACLE": short_flag(c0["row_minutes"], c0["row_min10"]).astype(float),
    }
    for name, (ha, pa) in {
        "FULL": ("R", "R"),
        "HEADS": ("R", None),
        "PL_FULL": ("PL", "PL"),
        "SHUF_CTRL": ("SHUF_CTRL", "SHUF_CTRL"),
        "SHUF_FULL": ("SHUF_R", "SHUF_R"),
    }.items():
        comps[name] = c0 if smoke else load_heads(stat, ha)
        if not same_rows(comps[name], c0):
            raise RuntimeError(f"{stat} {name}: rows differ from the stored components")
        pi_of[name] = pic["row_pi"] if pa is None else pis[pa]
    if smoke:  # plumbing only: every non-A0 arm is A0 (deltas exactly 0), except the oracle
        for k in ("FULL", "PI", "HEADS", "PL_FULL", "SHUF_CTRL", "SHUF_FULL"):
            pi_of[k] = pic["row_pi"]
    qis = {k: arm_qi(stat, comps[k], pic, pi_of[k]) for k in comps}
    scores = {k: PR.crps_int(q, y) for k, q in qis.items()}
    masks = PR.slice_masks(c0)
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    for s in SEASONS:
        sm = season == s
        n_games = int(len(np.unique(gid[sm])))
        for arm, sc in scores.items():
            rows.append(
                {
                    "stat": stat, "season": s, "arm": arm, "n_rows": int(sm.sum()), "n_games": n_games, "crps_int": float(sc[sm].mean()),
                }
            )  # fmt: skip
        for a, b in CONTRASTS:
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
        d = scores["FULL"] - scores["A0"]
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
        "coverage": {
            "A0": coverage_rows(qis["A0"], y, season),
            "FULL": coverage_rows(qis["FULL"], y, season),
        },
        "qi": {"A0": qis["A0"], "FULL": qis["FULL"]},
        "gid": c0["row_gid"].astype(str),
        "pid": c0["row_pid"].astype(np.int64),
    }


def add_bh(rows: list[dict[str, Any]]) -> None:
    """BH over the m = 4 stats within each season for the primary contrast FULL - A0 (adds `p_bh`)."""
    for s in SEASONS:
        tgt = [r for r in rows if r["season"] == s and r["arm"] == "FULL_minus_A0"]
        adj = bh_adjust(np.array([r["p"] for r in tgt]))
        for r, a in zip(tgt, adj, strict=True):
            r["p_bh"] = float(a)


def coverage_ok(cov: dict[str, dict[str, dict[str, float]]]) -> tuple[bool, list[Any]]:
    """FULL's coverage may be worse than A0's by at most COV_TOL (absolute distance from nominal) at every tau, per season."""
    bad = []
    for s in cov["A0"]:
        for t, v0 in cov["A0"][s].items():
            worse = abs(cov["FULL"][s][t] - float(t)) - abs(v0 - float(t))
            if worse > COV_TOL:
                bad.append((s, t, worse))
    return not bad, bad


def decide(res: dict[str, Any], gate_ok: bool) -> dict[str, Any]:
    """The frozen pass rule for the claim FULL. Per stat: 2023 delta <= FLOOR with CI upper < 0 and BH p <= 0.05; 2024 the same; pi ECE no worse
    by more than 0.005 in both seasons; coverage no worse than A0 by more than COV_TOL; no guard slice (n >= 300) worse by more than SLICE_TOL;
    FULL beats PL_FULL by <= PLACEBO_MARGIN in both seasons; the shuffled-label check holds for EVERY stat; gate and plants clean."""
    r = {(x["stat"], x["season"], x["arm"]): x for x in res["rows"] if "stat" in x}
    comp = {(x["season"], x["arm"]): x for x in res["pi_rows"]}
    ece_ok = all(comp[(s, "FULL_minus_A0")]["ece_delta"] <= PR.ECE_TOL for s in SEASONS)
    shuf = [
        PR.shuffle_check({**r[(st, s, "SHUF_FULL_minus_SHUF_CTRL")], "stat": st})
        for st in STATS
        for s in SEASONS
    ]
    shuf_ok = all(x["ok"] for x in shuf)
    per: dict[str, Any] = {}
    for st in STATS:
        a, b = r[(st, 2023, "FULL_minus_A0")], r[(st, 2024, "FULL_minus_A0")]
        c23 = a["delta"] <= FLOOR and a["ci_hi"] < 0 and a.get("p_bh", 1.0) <= 0.05
        c24 = b["delta"] <= FLOOR and b["ci_hi"] < 0 and b.get("p_bh", 1.0) <= 0.05
        worse = [
            (x["season"], x["slice"], x["n"], x["delta"])
            for x in res["slices"]
            if x["stat"] == st
            and x["slice"] in GUARD_SLICES
            and x.get("n", 0) >= MIN_SLICE_N
            and x.get("delta", -1.0) > SLICE_TOL
        ]
        plc = all(r[(st, s, "FULL_minus_PL_FULL")]["delta"] <= PLACEBO_MARGIN for s in SEASONS)
        cov_ok, cov_bad = coverage_ok(res["coverage"][st])
        per[st] = {
            "pass": bool(c23 and c24 and ece_ok and cov_ok and not worse and plc and shuf_ok and gate_ok),
            "select_2023": bool(c23), "report_2024": bool(c24), "ece_ok": bool(ece_ok), "coverage_ok": bool(cov_ok), "coverage_bad": cov_bad,
            "slices_ok": not worse, "slices_worse": worse, "placebo_ok": bool(plc), "shuffle_ok": bool(shuf_ok), "gate_ok": bool(gate_ok),
            "d2023": a["delta"], "d2024": b["delta"], "mde2024": b["mde"],
        }  # fmt: skip
    return {
        "per_stat": per,
        "any_pass": any(v["pass"] for v in per.values()),
        "shuffle": shuf,
        "ece_ok": ece_ok,
    }


# --------------------------------------------------------------------------- gate
def planted_heads(fr: pl.DataFrame, cfg: ContextResidualConfig) -> list[dict[str, Any]]:
    """As-of plant for the heads on the real frame (stat pts, 2024): overwrite every label (`pts`, `minutes`) and R column of rows dated on/after
    a mid-2024 cutoff; the centre/scale of every row in a block that ENDS before the cutoff is bit-identical, rows in later blocks change."""
    chk = WE._check
    d24 = fr.filter(pl.col("season") == 2024).sort("game_date")["game_date"]
    cut = d24[d24.len() // 2]
    late = pl.col("game_date") >= cut
    edited = fr.with_columns(
        pl.when(late).then(48.0).otherwise(pl.col("minutes")).alias("minutes"),
        pl.when(late).then(99.0).otherwise(pl.col("pts").cast(pl.Float64)).alias("pts"),
        *[pl.when(late).then(48.0).otherwise(pl.col(c)).alias(c) for c in R_COLS],
    )
    names = heads_names("pts", "R")
    a = collect_heads(fr, "pts", cfg, names, test_seasons=(2024,), log=False)
    b = collect_heads(edited, "pts", cfg, names, test_seasons=(2024,), log=False)
    ja = pl.DataFrame(
        {"game_id": a["row_gid"], "player_id": a["row_pid"], "ca": a["row_c"], "sa": a["row_s"]}
    )
    jb = pl.DataFrame({"game_id": b["row_gid"], "player_id": b["row_pid"], "cb": b["row_c"], "sb": b["row_s"]})  # fmt: skip
    date = fr.select("game_id", "player_id", "game_date").with_columns(
        pl.col("player_id").cast(pl.Int64)
    )
    j = ja.join(jb, on=KEYS).join(date, on=KEYS)
    nxt_start = cut.replace(day=1).replace(
        month=cut.month % 12 + 1, year=cut.year + (cut.month == 12)
    )
    pre = j.filter(
        pl.col("game_date") < cut.replace(day=1)
    )  # blocks that end before the cutoff's own month
    post = j.filter(pl.col("game_date") >= nxt_start)
    same = float(max(np.abs(pre["ca"].to_numpy() - pre["cb"].to_numpy()).max(), np.abs(pre["sa"].to_numpy() - pre["sb"].to_numpy()).max())) if pre.height else 0.0  # fmt: skip
    moved = (
        float(np.abs(post["ca"].to_numpy() - post["cb"].to_numpy()).max()) if post.height else 0.0
    )
    return [
        chk(
            "planted future (heads, pts): centre and scale of rows in blocks before the cutoff identical",
            {"cutoff": str(cut), "n_rows": pre.height, "max_abs_diff": same},
            "== 0 and n > 0",
            same == 0.0 and pre.height > 0,
        ),
        chk(
            "control (heads, pts): rows in later blocks change",
            {"n_rows": post.height, "max_abs_diff": moved},
            "> 0",
            moved > 0.0,
        ),
    ]


def run_gate(sha: str | None, draft: bool = False) -> dict[str, Any]:
    """Minimum-data gates. Scored arms: the CONTROL refits (heads and pi on production features only; must equal the stored components) and the PI
    arm (already frozen and scored as T460; reproduced to 1e-6). The FULL / HEADS / placebo / shuffle arms are NOT scored here."""
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
        c = PR.crps_int(WE.mixture_qi(comp, pic, st), comp["row_y"])
        for s in SEASONS:
            m = comp["row_season"] == s
            cells.append(
                {
                    "stat": st, "season": s, "crps_int": float(c[m].mean()), "reference": float(refs[st][str(s)]["mixture"]["crps_int"]),
                    "n": int(m.sum()), "n_ref": int(refs[st][str(s)]["off"]["n"]),
                }
            )  # fmt: skip
    for x in cells:
        x["abs_diff"] = abs(x["crps_int"] - x["reference"])
    tests.append(
        chk(
            "A0 reproduction from stored components (mixture integer CRPS vs candidates.json)",
            {"max_abs_diff": max(x["abs_diff"] for x in cells), "pts": {x["season"]: x["crps_int"] for x in cells if x["stat"] == "pts"}},
            f"<= {REPRO_TOL}, n equal",
            all(x["abs_diff"] <= REPRO_TOL and x["n"] == x["n_ref"] for x in cells),
        )
    )  # fmt: skip
    tests.append(
        chk(
            "A0 pts equals the quoted 3.140236 / 3.176840",
            {x["season"]: round(x["crps_int"], 6) for x in cells if x["stat"] == "pts"},
            "to 1e-6",
            all(abs(x["crps_int"] - PR.A0_REF["pts"][x["season"]]) <= 5e-7 for x in cells if x["stat"] == "pts"),
        )
    )  # fmt: skip
    fr, cfg, aux = build_frame()
    keys = pl.DataFrame(
        {"game_id": pic["row_gid"].astype(str), "player_id": pic["row_pid"].astype(np.int64)}
    )
    a0rows = keys.join(
        fr.select(*KEYS, "season", "game_date", "minutes", *R_COLS, *PL_COLS), on=KEYS, how="left"
    )
    nulls = {c: int(a0rows[c].is_null().sum() + a0rows[c].is_nan().sum()) for c in R_COLS}
    tests.append(
        chk(
            "the 5 R columns attach to every A0 row",
            {"n_a0_rows": a0rows.height, "null_or_nan": nulls, "minutes_null": int(a0rows["minutes"].null_count())},
            "all A0 rows found; null share < 2%",
            a0rows["minutes"].null_count() == 0 and a0rows.height == len(pic["row_gid"]) and all(v / a0rows.height < 0.02 for v in nulls.values()),
        )
    )  # fmt: skip
    CACHE.mkdir(parents=True, exist_ok=True)
    # heads control refit == stored, every stat (production features only, production blocks)
    hd: dict[str, Any] = {}
    for st in STATS:
        ctrl = collect_heads(fr, st, cfg, stat_feature_names(st))
        hd[st] = heads_vs_stored(ctrl, load_components(st, LT_DIR))
        _log(f"heads control {st}: {hd[st]}")
        del ctrl
    tests.append(
        chk(
            "control refit of the stat heads (production features only) equals the stored components",
            {st: {"rows_equal": v["rows_equal"], "max_abs_diff": v["max_abs_diff"]} for st, v in hd.items()},
            f"rows equal, max abs diff <= {REPRO_TOL} for every output",
            all(v["rows_equal"] and v["max_abs_diff"] <= REPRO_TOL for v in hd.values()),
        )
    )  # fmt: skip
    # pi control == stored; pi CAND walk reproduces T460 (already frozen and scored)
    ctrl_pi = PR.align_to_stored(
        PR.walk_pi(fr, COMMON_FEATURES, cfg), pic["row_gid"], pic["row_pid"]
    )
    dpi = np.abs(ctrl_pi - pic["row_pi"])
    tests.append(
        chk(
            "control refit of pi (COMMON_FEATURES only) equals the stored A0 pi",
            {"max_abs_diff": float(dpi.max()), "n": int(len(dpi))},
            f"max abs diff <= {PR.CTRL_PI_TOL}",
            dpi.max() <= PR.CTRL_PI_TOL,
        )
    )  # fmt: skip
    pi_r = PR.align_to_stored(PR.walk_pi(fr, PR.CAND_COLS, cfg), pic["row_gid"], pic["row_pid"])
    np.savez_compressed(pi_path("R"), pi=pi_r)
    t460 = json.loads(T460_RESULTS.read_text())
    ref460 = {
        (r["stat"], r["season"]): r["delta"]
        for r in t460["scores"]["rows"]
        if r.get("arm") == "CAND_minus_A0"
    }
    mine: dict[tuple[str, int], float] = {}
    for st in STATS:
        c0 = load_components(st, LT_DIR)
        d = PR.crps_int(arm_qi(st, c0, pic, pi_r), c0["row_y"]) - PR.crps_int(
            WE.mixture_qi(c0, pic, st), c0["row_y"]
        )
        for s in SEASONS:
            mine[(st, s)] = float(d[c0["row_season"] == s].mean())
    diff460 = max(abs(mine[k] - ref460[k]) for k in mine)
    tests.append(
        chk(
            "PI arm (pi refit with the 5 columns, stored heads) reproduces T460 (PI_RECENT_ROLE frozen deltas)",
            {"max_abs_diff": diff460, "pts": {s: [round(mine[("pts", s)], 6), round(ref460[("pts", s)], 6)] for s in SEASONS}},
            f"max abs diff over 4 stats x 2 seasons <= {T460_TOL}",
            diff460 <= T460_TOL,
        )
    )  # fmt: skip
    # missingness of the 5 columns by realised-minutes bucket (pass criterion); placebo columns reported
    sub = a0rows.filter(pl.col("season").is_in(list(SEASONS)))
    aud = WE.audit_nulls(sub, list(R_COLS), WE.minutes_bucket("minutes"))
    tests.append(
        chk(
            "missingness of the 5 R columns by realised-minutes bucket (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp")},
            f"<= {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )  # fmt: skip
    aud_p = WE.audit_nulls(sub, list(PL_COLS), WE.minutes_bucket("minutes"))
    nan_share = {c: float(sub[c].is_null().mean() + sub[c].is_nan().mean()) for c in PL_COLS}
    tests.append(
        chk(
            "past-only placebo columns: NaN share and gap by realised-minutes bucket (reported; conservative for FULL)",
            {"max_gap_pp": aud_p["max_gap_pp"], "worst_col": aud_p["worst_col"], "permuted_gap_pp": aud_p["placebo_max_gap_pp"], "nan_share": nan_share},
            "NaN share < 5%; gap reported",
            max(nan_share.values()) < 0.05,
        )
    )  # fmt: skip
    tests += WE.planted_tests(aux["src"], fr.select("game_id", "player_id", "season", "game_date"))
    tests += PR.planted_walk(fr, cfg)
    tests += planted_heads(fr, cfg)
    sizes: dict[str, Any] = {}
    for st in STATS:
        comp = load_components(st, LT_DIR)
        masks = PR.slice_masks(comp)
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
            all(sizes[st][str(s)][k] >= MIN_SLICE_N for st in STATS for s in SEASONS for k in GUARD_SLICES),
        )
    )  # fmt: skip
    log_text = HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else ""
    tests.append(
        chk(
            "HOLDOUT_ACCESS_LOG: no row names RECENT_ROLE_FULL (never evaluated on season 2025)",
            {"rows_naming_id": sum(ID in ln for ln in log_text.splitlines())},
            "== 0",
            ID not in log_text,
        )
    )  # fmt: skip
    gate = {"tests": tests, "gate_ok": all(t["ok"] for t in tests), "sha256": sha, "draft": draft, "seconds": time.time() - t0, "slice_sizes": sizes}  # fmt: skip
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


# --------------------------------------------------------------------------- score
def run_score(sha: str | None, smoke: bool = False) -> dict[str, Any]:
    """``smoke``: plumbing and runtime check with NO candidate information: every non-A0 arm is the stored A0 (deltas exactly 0), the realised-flag
    ORACLE is real; nothing frozen is required and everything goes to OUT_DIR/smoke."""
    if smoke:
        gate: dict[str, Any] = {"gate_ok": True, "tests": []}
        sha = "smoke"
    else:
        sha = require_frozen(sha)
        gate = require_gate(sha)
    out_dir = OUT_DIR / "smoke" if smoke else OUT_DIR
    pic = dict(np.load(LT_DIR / "components_pi.npz", allow_pickle=True))
    pis = {a: (pic["row_pi"].copy() if smoke else np.load(pi_path(a))["pi"]) for a in ARMS_PI}
    c0 = load_components("pts", LT_DIR)
    short = short_flag(c0["row_minutes"], c0["row_min10"])
    gid0, season0 = c0["row_gid"].astype(str), c0["row_season"]
    pi_arms = {"A0": pic["row_pi"], "FULL": pis["R"], "PL_FULL": pis["PL"], "SHUF_CTRL": pis["SHUF_CTRL"], "SHUF_FULL": pis["SHUF_R"]}  # fmt: skip
    pi_rows = PR.pi_component_rows(pi_arms, short, gid0, season0)
    rows: list[dict[str, Any]] = []
    slices: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {}
    qi_arms: dict[str, dict[str, dict[str, np.ndarray]]] = {"A0": {}, "FULL": {}}
    for st in STATS:
        t0 = time.time()
        sc = score_stat(st, pic, pis, smoke=smoke)
        rows += sc["rows"]
        slices += sc["slices"]
        coverage[st] = sc["coverage"]
        for a in qi_arms:
            qi_arms[a][st] = {"qi": sc["qi"][a], "gid": sc["gid"], "pid": sc["pid"]}
        _log(f"scored {st} in {time.time() - t0:.0f}s")
    add_bh(rows)
    res: dict[str, Any] = {"rows": rows, "slices": slices, "pi_rows": pi_rows, "coverage": coverage}
    verdict = decide(res, bool(gate["gate_ok"]))
    out = {
        "sha256": sha,
        "scores": res,
        "verdict": verdict,
        "gate": gate["tests"],
        "decision_score_dk_t60": PR.decision_scores(qi_arms),
        "stage2": "never run here; see `holdout`",
    }
    WE.write_json(out_dir / "results.json", out)
    if not smoke:
        REPORT_MD.write_text(render(out))
    return out


def render(out: dict[str, Any]) -> str:
    v = out["verdict"]
    f = PR._f
    lines = [f"# {ID} results", "", f"Rule sha256 {out['sha256'][:8]}. Season 2025 never loaded. Any stat passes: **{v['any_pass']}**. Per stat: " + ", ".join(f"{k} {'PASS' if x['pass'] else 'no'}" for k, x in v["per_stat"].items()), "",
             "| stat | season | arm | n rows | n games | integer CRPS / delta | CI | p | p BH | MDE |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in out["scores"]["rows"]:
        if "crps_int" in r:
            lines.append(
                f"| {r['stat']} | {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {f(r['crps_int'], 6)} | | | | |"
            )
        else:
            lines.append(f"| {r['stat']} | {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {f(r['delta'], 5)} | [{f(r['ci_lo'], 5)}, {f(r['ci_hi'], 5)}] | {f(r['p'])} | {f(r.get('p_bh'))} | {f(r['mde'], 5)} |")  # fmt: skip
    lines += [
        "",
        "| season | pi arm | n | log loss / delta | CI | ECE | AUC | mean pi (obs) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in out["scores"]["pi_rows"]:
        if "logloss" in r:
            lines.append(f"| {r['season']} | {r['arm']} | {r['n']} | {f(r['logloss'], 5)} | | {f(r['ece'])} | {f(r['auc'])} | {f(r['mean_pi'])} ({f(r['observed'])}) |")  # fmt: skip
        else:
            lines.append(f"| {r['season']} | {r['arm']} | {r['n']} | {f(r['delta'], 5)} | [{f(r['ci_lo'], 5)}, {f(r['ci_hi'], 5)}] | ECE delta {f(r['ece_delta'])} | | |")  # fmt: skip
    lines += ["", "| stat | season | slice | n | FULL - A0 | CI |", "|---|---|---|---|---|---|"]
    for s in out["scores"]["slices"]:
        ci = "" if "ci_lo" not in s else f"[{f(s['ci_lo'], 5)}, {f(s['ci_hi'], 5)}]"
        lines.append(
            f"| {s['stat']} | {s['season']} | {s['slice']} | {s['n']} | {f(s.get('delta'), 5)} | {ci} |"
        )
    lines += ["", "Coverage P(PIT < tau), A0 vs FULL:", "", "```json", json.dumps(out["scores"]["coverage"], indent=1), "```", "", "```json", json.dumps(v, indent=1, default=float), "```", ""]  # fmt: skip
    return "\n".join(lines)


def holdout(sha: str) -> None:
    """Stage 2. Refuses unless the pre-registered, Devin-confirmed row is committed and Stage 1 passed for >= 1 stat. Performs NO touch: the 2025
    loader (stat-head components, pi walk and the stored-mixture w_q on season 2025 under a season cap, as research/eval/recent_role_holdout.py) is
    written in the commit that follows the row; the decision rule does not change."""
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
        "--smoke", action="store_true", help="score only: plumbing check, A0 in every arm"
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
