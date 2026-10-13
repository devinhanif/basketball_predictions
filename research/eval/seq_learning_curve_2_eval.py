# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var,no-untyped-def"
"""SEQ_LEARNING_CURVE_2 CPU scorer (rule: docs/prereg/SEQ_LEARNING_CURVE_2.md; DRAFT until frozen; descriptive, no holdout touch).

    M="-m research.eval.seq_learning_curve_2_eval"
    .venv/bin/python -W ignore $M check                                   # the freeze + loader-review refusal only
    .venv/bin/python -W ignore $M run --run data/colab/runs/seq_learning_curve_2/<run_id>   # THE scoring run (heavy.lock; refuses without a committed APPROVED loader review)

Reuses the parent scorer (``research.eval.seq_learning_curve_eval``: shared game-cluster draws, the stored A0 / FULL world restricted to 2023-24 at load time, row identity checks,
normalizer round trip, slices, the epoch-cap test) and the SEQ_FULL_MIX blend / integer-grid machinery. It never scores a season other than 2023 (2023-24).

What is new (rule, "Metric, contrasts, CIs"):

* **Pooled 6-seed members.** The Colab job stores each seed's RAW 19 quantiles (``seed_q``); the pooled member of a seed multiset is their mean (clipped at 0 and made monotone, as the job's
  ensemble), divided by the target row's c_k, then the parent's 19 -> 199 extension and integer conversion. The blend weights are fit ONCE on the full 6-seed ensemble (a walk-forward
  softmax gate, ``S.learned_blend``) and held fixed for every resampled pool (rule, disclosed approximation); AVG = 0.5 SEQc + 0.5 FULL on the integer grid.
* **Two-way bootstrap.** 2,000 draws drawn once and shared by every contrast: ``game_id`` resampled with replacement (parent's draws, seed 0) AND, independently within each arm, its 6 seeds
  resampled with replacement. A resampled pool depends only on the multiset of seeds, so the 462 multisets (+ 6 leave-one-seed-out pools) per (arm, stat) are scored once, stored as per-game
  sums, and each draw is a look-up. Percentile intervals; decision intervals are 97.5% (Bonferroni over A_m and E), the 95% interval is also printed; a date-clustered variant is a robustness print.
* **Decision words** exactly as the rule states (``decide_A``, ``decide_E``); ties at an exact threshold follow the order the rule lists them in.

Gate first, curve last: run-time gates (config, exports, era, row identity) -> VOID without scoring; then ONLY U_4 and U_8 (pts, full ensemble) for the reproduction gate; U_4 outside its band
-> INCONCLUSIVE (construction) (the rule's declared fallback), U_8 outside its band or the FULL / A0 grids not reproducing -> VOID (gate); any unfinished arm -> INCONCLUSIVE (unfinished);
more than one third of the (window, seed) cold fits at the epoch cap -> INVALID (budget); only then is anything else scored.
"""

from __future__ import annotations

import argparse
import itertools
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from nba.eval.context_residual_eval import bh_adjust
from research.eval import ensemble_eval as E
from research.eval import seq_full_mix_eval as S
from research.eval import seq_learning_curve_2_rule as RULE
from research.eval import seq_learning_curve_eval as P1
from research.eval import workload_eval as WE
from research.features import seq_learning_curve_2_export as X2

ID = RULE.ID
ROOT = RULE.ROOT
OUT_DIR = ROOT / "reports/prereg_seq_learning_curve_2/results"
REPORT_MD = ROOT / "reports/prereg_seq_learning_curve_2_results.md"
CACHE = ROOT / "data/ops/seq_learning_curve_2_cache"
EXPORT_DIR = X2.OUT_DIR
STATS = P1.STATS
STAT_PRIMARY = "pts"
SCORE_LABEL = 2023
N_BOOT = 2000
BOOT_SEED = 0
N_SEEDS = 6
SEEDS = tuple(range(N_SEEDS))
ARM_NAMES = (
    "U_4",
    "UP_4",
    "U_8",
    "M_8",
    "U_10",
    "M_10",
    "U_6",
    "M_6",
    "U_9",
    "M_9",
)  # the job's UNITS order (a test pins it)
ARM_INDEX = {a: i for i, a in enumerate(ARM_NAMES)}
# thresholds, all from the rule text
A_FLOOR = -0.003  # A_m <= -0.003 with the 97.5% upper < 0 -> DATA; lower > -0.003 (with A <= -0.003, A_m >= -0.0015) -> STEPS
STEPS_POINT = -0.0015
E_FLOOR = -0.003
FLAT_BAND = 0.0015
HURT_FLOOR = 0.0015
DECISION_LEVEL = 0.975  # Bonferroni over m = 2 (A_m, E): two-sided 97.5% interval
REPRO_U4 = (-0.0201, 0.003)  # AVG - FULL on 2023-24 pts: T485 NORM(4), band
REPRO_U8 = (
    -0.0263,
    0.004,
)  # T485 NORM(8), looser band (position and S2 fills may legitimately move it)
EXACT_TOL = 1e-6
CAP_SHARE_MAX = 1.0 / 3.0
HIDDEN = 64
COLD_CFG = {"epochs": 25, "patience": 4, "lr": 1e-3, "batch": 1024}
WARM_CFG = {"epochs": 3, "lr": 3e-4, "batch": 1024}
N_TEST = 28197  # 2023-24 rows every arm scores (window-independent; the parent's count, checked against the export)
TWO_WAY_PTS = (
    "U_4",
    "U_8",
    "M_8",
    "M_10",
)  # arms whose pts decision contrasts need the full seed bootstrap
TWO_WAY_OTHER = ("U_4", "M_8", "M_10")  # reb / ast / fg3m: A_m and E only
MULTISETS = [
    tuple(sorted(m)) for m in itertools.combinations_with_replacement(range(N_SEEDS), N_SEEDS)
]  # 462 pools of 6 seeds
MS_INDEX = {m: i for i, m in enumerate(MULTISETS)}
COUNTS = np.array(
    [[m.count(s) for s in SEEDS] for m in MULTISETS]
    + [[0 if s == out else 1 for s in SEEDS] for out in SEEDS],  # + six leave-one-seed-out pools
    dtype=float,
)
FULL_ROW = MS_INDEX[SEEDS]
LOSO_ROWS = tuple(len(MULTISETS) + s for s in SEEDS)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- decision words (pure)
def decide_A(am: dict[str, float], a: dict[str, float]) -> str:
    """A_m: DATA / STEPS / MIXED. ``am`` has point and the 97.5% lo / hi; ``a`` is the unmatched A (point)."""
    if am["point"] <= A_FLOOR and am["hi"] < 0:
        return "DATA"
    if am["lo"] > A_FLOOR and a["point"] <= A_FLOOR and am["point"] >= STEPS_POINT:
        return "STEPS"
    return "MIXED"


def decide_E(e: dict[str, float], a_word: str) -> str:
    """E: RISING_10 / FLAT / HURTS / AMBIGUOUS, tested in the order the rule lists them."""
    if e["point"] <= E_FLOOR and e["hi"] < 0 and a_word in ("DATA", "MIXED"):
        return "RISING_10"
    if -FLAT_BAND <= e["point"] <= FLAT_BAND and e["lo"] > E_FLOOR:
        return "FLAT"
    if e["point"] >= HURT_FLOOR and e["lo"] > 0:
        return "HURTS"
    return "AMBIGUOUS"


def decide(am: dict[str, float], a: dict[str, float], e: dict[str, float]) -> dict[str, str]:
    w = decide_A(am, a)
    return {"A_m": w, "E": decide_E(e, w)}


def repro_gate(u4: float, u8: float, full_minus_a0: float, t467: float) -> dict[str, Any]:
    """Before the curve: U(4) and U(8) AVG - FULL near T485's NORM(4) / NORM(8); the stored A0 / FULL grids reproduce T467 to 1e-6."""
    checks = {
        "u4_avg_minus_full": {"value": u4, "target": REPRO_U4[0], "tol": REPRO_U4[1], "ok": abs(u4 - REPRO_U4[0]) <= REPRO_U4[1]},
        "u8_avg_minus_full": {"value": u8, "target": REPRO_U8[0], "tol": REPRO_U8[1], "ok": abs(u8 - REPRO_U8[0]) <= REPRO_U8[1]},
        "full_minus_a0_t467": {"value": full_minus_a0, "target": t467, "tol": EXACT_TOL, "ok": abs(full_minus_a0 - t467) <= EXACT_TOL},
    }  # fmt: skip
    return {"checks": checks, "ok": all(c["ok"] for c in checks.values())}


def gate_verdict(repro: dict[str, Any]) -> str | None:
    """The status a failed reproduction produces: grids not reproducing or U(8) outside its band -> VOID (gate); U(4) alone outside its band -> INCONCLUSIVE (construction)."""
    c = repro["checks"]
    if not c["full_minus_a0_t467"]["ok"] or not c["u8_avg_minus_full"]["ok"]:
        return "VOID (gate)"
    if not c["u4_avg_minus_full"]["ok"]:
        return "INCONCLUSIVE (construction)"
    return None


def budget_gate(infos: list[dict[str, Any]]) -> dict[str, Any]:
    """Epoch-cap diagnostic over the cold fits, each (window, seed) counted ONCE (U / M / UP of a window share them): INVALID (budget) when more than one third reached the cap
    (``fit_reached_cap``: ran the whole cap AND best epoch in the last PATIENCE epochs)."""
    seen: dict[tuple[int, int], dict[str, Any]] = {}
    for i in infos:
        for c in i["cold"]:
            seen.setdefault((int(i["window_n"]), int(c["seed"])), c)
    reached = sum(1 for c in seen.values() if P1.fit_reached_cap(c))
    share = reached / len(seen) if seen else float("nan")
    by_window: dict[str, int] = {}
    for (n, _s), c in seen.items():
        by_window[str(n)] = by_window.get(str(n), 0) + int(P1.fit_reached_cap(c))
    return {"cold_fits": len(seen), "reached_cap": int(reached), "share": share, "limit": CAP_SHARE_MAX, "ok": bool(seen and share <= CAP_SHARE_MAX), "reached_by_window": by_window}  # fmt: skip


# --------------------------------------------------------------------------- two-way draws
def seed_draw_rows(arm: str, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> np.ndarray:
    """Per draw, the table row of the arm's resampled seed multiset. Independent across arms (own stream), identical for every contrast that uses the arm."""
    rng = np.random.default_rng([seed, 20261013, ARM_INDEX[arm]])
    d = np.sort(rng.integers(0, N_SEEDS, size=(n_boot, N_SEEDS)), axis=1)
    return np.array([MS_INDEX[tuple(int(x) for x in r)] for r in d], dtype=np.int64)


class SeedTable:
    """Per-cluster sums of one arm's per-row CRPS for every seed pool (468 pools x G clusters) and a mask (``all`` or a slice). ``cnt``: rows per cluster in the mask."""

    def __init__(self, sums: np.ndarray, cnt: np.ndarray) -> None:
        self.sums, self.cnt = sums, cnt


def two_way_draws(
    boot: P1.SharedBoot,
    x: SeedTable,
    rows_x: np.ndarray,
    y: SeedTable | None,
    rows_y: np.ndarray | None,
    chunk: int = 250,
) -> np.ndarray:
    """Per draw the ratio-of-sums contrast mean(CRPS_x pool) - mean(CRPS_y pool) over the drawn clusters (NaN where the mask has no row in the draw)."""
    out = np.full(boot.n_boot, np.nan)
    for lo in range(0, boot.n_boot, chunk):
        hi = min(lo + chunk, boot.n_boot)
        idx = boot.idx[lo:hi]
        num = np.take_along_axis(x.sums[rows_x[lo:hi]], idx, axis=1).sum(axis=1)
        if y is not None:
            num = num - np.take_along_axis(y.sums[rows_y[lo:hi]], idx, axis=1).sum(axis=1)
        den = x.cnt[idx].sum(axis=1)
        ok = den > 0
        out[lo:hi][ok] = num[ok] / den[ok]
    return out


def summarize(draws: np.ndarray, point: float, n_rows: int, n_clusters: int) -> dict[str, Any]:
    """Point (full 6-seed ensemble on all rows), 95% and 97.5% percentile intervals, two-sided bootstrap p."""
    b = draws[~np.isnan(draws)]
    if not len(b):
        return {
            "point": point,
            "n": n_rows,
            "n_clusters": n_clusters,
            "lo95": np.nan,
            "hi95": np.nan,
            "lo": np.nan,
            "hi": np.nan,
            "p": np.nan,
        }
    a = (1.0 - DECISION_LEVEL) / 2.0
    p = 2.0 * min(float(np.mean(b >= 0.0)), float(np.mean(b <= 0.0)))
    return {
        "point": float(point), "n": int(n_rows), "n_clusters": int(n_clusters), "n_draws": int(len(b)),
        "lo95": float(np.quantile(b, 0.025)), "hi95": float(np.quantile(b, 0.975)),
        "lo": float(np.quantile(b, a)), "hi": float(np.quantile(b, 1.0 - a)),  # the decision interval: 97.5%
        "p": float(min(max(p, 1.0 / (len(b) + 1)), 1.0)),
    }  # fmt: skip


def contrast(
    boot: P1.SharedBoot,
    x: SeedTable,
    ax: str,
    y: SeedTable | None,
    ay: str | None,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    """C_x - C_y with the full two-way draws, plus the leave-one-seed-out spread (both arms drop the same seed)."""
    rx = seed_draw_rows(ax, n_boot)
    ry = seed_draw_rows(ay, n_boot) if ay else None
    d = two_way_draws(boot, x, rx, y, ry)

    def point(row: int) -> float:
        num = x.sums[row].sum() - (y.sums[row].sum() if y is not None else 0.0)
        tot = x.cnt.sum()
        return float(num / tot) if tot > 0 else float("nan")

    res = summarize(d, point(FULL_ROW), int(x.cnt.sum()), boot.g)
    loso = [point(r) for r in LOSO_ROWS]
    res["loso"] = {
        "values": loso,
        "min": float(min(loso)),
        "max": float(max(loso)),
        "sd": float(np.std(loso, ddof=1)),
    }
    return res


# --------------------------------------------------------------------------- one (arm, stat): seed pools -> integer-grid AVG CRPS
def oof_index(z: Any, base: dict[str, Any]) -> np.ndarray:
    """Index of every A0 row in an OOF file (the parent's join); refuses a season other than the select season or a missing row."""
    if int(z["season"].max()) != SCORE_LABEL or int(z["season"].min()) != SCORE_LABEL:
        raise RuntimeError("an OOF file holds a season other than the select season")
    src = pl.DataFrame(
        {
            "game_id": z["game_id"].astype(str),
            "player_id": z["player_id"].astype(np.int64),
            "_i": np.arange(len(z["game_id"])),
        }
    )
    j = pl.DataFrame({"game_id": base["game_id"].astype(str), "player_id": base["player_id"].astype(np.int64)}).join(src, on=["game_id", "player_id"], how="left")  # fmt: skip
    if j["_i"].null_count():
        raise RuntimeError(f"{j['_i'].null_count()} A0 rows missing from the OOF file")
    return j["_i"].to_numpy()


def pooled_grid(seed_q: np.ndarray, counts: np.ndarray, c_row: np.ndarray | None) -> np.ndarray:
    """Continuous 199-grid of a seed pool. ``seed_q`` (S, n, 19) RAW per-seed quantiles aligned to the A0 rows; ``counts`` (S,) multiplicities. Mean over the pool, clipped at 0 and made
    monotone (the job's ensemble), divided by the target row's c_k, then the parent's extension. The full pool equals the job's stored ensemble (checked at run time)."""
    q = np.tensordot(counts / counts.sum(), seed_q, axes=(0, 0))
    q = np.maximum.accumulate(np.clip(q, 0.0, None), axis=1)
    if c_row is not None:
        q = q / np.asarray(c_row, dtype=float)[:, None]
    return E.ext19(q)


def fixed_blend_avg(
    b: dict[str, Any], w: np.ndarray, reg: np.ndarray, grid199: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """SEQc and AVG integer grids of a pooled member with the blend weights ``w`` held fixed (``S.learned_blend_scored``'s combination line for line)."""
    cont = np.asarray(w[:, :1] * b["mix"] + w[:, 1:2] * grid199)
    sq = S.seqc_grid(b["a0_int"], cont, reg)
    return sq, S.mix_half(sq, b["full_int"])


def full_arm(
    b: dict[str, Any],
    world: dict[str, Any],
    stat: str,
    seed_q: np.ndarray,
    c_row: np.ndarray | None,
    verify: bool = False,
) -> dict[str, Any]:
    """The full 6-seed ensemble: the blend weights (fit once, walk-forward), per-row CRPS of AVG and SEQc, SEQc coverage. ``verify``: also check the fixed-weight AVG / SEQc equal
    ``S.build_arms`` bit for bit (done once per run; a second blend fit per arm is not needed)."""
    grid = pooled_grid(seed_q, np.ones(N_SEEDS), c_row)
    _, w = S.learned_blend(
        b["mix"], grid, b["y"], world["date_days"], b["season"], world["ctx"], stat, SCORE_LABEL
    )
    reg = S.regular_flag(b["game_id"])
    sq, avg = fixed_blend_avg(b, w, reg, grid)
    out = {
        "crps_avg": P1.E_crps(avg, b["y"]),
        "crps_seqc": P1.E_crps(sq, b["y"]),
        "cov_seqc": np.array(P1.coverage(sq, b["y"])),
        "w": w,
    }
    if verify:
        arms = S.build_arms(
            stat, b, world["date_days"], world["ctx"], {"E2": grid}, cap=SCORE_LABEL
        )
        out["fixed_blend_identity"] = bool(
            np.array_equal(avg, arms["AVG"]) and np.array_equal(sq, arms["SEQc"])
        )
    return out


def build_seed_tables(
    b: dict[str, Any],
    w: np.ndarray,
    seed_q: np.ndarray,
    c_row: np.ndarray | None,
    masks: dict[str, np.ndarray],
    clusters: dict[str, np.ndarray],
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, SeedTable]:
    """Score every seed pool (462 six-seed multisets + six leave-one-seed-out) once; keep per-cluster sums for each (cluster kind, mask) pair. Keys: ``<kind>:<mask>``."""
    reg = S.regular_flag(b["game_id"])
    inv = {k: np.unique(v, return_inverse=True)[1] for k, v in clusters.items()}
    ng = {k: int(v.max()) + 1 for k, v in inv.items()}
    keys = [(k, m) for k in clusters for m in masks if (k == "game" or m == "all")]
    sums = {f"{k}:{m}": np.zeros((len(COUNTS), ng[k])) for k, m in keys}
    cnts = {
        f"{k}:{m}": np.bincount(inv[k], weights=masks[m].astype(float), minlength=ng[k])
        for k, m in keys
    }
    for r in range(len(COUNTS)):
        grid = pooled_grid(seed_q, COUNTS[r], c_row)
        _, avg = fixed_blend_avg(b, w, reg, grid)
        crps = P1.E_crps(avg, b["y"])
        for k, m in keys:
            sums[f"{k}:{m}"][r] = np.bincount(inv[k], weights=crps * masks[m], minlength=ng[k])
        if progress and (r % 50 == 0 or r == len(COUNTS) - 1):
            progress(r + 1, len(COUNTS))
    return {name: SeedTable(sums[name], cnts[name]) for name in sums}


# --------------------------------------------------------------------------- slices
def debut_mask(player_id: np.ndarray, earlier_ids: set[int]) -> np.ndarray:
    """Players with no S1 / S2 export row (no box before 2022): the T486 survivorship check."""
    return ~np.isin(player_id, np.fromiter(earlier_ids, dtype=np.int64, count=len(earlier_ids)))


def slice_masks(
    b: dict[str, Any], date_days: np.ndarray, debut: np.ndarray | None
) -> dict[str, np.ndarray]:
    m = {"all": np.ones(len(b["y"]), dtype=bool), **P1.slice_masks(b, date_days)}
    if debut is not None:
        m["debut_2022_plus"] = debut
    return m


# --------------------------------------------------------------------------- the whole curve
def table_cache_path(arm: str, stat: str, tag: str) -> Path:
    return CACHE / f"tables__{arm}__{stat}__{tag}.npz"


def save_tables(path: Path, tabs: dict[str, SeedTable]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        **{f"s::{k}": v.sums for k, v in tabs.items()},
        **{f"c::{k}": v.cnt for k, v in tabs.items()},
    )


def load_tables(path: Path) -> dict[str, SeedTable]:
    d = np.load(path)
    return {f[3:]: SeedTable(d[f], d[f"c::{f[3:]}"]) for f in d.files if f.startswith("s::")}


def assemble(
    world: dict[str, Any],
    full: dict[tuple[str, str], dict[str, Any]],
    tables: dict[tuple[str, str], dict[str, SeedTable]],
    base_crps: dict[str, dict[str, np.ndarray]],
    masks: dict[str, np.ndarray],
    team: np.ndarray | None,
    date_clusters: bool = True,
) -> dict[str, Any]:
    """Everything the rule prints, from per-row CRPS vectors (``full``) and per-pool cluster sums (``tables``). ``full[(arm, stat)]`` carries crps_avg / crps_seqc / cov_seqc."""
    b = world["base"][STAT_PRIMARY]
    boot = P1.SharedBoot(b["game_id"])
    P = STAT_PRIMARY
    avg = {a: full[(a, P)]["crps_avg"] for a in ARM_NAMES if (a, P) in full}
    fa = base_crps[P]
    res: dict[str, Any] = {
        "n_rows": int(len(b["y"])),
        "n_games": boot.g,
        "n_boot": N_BOOT,
        "boot_seed": BOOT_SEED,
        "decision_level": DECISION_LEVEL,
    }
    res["C"] = {a: float(v.mean()) for a, v in avg.items()}
    res["avg_minus_full"] = {a: float((v - fa["FULL"]).mean()) for a, v in avg.items()}
    tp = {a: tables[(a, P)] for a in TWO_WAY_PTS if (a, P) in tables}
    # primary and second decision contrasts, then A and the step share, all on the same draws
    am = contrast(boot, tp["M_8"]["game:all"], "M_8", tp["U_4"]["game:all"], "U_4")
    e = contrast(boot, tp["M_10"]["game:all"], "M_10", tp["M_8"]["game:all"], "M_8")
    a_unm = contrast(boot, tp["U_8"]["game:all"], "U_8", tp["U_4"]["game:all"], "U_4")
    res["A_m"], res["E"], res["A"] = am, e, a_unm
    # step share A - A_m: draws of (U_8 - U_4) - (M_8 - U_4) = U_8 - M_8 on the SAME draws (U_4's pool cancels exactly)
    res["step_share_A_minus_Am"] = contrast(
        boot, tp["U_8"]["game:all"], "U_8", tp["M_8"]["game:all"], "M_8"
    )
    res["decision"] = decide(am, a_unm, e)
    # descriptive: game-clustered with the full ensemble (seed noise NOT included; disclosed), no family
    desc: dict[str, Any] = {}
    pairs = [
        ("U_6", "U_4"),
        ("U_8", "U_6"),
        ("U_9", "U_8"),
        ("U_10", "U_9"),
        ("M_6", "U_4"),
        ("M_8", "M_6"),
        ("M_9", "M_8"),
        ("M_10", "M_9"),
        ("UP_4", "U_4"),
    ]
    for x, y in pairs:
        if x in avg and y in avg:
            desc[f"{x}-{y}"] = boot.ci(avg[x] - avg[y])
    res["descriptive_game_only"] = desc
    # secondary: reb / ast / fg3m for A_m and E, BH over the six
    sec = []
    for st in STATS[1:]:
        if all((a, st) in tables for a in TWO_WAY_OTHER):
            t = {a: tables[(a, st)]["game:all"] for a in TWO_WAY_OTHER}
            sec.append(
                {"stat": st, "contrast": "A_m", **contrast(boot, t["M_8"], "M_8", t["U_4"], "U_4")}
            )
            sec.append(
                {"stat": st, "contrast": "E", **contrast(boot, t["M_10"], "M_10", t["M_8"], "M_8")}
            )
    if sec:
        for s, a in zip(sec, bh_adjust(np.array([s["p"] for s in sec])), strict=True):
            s["p_bh"] = float(a)
    res["secondary_family_S"] = {
        "m": 6,
        "rows": sec,
        "note": "descriptive; cannot change a verdict",
    }
    # slices: A_m and E per mask, two-way
    res["slices"] = {
        m: {
            "A_m": contrast(boot, tp["M_8"][f"game:{m}"], "M_8", tp["U_4"][f"game:{m}"], "U_4"),
            "E": contrast(boot, tp["M_10"][f"game:{m}"], "M_10", tp["M_8"][f"game:{m}"], "M_8"),
        }
        for m in masks if m != "all" and f"game:{m}" in tp["M_8"]
    }  # fmt: skip
    if team is not None:  # team drop-one: point estimates with each team's rows removed
        drops = {}
        for t_id in np.unique(team):
            keep = team != t_id
            drops[str(int(t_id))] = {
                "A_m": float((avg["M_8"][keep] - avg["U_4"][keep]).mean()),
                "E": float((avg["M_10"][keep] - avg["M_8"][keep]).mean()),
            }
        res["team_drop_one"] = {
            "n_teams": len(drops), "A_m_range": [min(v["A_m"] for v in drops.values()), max(v["A_m"] for v in drops.values())],
            "E_range": [min(v["E"] for v in drops.values()), max(v["E"] for v in drops.values())], "rows": drops,
        }  # fmt: skip
    if date_clusters:  # robustness print: dates instead of games
        dboot = P1.SharedBoot(world["date_days"])
        res["date_clustered"] = {
            "n_dates": dboot.g,
            "A_m": contrast(dboot, tp["M_8"]["date:all"], "M_8", tp["U_4"]["date:all"], "U_4"),
            "E": contrast(dboot, tp["M_10"]["date:all"], "M_10", tp["M_8"]["date:all"], "M_8"),
        }
    res["pts_coverage_seqc"] = {
        a: [float(x) for x in full[(a, P)]["cov_seqc"]] for a in ARM_NAMES if (a, P) in full
    }
    res["cov_taus"] = list(P1.COV_TAUS)
    return res


# --------------------------------------------------------------------------- run-time gates
def expected_seeds() -> list[int]:
    return list(SEEDS)


def check_run(
    cfg: dict[str, Any], infos: list[dict[str, Any]], sha: str, export_dir: Path, rv: dict[str, str]
) -> dict[str, Any]:
    """The run-time gates the rule names: frozen rule, reviewed commit and exports, cap season 2023, ONE configuration for every arm, 6 seeds, the same 2023-24 rows, no smoke run. Mismatch -> VOID."""
    bad: list[str] = []
    if cfg.get("rule_sha256") != sha:
        bad.append("run used a different rule sha")
    if cfg.get("cap_season") != 2023 or cfg.get("test_label") != 2023:
        bad.append("run cap / test season is not 2023")
    if cfg.get("review_commit") != rv.get("review_commit"):
        bad.append("the run was attested against a different review commit")
    info_f = export_dir / "export_info.json"
    got_v = json.loads(info_f.read_text()).get("history_data_version") if info_f.exists() else None
    if got_v != X2.HISTORY_DATA_VERSION:
        bad.append(
            f"the exports' history data_version {got_v} != the rule's {X2.HISTORY_DATA_VERSION}"
        )
    cexp = cfg.get("exports") or {}
    if set(cexp) != set(RULE.EXPORT_NAMES) or RULE.manifest_sha(cexp) != rv.get("exports_manifest"):
        bad.append("the run's exports are not the three exports named by the review's manifest")
    for name, esha in cexp.items():
        f = export_dir / name
        if not f.exists() or RULE.file_sha256(f) != esha:
            bad.append(f"export {name} differs from the one the run used")
    if cfg.get("hidden") != HIDDEN:
        bad.append(f"config hidden {cfg.get('hidden')} != {HIDDEN}")
    if cfg.get("smoke"):
        bad.append("a smoke run cannot be scored")
    if (cfg.get("cold") or {}) != COLD_CFG or (cfg.get("warm") or {}) != WARM_CFG:
        bad.append("config cold / warm settings differ from the frozen ones")
    if cfg.get("seeds") != expected_seeds():
        bad.append("config seeds differ from the frozen 0-5")
    names = [i.get("arm") for i in infos]
    if len(set(names)) != len(names) or not set(names) <= set(ARM_NAMES):
        bad.append("duplicate or unknown arm in the run's info files")
    for i in infos:
        arm = str(i.get("arm"))
        why = []
        if i.get("hidden") != HIDDEN:
            why.append(f"hidden {i.get('hidden')}")
        if i.get("cold_cfg") != COLD_CFG:
            why.append("cold_cfg")
        if i.get("warm_cfg") != WARM_CFG:
            why.append("warm_cfg")
        if (
            i.get("seeds") != expected_seeds()
            or [c.get("seed") for c in i.get("cold", [])] != expected_seeds()
        ):
            why.append("seeds (want 0-5)")
        if i.get("rule_sha256") != sha:
            why.append("rule sha")
        if i.get("n_test") != N_TEST:
            why.append(f"n_test {i.get('n_test')} != {N_TEST}")
        if i.get("smoke") is not False:
            why.append("smoke")
        if i.get("variant") != "norm":
            why.append("variant")
        if i.get("exports") != cexp:
            why.append("exports differ from the attested ones")
        if i.get("review_commit") != rv.get("review_commit"):
            why.append("review commit")
        if why:
            bad.append(f"{arm}: " + ", ".join(why))
    if len({i["test_keys_sha256"] for i in infos}) > 1:
        bad.append("the arms scored different 2023-24 row sets")
    if len({i["test_inputs_sha256"] for i in infos}) > 1:
        bad.append("the arms saw different 2023-24 test inputs")
    return {"problems": bad, "ok": not bad, "arms_done": sorted(i["arm"] for i in infos)}


DISCLOSURES = [
    "Blend weights are those fit on the full 6-seed ensemble and held fixed in every resampled pool (rule, disclosed approximation); the leave-one-seed-out spread of each contrast is printed beside its interval.",
    "Cold fits are shared by the U / M / UP arms of a window, so the independent per-arm seed resampling ignores that pairing: intervals are conservative (rule, disclosed).",
    "Descriptive contrasts other than A_m, E and A - A_m are game-clustered on the full 6-seed ensemble only (seed noise not included); slices use the two-way draws; team drop-one is point estimates.",
    "A failed U(4) reproduction is INCONCLUSIVE (construction) (the rule's Fallback, condition met); a failed U(8) band or a failed FULL / A0 grid reproduction is VOID (gate), as the rule states.",
    "The 'wall-clock of an interrupted arm' is not counted in the ceiling (the job sums runtime_s of completed arms only), as the parent disclosed.",
]


def verdict_only(out: dict[str, Any], verdict: str, why: str) -> None:
    out["curve"] = {"decision": {"verdict": verdict, "why": why}}


def score_run(
    out: dict[str, Any],
    infos: list[dict[str, Any]],
    runtime_ok: bool,
    world: dict[str, Any],
    meta: pd.DataFrame,
    load_oof: Callable[[str], tuple[Any, str]],
    team: np.ndarray | None = None,
    earlier_players: set[int] | None = None,
    date_clusters: bool = True,
) -> dict[str, Any]:
    chk, budget, unfinished = out["run_check"], out["budget"], out["unfinished"]
    out["world_rows"] = P1.check_world_rows(world, meta)
    gates = {
        "repro_ok": False,
        "runtime_ok": bool(runtime_ok and out["world_rows"]["ok"]),
        "budget_ok": bool(budget["ok"]),
    }
    out["gates"] = gates
    if not gates["runtime_ok"]:
        verdict_only(out, "VOID (gate)", "run-time gate failed; nothing was scored")
        return out
    base_crps: dict[str, dict[str, np.ndarray]] = {}

    def base(st: str) -> dict[str, np.ndarray]:
        if st not in base_crps:
            b = world["base"][st]
            base_crps[st] = {
                "A0": P1.E_crps(b["a0_int"], b["y"]),
                "FULL": P1.E_crps(b["full_int"], b["y"]),
            }
        return base_crps[st]

    full: dict[tuple[str, str], dict[str, Any]] = {}
    seed_cache: dict[tuple[str, str], tuple[np.ndarray, str]] = {}
    c_rows = {st: P1.c_lookup(meta, world["base"][st], st) for st in STATS}

    def seed_q(arm: str, st: str) -> tuple[np.ndarray, str]:
        if (arm, st) not in seed_cache:
            z, tag = load_oof(arm)
            if list(np.asarray(z["seeds"]).astype(int)) != expected_seeds():
                raise RuntimeError(f"{arm}: OOF seeds are not 0-5")
            b = world["base"][st]
            idx = oof_index(z, b)
            si = STATS.index(st)
            sq = np.asarray(z["seed_q"][:, idx, si, :], dtype=np.float64)
            stored = E.ext19(
                np.maximum.accumulate(
                    np.clip(np.asarray(z["q"][idx, si, :], dtype=float), 0.0, None), axis=1
                )
                / c_rows[st][:, None]
            )
            mine = pooled_grid(sq, np.ones(N_SEEDS), c_rows[st])
            if not np.allclose(mine, stored, atol=1e-4):
                raise RuntimeError(
                    f"{arm}/{st}: the pooled per-seed quantiles do not reproduce the job's stored ensemble"
                )
            seed_cache.clear()
            seed_cache[(arm, st)] = (sq, tag)
        return seed_cache[(arm, st)]

    def score_full(arm: str, st: str) -> None:
        if (arm, st) in full:
            return
        _log(f"full ensemble {arm} / {st}")
        sq, _tag = seed_q(arm, st)
        first = not full
        full[(arm, st)] = full_arm(world["base"][st], world, st, sq, c_rows[st], verify=first)
        if first and not full[(arm, st)]["fixed_blend_identity"]:
            raise RuntimeError(f"{arm}/{st}: the fixed-weight AVG differs from build_arms")

    if all(a in chk["arms_done"] for a in ("U_4", "U_8")):
        for a in ("U_4", "U_8"):
            score_full(a, STAT_PRIMARY)
        fa = base(STAT_PRIMARY)
        out["repro"] = repro_gate(
            float((full[("U_4", STAT_PRIMARY)]["crps_avg"] - fa["FULL"]).mean()),
            float((full[("U_8", STAT_PRIMARY)]["crps_avg"] - fa["FULL"]).mean()),
            float((fa["FULL"] - fa["A0"]).mean()),
            S.T467[SCORE_LABEL],
        )
        gates["repro_ok"] = bool(out["repro"]["ok"])
        bad = gate_verdict(out["repro"])
        if bad:
            verdict_only(out, bad, "reproduction gate failed; no other arm was scored")
            return out
    if unfinished:
        verdict_only(
            out, "INCONCLUSIVE (unfinished)", "arms not finished: " + ", ".join(unfinished)
        )
        return out
    if not gates["budget_ok"]:
        verdict_only(
            out, "INVALID (budget)", "more than one third of cold fits reached the epoch cap"
        )
        return out
    masks = slice_masks(
        world["base"][STAT_PRIMARY],
        world["date_days"],
        None
        if earlier_players is None
        else debut_mask(world["base"][STAT_PRIMARY]["player_id"], earlier_players),
    )
    tables: dict[tuple[str, str], dict[str, SeedTable]] = {}
    for st in STATS:  # every gate passed: now, and only now, the remaining arms and the seed pools
        for arm in ARM_NAMES:
            if st == STAT_PRIMARY or arm in TWO_WAY_OTHER:
                score_full(arm, st)
        arms2 = TWO_WAY_PTS if st == STAT_PRIMARY else TWO_WAY_OTHER
        for arm in arms2:
            sq, tag = seed_q(arm, st)
            f = table_cache_path(arm, st, tag)
            if f.exists():
                tables[(arm, st)] = load_tables(f)
                continue
            _log(f"seed pools {arm} / {st} ({len(COUNTS)} pools)")
            b = world["base"][st]
            m = masks if st == STAT_PRIMARY else {"all": masks["all"]}
            clusters = {
                "game": b["game_id"],
                **({"date": world["date_days"]} if st == STAT_PRIMARY else {}),
            }
            tabs = build_seed_tables(
                b,
                full[(arm, st)]["w"],
                sq,
                c_rows[st],
                m,
                clusters,
                lambda i, n, a=arm, s=st: _log(f"  {a}/{s} pool {i}/{n}"),
            )
            save_tables(f, tabs)
            tables[(arm, st)] = tabs
    out["curve"] = assemble(
        world, full, tables, {st: base(st) for st in STATS}, masks, team, date_clusters
    )
    out["curve"]["fixed_blend_identity"] = {
        f"{a}/{s}": v["fixed_blend_identity"]
        for (a, s), v in full.items()
        if "fixed_blend_identity" in v
    }
    out["curve"]["decision"] = {
        **out["curve"]["decision"],
        "verdict": f"A_m={out['curve']['decision']['A_m']}; E={out['curve']['decision']['E']}",
    }
    return out


# --------------------------------------------------------------------------- the run
def load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cfg = json.loads((run_dir / "config.json").read_text())
    infos = [json.loads(p.read_text()) for p in sorted(run_dir.glob("info_*.json"))]
    return cfg, infos


def run(run_dir: Path, export_dir: Path = EXPORT_DIR) -> dict[str, Any]:
    rv = RULE.require_review(
        export_dir=export_dir
    )  # hard stop: frozen rule + the latest committed review says APPROVED, covers this code and these exports
    _log(f"rule {rv['rule_sha256'][:8]}, review {rv['review_path']} @ {rv['review_commit'][:8]}")
    cfg, infos = load_run(run_dir)
    chk = check_run(cfg, infos, rv["rule_sha256"], export_dir, rv)
    z3 = np.load(export_dir / f"{X2.stem('S3')}.npz", allow_pickle=False)
    era = X2.era_runtime_gate(z3["ctx"], z3["season"], tuple(str(a) for a in z3["ctx_features"]))
    runtime_ok = bool(chk["ok"] and era["ok"])
    meta = P1.meta_from_exports(z3)
    team = np.asarray(z3["team_idx"])[np.asarray(z3["season"]).astype(int) == SCORE_LABEL]
    earlier = set()
    for seg in ("S1", "S2"):
        earlier |= {
            int(p)
            for p in np.load(export_dir / f"{X2.stem(seg)}.npz", allow_pickle=False)["player_id"]
        }
    budget = budget_gate(infos)
    unfinished = [a for a in ARM_NAMES if a not in chk["arms_done"]]
    out: dict[str, Any] = {
        "rule_sha256": rv["rule_sha256"],
        "review": rv,
        "run_dir": str(run_dir),
        "run_check": chk,
        "era_runtime_gate": era,
        "budget": budget,
        "unfinished": unfinished,
        "disclosures": DISCLOSURES,
    }
    _log(
        f"cap diagnostic: {budget['reached_cap']}/{budget['cold_fits']} (window, seed) cold fits at the cap, share {budget['share']:.3f}"
    )

    def load_oof(arm: str) -> tuple[Any, str]:
        f = run_dir / f"oof_{arm}.npz"
        return np.load(f, allow_pickle=False), RULE.file_sha256(f)[:8]

    if not runtime_ok:
        out["gates"] = {"repro_ok": False, "runtime_ok": False, "budget_ok": bool(budget["ok"])}
        verdict_only(out, "VOID (gate)", "run-time gate failed; nothing was scored")
    else:
        with WE.heavy():
            _log("loading the stored A0 / FULL world and restricting it to 2023-24")
            world = P1.load_world_2023()
            score_run(
                out, infos, runtime_ok, world, meta, load_oof, team=team, earlier_players=earlier
            )
    out["verdict"] = out["curve"]["decision"]["verdict"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=1, default=float))
    REPORT_MD.write_text(render(out))
    return out


def _f(x: float) -> str:
    return f"{x:+.4f}"


def _ci(c: dict[str, Any]) -> str:
    return f"{_f(c['point'])} [{_f(c['lo'])}, {_f(c['hi'])}] (97.5%); 95% [{_f(c['lo95'])}, {_f(c['hi95'])}]; n={c['n']}, clusters={c['n_clusters']}"


def render(out: dict[str, Any]) -> str:
    c = out.get("curve", {})
    lines = [
        f"# SEQ_LEARNING_CURVE_2 results (rule {out['rule_sha256'][:8]})",
        "",
        f"**Verdict: {out['verdict']}**",
        "",
    ]
    lines += [
        f"Gates: {json.dumps(out['gates'])}; unfinished arms: {out['unfinished'] or 'none'}.",
        "",
    ]
    b = out.get("budget") or {}
    if b:
        lines += [
            f"Epoch-cap diagnostic: {b['reached_cap']}/{b['cold_fits']} (window, seed) cold fits at the cap (share {b['share']:.3f}).",
            "",
        ]
    if "A_m" in c:
        lines += [
            f"n = {c['n_rows']} player-games, {c['n_games']} games (2023-24, pts), {c['n_boot']} two-way draws (game x seed).", "",
            f"A_m = C_M(8) - C_M(4): {_ci(c['A_m'])} -> **{c['decision']['A_m']}**",
            f"E = C_M(10) - C_M(8): {_ci(c['E'])} -> **{c['decision']['E']}**",
            f"A = C_U(8) - C_U(4): {_ci(c['A'])}",
            f"step share A - A_m = C_U(8) - C_M(8): {_ci(c['step_share_A_minus_Am'])}", "",
            "| arm | C (AVG) | AVG - FULL |", "|---|---|---|",
        ]  # fmt: skip
        lines += [f"| {a} | {c['C'][a]:.4f} | {_f(c['avg_minus_full'][a])} |" for a in c["C"]]
    elif "decision" in c:
        lines += [f"Decision: {json.dumps(c['decision'], default=float)}"]
    lines += ["", *[f"- {d}" for d in out.get("disclosures", [])]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("cmd", choices=("check", "run"))
    ap.add_argument(
        "--run", default=None, help="Colab artifact directory (oof_*.npz, info_*.json, config.json)"
    )
    a = ap.parse_args(argv)
    if a.cmd == "check":
        print(json.dumps(RULE.require_review(), indent=2))
        return 0
    if not a.run:
        ap.error("--run <dir> is required")
    res = run(Path(a.run))
    print(res["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
