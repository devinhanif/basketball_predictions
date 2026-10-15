# ruff: noqa: E501
"""MEDIAN_BIAS harness (rule: docs/prereg/MEDIAN_BIAS.md; DRAFT until frozen).

    .venv/bin/python -W ignore -m research.eval.median_bias_eval check    --data-root <main checkout>
    .venv/bin/python -W ignore -m research.eval.median_bias_eval rehearse --data-root <main checkout> --truth oracle|a0
    .venv/bin/python -W ignore -m research.eval.median_bias_eval run      --data-root <main checkout> --sha <64 hex>

* ``check``: label-free preflight. Recomputes every pinned count and input sha256 from the component files; the outcome arrays
  (``row_y``) are never loaded.
* ``rehearse``: the whole pipeline on SYNTHETIC outcomes drawn from the walk-forward calibration pools (truth ``oracle``: the pool of the
  row's own minutes cell, so a cell-wise map is right; truth ``a0``: the pooled pool, so production is right). ``row_y`` is never loaded.
* ``run``: the real outcomes. Refuses unless the rule is frozen (``prereg(MEDIAN_BIAS): <sha>``) AND the harness files equal the
  ``harness(MEDIAN_BIAS): <pin>`` commit (``median_bias_rule.require_ready``), and unless the preflight equalities hold. ``row_y`` is loaded
  only after the preflight passed.

Seasons 2023 and 2024 only. Candidate (CAND): production's continuous grid with the pooled standardized-residual quantiles of the walk-forward
calibration window replaced, for the row's minutes cell (``min10`` < 15, [15, 25), >= 25), by the quantiles of that cell's own calibration rows over
the WHOLE tau range (the existing LOWER_TAIL ``mondrian_minutes`` replaced only tau < 0.5). A cell with < MIN_BIN calibration rows keeps the pooled
quantiles. Nothing else changes: same centre ``c``, same scale ``s``, same integer map ceil(q - 0.5).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import median_bias_gate as gate
from research.eval import median_bias_rule as rule
from research.eval.median_bias_gate import (
    CELLS,
    H_CELL,
    MIN_BIN,
    SEASONS,
    STATS,
    TAUS,
    a0_grid,
    cell_of,
    crps_int,
    to_int,
)

MAX_SEASON = 2024
REPORT, CONSISTENCY = 2024, 2023
GAP_FLOOR_PP = 1.0  # P1a: pooled |gap| of the candidate in stratum H, percentage points (also the per-stat word)
STAT_CAP_PP = 1.5  # P1c: no stat may be left (or pushed) beyond this
REDUCTION_PP = 0.5  # P1b: practical floor of the pooled |gap| reduction
CRPS_HARM = 0.001  # P2: upper CI of CAND - A0 must not exceed this
CRPS_GAIN = -0.002  # informational word IMPROVES_CRPS (the project's -0.005 floor is not the claim)
CELL_WORSEN_PP = 0.5  # P5: no cell's |gap| may grow by more than this
DK_GAIN_PP = 1.0  # P4
PLACEBO_SHARE = 0.25  # G2
NULL_SD = 3.0  # G1
K_NULL = 50
K_DESCRIPTIVE = 10  # outcome-shuffle draws kept as a descriptive field only; never in a word
PINNED_VERSIONS = {"numpy": "2.5.3", "pandas": "3.0.6", "pyarrow": "25.0.1"}
TRUTHS = ("oracle", "a0", "mix50", "harm")
N_BOOT, SEED = 5000, 2026
ALPHA = 0.05
OUT_REAL = Path("reports/median_bias")
OUT_REHEARSAL = Path("reports/prereg_median_bias/rehearsal")
#: counts asserted before any outcome is read (reports/prereg_median_bias/gate.json, reproduced by the review)
PINNED: dict[str, Any] = {
    "rows": {"2023": 27_619, "2024": 27_701},
    "games": {"2023": 1_318, "2024": 1_320},
    "H_rows": {"2023": 12_489, "2024": 12_334},
    "cells": {"2023": {"lt15": 6_374, "15to25": 8_756, "ge25": 12_489}, "2024": {"lt15": 6_389, "15to25": 8_978, "ge25": 12_334}},
    "dk_rows": {"pts": {"2023": 14_354, "2024": 15_882}, "reb": {"2023": 15_052, "2024": 14_748}, "ast": {"2023": 9_650, "2024": 10_574}, "fg3m": {"2023": 14_191, "2024": 12_548}},
    "dk_legs": 106_999,
}  # fmt: skip


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------ candidate


def cand_grid(comp: dict[str, np.ndarray], cells: np.ndarray | None = None) -> np.ndarray:
    """CAND continuous 199-grid for every row. ``cells`` overrides the row cells (the PLACEBO permutes them within a block)."""
    n = len(comp["row_c"])
    rc = cell_of(comp["row_min10"]) if cells is None else cells
    out = np.empty((n, len(TAUS)))
    for b in np.unique(comp["row_blk"]):
        r = comp["row_blk"] == b
        cm = comp["cal_blk"] == b
        z = comp["cal_z"][cm]
        cc = cell_of(comp["cal_min10"][cm])
        pooled = np.quantile(z, TAUS)
        zq = np.empty((3, len(TAUS)))
        for k in range(3):
            zk = z[cc == k]
            zq[k] = np.quantile(zk, TAUS) if zk.size >= MIN_BIN else pooled
        q = comp["row_c"][r][:, None] + comp["row_s"][r][:, None] * zq[rc[r]]
        out[r] = np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)
    return out


def placebo_cells(comp: dict[str, np.ndarray], seed: int = SEED) -> np.ndarray:
    """Cell labels permuted among the test rows of each block (a fixed row order: the array order of the component file)."""
    rc = cell_of(comp["row_min10"]).copy()
    for b in np.unique(comp["row_blk"]):
        idx = np.flatnonzero(comp["row_blk"] == b)
        rc[idx] = np.random.default_rng(seed + int(b)).permutation(rc[idx])
    return rc


def leak_overlap(comp: dict[str, np.ndarray]) -> int:
    """Planted-future detector. A test row whose exact (c, s) pair also sits in the SAME block's calibration pool IS that row (the pool's rows
    are earlier games with different features), so the count must be 0. The unit test plants a test row in a pool and asserts a count >= 1."""
    bad = 0
    for b in np.unique(comp["row_blk"]):
        r = comp["row_blk"] == b
        cm = comp["cal_blk"] == b
        pairs_row = set(zip(comp["row_c"][r].tolist(), comp["row_s"][r].tolist(), strict=True))
        bad += sum(
            1
            for p in zip(comp["cal_c"][cm].tolist(), comp["cal_s"][cm].tolist(), strict=True)
            if p in pairs_row
        )
    return bad


# ------------------------------------------------------------------------------------ scores


def surv(qi: np.ndarray, k: np.ndarray) -> np.ndarray:
    """S(k) = P(Y >= k) = mean(qi >= k) per row (k = 0 gives 1)."""
    return np.asarray((qi >= k.astype(qi.dtype)[:, None]).sum(axis=1) / qi.shape[1])


def midmed(qi: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Per-row R - E at the arm's own integer median ``m = qi[:, 99]`` (the 100th sorted point of 199): R = 1{Y > m} + 0.5 * 1{Y = m},
    E = 0.5 * (S(m) + S(m + 1)). Zero in expectation when the arm is calibrated at its median."""
    m = qi[:, 99]
    r = (y > m) + 0.5 * (y == m)
    e = 0.5 * (surv(qi, m) + surv(qi, m + 1))
    return np.asarray(r - e)


def arm_rows(q: np.ndarray, y: np.ndarray) -> dict[str, np.ndarray]:
    qi = to_int(q)
    return {"mm": midmed(qi, y), "crps": crps_int(qi, y), "over_cont": (y > q[:, 99]).astype(float)}


class Boot:
    """Game-clustered bootstrap: ratio of resampled per-game sums, one weight matrix per season."""

    def __init__(self, gid: np.ndarray, seed: int, b: int = N_BOOT) -> None:
        u, self.code = np.unique(gid, return_inverse=True)
        self.G, self.B = len(u), b
        self.W = (
            np.random.default_rng(seed)
            .multinomial(self.G, np.full(self.G, 1.0 / self.G), size=b)
            .astype(float)
        )

    def sums(self, v: np.ndarray, m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.bincount(self.code[m], weights=v[m], minlength=self.G),
            np.bincount(self.code[m], minlength=self.G).astype(float),
        )

    def reps(self, v: np.ndarray, m: np.ndarray) -> np.ndarray:
        s, c = self.sums(v, m)
        den = self.W @ c
        return np.asarray((self.W @ s) / np.where(den > 0, den, np.nan))

    def point(self, v: np.ndarray, m: np.ndarray) -> float:
        return float(v[m].sum() / m.sum())

    @staticmethod
    def ci(point: float, rep: np.ndarray) -> dict[str, float]:
        lo, hi = np.nanpercentile(rep, [2.5, 97.5])
        return {"point": float(point), "lo": float(lo), "hi": float(hi)}


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


# ------------------------------------------------------------------------------------ labels


def synthetic_y(comp: dict[str, np.ndarray], truth: str, seed: int = 0) -> np.ndarray:
    """Synthetic integer outcomes: y = max(0, round(c + s * z*)), z* drawn per block from
    'oracle': the row's own cell pool (CAND exactly right); 'a0': the pooled pool (A0 exactly right);
    'mix50': the own cell pool with probability 0.5, else the pooled pool (the cell effect at half strength: CAND overshoots);
    'harm': the cell pool in H only, the pooled pool in the other two cells (CAND fixes H but mis-shifts the other cells: the review's `pooledH` world, PARTIAL with CRPS harm)."""
    if truth not in TRUTHS:
        raise ValueError(truth)
    rng = np.random.default_rng(seed)
    y = np.empty(len(comp["row_c"]))
    rc = cell_of(comp["row_min10"])
    for b in np.unique(comp["row_blk"]):
        r = np.flatnonzero(comp["row_blk"] == b)
        cm = comp["cal_blk"] == b
        z = comp["cal_z"][cm]
        cc = cell_of(comp["cal_min10"][cm])
        zs = np.empty(len(r))
        for k in range(3):
            sel = rc[r] == k
            n = int(sel.sum())
            cell_pool = z[cc == k] if (cc == k).sum() >= MIN_BIN else z
            if truth == "a0":
                zs[sel] = rng.choice(z, size=n)
            elif truth == "oracle":
                zs[sel] = rng.choice(cell_pool, size=n)
            elif truth == "mix50":
                use_cell = rng.random(n) < 0.5
                zs[sel] = np.where(use_cell, rng.choice(cell_pool, size=n), rng.choice(z, size=n))
            else:  # harm: the cell effect is real only in H; the other two cells keep the pooled pool
                zs[sel] = rng.choice(cell_pool if k == H_CELL else z, size=n)
        y[r] = np.maximum(np.round(comp["row_c"][r] + comp["row_s"][r] * zs), 0.0)
    return y


def versions() -> dict[str, str]:
    return {k: importlib.metadata.version(k) for k in PINNED_VERSIONS}


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=rule.ROOT
    ).stdout.strip()


# ------------------------------------------------------------------------------------ preflight


def preflight(root: Path, declared: dict[str, str]) -> dict[str, Any]:
    """Label-free: input sha256 equal the lines pinned in the rule; counts equal PINNED; no collision of the pinned rows with the pools."""
    shas = {
        rel: gate.sha256_file(root / rel)
        for rel in [*gate.COMP_FILES.values(), *gate.PAIRS_FILES.values()]
    }
    names = {f"COMPONENTS_SHA256_{s.upper()}": gate.COMP_FILES[s] for s in STATS} | {
        f"PAIRS_SHA256_{y}": gate.PAIRS_FILES[y] for y in SEASONS
    }
    bad = {
        k: (shas[v][:12], declared.get(k, "missing")[:12])
        for k, v in names.items()
        if declared.get(k) != shas[v]
    }
    if bad:
        raise RuntimeError(f"pinned input sha256 mismatch: {bad}")
    got_v = versions()
    if got_v != PINNED_VERSIONS:
        raise RuntimeError(f"library versions {got_v} != pinned {PINNED_VERSIONS}")
    uv_sha = gate.sha256_file(rule.ROOT / "uv.lock")
    if declared.get("UV_LOCK_SHA256") != uv_sha:
        raise RuntimeError(
            f"uv.lock sha256 {uv_sha[:12]} != pinned {declared.get('UV_LOCK_SHA256', 'missing')[:12]}"
        )
    keys = gate.dk_population(root)
    if len(keys) != PINNED["dk_legs"]:
        raise RuntimeError(f"DraftKings t60 legs {len(keys)} != pinned {PINNED['dk_legs']}")
    got: dict[str, Any] = {}
    for st in STATS:
        comp = gate.load_comp(root, st)
        c = gate.counts(comp)
        for s in SEASONS:
            want = {
                "rows": PINNED["rows"][str(s)],
                "games": PINNED["games"][str(s)],
                "H_rows": PINNED["H_rows"][str(s)],
                "cells": PINNED["cells"][str(s)],
            }
            have = {k: c[str(s)][k] for k in want}
            if have != want:
                raise RuntimeError(f"{st} {s}: counts {have} != pinned {want}")
        dk = gate.dk_mask(comp, st, keys)
        for s in SEASONS:
            n = int((dk & (comp["row_season"] == s)).sum())
            if n != PINNED["dk_rows"][st][str(s)]:
                raise RuntimeError(f"{st} {s}: DK rows {n} != pinned")
        if c["fallback_share"] > gate.MAX_FALLBACK_SHARE:
            raise RuntimeError(f"{st}: fallback share {c['fallback_share']}")
        got[st] = {
            "rows": {str(s): c[str(s)]["rows"] for s in SEASONS},
            "fallback_share": c["fallback_share"],
            "min_pool_cell_n": c["min_pool_cell_n"],
        }
    return {
        "inputs_equal_pinned": True,
        "counts_equal_pinned": True,
        "versions": got_v,
        "uv_lock_sha256": uv_sha,
        "git_head": git_head(),
        "per_stat": got,
    }


# ------------------------------------------------------------------------------------ evaluation


def report_subset(comp: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The REPORT-season rows and the calibration pools of their blocks (row order preserved), for the calibration-side null."""
    m = comp["row_season"] == REPORT
    blocks = np.unique(comp["row_blk"][m])
    cm = np.isin(comp["cal_blk"], blocks)
    keys = ("row_c", "row_s", "row_blk", "row_min10")
    out = {k: comp[k][m] for k in keys}
    out |= {k: comp[k][cm] for k in ("cal_z", "cal_blk", "cal_min10")}
    return out


def build_arms(
    root: Path, real: bool, truth: str, keys: dict[tuple[str, int, str], bool]
) -> dict[str, Any]:
    """Per stat: the per-row scores of A0 / CAND / PLACEBO. Real outcomes only when ``real``."""
    out: dict[str, Any] = {}
    for st in STATS:
        comp = gate.load_comp(root, st, labels=real)
        y = (
            comp["row_y"].astype(float)
            if real
            else synthetic_y(comp, truth, seed=7 + STATS.index(st))
        )
        q0, qc, qp = a0_grid(comp), cand_grid(comp), cand_grid(comp, placebo_cells(comp))
        rows = {"A0": arm_rows(q0, y), "CAND": arm_rows(qc, y), "PLACEBO": arm_rows(qp, y)}
        out[st] = {
            "rows": rows,
            "season": comp["row_season"].astype(int),
            "gid": comp["row_gid"].astype(str),
            "cell": cell_of(comp["row_min10"]),
            "dk": gate.dk_mask(comp, st, keys),
            "y": y,
            "qi": {"A0": to_int(q0).astype(np.int16), "CAND": to_int(qc).astype(np.int16)},
            "blk": comp["row_blk"],
            "leak": leak_overlap(comp),
            "null_comp": report_subset(comp),
        }
    return out


def gap_block(bt: Boot, v: dict[str, np.ndarray], m: np.ndarray) -> dict[str, Any]:
    """Gap (percentage points) of each arm on rows ``m``, with game-clustered CIs and the paired |gap| difference."""
    out: dict[str, Any] = {"n": int(m.sum())}
    rep = {a: 100.0 * bt.reps(v[a], m) for a in v}
    pt = {a: 100.0 * bt.point(v[a], m) for a in v}
    for a in v:
        out[a] = bt.ci(pt[a], rep[a])
    for a in v:
        if a == "A0":
            continue
        d = np.abs(rep[a]) - np.abs(rep["A0"])
        out[f"abs_{a}_minus_A0"] = bt.ci(abs(pt[a]) - abs(pt["A0"]), d)
        out[f"p_{a}_abs_not_smaller"] = float((1 + np.sum(d >= 0)) / (len(d) + 1))
    return out


def score(arms: dict[str, Any]) -> dict[str, Any]:
    res: dict[str, Any] = {"by_season": {}}
    for s in SEASONS:
        gid_s = np.concatenate([arms[st]["gid"][arms[st]["season"] == s] for st in STATS])
        bt = Boot(gid_s, SEED + s)
        per: dict[str, Any] = {"games": bt.G}
        ps: list[float] = []
        # pooled-over-stats vectors for this season
        pool: dict[str, list[np.ndarray]] = {
            k: []
            for k in (
                "mm_A0",
                "mm_CAND",
                "mm_PLACEBO",
                "cr_A0",
                "cr_CAND",
                "cr_PLACEBO",
                "oc_A0",
                "oc_CAND",
                "H",
                "dk",
            )
        }
        for st in STATS:
            a = arms[st]
            sm = a["season"] == s
            lo = np.flatnonzero(sm)
            H = a["cell"][lo] == H_CELL
            sub = {k: a["rows"][k]["mm"][lo] for k in ("A0", "CAND", "PLACEBO")}
            sub_cr = {k: a["rows"][k]["crps"][lo] for k in ("A0", "CAND", "PLACEBO")}
            bs = Boot(a["gid"][lo], SEED + s)
            cell_out = {
                c: gap_block(bs, {k: sub[k] for k in ("A0", "CAND")}, a["cell"][lo] == i)
                for i, c in enumerate(CELLS)
            }
            dcr = {k: sub_cr[k] - sub_cr["A0"] for k in ("CAND", "PLACEBO")}
            crps_out = {
                "A0_mean": float(sub_cr["A0"].mean()),
                **{
                    f"{k}_minus_A0": bs.ci(
                        float(dcr[k].mean()), bs.reps(dcr[k], np.ones(len(lo), bool))
                    )
                    for k in dcr
                },
            }
            dkm = a["dk"][lo]
            per[st] = {
                "H": gap_block(bs, sub, H),
                "cells": cell_out,
                "crps_int": crps_out,
                "dk": gap_block(bs, {k: sub[k] for k in ("A0", "CAND")}, dkm),
                "dk_over_cont": {
                    k: float(a["rows"][k]["over_cont"][lo][dkm].mean()) for k in ("A0", "CAND")
                },
            }
            ps.append(per[st]["H"]["p_CAND_abs_not_smaller"])
            pool["mm_A0"].append(sub["A0"])
            pool["mm_CAND"].append(sub["CAND"])
            pool["mm_PLACEBO"].append(sub["PLACEBO"])
            pool["cr_A0"].append(sub_cr["A0"])
            pool["cr_CAND"].append(sub_cr["CAND"])
            pool["cr_PLACEBO"].append(sub_cr["PLACEBO"])
            pool["oc_A0"].append(a["rows"]["A0"]["over_cont"][lo])
            pool["oc_CAND"].append(a["rows"]["CAND"]["over_cont"][lo])
            pool["H"].append(H)
            pool["dk"].append(dkm)
        adj = bh_adjust(np.array(ps))
        for st, p in zip(STATS, adj, strict=True):
            per[st]["H"]["p_bh"] = float(p)
        cat = {k: np.concatenate(v) for k, v in pool.items()}
        per["pooled"] = {
            "H": gap_block(bt, {k: cat[f"mm_{k}"] for k in ("A0", "CAND", "PLACEBO")}, cat["H"]),
            "dk": gap_block(bt, {k: cat[f"mm_{k}"] for k in ("A0", "CAND")}, cat["dk"]),
            "dk_over_cont": {k: float(cat[f"oc_{k}"][cat["dk"]].mean()) for k in ("A0", "CAND")},
        }
        dk = per["pooled"]["dk"]
        diff = (
            np.abs(bt.reps(cat["mm_A0"], cat["dk"])) - np.abs(bt.reps(cat["mm_CAND"], cat["dk"]))
        ) * 100.0
        pdiff = 100.0 * (
            abs(bt.point(cat["mm_A0"], cat["dk"])) - abs(bt.point(cat["mm_CAND"], cat["dk"]))
        )
        per["pooled"]["dk_gain_pp"] = bt.ci(pdiff, diff)
        per["pooled"]["dk_n"] = dk["n"]
        res["by_season"][str(s)] = per
    return res


def outcome_shuffle_descriptive(
    arms: dict[str, Any], season: int, k: int, seed: int = SEED
) -> dict[str, np.ndarray]:
    """DESCRIPTIVE ONLY (not in any word; centres near -1 pp under every truth, review item 1): y permuted among the rows of each (stat, block) of ``season`` (breaks the cell-outcome link, keeps every block marginal); the two
    arms' quantiles do not depend on y, so only the labels change. Returns per draw the pooled stratum-H gains and the mean CRPS change."""
    gain = np.empty(k)
    dcrps = np.empty(k)
    rng = np.random.default_rng(seed)
    for i in range(k):
        s0 = sc = cnt = 0.0
        d_cr = []
        for st in STATS:
            a = arms[st]
            lo = np.flatnonzero(a["season"] == season)
            y = a["y"][lo].copy()
            blk = a["blk"][lo]
            for b in np.unique(blk):
                ix = np.flatnonzero(blk == b)
                y[ix] = rng.permutation(y[ix])
            H = a["cell"][lo] == H_CELL
            mm0 = midmed(a["qi"]["A0"][lo], y)[H]
            mmc = midmed(a["qi"]["CAND"][lo], y)[H]
            s0 += float(mm0.sum())
            sc += float(mmc.sum())
            cnt += float(H.sum())
            d_cr.append(
                float((crps_int(a["qi"]["CAND"][lo], y) - crps_int(a["qi"]["A0"][lo], y)).mean())
            )
        gain[i] = 100.0 * (abs(s0 / cnt) - abs(sc / cnt))
        dcrps[i] = float(np.mean(d_cr))
    return {"gain_pp": gain, "dcrps": dcrps}


def cal_shuffle_null(
    arms: dict[str, Any], season: int, k: int, seed: int = SEED
) -> dict[str, np.ndarray]:
    """G1: permute the CALIBRATION rows' min10 within each block (breaks the cell structure of the pool, keeps every pool's z values and the
    outcomes), rebuild CAND, score it on the same ``season`` outcomes. Per draw i the generator is ``default_rng(seed + i)``; stats in STATS order.
    Returns the pooled stratum-H |gap| reduction (pp) and the mean-over-stats CRPS change CAND minus A0 per draw."""
    gain = np.empty(k)
    dcrps = np.empty(k)
    for i in range(k):
        rng = np.random.default_rng(seed + i)
        s0 = sc = cnt = 0.0
        d_cr = []
        for st in STATS:
            a = arms[st]
            comp = dict(a["null_comp"])
            cm10 = comp["cal_min10"].copy()
            for b in np.unique(comp["cal_blk"]):
                ix = np.flatnonzero(comp["cal_blk"] == b)
                cm10[ix] = rng.permutation(cm10[ix])
            comp["cal_min10"] = cm10
            lo = np.flatnonzero(a["season"] == season)
            qn = to_int(cand_grid(comp)).astype(np.int16)
            y = a["y"][lo]
            H = a["cell"][lo] == H_CELL
            s0 += float(midmed(a["qi"]["A0"][lo], y)[H].sum())
            sc += float(midmed(qn, y)[H].sum())
            cnt += float(H.sum())
            d_cr.append(float((crps_int(qn, y) - crps_int(a["qi"]["A0"][lo], y)).mean()))
        gain[i] = 100.0 * (abs(s0 / cnt) - abs(sc / cnt))
        dcrps[i] = float(np.mean(d_cr))
    return {"gain_pp": gain, "dcrps": dcrps}


def words(sc: dict[str, Any], guards: dict[str, Any]) -> dict[str, Any]:
    """Apply the frozen decision rule (docs/prereg/MEDIAN_BIAS.md, 'Decision rule')."""
    rep, con = sc["by_season"][str(REPORT)], sc["by_season"][str(CONSISTENCY)]
    ph, ch = rep["pooled"]["H"], con["pooled"]["H"]
    p1a = bool(abs(ph["CAND"]["point"]) <= GAP_FLOOR_PP)
    p1b = bool(
        -ph["abs_CAND_minus_A0"]["point"] >= REDUCTION_PP and ph["p_CAND_abs_not_smaller"] <= ALPHA
    )
    p1c = {st: bool(abs(rep[st]["H"]["CAND"]["point"]) <= STAT_CAP_PP) for st in STATS}
    p1 = p1a and p1b and all(p1c.values())
    per_stat = {
        st: bool(
            abs(rep[st]["H"]["CAND"]["point"]) <= GAP_FLOOR_PP and rep[st]["H"]["p_bh"] <= ALPHA
        )
        for st in STATS
    }
    p2 = {st: bool(rep[st]["crps_int"]["CAND_minus_A0"]["hi"] <= CRPS_HARM) for st in STATS}
    smaller = sum(
        abs(con[st]["H"]["CAND"]["point"]) < abs(con[st]["H"]["A0"]["point"]) for st in STATS
    )
    p3 = bool(ch["abs_CAND_minus_A0"]["hi"] < 0 and smaller >= 3)
    p5 = {
        f"{st}:{c}": bool(
            (abs(rep[st]["cells"][c]["CAND"]["point"]) - abs(rep[st]["cells"][c]["A0"]["point"]))
            <= CELL_WORSEN_PP
        )
        for st in STATS
        for c in CELLS
    }
    dkg = rep["pooled"]["dk_gain_pp"]
    p4 = bool(dkg["point"] >= DK_GAIN_PP and dkg["lo"] > 0)
    imp = all(
        rep[st]["crps_int"]["CAND_minus_A0"]["point"] <= CRPS_GAIN
        and rep[st]["crps_int"]["CAND_minus_A0"]["hi"] < 0
        for st in STATS
    )
    gd = all(guards[k]["pass"] for k in ("G1_null", "G2_placebo", "G3_leak", "G4_a0_repro"))
    crps_harm = not all(p2.values())
    if p1 and p3 and all(p5.values()):
        w = ("MEDIAN_FIXED" if all(p2.values()) else "FIXED_BUT_HARMS_CRPS") if gd else "GUARD_FAIL"
    elif p1b:
        w = "PARTIAL"  # the gap shrinks significantly but is not closed (or a stat is left off, or 2023 / a cell disagrees)
    else:
        w = "NOT_FIXED"
    under_correction = bool(
        ph["CAND"]["point"] * ph["A0"]["point"] > 0
    )  # pooled 2024 H gap keeps A0's sign
    return {
        "word": w, "CRPS_HARM": crps_harm, "FOLLOWUP_OPEN": bool(w == "PARTIAL" and not crps_harm and under_correction),
        "ON_LINES": bool(w == "MEDIAN_FIXED" and p4), "IMPROVES_CRPS": bool(w == "MEDIAN_FIXED" and imp),
        "P1a": p1a, "P1b": p1b, "P1c": p1c, "P1": p1, "per_stat_fixed_bh": per_stat, "P2": p2, "P3": p3, "P4": p4, "P5": p5, "guards_pass": gd,
    }  # fmt: skip


def evaluate(
    root: Path, real: bool, truth: str, k_null: int, repro: dict[str, Any] | None
) -> dict[str, Any]:
    keys = gate.dk_population(root)
    arms = build_arms(root, real, truth, keys)
    sc = score(arms)
    rep = sc["by_season"][str(REPORT)]
    nd = cal_shuffle_null(arms, REPORT, k_null)
    desc = outcome_shuffle_descriptive(arms, REPORT, K_DESCRIPTIVE)
    obs_gain = float(
        abs(rep["pooled"]["H"]["A0"]["point"]) - abs(rep["pooled"]["H"]["CAND"]["point"])
    )
    obs_dc = float(np.mean([rep[st]["crps_int"]["CAND_minus_A0"]["point"] for st in STATS]))
    g1: dict[str, Any] = {
        "obs_gain_pp": obs_gain, "null_gain_mean": float(nd["gain_pp"].mean()), "null_gain_sd": float(nd["gain_pp"].std(ddof=1)),
        "obs_dcrps": obs_dc, "null_dcrps_mean": float(nd["dcrps"].mean()), "null_dcrps_sd": float(nd["dcrps"].std(ddof=1)), "k": k_null,
        "descriptive_outcome_shuffle": {"gain_mean": float(desc["gain_pp"].mean()), "dcrps_mean": float(desc["dcrps"].mean()), "k": K_DESCRIPTIVE, "note": "not in any word"},
    }  # fmt: skip
    g1["pass"] = bool(
        obs_gain > g1["null_gain_mean"] + NULL_SD * g1["null_gain_sd"]
        and obs_dc < g1["null_dcrps_mean"] - NULL_SD * g1["null_dcrps_sd"]
    )
    red = abs(rep["pooled"]["H"]["A0"]["point"]) - abs(rep["pooled"]["H"]["CAND"]["point"])
    red_p = abs(rep["pooled"]["H"]["A0"]["point"]) - abs(rep["pooled"]["H"]["PLACEBO"]["point"])
    g2 = {
        "cand_reduction_pp": float(red),
        "placebo_reduction_pp": float(red_p),
        "pass": bool(red < 0.5 or red_p <= PLACEBO_SHARE * red),
    }
    coll = {st: int(arms[st]["leak"]) for st in STATS}
    g3 = {"collisions_by_stat": coll, "pass": all(v == 0 for v in coll.values())}
    g4 = repro if repro is not None else {"skipped": "synthetic outcomes", "pass": True}
    guards = {"G1_null": g1, "G2_placebo": g2, "G3_leak": g3, "G4_a0_repro": g4}
    return {
        "rule": "docs/prereg/MEDIAN_BIAS.md",
        "real_outcomes": real,
        "truth": None if real else truth,
        "score": sc,
        "guards": guards,
        "decision": words(sc, guards),
    }


def a0_repro(root: Path, pins: dict[str, Any]) -> dict[str, Any]:
    """Real-outcome A0 reproduction (G4): integer-support CRPS per stat and season to 1e-9 and the pooled DraftKings P(Y > A0 continuous median)."""
    keys = gate.dk_population(root)
    cur = gate.a0_pins(root, keys)
    bad: dict[str, Any] = {}
    for st in STATS:
        for s in SEASONS:
            d = abs(cur["crps_int"][st][str(s)] - pins["crps_int"][st][str(s)])
            if d > 1e-9:
                bad[f"{st}_{s}"] = d
    dp = abs(cur["dk_over_cont_median"]["pooled"]["p"] - pins["dk_over_cont_median"]["pooled"]["p"])
    if dp > 1e-9:
        bad["dk_over_cont_median_pooled"] = dp
    return {
        "crps_equal": not bad,
        "mismatches": bad,
        "dk_over_cont_median_pooled": cur["dk_over_cont_median"]["pooled"],
        "pass": not bad,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("check", "rehearse", "run"))
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--truth", choices=TRUTHS, default="oracle")
    ap.add_argument("--k", type=int, default=K_NULL)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        blobs = rule.require_ready(a.sha)
        _log(f"rule frozen and harness pinned: {rule.pin_of(blobs)[:12]}")
    pre = preflight(a.data_root, rule.declared())
    _log("preflight: input sha256 and every pinned count equal; no outcome array loaded")
    if a.cmd == "check":
        print(json.dumps(pre, indent=1, default=str))
        return 0
    t0 = time.time()
    if a.cmd == "rehearse":
        res = evaluate(a.data_root, False, a.truth, a.k, None)
        out = a.out or (rule.ROOT / OUT_REHEARSAL / a.truth)
    else:
        pins = rule.a0_pins_declared()
        repro = a0_repro(a.data_root, pins)
        if not repro["pass"]:
            raise RuntimeError(
                f"G4 failed ({repro['mismatches']}): refusing before any candidate is built or scored"
            )
        _log(
            "G4 holds: A0 reproduces its pinned CRPS and the DK median statistic; building the candidate"
        )
        res = evaluate(a.data_root, True, "", a.k, repro)
        out = a.out or (rule.ROOT / OUT_REAL)
    res["preflight"] = pre
    res["wall_seconds"] = round(time.time() - t0, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    _log(f"wrote {out / 'results.json'} word {res['decision']['word']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
