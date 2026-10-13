# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var,no-untyped-def"
"""SEQ_LEARNING_CURVE CPU scorer (rule: docs/prereg/SEQ_LEARNING_CURVE.md, frozen; descriptive, no holdout touch).

    M="-m research.eval.seq_learning_curve_eval"
    .venv/bin/python -W ignore $M check                                   # the freeze + loader-review refusal only
    .venv/bin/python -W ignore $M run --run data/colab/runs/seq_learning_curve/<run_id>   # THE scoring run (heavy.lock; refuses without a committed loader review)

What it reads: the Colab artifacts (oof_<arm>.npz, info_<arm>.json, config.json), the stored A0 / FULL integer grids (T467 identity) FILTERED TO SEASON 2023 THE MOMENT THEY ARE LOADED,
and the 2023-24 export meta (for the per-row normalizer factors). It never scores a season other than 2023 (2023-24); the exports are capped at 2023 and the OOF files are asserted to hold
nothing else. Season 2024 and 2025 rows of the stored caches are dropped before any computation.

Predictions of the NORM / NREP arms are in normalized units: the mean and the 19 quantiles are DIVIDED by the target row's c_k(d) BEFORE the integer-grid conversion, so every arm is scored on
the raw integer grid (rule, "Stat normalizer"). RAW arms are raw already (c = 1).

Gate first, curve last: settle runtime / unfinished / budget, score only RAW_1 and RAW_4 (pts) for the reproduction gate, and score NORM / NREP / RAW_8 only if every gate passes (``score_run``);
a voided, unfinished or invalid run leaves no NORM-arm CRPS on disk. Reads OOF files with allow_pickle=False.

Verdict ordering (the rule's text leaves the overlaps to the code; ambiguities are listed in the report): VOID (gate) > INCONCLUSIVE (unfinished) > INVALID (budget) > INCONCLUSIVE (construction)
> HURTS > RISING > NOT RISING (closed) > INCONCLUSIVE (replicate guard: criteria (i) and (ii) pass but (iii) fails, which the rule does not name).
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from nba.eval.context_residual_eval import bh_adjust, crps_from_quantiles
from nba.props.metrics import _MAX_BOOTSTRAP_CELLS
from research.eval import ensemble_eval as E
from research.eval import seq_full_mix_eval as S
from research.eval import seq_learning_curve_rule as RULE
from research.eval import workload_eval as WE
from research.features import seq_learning_curve_export as XP

ID = RULE.ID
ROOT = RULE.ROOT
OUT_DIR = ROOT / "reports/prereg_seq_learning_curve/results"
REPORT_MD = ROOT / "reports/prereg_seq_learning_curve_results.md"
CACHE = ROOT / "data/ops/seq_learning_curve_cache"
EXPORT_DIR = XP.OUT_DIR
STATS = ("pts", "reb", "ast", "fg3m")
STAT_PRIMARY = "pts"
SCORE_LABEL = 2023  # games.season of the select season (2023-24); the only season ever scored here
NORM_NS = tuple(range(1, 9))
RAW_NS = (1, 4, 8)
REP_NS = (4, 6, 8)
N_BOOT = 2000
BOOT_SEED = 0
A_FLOOR = -0.003  # A = C(8) - C(4)
B_FLOOR = -0.0015  # B = C(8) - C(6)
REP_A_FLOOR = -0.0015  # replicate guard: direction + half the floor
FALLBACK_FLOOR = (
    0.003  # NORM(4) - RAW(4) >= +0.003 with CI lower > 0 -> INCONCLUSIVE (construction)
)
HURT_FLOOR = 0.0015  # A >= +0.0015 with CI lower > 0 -> HURTS
REPRO = {1: -0.0083, 4: -0.0203}  # run #1 AVG - FULL on 2023-24 pts: W22 diagnostic and WALL
REPRO_TOL = 0.003
EXACT_TOL = 1e-6
CAP_SHARE_MAX = 1.0 / 3.0
EPOCH_CAP = 25  # frozen cold-fit epoch cap and patience (rule; the Colab job's COLD)
PATIENCE = 4
CAP_BEST_EPOCH = (
    EPOCH_CAP - PATIENCE
)  # a fit "reached the cap" iff it ran EPOCH_CAP epochs AND best_epoch >= 21: the cap ended it, not patience (review ruling 1)
HIDDEN = 64
COLD_CFG = {"epochs": EPOCH_CAP, "patience": PATIENCE, "lr": 1e-3, "batch": 1024}
WARM_CFG = {"epochs": 3, "lr": 3e-4, "batch": 1024}
SEEDS_MAIN = [0, 1, 2]
SEEDS_REP = [3, 4, 5]
N_TEST = 28197  # 2023-24 rows every arm scores (window-independent)
COV_TAUS = tuple(round(0.05 * i, 2) for i in range(1, 20))
CRPS_TAUS_N = 199
ARM_NORM = [f"NORM_{n}" for n in NORM_NS]
ARM_RAW = [f"RAW_{n}" for n in RAW_NS]
ARM_REP = [f"NREP_{n}" for n in REP_NS]
ALL_ARMS = [*ARM_RAW[:2], *ARM_NORM, ARM_RAW[2], *ARM_REP]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- the shared paired game-clustered bootstrap
class SharedBoot:
    """ONE set of ``n_boot`` resamples of ``game_id`` (seed 0), drawn at construction in the same chunking as ``nba.props.metrics._clustered_boot_means``; every contrast reuses the draws.

    ``ci(values, mask=None)``: percentile CI of the resampled ratio of sums (exactly ``E.clustered`` for the full sample); with ``mask`` the statistic is the mean over the slice rows of the drawn
    games (same draws), draws with no slice row are dropped."""

    def __init__(self, gid: np.ndarray, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> None:
        self.games, self.inv = np.unique(np.asarray(gid), return_inverse=True)
        self.g = len(self.games)
        rng = np.random.default_rng(seed)
        chunk = max(1, min(n_boot, _MAX_BOOTSTRAP_CELLS // max(self.g, 1)))
        parts = []
        start = 0
        while start < n_boot:
            b = min(chunk, n_boot - start)
            parts.append(rng.integers(0, self.g, size=(b, self.g)))
            start += b
        self.idx = np.concatenate(parts) if parts else np.zeros((0, self.g), dtype=np.int64)
        self.n_boot = n_boot

    def _sums(self, values: np.ndarray, mask: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
        w = np.ones(len(values)) if mask is None else mask.astype(float)
        gs = np.bincount(self.inv, weights=np.asarray(values, dtype=float) * w, minlength=self.g)
        gc = np.bincount(self.inv, weights=w, minlength=self.g)
        return gs, gc

    def means(self, values: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
        gs, gc = self._sums(values, mask)
        s, c = gs[self.idx].sum(axis=1), gc[self.idx].sum(axis=1)
        out = np.full(len(s), np.nan)
        ok = c > 0
        out[ok] = s[ok] / c[ok]
        return out

    def ci(self, values: np.ndarray, mask: np.ndarray | None = None) -> dict[str, float]:
        values = np.asarray(values, dtype=float)
        b = self.means(values, mask)
        b = b[~np.isnan(b)]
        sel = slice(None) if mask is None else mask
        point = float(values[sel].mean()) if (mask is None or mask.any()) else float("nan")
        if not len(b):
            return {
                "point": point,
                "lo": float("nan"),
                "hi": float("nan"),
                "p": float("nan"),
                "n": 0,
                "n_games": 0,
            }
        p = 2.0 * min(float(np.mean(b >= 0.0)), float(np.mean(b <= 0.0)))
        n_rows = len(values) if mask is None else int(mask.sum())
        n_games = self.g if mask is None else int(len(np.unique(self.inv[mask])))
        return {
            "point": point, "lo": float(np.quantile(b, 0.025)), "hi": float(np.quantile(b, 0.975)),
            "p": float(min(max(p, 1.0 / (self.n_boot + 1)), 1.0)), "n": n_rows, "n_games": n_games,
        }  # fmt: skip


# --------------------------------------------------------------------------- pure pieces
def denormalize(mean: np.ndarray, q: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Predicted mean (n, S) and quantiles (n, S, Q) in normalized units -> raw units: divide by the target row's c (n, S)."""
    c = np.asarray(c, dtype=float)
    return mean / c, q / c[:, :, None]


def coverage(grid: np.ndarray, y: np.ndarray) -> list[float]:
    """Empirical P(y <= q_tau) at tau = 0.05 .. 0.95 from the 199-point integer grid (tau_i = i / 200)."""
    out = []
    for t in COV_TAUS:
        k = int(round(t * 200)) - 1
        out.append(float(np.mean(y <= grid[:, k])))
    return out


def repro_gate(
    raw1_minus_full: float, raw4_minus_full: float, full_minus_a0_2023: float, t467: float
) -> dict[str, Any]:
    """Before the curve is read: RAW(1), RAW(4) AVG - FULL within 0.003 of run #1 (-0.0083, -0.0203) and the stored A0 / FULL grids reproducing T467 to 1e-6."""
    checks = {
        "raw1_avg_minus_full": {"value": raw1_minus_full, "target": REPRO[1], "tol": REPRO_TOL, "ok": abs(raw1_minus_full - REPRO[1]) <= REPRO_TOL},
        "raw4_avg_minus_full": {"value": raw4_minus_full, "target": REPRO[4], "tol": REPRO_TOL, "ok": abs(raw4_minus_full - REPRO[4]) <= REPRO_TOL},
        "full_minus_a0_t467": {"value": full_minus_a0_2023, "target": t467, "tol": EXACT_TOL, "ok": abs(full_minus_a0_2023 - t467) <= EXACT_TOL},
    }  # fmt: skip
    return {"checks": checks, "ok": all(c["ok"] for c in checks.values())}


def fit_reached_cap(c: dict[str, Any]) -> bool:
    """Computed here, never trusted from the job: ran the whole epoch cap AND the best epoch is in the last PATIENCE epochs (patience did not end it)."""
    return bool(c["epochs_run"] >= EPOCH_CAP and c["best_epoch"] >= CAP_BEST_EPOCH)


def budget_gate(infos: list[dict[str, Any]]) -> dict[str, Any]:
    """Epoch-cap diagnostic over every completed cold fit: INVALID (budget) when more than one third reached the cap (``fit_reached_cap``)."""
    fits = [(i["arm"], c) for i in infos for c in i["cold"]]
    reached = sum(1 for _, c in fits if fit_reached_cap(c))
    share = reached / len(fits) if fits else float("nan")
    by_arm = {
        i["arm"]: {
            "epochs_run": [c["epochs_run"] for c in i["cold"]],
            "best_epoch": [c["best_epoch"] for c in i["cold"]],
            "reached_cap": sum(fit_reached_cap(c) for c in i["cold"]),
        }
        for i in infos
    }
    norm_fits = [c for i in infos if i["arm"].startswith("NORM") for c in i["cold"]]
    all_cap = sorted(
        a
        for a in ("NORM_4", "NORM_6", "NORM_8")
        if a in by_arm and by_arm[a]["reached_cap"] == len(infos_by(infos, a)["cold"])
    )
    return {
        "cold_fits": len(fits), "reached_cap": int(reached), "share": share, "limit": CAP_SHARE_MAX, "ok": bool(fits and share <= CAP_SHARE_MAX),
        "share_norm_only": (sum(fit_reached_cap(c) for c in norm_fits) / len(norm_fits)) if norm_fits else float("nan"), "by_arm": by_arm,
        "arms_with_every_fit_at_cap": all_cap,  # disclosed beside the verdict (review ruling 1)
    }  # fmt: skip


def infos_by(infos: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    return next(i for i in infos if i["arm"] == arm)


def decide(r: dict[str, Any]) -> dict[str, Any]:
    """The rule's verdict from numbers. ``r``: A, B (dict point/lo/hi), A_rep, B_rep (points), NR4 (NORM(4) - RAW(4) dict), gates (repro_ok, runtime_ok, budget_ok), unfinished (list)."""
    A, B, nr4 = r["A"], r["B"], r["NR4"]
    crit = {
        "i": bool(A["point"] <= A_FLOOR and A["hi"] < 0),
        "ii": bool(B["point"] <= B_FLOOR and B["hi"] < 0),
        "iii": bool(r["A_rep"] <= REP_A_FLOOR and r["B_rep"] < 0),
        "iv_gates": bool(r["gates"]["repro_ok"] and r["gates"]["runtime_ok"]),
        "v_budget": bool(r["gates"]["budget_ok"]),
    }
    fallback = bool(nr4["point"] >= FALLBACK_FLOOR and nr4["lo"] > 0)
    hurts = bool(A["point"] >= HURT_FLOOR and A["lo"] > 0)
    if not crit["iv_gates"]:
        v = "VOID (gate)"
    elif r["unfinished"]:
        v = "INCONCLUSIVE (unfinished)"
    elif not crit["v_budget"]:
        v = "INVALID (budget)"
    elif fallback:
        v = "INCONCLUSIVE (construction)"
    elif hurts:
        v = "HURTS"
    elif crit["i"] and crit["ii"] and crit["iii"]:
        v = "RISING"
    elif not (crit["i"] and crit["ii"]):
        v = "NOT RISING (closed)"
    else:
        v = "INCONCLUSIVE (replicate guard)"
    return {"verdict": v, "criteria": crit, "fallback_construction": fallback, "hurts": hurts}


def step_table(crps: dict[int, np.ndarray], boot: SharedBoot) -> dict[str, Any]:
    """Every step C(N) - C(N-1) for N = 2..8 plus A = C(8) - C(4) and B = C(8) - C(6), all on the same draws."""
    out: dict[str, Any] = {"C": {str(n): float(v.mean()) for n, v in crps.items()}, "steps": {}}
    for n in range(2, 9):
        out["steps"][f"{n}-{n - 1}"] = boot.ci(crps[n] - crps[n - 1])
    out["A"] = boot.ci(crps[8] - crps[4])
    out["B"] = boot.ci(crps[8] - crps[6])
    return out


def slice_masks(b: dict[str, Any], date_days: np.ndarray) -> dict[str, np.ndarray]:
    """Fixed descriptive slices (rule): month blocks (regular season), postseason, starters vs bench, cold start, realised minutes < 5, rows without a report token."""
    reg = S.regular_flag(b["game_id"]) > 0
    month = pd.to_datetime(date_days.astype("datetime64[D]")).month.to_numpy()
    return {
        "Oct-Nov": reg & np.isin(month, (10, 11)),
        "Dec-Jan": reg & np.isin(month, (12, 1)),
        "Feb-Apr": reg & np.isin(month, (2, 3, 4)),
        "postseason": ~reg,
        "starters": b["starter10"] >= 0.5,
        "bench": b["starter10"] < 0.5,
        "cold_start": b["n_prior"] < 20,
        "minutes<5": b["minutes"] < 5,
        "no_report": b["has_report"] == 0,
    }


# --------------------------------------------------------------------------- the stored world, restricted to 2023-24 at load time
def restrict_world(w: dict[str, Any], season: int = SCORE_LABEL) -> dict[str, Any]:
    """Keep ONLY rows of ``season`` in every stat's base and drop everything else the loader brought (stored SEQ, other seasons). Row-aligned arrays are masked by length."""
    keep: dict[str, Any] = {"base": {}, "date_days": None}
    m = None
    for st in STATS:
        b = w["base"][st]
        n = len(b["y"])
        mm = b["season"] == season
        if m is not None and not np.array_equal(m, mm):
            raise RuntimeError(f"{st}: season mask differs from the first stat's")
        m = mm
        keep["base"][st] = {
            k: (v[mm] if isinstance(v, np.ndarray) and v.shape[:1] == (n,) else v)
            for k, v in b.items()
        }
    keep["date_days"] = w["date_days"][m]
    keep["ctx"] = S.blend_ctx(keep["base"][STAT_PRIMARY])
    if (
        int(keep["base"][STAT_PRIMARY]["season"].max()) != season
        or int(keep["base"][STAT_PRIMARY]["season"].min()) != season
    ):
        raise RuntimeError("the restricted world holds a season other than the select season")
    return keep


def load_world_2023() -> dict[str, Any]:
    """A0 / FULL for 2023-24 from the stored caches (run #1's real-tip meta supplies the blend context); the other seasons are dropped before use."""
    S.configure_rt(None, None)
    w = S.stored_world_base()
    return restrict_world(w)


def check_world_rows(world: dict[str, Any], meta: pd.DataFrame) -> dict[str, Any]:
    """Row-set identity between the A0 rows and the 2023-24 export meta (the rows the Colab arms were scored on)."""
    b = world["base"][STAT_PRIMARY]
    a = pl.DataFrame(
        {"game_id": b["game_id"].astype(str), "player_id": b["player_id"].astype(np.int64)}
    )
    m = pl.DataFrame(
        {
            "game_id": meta["game_id"].astype(str).to_numpy(),
            "player_id": meta["player_id"].astype(np.int64).to_numpy(),
        }
    )
    j = a.join(m.with_columns(pl.lit(1).alias("_in")), on=["game_id", "player_id"], how="left")
    missing = int(j["_in"].null_count())
    return {
        "world_rows": a.height,
        "export_rows": m.height,
        "world_rows_missing_from_export": missing,
        "ok": bool(missing == 0),
    }


def c_lookup(meta: pd.DataFrame, base: dict[str, Any], stat: str) -> np.ndarray:
    """norm_c of the stat per A0 row (join on game_id, player_id)."""
    j = pl.DataFrame({"game_id": base["game_id"].astype(str), "player_id": base["player_id"].astype(np.int64)}).join(
        pl.DataFrame({"game_id": meta["game_id"].astype(str).to_numpy(), "player_id": meta["player_id"].astype(np.int64).to_numpy(), "c": meta[f"norm_c_{stat}"].to_numpy()}),
        on=["game_id", "player_id"], how="left",
    )  # fmt: skip
    if j["c"].null_count():
        raise RuntimeError(f"{stat}: A0 rows missing from the 2023-24 export meta")
    return j["c"].to_numpy()


def seq_grid_from_oof(
    z: Any, base: dict[str, Any], si: int, c_by_row: np.ndarray | None
) -> np.ndarray:
    """Continuous 199-grid of one stat of an OOF file, aligned to the A0 rows. ``c_by_row``: the factor per A0 row (None for RAW arms); the 19 quantiles are DIVIDED by it
    BEFORE the 19 -> 199 extension and the integer conversion downstream."""
    gid, pid = z["game_id"].astype(str), z["player_id"].astype(np.int64)
    if int(z["season"].max()) != SCORE_LABEL or int(z["season"].min()) != SCORE_LABEL:
        raise RuntimeError("an OOF file holds a season other than the select season")
    src = pl.DataFrame({"game_id": gid, "player_id": pid, "_i": np.arange(len(gid))})
    j = pl.DataFrame(
        {"game_id": base["game_id"].astype(str), "player_id": base["player_id"].astype(np.int64)}
    ).join(src, on=["game_id", "player_id"], how="left")
    if j["_i"].null_count():
        raise RuntimeError(f"{j['_i'].null_count()} A0 rows missing from the OOF file")
    q = z["q"][j["_i"].to_numpy(), si, :].astype(float)
    if c_by_row is not None:
        q = q / np.asarray(c_by_row, dtype=float)[:, None]
    return E.ext19(q)


# --------------------------------------------------------------------------- scoring one (arm, stat) with a resumable cache
def score_arm_stat(
    arm: str, stat: str, world: dict[str, Any], z: Any, meta: pd.DataFrame | None, tag: str
) -> dict[str, np.ndarray]:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{arm}__{stat}__{tag}.npz"
    if f.exists():
        d = np.load(f)
        return {k: d[k] for k in d.files}
    b = world["base"][stat]
    si = STATS.index(stat)
    c = c_lookup(meta, b, stat) if (meta is not None and not arm.startswith("RAW")) else None
    grid = seq_grid_from_oof(z, b, si, c)
    arms = S.build_arms(stat, b, world["date_days"], world["ctx"], {"E2": grid}, cap=SCORE_LABEL)
    out = {
        "crps_avg": E_crps(arms["AVG"], b["y"]),
        "crps_seqc": E_crps(arms["SEQc"], b["y"]),
        "cov_seqc": np.array(coverage(arms["SEQc"], b["y"])),
    }
    np.savez(f, **out)
    return out


def E_crps(grid: np.ndarray, y: np.ndarray) -> np.ndarray:
    return crps_from_quantiles(grid.astype(float), y)


# --------------------------------------------------------------------------- the whole curve from per-row CRPS vectors
def assemble(
    base_crps: dict[str, dict[str, np.ndarray]],
    arm_crps: dict[tuple[str, str], dict[str, np.ndarray]],
    world: dict[str, Any],
    gates: dict[str, Any],
    unfinished: list[str],
) -> dict[str, Any]:
    """``base_crps[stat] = {A0, FULL}`` per-row CRPS; ``arm_crps[(arm, stat)] = {crps_avg, crps_seqc, cov_seqc}``."""
    b = world["base"][STAT_PRIMARY]
    boot = SharedBoot(b["game_id"])
    P = STAT_PRIMARY
    avg = {a: arm_crps[(a, P)]["crps_avg"] for a in ALL_ARMS if (a, P) in arm_crps}
    res: dict[str, Any] = {
        "n_rows": int(len(b["y"])),
        "n_games": boot.g,
        "n_boot": N_BOOT,
        "boot_seed": BOOT_SEED,
    }
    fa = base_crps[P]
    res["pts_C_A0_FULL"] = {
        "A0": float(fa["A0"].mean()),
        "FULL": float(fa["FULL"].mean()),
        "FULL_minus_A0": float((fa["FULL"] - fa["A0"]).mean()),
    }
    res["repro"] = repro_gate(
        float((avg["RAW_1"] - fa["FULL"]).mean()),
        float((avg["RAW_4"] - fa["FULL"]).mean()),
        float((fa["FULL"] - fa["A0"]).mean()),
        S.T467[SCORE_LABEL],
    )
    if all(f"NORM_{n}" in avg for n in NORM_NS):
        res["norm"] = step_table({n: avg[f"NORM_{n}"] for n in NORM_NS}, boot)
        res["norm"]["AVG_minus_FULL"] = {
            str(n): boot.ci(avg[f"NORM_{n}"] - fa["FULL"]) for n in NORM_NS
        }
    if all(f"RAW_{n}" in avg for n in RAW_NS):
        res["raw"] = {
            "C": {str(n): float(avg[f"RAW_{n}"].mean()) for n in RAW_NS},
            "A_raw": boot.ci(avg["RAW_8"] - avg["RAW_4"]),
            "AVG_minus_FULL": {str(n): boot.ci(avg[f"RAW_{n}"] - fa["FULL"]) for n in RAW_NS},
        }
        if "norm" in res:
            res["NR4"] = boot.ci(avg["NORM_4"] - avg["RAW_4"])
            res["NR8"] = boot.ci(avg["NORM_8"] - avg["RAW_8"])
            res["N8_minus_N4"] = boot.ci(avg["NORM_8"] - avg["NORM_4"])
            res["norm_vs_raw_A"] = boot.ci(
                (avg["NORM_8"] - avg["NORM_4"]) - (avg["RAW_8"] - avg["RAW_4"])
            )
    if all(f"NREP_{n}" in avg for n in REP_NS):
        rep = {n: avg[f"NREP_{n}"] for n in REP_NS}
        res["replicates"] = {
            "C": {str(n): float(v.mean()) for n, v in rep.items()},
            "A_rep": boot.ci(rep[8] - rep[4]), "B_rep": boot.ci(rep[8] - rep[6]),
            "main_minus_rep": {str(n): float((avg[f"NORM_{n}"] - rep[n]).mean()) for n in REP_NS},
        }  # fmt: skip
        d = np.array(list(res["replicates"]["main_minus_rep"].values()))
        # NOT the seed sd: the ddof=1 sd of the three main-minus-replicate differences says how the difference varies across N (review ruling 11)
        res["replicates"]["main_minus_rep_sd_across_N"] = (
            float(d.std(ddof=1)) if len(d) > 1 else float("nan")
        )
        # the seed sd of ONE 3-seed ensemble's C(N), assuming equal means: sqrt(mean(d^2) / 2); rests on 3 values (95% range about 0.6x-3.7x)
        res["replicates"]["seed_sd_single_ensemble_estimate"] = float(np.sqrt(np.mean(d**2) / 2.0))
    # SEQc(N) - A0 curves, four stats; secondary family S (A, B for reb, ast, fg3m on the AVG - FULL scale)
    res["seqc_minus_a0"] = {
        st: {str(n): boot.ci(arm_crps[(f"NORM_{n}", st)]["crps_seqc"] - base_crps[st]["A0"]) for n in NORM_NS}
        for st in STATS if all((f"NORM_{n}", st) in arm_crps for n in NORM_NS)
    }  # fmt: skip
    sec = []
    for st in STATS[1:]:
        if all((f"NORM_{n}", st) in arm_crps for n in (4, 6, 8)):
            av = {n: arm_crps[(f"NORM_{n}", st)]["crps_avg"] for n in (4, 6, 8)}
            sec.append({"stat": st, "contrast": "A", **boot.ci(av[8] - av[4])})
            sec.append({"stat": st, "contrast": "B", **boot.ci(av[8] - av[6])})
    if sec:
        adj = bh_adjust(np.array([s["p"] for s in sec]))
        for s, a in zip(sec, adj, strict=True):
            s["p_bh"] = float(a)
    res["secondary_family_S"] = {
        "m": 6,
        "rows": sec,
        "note": "descriptive; cannot rescue or change a verdict",
    }
    # slices (descriptive): A and B in each fixed slice, same draws
    if "norm" in res:
        sm = slice_masks(b, world["date_days"])
        res["slices"] = {
            k: {
                "A": boot.ci(avg["NORM_8"] - avg["NORM_4"], m),
                "B": boot.ci(avg["NORM_8"] - avg["NORM_6"], m),
            }
            for k, m in sm.items()
        }
    # calibration (descriptive): pts coverage of SEQc(N) at q05..q95
    res["pts_coverage_seqc"] = {
        a: [float(x) for x in arm_crps[(a, P)]["cov_seqc"]] for a in ALL_ARMS if (a, P) in arm_crps
    }
    res["cov_taus"] = list(COV_TAUS)
    if "norm" in res and "NR4" in res and "replicates" in res:
        res["decision"] = decide(
            {
                "A": res["norm"]["A"], "B": res["norm"]["B"], "A_rep": res["replicates"]["A_rep"]["point"], "B_rep": res["replicates"]["B_rep"]["point"], "NR4": res["NR4"],
                "gates": gates, "unfinished": unfinished,
            }
        )  # fmt: skip
    else:
        res["decision"] = {
            "verdict": "INCONCLUSIVE (unfinished)",
            "note": "curve arms missing: " + ", ".join(unfinished),
        }
    return res


# --------------------------------------------------------------------------- the run
def load_run(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cfg = json.loads((run_dir / "config.json").read_text())
    infos = [json.loads(p.read_text()) for p in sorted(run_dir.glob("info_*.json"))]
    return cfg, infos


def expected_arm(name: str) -> tuple[str, list[int]]:
    """(variant, seeds) the frozen job uses for an arm: RAW_* raw, NORM_* / NREP_* norm; NREP uses the second seed set."""
    return (
        "raw" if name.startswith("RAW") else "norm",
        SEEDS_REP if name.startswith("NREP") else SEEDS_MAIN,
    )


def check_run(
    cfg: dict[str, Any],
    infos: list[dict[str, Any]],
    sha: str,
    export_dir: Path,
    rv: dict[str, str],
) -> dict[str, Any]:
    """The run-time gates the rule names. The run used the frozen rule, the reviewed commit and the reviewed exports (cfg and every info agree with ``rv`` and the files on disk), cap
    season 2023, ONE configuration for every arm (hidden 64, the frozen cold / warm settings, the frozen seeds), the same 28,197 test rows, no smoke run. Any mismatch is VOID (runtime)."""
    bad: list[str] = []
    if cfg.get("rule_sha256") != sha:
        bad.append("run used a different rule sha")
    if cfg.get("cap_season") != 2023 or cfg.get("test_label") != 2023:
        bad.append("run cap / test season is not 2023")
    if cfg.get("review_commit") != rv.get("review_commit"):
        bad.append("the run was attested against a different review commit")
    cexp = cfg.get("exports") or {}
    if set(cexp) != set(RULE.EXPORT_NAMES) or RULE.manifest_sha(cexp) != rv.get("exports_manifest"):
        bad.append("the run's exports are not the six exports named by the review's manifest")
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
    if cfg.get("seeds_main") != SEEDS_MAIN or cfg.get("seeds_rep") != SEEDS_REP:
        bad.append("config seed sets differ from the frozen ones")
    names = [i.get("arm") for i in infos]
    if len(set(names)) != len(names) or not set(names) <= set(ALL_ARMS):
        bad.append("duplicate or unknown arm in the run's info files")
    for i in infos:
        arm = str(i.get("arm"))
        if arm not in ALL_ARMS:
            continue
        variant, seeds = expected_arm(arm)
        why = []
        if i.get("hidden") != HIDDEN:
            why.append(f"hidden {i.get('hidden')}")
        if i.get("cold_cfg") != COLD_CFG:
            why.append("cold_cfg")
        if i.get("warm_cfg") != WARM_CFG:
            why.append("warm_cfg")
        if i.get("seeds") != seeds or [c.get("seed") for c in i.get("cold", [])] != seeds:
            why.append(f"seeds (want {seeds})")
        if i.get("rule_sha256") != sha:
            why.append("rule sha")
        if i.get("n_test") != N_TEST:
            why.append(f"n_test {i.get('n_test')} != {N_TEST}")
        if i.get("smoke") is not False:
            why.append("smoke")
        if i.get("variant") != variant:
            why.append("variant")
        if i.get("exports") != cexp:
            why.append("exports differ from the attested ones")
        if i.get("review_commit") != rv.get("review_commit"):
            why.append("review commit")
        if why:
            bad.append(f"{arm}: " + ", ".join(why))
    keys = {i["test_keys_sha256"] for i in infos}
    if len(keys) > 1:
        bad.append("the arms scored different 2023-24 row sets")
    for v in ("norm", "raw"):
        shas = {i["test_inputs_sha256"] for i in infos if i["variant"] == v}
        if len(shas) > 1:
            bad.append(f"the {v} arms saw different 2023-24 test inputs")
    return {"problems": bad, "ok": not bad, "arms_done": sorted(i["arm"] for i in infos)}


def meta_from_exports(z3: Any) -> pd.DataFrame:
    """The 2023-24 row table (game_id, player_id, season, norm_c_<stat>) read from the ATTESTED S3 npz (its sha is checked against the review's manifest), not from the unchecked
    meta parquet (review F6)."""
    if tuple(XP.P.STATS) != STATS:
        raise RuntimeError("stat order of the export differs from the scorer's")
    c = np.asarray(z3["norm_c"], dtype=float)
    df = pd.DataFrame(
        {
            "game_id": np.asarray(z3["game_id"]).astype(str),
            "player_id": np.asarray(z3["player_id"]).astype(np.int64),
            "season": np.asarray(z3["season"]).astype(int),
            **{f"norm_c_{st}": c[:, k] for k, st in enumerate(STATS)},
        }
    )
    return df[df["season"] == SCORE_LABEL].reset_index(drop=True)


DISCLOSURES = [
    "Cost ceiling (review F9): the job sums runtime_s of COMPLETED arms only, so wall-clock spent in an arm that was interrupted before it wrote its info file is not counted; the 240 T4-minute ceiling can be overrun by one partial arm.",
    "Seed noise (review ruling 11): the figures under 'replicates' rest on three main-minus-replicate differences (N = 4, 6, 8); the 95% range of a standard deviation from 3 values is about 0.6x to 3.7x.",
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
) -> dict[str, Any]:
    """Gate first, curve last (review F2 / F5). Order: runtime (config, exports, era, world rows) -> VOID without scoring anything; then ONLY RAW_1 and RAW_4 on pts for the reproduction gate
    (a failed reproduction -> VOID); a missing RAW_1 / RAW_4 or any unfinished arm -> INCONCLUSIVE (unfinished); a failed budget gate -> INVALID (budget); only when every gate passes are the NORM / NREP /
    RAW_8 arms and the other stats scored. So a voided, unfinished or invalid run never leaves a NORM-arm CRPS anywhere, including the cache."""
    chk = out["run_check"]
    budget = out["budget"]
    unfinished = out["unfinished"]
    out["world_rows"] = check_world_rows(world, meta)
    gates = {
        "repro_ok": False,
        "runtime_ok": bool(runtime_ok and out["world_rows"]["ok"]),
        "budget_ok": bool(budget["ok"]),
    }
    out["gates"] = gates  # fmt: skip
    P = STAT_PRIMARY
    if not gates["runtime_ok"]:
        verdict_only(out, "VOID (gate)", "run-time gate failed; nothing was scored")
        return out
    base_crps: dict[str, dict[str, np.ndarray]] = {}

    def base(st: str) -> dict[str, np.ndarray]:
        if st not in base_crps:
            b = world["base"][st]
            base_crps[st] = {
                "A0": E_crps(b["a0_int"], b["y"]),
                "FULL": E_crps(b["full_int"], b["y"]),
            }
        return base_crps[st]

    arm_crps: dict[tuple[str, str], dict[str, np.ndarray]] = {}

    def score(arm: str, st: str) -> None:
        _log(f"scoring {arm} / {st}")
        z, tag = load_oof(arm)
        arm_crps[(arm, st)] = score_arm_stat(arm, st, world, z, meta, tag)

    repro_arms = ("RAW_1", "RAW_4")
    if all(a in chk["arms_done"] for a in repro_arms):
        for a in repro_arms:
            score(a, P)
        fa = base(P)
        out["repro"] = repro_gate(
            float((arm_crps[("RAW_1", P)]["crps_avg"] - fa["FULL"]).mean()),
            float((arm_crps[("RAW_4", P)]["crps_avg"] - fa["FULL"]).mean()),
            float((fa["FULL"] - fa["A0"]).mean()),
            S.T467[SCORE_LABEL],
        )
        gates["repro_ok"] = bool(out["repro"]["ok"])
        if not gates["repro_ok"]:
            verdict_only(out, "VOID (gate)", "reproduction gate failed; no other arm was scored")
            return out
    if unfinished:  # includes a missing RAW_1 / RAW_4 (F5): the reproduction gate could not be computed, which is unfinished, not void
        verdict_only(
            out, "INCONCLUSIVE (unfinished)", "arms not finished: " + ", ".join(unfinished)
        )
        return out
    if not gates["budget_ok"]:
        verdict_only(
            out, "INVALID (budget)", "more than one third of cold fits reached the epoch cap"
        )
        return out
    for arm in ALL_ARMS:  # every gate passed: now, and only now, the curve arms
        for st in STATS if arm.startswith("NORM") else (P,):
            if (arm, st) not in arm_crps:
                score(arm, st)
    out["curve"] = assemble({st: base(st) for st in STATS}, arm_crps, world, gates, unfinished)
    return out


def run(run_dir: Path, export_dir: Path = EXPORT_DIR) -> dict[str, Any]:
    rv = RULE.require_review(
        export_dir=export_dir
    )  # hard stop: frozen rule + the latest committed review says APPROVED, covers this code and these exports
    _log(f"rule {rv['rule_sha256'][:8]}, review {rv['review_path']} @ {rv['review_commit'][:8]}")
    cfg, infos = load_run(run_dir)
    chk = check_run(cfg, infos, rv["rule_sha256"], export_dir, rv)
    z3 = np.load(export_dir / f"{XP.stem('norm', 'S3')}.npz", allow_pickle=False)
    era = XP.era_runtime_gate(z3["ctx"], z3["season"], tuple(str(a) for a in z3["ctx_features"]))
    runtime_ok = bool(chk["ok"] and era["ok"])
    meta = meta_from_exports(z3)
    budget = budget_gate(infos)
    unfinished = [a for a in ALL_ARMS if a not in chk["arms_done"]]
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
        "cap diagnostic: "
        + json.dumps({a: v["reached_cap"] for a, v in budget["by_arm"].items()})
        + f"; share {budget['share']:.3f}, NORM-only {budget['share_norm_only']:.3f}"
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
            world = load_world_2023()
            score_run(out, infos, runtime_ok, world, meta, load_oof)
    out["verdict"] = out["curve"]["decision"]["verdict"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps(out, indent=1, default=float))
    REPORT_MD.write_text(render(out))
    return out


def _f(x: float) -> str:
    return f"{x:+.4f}"


def _ci(c: dict[str, Any]) -> str:
    return f"{_f(c['point'])} [{_f(c['lo'])}, {_f(c['hi'])}]"


def render(out: dict[str, Any]) -> str:
    c = out.get("curve", {})
    lines = [
        f"# SEQ_LEARNING_CURVE results (rule {out['rule_sha256'][:8]})",
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
            f"Epoch-cap diagnostic: {b['reached_cap']}/{b['cold_fits']} cold fits reached the cap (share {b['share']:.3f}, NORM-only {b['share_norm_only']:.3f}); "
            f"arms with every fit at the cap: {b.get('arms_with_every_fit_at_cap') or 'none'}.",
            "",
        ]
    if "norm" in c:
        lines += [
            f"n = {c['n_rows']} player-games, {c['n_games']} games (2023-24, pts), {c['n_boot']} paired game-clustered draws.",
            "",
            "| N | C(N) AVG | AVG - FULL |",
            "|---|---|---|",
        ]
        for n in NORM_NS:
            lines.append(
                f"| {n} | {c['norm']['C'][str(n)]:.4f} | {_ci(c['norm']['AVG_minus_FULL'][str(n)])} |"
            )
        lines += [
            "",
            f"A = C(8) - C(4): {_ci(c['norm']['A'])}; B = C(8) - C(6): {_ci(c['norm']['B'])}",
        ]
    if "decision" in c:
        lines += ["", f"Decision detail: {json.dumps(c['decision'], default=float)}"]
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
