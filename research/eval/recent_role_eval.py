# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc"
"""RECENT_ROLE evaluation, Stage 1 (docs/prereg/RECENT_ROLE.md; DRAFT until the main session commits `prereg(RECENT_ROLE): <sha256>`).

    M="-m research.eval.recent_role_eval"
    .venv/bin/python -W ignore $M gate   --sha <64 hex>   # control reproductions + parity + plants + audits; fits only the control C
    .venv/bin/python -W ignore $M arms   --sha <64 hex>   # Cp, R, R1, KO1, PL walk-forward + shared-permutation shuffled Cp/R (heavy.lock)
    .venv/bin/python -W ignore $M report --sha <64 hex>   # score, pass rule, markdown (light)
    .venv/bin/python -W ignore $M run    --sha <64 hex>   # gate -> arms -> report under ONE data/ops/heavy.lock
    .venv/bin/python -W ignore $M stage3 --sha <64 hex>   # refuses unless the holdout row is committed (see require_holdout_row)

Every stage refuses unless (a) the sha256 of the doc above ``=== RESULTS BELOW ===`` equals ``--sha`` and (b) ``git log`` on
main holds a commit whose subject is ``prereg(RECENT_ROLE): <sha256>``. ``nba.duckdb`` is read-only, ``season <= 2024`` is asserted,
season 2025 is never loaded in Stages 1 and 2. Stage 2a lives in ``research.eval.recent_role_props``.

Reuses (does not copy) the WORKLOAD harness: lock, clustered bootstrap, ECE, null audit, planted tests. Fixes its two defects:
the shuffled-label arm retrains BOTH arms on ONE permutation per block (``walk``), and the placebo is past-only
(``research.recent_role.placebo``).
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

import lightgbm as lgb
import numpy as np
import polars as pl

from nba.eval.context_residual_eval import TEST_SEASONS, month_blocks
from nba.props.context_residual import ContextResidualConfig, _matrix, _params
from research.eval import workload_eval as WE
from research.recent_role import placebo as PB
from research.recent_role import prod_state as PS

ID = "RECENT_ROLE"
DOC = Path("docs/prereg/RECENT_ROLE.md")
HOLDOUT_LOG = Path("docs/HOLDOUT_ACCESS_LOG.md")
OUT_DIR = Path("reports/prereg_recent_role")
REPORT_MD = Path("reports/prereg_recent_role.md")
CACHE = Path("data/ops/recent_role_cache")
DB = "nba.duckdb"
KEYS = ["game_id", "player_id"]
SEASONS = (2023, 2024)
N_BOOT = 2000
SEED = 0
MIN_TRAIN = 1500

FLOOR = -0.005  # log loss, R - C' (house P(play) floor, WORKLOAD / PPLAY_CONTEXT)
PLACEBO_MARGIN = -0.003  # R must beat its past-only placebo by this much in EACH season
SHUF_TOL = 0.001  # shuffled-label check FAILS if R_shuf - Cp_shuf <= -SHUF_TOL with CI upper < 0
ECE_TOL = 0.005
SLICE_TOL = 0.01
MIN_SLICE_N = 300
NULL_GAP_PP = 2.0
PLACEBO_MULT = 3.0
CARRY = 0.8
REPRO_TOL = 0.005  # C refit vs WORKLOAD's stored p_base, log loss per season (data clean-up drift; PPLAY_CONTEXT precedent)
SHARE_TOL = (
    0.005  # relative row-count drift vs the WORKLOAD gate (33,093 / 33,127) that is only reported
)

R_COLS: tuple[str, ...] = (
    "wl_min_last",
    "wl_mean_min_l3",
    "wl_min_3d",
    "wl_min_7d",
    "wl_mean_min_l5",
)
R1_COLS: tuple[str, ...] = ("wl_min_last",)
KO1_COLS: tuple[str, ...] = tuple(c for c in R_COLS if c not in R1_COLS)
PL_PREFIX = "pl_"
CP_ADD: tuple[str, ...] = PS.STAGE1_PROD_COLS
ARMS = ("base", "Cp", "R", "R1", "KO1", "PL")
SHUF_ARMS = ("Cp", "R")

AMBIGUITIES: dict[str, str] = {
    "C'": "production's player-state recency/role columns (PS.STAGE1_PROD_COLS) evaluated at any (player, date) by prod_state_rows, which equals build_features on played rows (parity gate) and the forward-path placeholder on DNP rows (truncation gate)",
    "freeze": "the harness needs `--sha` equal to the hash of the text above the marker AND a commit with subject `prereg(RECENT_ROLE): <sha>` in git log; before the commit it refuses, so a draft cannot run",
    "shuffle": "ONE permutation of the training labels per month block (GLOBAL across the training rows), shared by Cp and R; scored on the REAL labels of the block; the check fails if R_shuf - Cp_shuf <= -0.001 with CI upper < 0",
    "placebo": "past-only: each row takes the five R columns of ONE uniformly drawn strictly-earlier row of its player-season (NaN for a player-season's first row)",
    "slice playoffs": "game_id prefix 004",
    "bench/starter": "as-of starter10 >= 0.5 (production's exponentially weighted start share) is a starter",
    "p-value family": "a single primary test (R vs Cp) per season: m = 1, the BH step is the identity and is stated, not hidden",
}


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- freeze and holdout refusal
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def freeze_commit(sha: str, git: Callable[..., str] = _git) -> str | None:
    """Hash of the commit whose subject is ``prereg(RECENT_ROLE): <sha>`` reachable from HEAD, else None."""
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
    """Stage 3 gate: the pre-registered holdout row must exist in the log, say Devin confirmed, be committed and unmodified."""
    require_frozen(sha, doc or DOC, git)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            "no committed holdout row for RECENT_ROLE (PRE-REGISTERED, DEVIN CONFIRMED, sha8): Stage 3 refuses"
        )
    if git("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(
            "HOLDOUT_ACCESS_LOG.md has uncommitted changes: commit the row BEFORE the run"
        )
    if not git("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is not in any commit: Stage 3 refuses")
    return rows[0]


# --------------------------------------------------------------------------- arms
def arm_columns() -> dict[str, list[str]]:
    """C (WORKLOAD's comparator), C' = C + production's recent-minutes/role columns, R = C' + 5 recent-role columns, and the
    descriptive R1 (min_last only), KO1 (R without min_last), PL (C' + the past-only placebo of the five R columns)."""
    from research.eval import pplay_context_eval as PC

    c = [*PC.BASE_FEATURES, *PC.TERM_TAG["T-60"], *PC.TERM_REPORT]
    cp = [*c, *CP_ADD]
    return {
        "base": c,
        "Cp": cp,
        "R": [*cp, *R_COLS],
        "R1": [*cp, *R1_COLS],
        "KO1": [*cp, *KO1_COLS],
        "PL": [*cp, *[PL_PREFIX + x for x in R_COLS]],
    }


# --------------------------------------------------------------------------- walk-forward with a shared shuffle
def walk(
    frame: pl.DataFrame,
    arms: dict[str, list[str]],
    cfg: ContextResidualConfig,
    shuffle: bool = False,
    seed: int = SEED,
    ckpt: Path | None = None,
    min_train: int = MIN_TRAIN,
    n_estimators: int | None = None,
    permute: bool = True,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Month-block walk-forward for P(play): each block is fit on the rows dated strictly before it (2022 is warm-up).

    With ``shuffle`` ALL arms train on ONE permutation of the training labels per block (the same permutation for every
    arm, drawn from ``default_rng([seed, block])`` so a resumed run repeats it) and are scored on the REAL block labels.
    ``permute=False`` reproduces the old defect (labels not actually permuted) for the harness's own can-fail test.
    Returns (arrays, audit); the audit holds a hash of the labels each arm was fitted on, per block."""
    WE.assert_seasons(frame)
    test_all = frame.filter(pl.col("season").is_in(list(TEST_SEASONS)))
    parts: list[dict[str, np.ndarray]] = []
    hashes: dict[str, list[str]] = {a: [] for a in arms}
    blocks = month_blocks(test_all)
    for bi, (start, end) in enumerate(blocks):
        path = None if ckpt is None else ckpt / f"block_{start}.npz"
        if path is not None and path.exists():
            z = np.load(path, allow_pickle=True)
            parts.append({k: z[k] for k in z.files if not k.startswith("_hash_")})
            for a in arms:
                hashes[a].append(str(z[f"_hash_{a}"]))
            _log(f"block {bi + 1}/{len(blocks)} {start} from checkpoint")
            continue
        block = frame.filter((pl.col("game_date") >= start) & (pl.col("game_date") < end)).filter(
            pl.col("season").is_in(list(TEST_SEASONS))
        )
        train = frame.filter(pl.col("game_date") < start)
        if block.is_empty() or train.height < min_train:
            continue
        ytr = train["played"].to_numpy().astype(int)
        y_fit = ytr
        if shuffle and permute:
            y_fit = ytr[np.random.default_rng([seed, bi]).permutation(len(ytr))]
        h = hashlib.sha256(np.ascontiguousarray(y_fit).tobytes()).hexdigest()[:16]
        part: dict[str, np.ndarray] = {
            "gid": block["game_id"].to_numpy().astype(str),
            "pid": block["player_id"].to_numpy().astype(np.int64),
            "season": block["season"].to_numpy(),
            "y": block["played"].to_numpy(),
            "blk": np.full(block.height, bi),
        }
        params = _params(cfg, "binary")
        if n_estimators is not None:
            params["n_estimators"] = n_estimators
        for arm, names in arms.items():
            clf = lgb.LGBMClassifier(**params).fit(_matrix(train, names), y_fit)  # type: ignore[arg-type]
            part[f"p_{arm}"] = np.asarray(clf.predict_proba(_matrix(block, names)))[:, 1]
            hashes[arm].append(h)
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                path, **part, **{f"_hash_{a}": np.array(hashes[a][-1]) for a in arms}
            )
        parts.append(part)
        _log(f"block {bi + 1}/{len(blocks)} {start} n_test={block.height} train={train.height}")
    if not parts:
        raise ValueError("no month block had enough training rows")
    res = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return res, {"label_hashes": hashes, "n_blocks": len(parts)}


# --------------------------------------------------------------------------- scoring
def _ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    return WE._ll(p, y)


COMPARISONS: tuple[tuple[str, str], ...] = (
    ("R", "Cp"),  # PRIMARY
    ("R1", "Cp"),
    ("KO1", "Cp"),
    ("R", "PL"),
    ("PL", "Cp"),
    ("Cp", "base"),
    ("R", "base"),
)


def slice_masks_p(meta: pl.DataFrame) -> dict[str, np.ndarray]:
    """Declared slices on the P(play) rows. ``meta`` carries starter10, n_played_prior, is_b2b_second, game_id, wl_min_last."""
    f = lambda c: meta[c].to_numpy().astype(float)  # noqa: E731
    return {
        "all": np.ones(meta.height, dtype=bool),
        "starter": f("starter10") >= 0.5,
        "bench": f("starter10") < 0.5,
        "second_night": f("is_b2b_second") == 1,
        "cold5_9": (f("n_played_prior") >= 5) & (f("n_played_prior") < 10),
        "playoffs": meta["game_id"].str.slice(0, 3).to_numpy() == "004",
        "min_last_lt5": f("wl_min_last") < 5,
    }


def score_stage1(
    res: dict[str, np.ndarray], meta: pl.DataFrame, shuf: dict[str, np.ndarray] | None = None
) -> dict[str, Any]:
    """Rows: per season and arm the log loss, ECE and each declared comparison (paired, game-clustered); slices for R - Cp."""
    y = res["y"]
    out: dict[str, Any] = {"rows": [], "slices": []}
    masks = slice_masks_p(meta)
    for season in SEASONS:
        sm = res["season"] == season
        gid = res["gid"][sm]
        n_games = int(len(np.unique(gid)))
        for arm in ARMS:
            if f"p_{arm}" in res:
                p = res[f"p_{arm}"][sm]
                out["rows"].append(
                    {
                        "season": season,
                        "arm": arm,
                        "n_rows": int(sm.sum()),
                        "n_games": n_games,
                        "logloss": float(_ll(p, y[sm]).mean()),
                        "ece": float(WE.ece(p, y[sm])),
                    }
                )
        for a, b in COMPARISONS:
            if f"p_{a}" in res and f"p_{b}" in res:
                d = _ll(res[f"p_{a}"][sm], y[sm]) - _ll(res[f"p_{b}"][sm], y[sm])
                out["rows"].append({"season": season, "arm": f"{a}_minus_{b}", "n_rows": int(sm.sum()), "n_games": n_games, **WE.delta_stats(d, gid),
                                    "ece_delta": float(WE.ece(res[f"p_{a}"][sm], y[sm]) - WE.ece(res[f"p_{b}"][sm], y[sm]))})  # fmt: skip
        if shuf is not None:
            ss = shuf["season"] == season
            d = _ll(shuf["p_R"][ss], shuf["y"][ss]) - _ll(shuf["p_Cp"][ss], shuf["y"][ss])
            out["rows"].append(
                {
                    "season": season,
                    "arm": "R_shuf_minus_Cp_shuf",
                    "n_rows": int(ss.sum()),
                    "n_games": int(len(np.unique(shuf["gid"][ss]))),
                    **WE.delta_stats(d, shuf["gid"][ss]),
                }
            )
        if "p_R" in res and "p_Cp" in res:
            dd = _ll(res["p_R"], y) - _ll(res["p_Cp"], y)
            for name, mk in masks.items():
                m = mk & sm
                rec: dict[str, Any] = {"season": season, "slice": name, "n": int(m.sum())}
                if m.sum() >= MIN_SLICE_N or name == "all":
                    ds = WE.delta_stats(dd[m], res["gid"][m])
                    rec.update({k: ds[k] for k in ("delta", "ci_lo", "ci_hi")})
                out["slices"].append(rec)
    return out


def shuffle_check(row: dict[str, Any]) -> dict[str, Any]:
    """The shuffled-label check FAILS when a model trained on permuted labels still shows R beating Cp."""
    failed = bool(row["delta"] <= -SHUF_TOL and row["ci_hi"] < 0)
    return {"season": row["season"], "delta": row["delta"], "ci_hi": row["ci_hi"], "ok": not failed}


def decide_stage1(sc: dict[str, Any], gate_ok: bool, claims_ok: bool = True) -> dict[str, Any]:
    """The frozen pass rule on a ``score_stage1`` result. All of rules 1-4 must hold; the reasons are returned either way."""
    r = {(x["season"], x["arm"]): x for x in sc["rows"]}
    sel, rep = r[(2023, "R_minus_Cp")], r[(2024, "R_minus_Cp")]
    rule1 = bool(sel["delta"] <= FLOOR and sel["ci_hi"] < 0)
    rule2 = bool(rep["delta"] <= FLOOR and rep["ci_hi"] < 0 and rep["p"] <= 0.05)
    guards: dict[str, Any] = {}
    guards["ece"] = all(r[(s, "R_minus_Cp")]["ece_delta"] <= ECE_TOL for s in SEASONS)
    worse = [
        x
        for x in sc["slices"]
        if x.get("n", 0) >= MIN_SLICE_N and x["slice"] != "all" and x.get("delta", -1.0) > SLICE_TOL
    ]
    guards["slices"] = not worse
    guards["slices_worse"] = [(x["season"], x["slice"], x["n"], x["delta"]) for x in worse]
    attack: dict[str, Any] = {}
    attack["placebo"] = all(
        (s, "R_minus_PL") in r and r[(s, "R_minus_PL")]["delta"] <= PLACEBO_MARGIN for s in SEASONS
    )
    shuf = [
        shuffle_check(r[(s, "R_shuf_minus_Cp_shuf")])
        for s in SEASONS
        if (s, "R_shuf_minus_Cp_shuf") in r
    ]
    attack["shuffle"] = len(shuf) == len(SEASONS) and all(x["ok"] for x in shuf)
    attack["shuffle_detail"] = shuf
    attack["gate_and_plants"] = bool(gate_ok and claims_ok)
    carrier: dict[str, Any] = {}
    for s in SEASONS:
        full, one = r[(s, "R_minus_Cp")]["delta"], r.get((s, "R1_minus_Cp"), {}).get("delta")
        carrier[str(s)] = {
            "R": full,
            "R1": one,
            "R1_keeps_share": (one / full) if (one is not None and full < 0) else None,
        }
    named = (
        "min_last"
        if all((carrier[str(s)]["R1_keeps_share"] or 0.0) >= CARRY for s in SEASONS)
        else "recent-role set"
    )
    passed = bool(
        rule1
        and rule2
        and guards["ece"]
        and guards["slices"]
        and attack["placebo"]
        and attack["shuffle"]
        and attack["gate_and_plants"]
    )
    return {"rule1_2023": rule1, "rule2_2024": rule2, "guards": guards, "attack": attack, "carrier": carrier, "named_claim": named, "pass": passed,
            "mde": {str(s): r[(s, "R_minus_Cp")]["mde"] for s in SEASONS}}  # fmt: skip


# --------------------------------------------------------------------------- frames
def build_frame() -> tuple[pl.DataFrame, ContextResidualConfig, dict[str, Any]]:
    """Cell-P rows (PPLAY_CONTEXT / WORKLOAD universe) + workload LOAD columns + production state + past-only placebo."""
    from research.eval import pplay_context_eval as PC

    prep = PC.prepare(DB)
    frame = prep["frame"]
    WE.assert_seasons(frame)
    src = WE.load_sources()
    wl = WE.attach_workload(frame.select("game_id", "player_id", "team_id", "game_date"), src)
    fr = frame.with_columns(pl.col("player_id").cast(pl.Int64)).join(
        wl.drop("team_id", "game_date"), on=KEYS, how="left"
    )
    dup = sorted(set(CP_ADD) & set(fr.columns))
    if dup:
        raise ValueError(f"production-state columns already in the P frame: {dup}")
    box = prep["pgs"].join(prep["games"].select("game_id", "game_date", "season"), on="game_id")
    st = PS.prod_state_rows(
        box, fr.select("game_id", "player_id", "team_id", "season", "game_date")
    )
    fr = fr.join(st.select(*KEYS, *CP_ADD), on=KEYS, how="left")
    if fr.height != frame.height:
        raise ValueError("a join changed the P(play) rows")
    fr = PB.permute_past_only(fr, R_COLS, SEED, PL_PREFIX).sort(
        ["game_date", "game_id", "player_id"]
    )
    WE.assert_seasons(fr)
    return fr, PC._load_cfg(), {"src": src, "box": box, "prep": prep}


def meta_of(fr: pl.DataFrame) -> pl.DataFrame:
    return fr.select(
        *KEYS, "season", "starter10", "n_played_prior", "is_b2b_second", "wl_min_last", "min_real"
    )


# --------------------------------------------------------------------------- stages
def p_gate(
    fr: pl.DataFrame, cfg: ContextResidualConfig, aux: dict[str, Any]
) -> list[dict[str, Any]]:
    """Frozen gate. Fits only the control C (WORKLOAD's comparator); no Cp / R arm is fitted here."""
    from research.eval import workload_gates as G

    tests: list[dict[str, Any]] = []
    chk = WE._check
    a0 = G.gate_a0()
    tests.append(
        chk(
            "A0 reproduction from stored components",
            a0["max_abs_diff"],
            "<= 1e-6, n equal",
            a0["pass"],
        )
    )
    counts = {str(s): int((fr["season"] == s).sum()) for s in SEASONS}
    tests.append(
        chk(
            "Cell P rows (WORKLOAD gate: 33,093 / 33,127)",
            counts,
            f"within {SHARE_TOL:.1%} of the WORKLOAD run",
            all(
                abs(counts[str(s)] / t - 1) <= SHARE_TOL
                for s, t in zip(SEASONS, (33093, 33127), strict=True)
            ),
        )
    )
    # control C refit versus the stored WORKLOAD p_base
    res, _ = walk(fr, {"base": arm_columns()["base"]}, cfg, ckpt=CACHE / "gate_C_blocks")
    stored = dict(np.load(WE.CACHE / "P__scores.npz", allow_pickle=True))
    diffs = {}
    for s in SEASONS:
        m, ms = res["season"] == s, stored["season"] == s
        diffs[str(s)] = {
            "mine": float(_ll(res["p_base"][m], res["y"][m]).mean()),
            "stored": float(_ll(stored["p_base"][ms], stored["y"][ms]).mean()),
            "n_mine": int(m.sum()),
            "n_stored": int(ms.sum()),
        }
        diffs[str(s)]["abs_diff"] = abs(diffs[str(s)]["mine"] - diffs[str(s)]["stored"])
    tests.append(
        chk(
            "control C refit reproduces WORKLOAD's stored p_base (log loss)",
            diffs,
            f"abs diff <= {REPRO_TOL} per season",
            all(d["abs_diff"] <= REPRO_TOL for d in diffs.values()),
        )
    )
    # production state: parity on all played rows of the props frame, truncation truth on DNP rows
    from research.recent_role import gates as RG

    tests += RG.state_gates(aux, fr)
    # missingness of the added columns by realised-minutes bucket, DNP its own bucket
    cols = [*CP_ADD, *R_COLS, *[PL_PREFIX + c for c in R_COLS]]
    aud = WE.audit_nulls(
        fr.filter(pl.col("season").is_in(list(SEASONS))),
        cols,
        WE.minutes_bucket("min_real", dnp=True),
    )
    tests.append(
        chk(
            "missingness of added columns by realised-minutes bucket incl DNP (pp)",
            {k: aud[k] for k in ("max_gap_pp", "worst_col", "placebo_max_gap_pp")},
            f"<= {aud['threshold_pp']:.2f}",
            aud["ok"],
        )
    )
    nan_share = {
        str(s): float(
            np.isnan(fr.filter(pl.col("season") == s)[PL_PREFIX + "wl_min_last"].to_numpy()).mean()
        )
        for s in SEASONS
    }
    tests.append(
        chk(
            "past-only placebo: share of rows with no earlier row in the player-season (NaN)",
            nan_share,
            "reported (< 0.05 expected)",
            all(v < 0.05 for v in nan_share.values()),
        )
    )
    tests += WE.planted_tests(aux["src"], fr.select("game_id", "player_id", "season", "game_date"))
    masks = slice_masks_p(meta_of(fr))
    sizes = {
        s: {name: int((mk & (fr["season"].to_numpy() == s)).sum()) for name, mk in masks.items()}
        for s in SEASONS
    }
    tests.append(
        chk(
            "slice sizes (n rows)",
            sizes,
            f"every declared slice >= {MIN_SLICE_N} in both seasons",
            all(v >= MIN_SLICE_N for d in sizes.values() for v in d.values()),
        )
    )
    return tests


def run_gate(sha: str | None, draft: bool = False) -> dict[str, Any]:
    """``draft=True`` (the pre-freeze measurement, `gate --allow-draft`) skips the freeze check and writes gate_draft.json."""
    if not draft:
        require_frozen(sha)
    t0 = time.time()
    fr, cfg, aux = build_frame()
    tests = p_gate(fr, cfg, aux)
    gate = {
        "tests": tests,
        "gate_ok": all(t["ok"] for t in tests),
        "sha256": sha,
        "draft": draft,
        "seconds": time.time() - t0,
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


def run_arms(sha: str) -> None:
    require_frozen(sha)
    require_gate(sha)
    fr, cfg, _ = build_frame()
    CACHE.mkdir(parents=True, exist_ok=True)
    meta_of(fr).write_parquet(CACHE / "meta.parquet")
    arms = arm_columns()
    out = CACHE / "scores.npz"
    if not out.exists():
        res, audit = walk(fr, arms, cfg, ckpt=CACHE / "blocks")
        np.savez_compressed(out, **res)
        WE.write_json(CACHE / "audit.json", audit)
    out_s = CACHE / "shuffle.npz"
    if not out_s.exists():
        res_s, audit_s = walk(
            fr, {k: arms[k] for k in SHUF_ARMS}, cfg, shuffle=True, ckpt=CACHE / "blocks_shuf"
        )
        hs = audit_s["label_hashes"]
        if hs["Cp"] != hs["R"]:
            raise RuntimeError(
                "the shuffled Cp and R arms were not fitted on the same permuted labels"
            )
        np.savez_compressed(out_s, **res_s)
        WE.write_json(CACHE / "audit_shuf.json", audit_s)


def run_report(sha: str) -> dict[str, Any]:
    require_frozen(sha)
    gate = require_gate(sha)
    res = dict(np.load(CACHE / "scores.npz", allow_pickle=True))
    shuf = (
        dict(np.load(CACHE / "shuffle.npz", allow_pickle=True))
        if (CACHE / "shuffle.npz").exists()
        else None
    )
    meta = pl.read_parquet(CACHE / "meta.parquet")
    keys = pl.DataFrame({"game_id": res["gid"], "player_id": res["pid"].astype(np.int64)})
    mm = keys.join(meta.unique(KEYS), on=KEYS, how="left")
    if mm.height != len(res["gid"]):
        raise RuntimeError("meta join changed the row count")
    sc = score_stage1(res, mm, shuf)
    verdict = decide_stage1(sc, bool(gate["gate_ok"]))
    out = {
        "sha256": sha,
        "scores": sc,
        "verdict": verdict,
        "gate": gate["tests"],
        "ambiguities": AMBIGUITIES,
    }
    WE.write_json(OUT_DIR / "results.json", out)
    REPORT_MD.write_text(render(out))
    return out


def _f(x: Any, nd: int = 4) -> str:
    return "" if x is None else f"{x:.{nd}f}"


def render(res: dict[str, Any]) -> str:
    v = res["verdict"]
    lines = [f"# {ID} Stage 1 results", "", f"Rule sha256 {res['sha256'][:8]}. Season 2025 never loaded. Verdict: **{'PASS' if v['pass'] else 'NOT A PASS'}** (claim name if pass: {v['named_claim']}).", "",
             "| season | arm | n rows | n games | log loss / delta | CI | p | MDE | ECE delta |", "|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for r in res["scores"]["rows"]:
        if "logloss" in r:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {_f(r['logloss'])} | | | | {_f(r['ece'])} |"
            )
        else:
            lines.append(
                f"| {r['season']} | {r['arm']} | {r['n_rows']} | {r['n_games']} | {_f(r['delta'])} | [{_f(r['ci_lo'])}, {_f(r['ci_hi'])}] | {_f(r['p'])} | {_f(r['mde'])} | {_f(r.get('ece_delta'))} |"
            )
    lines += ["", "| season | slice | n | R - Cp | CI |", "|---|---|---|---|---|"]
    for s in res["scores"]["slices"]:
        ci = "" if "ci_lo" not in s else "[" + _f(s["ci_lo"]) + ", " + _f(s["ci_hi"]) + "]"
        lines.append(f"| {s['season']} | {s['slice']} | {s['n']} | {_f(s.get('delta'))} | {ci} |")
    lines += [
        "",
        "```json",
        json.dumps(
            {k: v[k] for k in ("rule1_2023", "rule2_2024", "guards", "attack", "carrier", "mde")},
            indent=1,
            default=float,
        ),
        "```",
    ]
    return "\n".join(lines) + "\n"


def stage3(sha: str) -> None:
    """Holdout touch. Refuses unless the pre-registered, Devin-confirmed row is committed. The 2025 loader is written AFTER the row
    exists and is plumbing only (it calls the same ``walk`` / ``score_stage1`` / ``decide_stage1`` with the report season 2025)."""
    require_holdout_row(sha)
    raise NotImplementedError(
        "the 2025 frame loader is added in the commit that follows the holdout row; the decision rule does not change"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "arms", "report", "stage2", "run", "stage3"])
    ap.add_argument("--sha", default=None)
    ap.add_argument(
        "--allow-draft",
        action="store_true",
        help="gate only: measure before the freeze (no sha needed)",
    )
    a = ap.parse_args(argv)
    if a.cmd == "gate" and a.allow_draft:
        return 0 if run_gate(None, draft=True)["gate_ok"] else 1
    require_frozen(a.sha)
    sha = str(a.sha)
    if a.cmd == "gate":
        return 0 if run_gate(sha)["gate_ok"] else 1
    if a.cmd == "report":
        run_report(sha)
        return 0
    if a.cmd == "stage3":
        stage3(sha)
        return 0
    if a.cmd == "stage2":
        with WE.heavy():
            run_stage2(sha)
        return 0
    with WE.heavy():
        if a.cmd == "arms":
            run_arms(sha)
            return 0
        if not run_gate(sha)["gate_ok"]:
            return 1
        run_arms(sha)
        out = run_report(sha)
        if out["verdict"]["pass"]:
            run_stage2(sha)
        else:
            _log("Stage 1 is not a pass: Stage 2 is not run (declared)")
    return 0


def run_stage2(sha: str) -> dict[str, Any]:
    """Stage 2a only if Stage 1 passed (re-read from results.json); 2b is recorded as not applicable."""
    require_frozen(sha)
    prev = json.loads((OUT_DIR / "results.json").read_text())
    if not prev["verdict"]["pass"]:
        raise RuntimeError("Stage 1 is not a pass: Stage 2 refuses")
    from research.eval import recent_role_props as RP

    res = RP.run_2a(sha)
    WE.write_json(OUT_DIR / "stage2.json", res)
    prev["stage2"] = res
    WE.write_json(OUT_DIR / "results.json", prev)
    return res


if __name__ == "__main__":
    raise SystemExit(main())
