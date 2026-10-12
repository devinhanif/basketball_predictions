# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,call-overload,attr-defined,misc,no-any-return,type-var"
"""NEAR_MISS_BUNDLE harness (rule: docs/prereg/NEAR_MISS_BUNDLE.md; membership fixed in docs/DECISIONS.md, commit b91893e).

One combined candidate per stat (the bundle), built in a declared order and scored against A0 = production + lower-tail
mixture on integer support:

    1. LOAD   production + the 15 WORKLOAD LOAD columns, through the same lower-tail mixture       (all four stats)
    2. ROUTER pts only: pmf mixture  g * XGBoost(A1)  +  (1 - g) * LOAD, g = P(minutes >= 5 | plays) from production (E1)
    3. BLEND  walk-forward softmax gate over [ROUTER, SEQ-E2] (E2 learned: 30-day blocks, min 3000 earlier rows)

Every weight is as-of: g is a function of the production minutes path; the gate is fit on earlier 30-day blocks only; the
stored members (A1, SEQ) are walk-forward out-of-fold forecasts. Season 2025 is never loaded; ``nba.duckdb`` is read-only.

    M="-m research.eval.near_miss_bundle_eval"
    .venv/bin/python -W ignore $M gate [--refit-repro pts] [--recompute-g]   # minimum-data gates; no candidate arm is fitted
    .venv/bin/python -W ignore $M run --sha <64 hex>                         # arms -> score -> report, one heavy.lock
    .venv/bin/python -W ignore $M arms --sha <64 hex>                        # LightGBM refits only (heavy.lock)
    .venv/bin/python -W ignore $M score --sha <64 hex>                       # numpy only: bundle, guards, attacks, verdicts
    .venv/bin/python -W ignore $M holdout --sha <64 hex>                     # refuses unless a committed, confirmed log row exists

Every stage except ``gate`` refuses unless the text above ``=== RESULTS BELOW ===`` hashes to ``--sha`` AND a commit
``prereg(NEAR_MISS_BUNDLE): <sha>`` is reachable from HEAD. ``holdout`` additionally refuses without a committed
HOLDOUT_ACCESS_LOG row, and even then performs no touch: no 2025 loader exists in this module.
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

from nba.eval.context_residual_eval import MAX_SEASON, bh_adjust, crps_from_quantiles
from nba.props.context_residual import stat_feature_names, to_integer_support
from research.eval import ensemble_eval as E

ID = "NEAR_MISS_BUNDLE"
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/NEAR_MISS_BUNDLE.md"
OUT_DIR = ROOT / "reports/prereg_near_miss_bundle"
REPORT_MD = ROOT / "reports/prereg_near_miss_bundle.md"
CACHE = ROOT / "data/ops/near_miss_bundle_cache"
SEQ_RUN = ROOT / "data/colab/runs/seq_ensemble/20261011_215342"
WL_CACHE = ROOT / "data/ops/workload_cache"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
PAIRS_DIR = ROOT / "data/ops/model_clv_pairs"
MARKER = "=== RESULTS BELOW ==="
DEFAULT_BASELINE_DIR = ROOT / "reports/lower_tail"
BASELINE_DIR = DEFAULT_BASELINE_DIR  # A0 = the lower-tail mixture stored here (components_*.npz + components_pi.npz)


def baseline_is_current() -> bool:
    return BASELINE_DIR.resolve() == DEFAULT_BASELINE_DIR.resolve()


def set_baseline(path: Path | str | None) -> Path:
    """Point A0 at another directory of lower-tail components (same schema), e.g. a passing PI_RECENT_ROLE mixture. Caches and
    outputs get a suffix so the two baselines never share an arm. The rule names the condition for choosing it."""
    global BASELINE_DIR, CACHE, OUT_DIR, REPORT_MD
    BASELINE_DIR = DEFAULT_BASELINE_DIR if not path else Path(path).resolve()
    E.LT_DIR = BASELINE_DIR
    from research.eval import workload_eval as WE

    WE.LT_DIR = BASELINE_DIR
    sfx = "" if baseline_is_current() else "_" + BASELINE_DIR.name
    CACHE = ROOT / f"data/ops/near_miss_bundle_cache{sfx}"
    OUT_DIR = ROOT / f"reports/prereg_near_miss_bundle{sfx}"
    REPORT_MD = ROOT / f"reports/prereg_near_miss_bundle{sfx}.md"
    return BASELINE_DIR


STATS = ("pts", "reb", "ast", "fg3m")
SEASONS = (2023, 2024)
KEYS = ["game_id", "player_id"]
#: the three members, fixed by docs/DECISIONS.md (b91893e). Adding or removing one is a new rule.
MEMBERS_PTS = ("LOAD", "ROUTER", "SEQ")
MEMBERS_OTHER = ("LOAD", "SEQ")
N_BOOT = 2000
BOOT_SEED = 0
PLACEBO_SEED = 0
# --- constants of the rule (changing one changes the rule)
FLOOR = -0.005
PLACEBO_MARGIN = (
    -0.001
)  # bundle minus bundle-with-the-member's-past-only-placebo, point, CI upper < 0, EACH season
SHUFFLE_MIN = -0.0025  # a shuffled-label effect at or below this FAILS the check (half the floor)
CARRY = 0.8
SLICE_WORSE = 0.01
MIN_SLICE_N = 300
REPRO_TOL = 1e-6
MDE_MAX = 0.005
GATE_BLOCK_DAYS = 30
GATE_MIN_ROWS = 3000
NULL_GAP_PP = 2.0
G_BUCKETS = ("<5", "5-20", ">20")
GUARD_SLICES = (*E.GUARD_SLICES, "minutes<5")
#: A0 mixture integer CRPS (select 2023, report 2024), reports/prereg_workload/gate.json G4
A0_REF = {
    "pts": (3.140236, 3.176840),
    "reb": (1.288179, 1.311401),
    "ast": (0.901954, 0.898517),
    "fg3m": (0.566212, 0.590155),
}
#: member numbers already in the ledger (T440 E1, T442 E2 learned, T452 AL_LOAD pts), reproduced as a gate
E1_REF = (-0.0053, -0.0012)
ALLOAD_PTS_REF = (-0.003424, -0.006491)

AMBIGUITIES: dict[str, str] = {
    "LOAD member": "the 15 LOAD columns of research/workload (LOAD_MIN 8 + LOAD_TRACK 7; workload_eval arm AL_LOAD), NOT the 27-column B arm: the carrier of the WORKLOAD gain was the LOAD family (KO_LOAD removed it) and the review named LOAD",
    "ROUTER": "E1 with A0 replaced by the LOAD arm: pmf mixture g*A1 + (1-g)*LOAD; A1 rows missing from the XGBoost export (118 of 55,320) take weight 0 (router = LOAD); g is the stored P(minutes>=5|plays) of the production minutes path",
    "BLEND": "ensemble_eval.learned_pool, re-implemented to keep input row order (weights are returned in the order of the rows, never re-sorted): candidates [ROUTER integer grid, SEQ E2 199-grid], 30-day blocks, min 3000 earlier rows, rows before the first fit weigh 0.5/0.5, output pooled 199-grid -> integer support",
    "placebo": "past-only: each row takes the vector (15 LOAD columns / the stored A1 grid / the stored SEQ grid) of ONE uniformly drawn strictly earlier row of the same player in the frame; a player's first row draws from a uniformly drawn strictly earlier row of any player; the earliest date keeps its own (counted)",
    "shuffle": "two parts, both must pass: SHUF-M retrains production and production+LOAD on the SAME permutation of the training residuals (collect_components shuffle=True, one permutation per month block, shared by both arms) and scores on the REAL labels; SHUF-C draws labels from A0's own predictive distribution (A0 is the truth), keeps every stored member as is, refits the gate on those labels and scores bundle vs A0 on them. The effect must stay above -0.0025 in both seasons. SEQ and XGBoost are stored out-of-fold forecasts and cannot be retrained here, which is why SHUF-C uses the A0-null world instead of permuted labels (permuted labels reward any arm with a tighter location spread, which the unit test showed)",
    "slices": "regular / postseason (game_id not 002: playoffs and play-in) / starter / bench / cold start / first 15 / teammate OUT / realised minutes < 5, each n >= 300, bundle minus A0 point <= +0.01",
    "knockout": "member removed from the pipeline (LOAD -> production grid; ROUTER -> none; SEQ -> no blend); alone: the member added to production only. Flag if any alone arm keeps >= 80% of the 2023 or 2024 gain, or any removal leaves <= 20% of it. A flag is never a pass until the adversary clears it",
    "family": "BH over the four stats {pts, reb, ast, fg3m} separately for 2023 and 2024, two-sided game-clustered bootstrap p; guards, placebos, shuffles are conjunctive requirements, not tests in the family",
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=float))


# --------------------------------------------------------------------------- freeze and holdout refusal
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def head_text(doc: Path = DOC) -> str:
    out: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        out.append(line)
    return "".join(out)


def frozen_sha(doc: Path = DOC) -> str:
    return hashlib.sha256(head_text(doc).encode()).hexdigest()


def doc_status(doc: Path = DOC) -> str:
    for line in head_text(doc).splitlines():
        if line.startswith("Status:"):
            return line.split()[1].strip("*:").upper()
    return "UNKNOWN"


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path = DOC, git: Callable[..., str] = _git) -> str:
    """Refuse unless ``sha`` is the hash of the rule text, the document says FROZEN, and the freeze commit exists."""
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if doc_status(doc) != "FROZEN":
        raise RuntimeError(f"{doc} is not marked FROZEN: no arm may be fitted")
    if freeze_commit(sha, git) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; no arm may be fitted"
        )
    return sha


def require_holdout_row(
    sha: str, log: Path = HOLDOUT_LOG, doc: Path = DOC, git: Callable[..., str] = _git
) -> str:
    """The holdout stage gate: a committed, unmodified HOLDOUT_ACCESS_LOG row naming this rule, marked PRE-REGISTERED and DEVIN CONFIRMED."""
    require_frozen(sha, doc, git)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            f"no committed holdout row for {ID} (PRE-REGISTERED, DEVIN CONFIRMED, sha8): the holdout stage refuses"
        )
    if git("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(
            "HOLDOUT_ACCESS_LOG.md has uncommitted changes: commit the row BEFORE the run"
        )
    if not git("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is not in any commit: the holdout stage refuses")
    return rows[0]


# --------------------------------------------------------------------------- pure helpers (unit tested)
def past_only_index(
    player: np.ndarray, date_days: np.ndarray, seed: int = PLACEBO_SEED
) -> tuple[np.ndarray, dict[str, int]]:
    """Source row for every row: a uniformly drawn STRICTLY earlier row of the same player; a player's first row draws from a
    strictly earlier row of any player; rows on the earliest date keep their own index. Returns (index, counts)."""
    n = len(player)
    rng = np.random.default_rng(seed)
    order = np.lexsort((np.arange(n), date_days, player))
    p, d = player[order], date_days[order]
    new = np.r_[True, p[1:] != p[:-1]]
    start = np.maximum.accumulate(np.where(new, np.arange(n), 0))
    # first position of each (player, date) run: strictly earlier rows are those before the run start
    run_new = new | np.r_[True, d[1:] != d[:-1]]
    run_start = np.maximum.accumulate(np.where(run_new, np.arange(n), 0))
    k = run_start - start  # number of strictly earlier rows of this player
    src_sorted = np.arange(n)
    own = k > 0
    src_sorted[own] = start[own] + np.floor(rng.random(own.sum()) * k[own]).astype(np.int64)
    idx = np.empty(n, dtype=np.int64)
    idx[order] = order[src_sorted]
    # rows with no earlier row of their own player: any strictly earlier row (global date order)
    own_orig = np.empty(n, dtype=bool)
    own_orig[order] = own
    by_date = np.argsort(date_days, kind="stable")
    first_pos = np.searchsorted(
        date_days[by_date], date_days, side="left"
    )  # rows strictly earlier = by_date[:first_pos]
    n_global = n_self = 0
    for i in np.flatnonzero(~own_orig):
        m = first_pos[i]
        if m > 0:
            idx[i] = by_date[int(rng.integers(0, m))]
            n_global += 1
        else:
            idx[i] = i
            n_self += 1
    return idx, {"own_player": int(own.sum()), "any_player": n_global, "self": n_self}


def a0_null_labels(a0_int: np.ndarray, seed: int = 1) -> np.ndarray:
    """Labels drawn from A0's own predictive distribution (one uniform rank per row on its integer equal-mass grid).
    In this world A0 is the truth: no member can know more than A0, so any gain of the bundle over A0 is an artefact of the
    pipeline (an in-sample gate, a peeking router), never information. Counts stay integers."""
    rng = np.random.default_rng(seed)
    k = rng.integers(0, a0_int.shape[1], size=a0_int.shape[0])
    return a0_int[np.arange(a0_int.shape[0]), k].astype(float)


def router_grid(
    first_int: np.ndarray,
    a1_int: np.ndarray | None,
    g: np.ndarray | None,
    present: np.ndarray | None,
) -> np.ndarray:
    """E1: pmf mixture g * A1 + (1 - g) * first. No A1 (or a row without one) -> the first-stage grid unchanged."""
    if a1_int is None:
        return first_int
    w = np.where(present, np.clip(g, 0.0, 1.0), 0.0)
    return E.mix_integer_quantiles(np.where(present[:, None], a1_int, first_int), first_int, w)


def learned_blend(
    first199: np.ndarray,
    seq199: np.ndarray,
    y: np.ndarray,
    date_days: np.ndarray,
    season: np.ndarray,
    ctx: Any,
    stat: str = "pts",
) -> tuple[np.ndarray, np.ndarray]:
    """Walk-forward softmax gate over [first, seq]; the gate for a 30-day block is fit on rows dated strictly before it.
    Rows keep their input order. Rows before the first fit weigh 0.5 / 0.5. Returns (pooled 199-grid, weights (n, 2))."""
    import pandas as pd

    from research.stack.data import StackData
    from research.stack.router import SoftmaxGate, WalkForwardConfig, walk_forward

    keys = pd.DataFrame(
        {"game_date": pd.to_datetime(date_days.astype("datetime64[D]")), "season": season}
    )
    cand = np.stack([E.grid19(first199), E.grid19(seq199)], axis=1)
    data = StackData(
        stat, ["first", "seq"], keys, y.astype(float), cand, ctx.reset_index(drop=True)
    )
    res = walk_forward(
        data,
        SoftmaxGate,
        WalkForwardConfig(block_days=GATE_BLOCK_DAYS, min_train_rows=GATE_MIN_ROWS),
    )
    w = np.where(np.isnan(res.weights), 0.5, res.weights)
    return np.asarray(w[:, :1] * first199 + w[:, 1:2] * seq199), w


def shuffle_fails(effects: dict[str, float], threshold: float = SHUFFLE_MIN) -> bool:
    """The shuffled-label check FAILS when the candidate beats the baseline by at least half the floor in any season."""
    return any(v <= threshold for v in effects.values())


def one_member_flag(
    gain_full: float, alone: dict[str, float], without: dict[str, float]
) -> dict[str, Any]:
    """Gains are positive = better than A0. Flag if a member alone keeps >= CARRY of the gain or removing it leaves <= 1 - CARRY."""
    if not gain_full > 0:
        return {"flag": False, "carriers": [], "na": True}
    carriers = [m for m, a in alone.items() if a / gain_full >= CARRY]
    carriers += [
        m for m, w in without.items() if (gain_full - w) / gain_full >= CARRY and m not in carriers
    ]
    return {"flag": bool(carriers), "carriers": carriers, "na": False}


def stat_verdict(
    harm: bool,
    core: bool,
    guards: bool,
    placebo: bool,
    shuffle_ok: bool,
    flag: bool,
    repro_ok: bool = True,
) -> str:
    if harm:
        return "HARM"
    if not (core and repro_ok):
        return "FAIL"
    if not (guards and placebo and shuffle_ok):
        return "FAIL_GUARD"
    return "FLAG_ONE_MEMBER" if flag else "PASS"


# --------------------------------------------------------------------------- data
def minutes_bucket(m: np.ndarray) -> np.ndarray:
    return np.where(m < 5, 0, np.where(m <= 20, 1, 2))


def load_world() -> dict[str, Any]:
    """A0 arrays, slice columns, dates, ctx, the stored members aligned to the A0 row order (hard errors on any misalignment)."""
    a0 = E.load_a0()
    base = E.build_base(a0)
    for st in STATS[1:]:
        if not (
            np.array_equal(base[st]["game_id"], base["pts"]["game_id"])
            and np.array_equal(base[st]["player_id"], base["pts"]["player_id"])
        ):
            raise RuntimeError(f"{st}: A0 rows are not the pts rows")
    b0 = base["pts"]
    cm = pl.read_parquet(E.EXPORT_DIR / "seq_ensemble_cur_meta.parquet").select(
        "game_id", pl.col("player_id").cast(pl.Int64), pl.col("game_date").cast(pl.Date).alias("_d")
    )
    j = pl.DataFrame({"game_id": b0["game_id"], "player_id": b0["player_id"]}).join(
        cm, on=KEYS, how="left"
    )
    if j["_d"].null_count():
        raise RuntimeError("game dates missing for A0 rows")
    date_days = j["_d"].to_numpy().astype("datetime64[D]").astype(np.int64)
    import pandas as pd

    ctx = pd.DataFrame(
        {
            "log_n_prior": np.log1p(b0["n_prior"]),
            "starter10": b0["starter10"],
            "n_rep_out": b0["n_rep_out"],
            "has_report": b0["has_report"],
            "team_game_no": b0["team_game_no"],
        }
    )
    gz = np.load(E.CACHE / "g_pts.npz", allow_pickle=True)
    if not (
        np.array_equal(gz["game_id"], b0["game_id"])
        and np.array_equal(gz["player_id"], b0["player_id"])
    ):
        raise RuntimeError("stored g is not aligned with the A0 rows")
    a1 = np.load(E.A1_ALIGNED, allow_pickle=True)
    pos = _positions(b0, a1["game_id"], a1["player_id"])
    present = pos >= 0
    a1_int = np.zeros((len(present), 199), dtype=np.int16)
    a1_int[present] = to_integer_support(a1["q"][pos[present]].astype(float)).astype(np.int16)
    seq = E.load_seq(SEQ_RUN, "E2", base)
    return {
        "base": base, "date_days": date_days, "ctx": ctx, "g": gz["g"].astype(float), "a1_int": a1_int,
        "a1_present": present, "seq": seq,
    }  # fmt: skip


def _positions(b0: dict[str, Any], gid: np.ndarray, pid: np.ndarray) -> np.ndarray:
    kidx = {(str(a), int(p)): i for i, (a, p) in enumerate(zip(gid, pid, strict=True))}
    return np.array(
        [
            kidx.get((str(a), int(p)), -1)
            for a, p in zip(b0["game_id"], b0["player_id"], strict=True)
        ]
    )


def load_cached_arm(stat: str, tag: str, b: dict[str, Any]) -> np.ndarray:
    z = np.load(CACHE / f"{stat}__{tag}.npz", allow_pickle=True)
    if not (
        np.array_equal(z["gid"].astype(str), b["game_id"].astype(str))
        and np.array_equal(z["pid"].astype(np.int64), b["player_id"])
    ):
        raise RuntimeError(f"{stat}/{tag}: cached arm rows are not the A0 rows")
    if not np.array_equal(z["y"], b["y"]):
        raise RuntimeError(f"{stat}/{tag}: labels differ from A0")
    return z["qi"].astype(np.int16)


# --------------------------------------------------------------------------- bundle arms (numpy; one stat)
def build_arms(
    stat: str, w: dict[str, Any], arms_in: dict[str, np.ndarray], y_fit: np.ndarray | None = None
) -> dict[str, np.ndarray]:
    """Every pipeline variant of one stat as integer 199-grids. ``arms_in``: A0, L (LOAD arm), PL_L (LOAD placebo arm).
    ``y_fit`` replaces the labels the gate is fit on (the SHUF-C world); the stored members are untouched."""
    b = w["base"][stat]
    y = b["y"] if y_fit is None else y_fit
    dd, se = w["date_days"], b["season"]
    pts = stat == "pts"
    a1 = w["a1_int"] if pts else None
    pr = w["a1_present"] if pts else None
    g = w["g"] if pts else None
    seq = w["seq"][stat]
    a0, lo, plo = arms_in["A0"], arms_in["L"], arms_in["PL_L"]
    pidx, _ = past_only_index(b["player_id"], dd, PLACEBO_SEED + 1)
    pidx_s, _ = past_only_index(b["player_id"], dd, PLACEBO_SEED + 2)

    def blend(first_int: np.ndarray, seqg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        pooled, ww = learned_blend(first_int.astype(float), seqg, y, dd, se, w["ctx"], stat)
        return to_integer_support(pooled).astype(np.int16), ww

    out: dict[str, np.ndarray] = {"A0": a0}
    R = router_grid(lo, a1, g, pr)
    out["R"] = R
    out["B"], wB = blend(R, seq)
    out["B_weight_seq"] = wB[:, 1]  # type: ignore[assignment]
    out["K_LOAD"], _ = blend(router_grid(a0, a1, g, pr), seq)
    out["K_SEQ"] = R
    out["AL_LOAD"] = lo
    out["AL_SEQ"], _ = blend(a0, seq)
    out["PL_LOAD"], _ = blend(router_grid(plo, a1, g, pr), seq)
    out["PL_SEQ"], _ = blend(R, seq[pidx_s])
    if pts:
        out["K_ROUTER"], _ = blend(lo, seq)
        out["AL_ROUTER"] = router_grid(a0, a1, g, pr)
        a1_pl, pr_pl = a1[pidx], pr[pidx]
        out["PL_ROUTER"], _ = blend(router_grid(lo, a1_pl, g, pr_pl), seq)
    return out


# --------------------------------------------------------------------------- scoring
def delta_by_season(
    stat: str, b: dict[str, Any], q1: np.ndarray, q0: np.ndarray, y: np.ndarray | None = None
) -> dict[str, dict[str, Any]]:
    yy = b["y"] if y is None else y
    out: dict[str, dict[str, Any]] = {}
    for s in SEASONS:
        m = b["season"] == s
        d = crps_from_quantiles(q1[m].astype(float), yy[m]) - crps_from_quantiles(
            q0[m].astype(float), yy[m]
        )
        out[str(s)] = {
            "n": int(m.sum()),
            **E.clustered(d, b["game_id"][m]),
            "mean": float(d.mean()),
        }
    return out


def score_stat(
    stat: str,
    w: dict[str, Any],
    arms_in: dict[str, np.ndarray],
    shuf_m: dict[str, dict[str, float]],
) -> dict[str, Any]:
    b = w["base"][stat]
    pts = stat == "pts"
    rows = np.arange(len(b["y"]))
    _log(f"{stat}: building bundle arms")
    arms = build_arms(stat, w, arms_in)
    members = MEMBERS_PTS if pts else MEMBERS_OTHER
    ev = E.evaluate_arm(stat, b, rows, arms["B"], ci_slices=False, dk=pts)
    res: dict[str, Any] = {"stat": stat, "members": list(members), "seasons": {}}
    a0 = arms["A0"]
    for s in SEASONS:
        r = ev[str(s)]
        g = E.guards_for({k: v for k, v in r.items() if not k.startswith("_")}, stat, e1_like=pts)
        sl = r["slices"]["minutes<5"]
        g["g2b_minutes<5"] = bool(sl["n"] < MIN_SLICE_N or sl.get("point", -1.0) <= SLICE_WORSE)
        a0cal = E.calibration(a0[b["season"] == s].astype(np.int16), b["y"][b["season"] == s])
        res["seasons"][str(s)] = {
            "n": r["n"], "n_games": r["n_games"], "crps_bundle": r["crps_arm"], "crps_a0": r["crps_a0"], "delta": r["delta"],
            "p": r["p"], "calibration": r["calibration"], "a0_calibration": a0cal, "slices": r["slices"],
            "team_share": r["team_share"], "tll_dk": r.get("tll_dk"), "tll_fixed": r.get("tll_fixed"), "guards": g,
        }  # fmt: skip
    # component deltas (all vs A0, plus bundle-minus-variant for placebo / knockout)
    comp: dict[str, Any] = {}
    for name in ("R", "AL_LOAD", "AL_SEQ", "K_LOAD", "K_SEQ"):
        comp[name] = delta_by_season(stat, b, arms[name], a0)
    if pts:
        for name in ("AL_ROUTER", "K_ROUTER"):
            comp[name] = delta_by_season(stat, b, arms[name], a0)
    pl_ok: dict[str, bool] = {}
    pl_rec: dict[str, Any] = {}
    for m in members:
        d = delta_by_season(stat, b, arms["B"], arms[f"PL_{m}"])
        pl_rec[m] = d
        pl_ok[m] = all(
            d[str(s)]["point"] <= PLACEBO_MARGIN and d[str(s)]["hi"] < 0 for s in SEASONS
        )
    res["components"], res["placebo"], res["placebo_ok"] = comp, pl_rec, pl_ok
    # knockout / alone flags per season
    flags: dict[str, Any] = {}
    for s in SEASONS:
        full = -res["seasons"][str(s)]["delta"]["point"]
        alone = {m: -comp[f"AL_{m}"][str(s)]["point"] for m in members}
        wo = {m: -comp[f"K_{m}"][str(s)]["point"] for m in members}
        flags[str(s)] = {
            "gain_full": full,
            "alone": alone,
            "without": wo,
            **one_member_flag(full, alone, wo),
        }
    res["flags"] = flags
    # SHUF-C: labels drawn from A0's own predictive distribution (A0 is the truth), the gate refit on them, scored on them
    yp = a0_null_labels(arms_in["A0"], seed=1)
    arms_c = build_arms(stat, w, arms_in, y_fit=yp)
    shuf_c = delta_by_season(stat, b, arms_c["B"], arms_c["A0"], y=yp)
    res["shuffle"] = {
        "SHUF_M": shuf_m,
        "SHUF_C": {s: v["point"] for s, v in shuf_c.items()},
        "SHUF_C_detail": shuf_c,
    }
    res["shuffle_ok"] = not (shuffle_fails(shuf_m) or shuffle_fails(res["shuffle"]["SHUF_C"]))
    # descriptive: bundle vs its best single member (not a pass condition)
    best = min(comp[f"AL_{m}"]["2024"]["mean"] for m in members)
    res["bundle_minus_best_member_2024"] = res["seasons"]["2024"]["delta"]["point"] - best
    # weights of the final gate (SEQ share by season, descriptive)
    res["seq_weight_mean"] = {
        str(s): float(arms["B_weight_seq"][b["season"] == s].mean()) for s in SEASONS
    }
    res["_arms"] = arms
    return res


def shuffle_model_layer(stat: str, b: dict[str, Any]) -> dict[str, dict[str, float]]:
    """SHUF-M effects (L' minus base', scored on real labels) per season, from the two retrained shuffled arms."""
    base_s = load_cached_arm(stat, "SH_base", b)
    load_s = load_cached_arm(stat, "SH_L", b)
    d = delta_by_season(stat, b, load_s, base_s)
    return {s: v["point"] for s, v in d.items()}


def decide_stat(res: dict[str, Any], repro_ok: bool, bh: dict[str, float]) -> dict[str, Any]:
    seasons = res["seasons"]
    core = {
        s: bool(
            seasons[s]["delta"]["point"] <= FLOOR and seasons[s]["delta"]["hi"] < 0 and bh[s] < 0.05
        )
        for s in seasons
    }
    guards = {s: all(seasons[s]["guards"].values()) for s in seasons}
    harm = any(
        seasons[s]["delta"]["point"] >= -FLOOR and seasons[s]["delta"]["lo"] > 0 for s in seasons
    )
    flag = any(res["flags"][s]["flag"] for s in seasons)
    v = stat_verdict(
        harm,
        all(core.values()),
        all(guards.values()),
        all(res["placebo_ok"].values()),
        res["shuffle_ok"],
        flag,
        repro_ok,
    )
    return {"verdict": v, "core": core, "guards_ok": guards, "bh_p": bh, "flag": flag}


# --------------------------------------------------------------------------- secondary: decision score at DK T-60
def decision_scores(w: dict[str, Any], by_stat: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    """Descriptive: DraftKings T-60 legs re-priced with A0 and with the bundle (docs/DECISION_SCORING.md). Cannot rescue a failure."""
    try:
        from nba.markets import decision_scoring as DS
        from research.eval.team_budget_eval import legs_with_p, p_over_from_grid

        legs = DS.load_legs(PAIRS_DIR)
        out: dict[str, Any] = {}
        for which in ("A0", "B"):
            p = np.full(len(legs["line"]), np.nan)
            for stat, arms in by_stat.items():
                b = w["base"][stat]
                idx = pl.DataFrame(
                    {"game_id": b["game_id"], "player_id": b["player_id"]}
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
                pp = p_over_from_grid(arms[which][j["_i"].fill_null(0).to_numpy()], legs["line"][m])
                p[m[ok]] = pp[ok]
            keep = ~np.isnan(p)
            lg = {
                k: (v[keep] if isinstance(v, np.ndarray) and v.shape[:1] == keep.shape else v)
                for k, v in legs.items()
            }
            gm = {g: i for i, g in enumerate(np.unique(lg["game_id"]))}
            lg["gi"], lg["G"] = np.array([gm[g] for g in lg["game_id"]]), len(gm)
            r = DS.run_all(legs_with_p(lg, p[keep]))
            out[which] = {
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
    except Exception as exc:  # secondary; neither rescues nor fails the rule
        return {"error": repr(exc)}


# --------------------------------------------------------------------------- refit arms (LightGBM; heavy.lock)
def add_past_only_placebo(
    fr: pl.DataFrame, cols: tuple[str, ...], seed: int = PLACEBO_SEED
) -> pl.DataFrame:
    """``pp_<col>``: the row's LOAD vector replaced by that of a strictly earlier row (same player where one exists)."""
    dd = fr["game_date"].cast(pl.Date).to_numpy().astype("datetime64[D]").astype(np.int64)
    idx, cnt = past_only_index(fr["player_id"].to_numpy().astype(np.int64), dd, seed)
    _log(f"past-only placebo rows: {cnt}")
    return fr.with_columns([pl.Series("pp_" + c, fr[c].to_numpy()[idx]) for c in cols])


def fit_arm(
    fr: pl.DataFrame,
    stat: str,
    cfg: Any,
    pic: dict[str, np.ndarray],
    tag: str,
    cols: list[str],
    shuffle: bool,
) -> Path:
    """One walk-forward LightGBM arm through the stored lower-tail mixture (the WORKLOAD ``run_props_arm`` path), cached."""
    from research.eval import workload_eval as WE

    p = CACHE / f"{stat}__{tag}.npz"
    if p.exists():
        return p
    t0 = time.time()
    comp = WE.collect_components(
        fr, stat, cfg, stat_feature_names(stat) + list(cols), shuffle=shuffle
    )
    qi = WE.mixture_qi(comp, pic, stat)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(
        p,
        qi=qi,
        y=comp["row_y"],
        gid=comp["row_gid"].astype(str),
        pid=comp["row_pid"].astype(np.int64),
        season=comp["row_season"],
    )
    _log(f"arm {stat}/{tag} done in {time.time() - t0:.0f}s")
    return p


def repro_check(stat: str, b: dict[str, Any], tag: str = "base") -> dict[str, Any]:
    """The refit with no extra column must equal the stored A0 (mean integer CRPS per season, tolerance REPRO_TOL)."""
    q = load_cached_arm(stat, tag, b)
    out: dict[str, Any] = {
        "stat": stat,
        "rows_equal_frac": float((q == b["a0_int"]).all(axis=1).mean()),
    }
    ok = True
    for s in SEASONS:
        m = b["season"] == s
        c1 = float(crps_from_quantiles(q[m].astype(float), b["y"][m]).mean())
        c0 = float(crps_from_quantiles(b["a0_int"][m].astype(float), b["y"][m]).mean())
        out[str(s)] = {"refit": c1, "stored": c0, "diff": c1 - c0}
        ok &= abs(c1 - c0) <= REPRO_TOL
    out["pass"] = bool(ok)
    return out


def load_audit(fr: pl.DataFrame, b: dict[str, Any], cols: list[str]) -> dict[str, Any]:
    """Null rate of each LOAD column by realised-minutes bucket on the A0 rows (limit NULL_GAP_PP)."""
    keys = pl.DataFrame({"game_id": b["game_id"], "player_id": b["player_id"], "_m": b["minutes"]})
    j = keys.join(
        fr.select("game_id", pl.col("player_id").cast(pl.Int64), *cols).unique(KEYS),
        on=KEYS,
        how="left",
    )
    bk = minutes_bucket(j["_m"].to_numpy())
    out: dict[str, Any] = {}
    for c in cols:
        col = j[c].to_numpy().astype(float)
        isn = np.isnan(col)  # polars nulls arrive as NaN
        rates = [float(isn[bk == k].mean()) if (bk == k).any() else 0.0 for k in range(3)]
        out[c] = {"by_bucket": rates, "gap_pp": 100.0 * (max(rates) - min(rates))}
    out["max_gap_pp"] = max(v["gap_pp"] for v in out.values())
    out["pass"] = bool(out["max_gap_pp"] <= NULL_GAP_PP)
    return out


def stage_arms(
    sha: str | None,
    stats: tuple[str, ...] = STATS,
    allow_draft: bool = False,
    repro_only: bool = False,
) -> dict[str, Any]:
    from research.eval import workload_eval as WE

    if not allow_draft:
        require_frozen(sha)
    w = load_world()
    out: dict[str, Any] = {"repro": {}}
    with WE.heavy():
        fr, cfg, _ = WE.load_props_frame()
        fr = add_past_only_placebo(fr, tuple(WE.LOAD_COLS))
        pic = WE.load_pic()
        load = list(WE.LOAD_COLS)
        pp = ["pp_" + c for c in load]
        out["load_nulls"] = load_audit(fr, w["base"]["pts"], load)
        _log(f"LOAD null audit: max gap {out['load_nulls']['max_gap_pp']:.3f} pp")
        for st in stats:
            b = w["base"][st]
            fit_arm(fr, st, cfg, pic, "base", [], False)
            out["repro"][st] = repro_check(st, b)
            _log(f"{st}: refit-base reproduction {out['repro'][st]}")
            write_json(OUT_DIR / ("repro_draft.json" if allow_draft else "repro.json"), out)
            if not out["repro"][st]["pass"]:
                raise SystemExit(
                    f"STOP: the refit base does not reproduce the stored A0 for {st}; no candidate arm is fitted"
                )
            if repro_only:
                continue
            fit_arm(fr, st, cfg, pic, "L", load, False)
            fit_arm(fr, st, cfg, pic, "PL_L", pp, False)
            fit_arm(fr, st, cfg, pic, "SH_base", [], True)
            fit_arm(fr, st, cfg, pic, "SH_L", load, True)
    return out


# --------------------------------------------------------------------------- score stage
def stage_score(sha: str | None) -> dict[str, Any]:
    sha_ok = require_frozen(sha)
    repro = json.loads((OUT_DIR / "repro.json").read_text())
    repro_ok = all(repro["repro"].get(s, {}).get("pass", False) for s in STATS) and bool(
        repro.get("load_nulls", {}).get("pass", False)
    )
    w = load_world()
    res: dict[str, Any] = {
        "rule_sha256": sha_ok,
        "baseline_dir": str(BASELINE_DIR),
        "ambiguities": AMBIGUITIES,
        "repro": repro,
        "stats": {},
    }
    keep: dict[str, dict[str, np.ndarray]] = {}
    for st in STATS:
        b = w["base"][st]
        arms_in = {
            "A0": b["a0_int"],
            "L": load_cached_arm(st, "L", b),
            "PL_L": load_cached_arm(st, "PL_L", b),
        }
        r = score_stat(st, w, arms_in, shuffle_model_layer(st, b))
        keep[st] = {"A0": r["_arms"]["A0"], "B": r["_arms"]["B"]}
        del r["_arms"]
        res["stats"][st] = r
        write_json(OUT_DIR / "results_partial.json", res)
    bh = {}
    for s in SEASONS:
        adj = bh_adjust(np.array([res["stats"][st]["seasons"][str(s)]["p"] for st in STATS]))
        bh[str(s)] = dict(zip(STATS, (float(a) for a in adj), strict=True))
    for st in STATS:
        res["stats"][st]["decision"] = decide_stat(
            res["stats"][st], repro_ok, {s: bh[s][st] for s in bh}
        )
    res["decision_score_dk_t60"] = decision_scores(w, keep)
    res["eligible_for_holdout_row"] = [
        st for st in STATS if res["stats"][st]["decision"]["verdict"] == "PASS"
    ]
    write_json(OUT_DIR / "results.json", res)
    REPORT_MD.write_text(render(res))
    return res


def _f(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else (f"{x:+.{nd}f}" if isinstance(x, (int, float)) else str(x))


def render(res: dict[str, Any]) -> str:
    L = [f"# {ID}: results under rule sha256 {res['rule_sha256'][:8]}", ""]
    L += [
        "| stat | season | n | A0 | bundle | delta [95% CI, game-clustered] | BH p | cov80 | guards |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for st, r in res["stats"].items():
        for s, x in r["seasons"].items():
            d = x["delta"]
            L.append(
                f"| {st} | {s} | {x['n']} | {x['crps_a0']:.4f} | {x['crps_bundle']:.4f} | {_f(d['point'])} [{_f(d['lo'])}, {_f(d['hi'])}] | {r['decision']['bh_p'][s]:.4f} | {x['calibration']['cov80']:.3f} | {'ok' if all(x['guards'].values()) else 'FAIL ' + ','.join(k for k, v in x['guards'].items() if not v)} |"
            )
    L += [
        "",
        "| stat | verdict | placebo ok | shuffle ok | flag | carriers 2023 / 2024 |",
        "|---|---|---|---|---|---|",
    ]
    for st, r in res["stats"].items():
        d = r["decision"]
        L.append(
            f"| {st} | {d['verdict']} | {r['placebo_ok']} | {r['shuffle_ok']} | {d['flag']} | {r['flags']['2023']['carriers']} / {r['flags']['2024']['carriers']} |"
        )
    L += [
        "",
        f"Eligible for a holdout row (Devin's word): {res['eligible_for_holdout_row'] or 'none'}",
    ]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- gate stage (no candidate arm is fitted)
def _season_mean(q: np.ndarray, b: dict[str, Any], s: int) -> float:
    m = b["season"] == s
    return float(crps_from_quantiles(q[m].astype(float), b["y"][m]).mean())


def stage_gate(refit_stats: tuple[str, ...] = (), recompute_g: bool = False) -> dict[str, Any]:
    t0 = time.time()
    w = load_world()
    base = w["base"]
    b0 = base["pts"]
    res: dict[str, Any] = {
        "id": ID,
        "doc_status": doc_status(),
        "doc_sha256": frozen_sha(),
        "checks": {},
    }
    ck = res["checks"]
    # G1: A0 reproduction, same rows across stats, seasons <= 2024
    g1: dict[str, Any] = {}
    for st in STATS:
        b = base[st]
        g1[st] = {
            str(s): {
                "crps": _season_mean(b["a0_int"], b, s),
                "ref": A0_REF[st][i],
                "n": int((b["season"] == s).sum()),
            }
            for i, s in enumerate(SEASONS)
        }
        for s in SEASONS:
            g1[st][str(s)]["pass"] = (
                abs(g1[st][str(s)]["crps"] - g1[st][str(s)]["ref"]) <= REPRO_TOL
            )
    g1["max_season"] = int(b0["season"].max())
    if (
        not baseline_is_current()
    ):  # alternative baseline: no stored reference; alignment and season bound only
        for st in STATS:
            for sn in ("2023", "2024"):
                g1[st][sn]["pass"] = True
    g1["baseline_dir"] = str(BASELINE_DIR)
    g1["baseline_is_current"] = baseline_is_current()
    g1["pass"] = bool(
        all(g1[st][str(s)]["pass"] for st in STATS for s in SEASONS)
        and g1["max_season"] <= MAX_SEASON
    )
    ck["G1_A0_reproduction"] = g1
    # G2: stored artefacts align to the A0 keys
    al = np.load(WL_CACHE / "pts__AL_LOAD.npz", allow_pickle=True)
    pos_a1 = w["a1_present"]
    g2: dict[str, Any] = {
        "al_load_pts_rows_equal_a0": bool(
            np.array_equal(al["gid"].astype(str), b0["game_id"])
            and np.array_equal(al["pid"].astype(np.int64), b0["player_id"])
            and np.array_equal(al["y"], b0["y"])
        ),
        "seq_E2_rows_missing": 0,
        "a1_present": int(pos_a1.sum()),
        "a1_missing_by_season": {
            str(s): int((~pos_a1 & (b0["season"] == s)).sum()) for s in SEASONS
        },
        "g_finite_in_unit_interval": bool(
            np.isfinite(w["g"]).all() and (w["g"] >= 0).all() and (w["g"] <= 1).all()
        ),
        "n_rows": int(len(b0["y"])),
    }
    bk = minutes_bucket(b0["minutes"])
    miss = [float((~pos_a1)[bk == k].mean()) for k in range(3)]
    g2["a1_missing_rate_by_minutes_bucket"] = dict(zip(G_BUCKETS, miss, strict=True))
    g2["a1_missing_gap_pp"] = 100.0 * (max(miss) - min(miss))
    g2["pass"] = bool(
        g2["al_load_pts_rows_equal_a0"]
        and g2["g_finite_in_unit_interval"]
        and g2["a1_missing_gap_pp"] <= NULL_GAP_PP
        and g2["a1_present"] >= 0.99 * g2["n_rows"]
    )
    ck["G2_alignment"] = g2
    # G3: member reproduction against the ledger-backed stored results
    e1_ref = json.loads((E.OUT_DIR / "e1_results.json").read_text())["e1"]
    arr = E.e1_arrays(b0, w["g"])
    rr = arr["rows"]
    sub = {
        k: (v[rr] if isinstance(v, np.ndarray) and len(v) == len(b0["y"]) else v)
        for k, v in b0.items()
    }
    g3: dict[str, Any] = {"E1_pts": {}, "E2_learned": {}, "AL_LOAD_pts": {}}
    for s in SEASONS:
        m = sub["season"] == s
        d = crps_from_quantiles(arr["e1"][m].astype(float), sub["y"][m]) - crps_from_quantiles(
            arr["a0_int"][m].astype(float), sub["y"][m]
        )
        ref = e1_ref[str(s)]["delta"]["point"]
        g3["E1_pts"][str(s)] = {
            "delta": float(d.mean()),
            "stored": ref,
            "n": int(m.sum()),
            "pass": abs(float(d.mean()) - ref) <= REPRO_TOL,
        }
    sc = json.loads((E.OUT_DIR / "score_results.json").read_text())["cells"]
    mde: dict[str, Any] = {}
    for st in STATS:
        b = base[st]
        pooled, _ = learned_blend(
            b["mix"], w["seq"][st], b["y"], w["date_days"], b["season"], w["ctx"], st
        )
        q = to_integer_support(pooled).astype(np.int16)
        g3["E2_learned"][st] = {}
        for s in SEASONS:
            m = b["season"] == s
            d = crps_from_quantiles(q[m].astype(float), b["y"][m]) - crps_from_quantiles(
                b["a0_int"][m].astype(float), b["y"][m]
            )
            ref = sc[f"E2|learned|{st}"][str(s)]["delta"]["point"]
            ci = E.clustered(d, b["game_id"][m])
            g3["E2_learned"][st][str(s)] = {
                "delta": float(d.mean()),
                "stored": ref,
                "pass": abs(float(d.mean()) - ref) <= REPRO_TOL,
            }
            mde.setdefault(st, {})[str(s)] = (ci["hi"] - ci["lo"]) / 2.0
    al_q = al["qi"].astype(np.int16)
    for i, s in enumerate(SEASONS):
        d = _season_mean(al_q, b0, s) - _season_mean(b0["a0_int"], b0, s)
        g3["AL_LOAD_pts"][str(s)] = {
            "delta": d,
            "stored": ALLOAD_PTS_REF[i],
            "pass": abs(d - ALLOAD_PTS_REF[i]) <= 5e-6,
        }
    cells3 = [v for k in ("E1_pts", "AL_LOAD_pts") for v in g3[k].values()]
    cells3 += [v for st in STATS for v in g3["E2_learned"][st].values()]
    g3["pass"] = bool(all(v["pass"] for v in cells3)) or not baseline_is_current()
    g3["compared_with_stored"] = baseline_is_current()
    ck["G3_member_reproduction"] = g3
    # G4: power: MDE (1.96 x clustered SE) from the E2-learned deltas vs A0, per stat and season, against the floor
    ck["G4_power"] = {
        "mde": mde,
        "floor": FLOOR,
        "pass": bool(all(v <= MDE_MAX for st in mde.values() for v in st.values())),
    }
    # G5: A0 calibration against the absolute guard (a cell whose A0 already fails it could never pass), guard-slice sizes, DK rows
    g5: dict[str, Any] = {"a0_guard": {}, "slice_n": {}, "dk_rows_pts": {}}
    for st in STATS:
        b = base[st]
        g5["a0_guard"][st] = {}
        g5["slice_n"][st] = {}
        for s in SEASONS:
            m = b["season"] == s
            cal = E.calibration(b["a0_int"][m], b["y"][m])
            g5["a0_guard"][st][str(s)] = {
                "bias": cal["bias"],
                "cov80": cal["cov80"],
                "ok": cal["guard"]["ok"],
                "cover": cal["guard"]["cover"],
            }
            mk = E.slice_masks(b, np.flatnonzero(m))
            g5["slice_n"][st][str(s)] = {k: int(mk[k].sum()) for k in GUARD_SLICES}
    for s in SEASONS:
        pos, _ = E.dk_rows(b0, np.flatnonzero(b0["season"] == s), s)
        g5["dk_rows_pts"][str(s)] = int(len(pos))
    g5["slices_testable"] = bool(
        all(
            n >= MIN_SLICE_N
            for st in STATS
            for s in SEASONS
            for n in g5["slice_n"][st][str(s)].values()
        )
    )
    g5["a0_guard_all_ok"] = bool(
        all(g5["a0_guard"][st][str(s)]["ok"] for st in STATS for s in SEASONS)
    )
    g5["pass"] = bool(g5["a0_guard_all_ok"])
    ck["G5_guards_testable"] = g5
    # G6: first gate fit (rows that weigh 0.5/0.5 because fewer than GATE_MIN_ROWS earlier rows exist)
    dd = w["date_days"]
    first_fit = int(np.sort(dd)[GATE_MIN_ROWS - 1]) if len(dd) > GATE_MIN_ROWS else -1
    start0 = int(dd.min())
    g6 = {
        "rows_before_first_fit": {
            str(s): int(
                (
                    (dd < start0 + ((first_fit - start0) // GATE_BLOCK_DAYS + 1) * GATE_BLOCK_DAYS)
                    & (b0["season"] == s)
                ).sum()
            )
            for s in SEASONS
        }
    }
    g6["pass"] = bool(g6["rows_before_first_fit"]["2024"] == 0)
    ck["G6_gate_warmup"] = g6
    # G7: provenance and the production-minutes drift (COACH_PHASE, T450): which stored inputs are post-clean-up
    prov = {}
    for name, p in {
        "lower_tail components (A0)": ROOT / "reports/lower_tail/components_pts.npz",
        "g (production minutes survival)": E.CACHE / "g_pts.npz",
        "A1 XGBoost aligned": E.A1_ALIGNED,
        "SEQ E2 OOF": SEQ_RUN / "oof_E2.npz",
        "AL_LOAD pts (WORKLOAD)": WL_CACHE / "pts__AL_LOAD.npz",
    }.items():
        prov[name] = {
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "after_cleanup_rebaseline_2026-10-11_15:08": p.stat().st_mtime
            >= time.mktime(time.strptime("2026-10-11 15:08", "%Y-%m-%d %H:%M")),
        }
    res["checks"]["G7_provenance"] = {
        "inputs": prov,
        "pass": all(v["after_cleanup_rebaseline_2026-10-11_15:08"] for v in prov.values()),
    }
    if recompute_g:
        g_new = E.g_scores(b0)
        diff = np.abs(g_new - w["g"])
        res["checks"]["G7b_g_recompute"] = {
            "max_abs_diff": {str(s): float(diff[b0["season"] == s].max()) for s in SEASONS},
            "mean_abs_diff": {str(s): float(diff[b0["season"] == s].mean()) for s in SEASONS},
            "pass": bool(diff.max() <= 1e-9),
        }
    res["gate_pass"] = bool(all(v.get("pass", True) for v in res["checks"].values()))
    res["seconds"] = time.time() - t0
    write_json(OUT_DIR / "gate.json", res)
    if refit_stats:
        res["refit_repro"] = stage_arms(None, tuple(refit_stats), allow_draft=True, repro_only=True)
        write_json(OUT_DIR / "gate.json", res)
    return res


def stage_holdout(sha: str | None) -> None:
    row = require_holdout_row(sha or "")
    raise SystemExit(
        f"holdout row present ({row[:80]}...) but no 2025 loader exists in this module: the touch is a separate committed script written after the row"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "arms", "score", "run", "holdout"])
    ap.add_argument("--sha", default=None)
    ap.add_argument(
        "--refit-repro",
        default="",
        help="comma list of stats whose refit base is compared with the stored A0 (gate only; takes heavy.lock)",
    )
    ap.add_argument("--recompute-g", action="store_true")
    ap.add_argument(
        "--baseline-dir",
        default=None,
        help="A0 components directory (default reports/lower_tail; the rule states when the PI_RECENT_ROLE one is used)",
    )
    a = ap.parse_args(argv)
    set_baseline(a.baseline_dir)
    if a.stage == "gate":
        r = stage_gate(tuple(x for x in a.refit_repro.split(",") if x), a.recompute_g)
        print(
            json.dumps(
                {
                    "gate_pass": r["gate_pass"],
                    "checks": {k: v.get("pass") for k, v in r["checks"].items()},
                }
            )
        )
        return 0 if r["gate_pass"] else 1
    if a.stage == "holdout":
        stage_holdout(a.sha)
        return 0
    require_frozen(a.sha)
    if a.stage in ("arms", "run"):
        gate = OUT_DIR / "gate.json"
        if not (gate.exists() and json.loads(gate.read_text()).get("gate_pass")):
            raise SystemExit("STOP: gate.json missing or not passing; run the gate stage first")
        stage_arms(a.sha)
    if a.stage in ("score", "run"):
        stage_score(a.sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
