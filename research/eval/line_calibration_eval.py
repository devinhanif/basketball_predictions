# ruff: noqa: E501
"""LINE_CALIBRATION harness (rule: docs/prereg/LINE_CALIBRATION.md; DRAFT until frozen).

    M="-m research.eval.line_calibration_eval"
    .venv/bin/python -W ignore $M gate     --allow-draft          # data, timing, A0 reproduction, planted future, power, null sizing
    .venv/bin/python -W ignore $M rehearse                        # whole select path on SYNTHETIC labels, 2024 standing in
    .venv/bin/python -W ignore $M select   --sha <64 hex>         # stage 1: score 2024 (real labels), after the freeze commit
    .venv/bin/python -W ignore $M report   --sha <64 hex> --pairs-2025 <parquet> --grid-2025 <parquet>   # stage 2: 2025, once, after the holdout row

What it does: a market-free recalibration of production's own P(over line). Per (book, stat, calendar-month block) one
logistic regression is fit on every earlier T-60 leg of that book and stat (strictly dated before the block's first day,
all loaded seasons, each row weighted 0.5 ** (age in days / 365)) with the terms [1, logit p_model, z, bench,
bench * logit p_model]. z = (line - continuous median) / max((q75 - q25) / 1.349, 0.5), clipped to [-5, 5], read from the
production CONTINUOUS 199-point grid (before integer rounding), which is recovered exactly from the stored OOF rows
(``c + s * zq``; one zq grid per stat and month block, identified from the stored integer survival vectors; no refit).
bench = starter10 < 0.5. Nothing derived from a price enters a fit; the de-vigged market probability is read only for a
reference row. Inputs: the MODEL_CLV paired frames (``data/ops/model_clv_pairs/pairs_{2023,2024}.parquet``) and the
ODDS_HISTORY OOF rows (``reports/odds_history/model_rows_{2023,2024}.parquet``); season 2025 is read only by ``report`` after
``line_calibration_rule.require_ready``. No DuckDB is opened. Light job (numpy); ``data/ops/heavy.lock`` is never taken
and never removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nba.eval.context_residual_eval import bh_adjust
from research.eval import line_calibration_rule as rule
from research.eval.model_clv_eval import Boot

DOC = Path("docs/prereg/LINE_CALIBRATION.md")
OUT_DIR = Path("reports/prereg_line_calibration")
PAIRS_DIR = Path("data/ops/model_clv_pairs")
ODDS_RESULTS = Path("reports/odds_history")
MODEL_DIR = Path("reports/odds_history")
LOCK = Path("data/ops/heavy.lock")

MAX_SEASON = 2024
FIT_SEASONS = (2023, 2024)
SELECT_SEASON, REPORT_SEASON = 2024, 2025
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
BOOKS: tuple[str, ...] = ("pinnacle", "draftkings")
PRIMARY_BOOK = "pinnacle"
K4 = ["game_id", "player_id", "stat", "book"]

EPS = 0.5 / 199  # production grid resolution; the ODDS_HISTORY convention for model probabilities
MARKET_EPS = 1e-9
SPREAD_MIN = (
    0.5  # half an integer unit: a spread below the outcome's own resolution is not resolvable
)
IQR_TO_SD = 1.349
HALF_LIFE_DAYS = 365.0  # one season; fixed from first principles, never tuned
NQ = 199
# 0-based positions on the 199-point grid of tau = 0.25, 0.5, 0.75 (tau_j = (j + 0.5) / 199): 49.25, 99, 148.75
TAU_POS = {"q25": 0.25 * NQ - 0.5, "q50": 0.5 * NQ - 0.5, "q75": 0.75 * NQ - 0.5}
ZQ_WIDTH_MAX = 0.1  # identification tolerance of a needed grid point, z units
Z_CLIP = 5.0
RIDGE = 1.0
MIN_FIT = 2000
FLOOR = -0.003
CAL_ABS_MAX = 0.015
SLICE_MIN_N = 300
K_NULL = 50
NULL_SD_MULT = 2.0
N_BOOT = 2000
SEED = 0
ALPHA = 0.05
# synthetic truth for the power check and the rehearsal (never a real label): logit p = A + B logit p_model
SYN_A, SYN_B = 0.17, 0.85
CONF_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-0.05", 0.0, 0.05),
    ("0.05-0.10", 0.05, 0.10),
    ("0.10-0.20", 0.10, 0.20),
    (">0.20", 0.20, 0.51),
)
NEED_COLS = [
    "game_id", "player_id", "stat", "book", "snapshot_kind", "snapshot_ts", "line", "p_over_mult", "p_model",
    "over", "tipoff_utc", "starter10", "season", "game_date",
]  # fmt: skip
GRID_COLS = ["game_id", "player_id", "stat", "game_date", "season", "c", "s", "p_ge"]

UNSTATED: list[str] = [
    "leg = one t60 row of pinnacle or draftkings in the MODEL_CLV pairs frame (main line, unique per game/player/stat/book, DNP voided upstream, every line x.5)",
    "block = calendar month of game_date; fit rows = same book and stat, game_date strictly before the block's first day, every loaded season (2023 onward); < 2,000 fit rows -> identity (raw p_model) for every arm",
    "fit weights w = 0.5 ** ((block start - game_date) / 365 days) for CAL, C0 and SH alike; weights are not used in scoring (every scored leg weighs 1)",
    "continuous grid: per (stat, season, calendar month of game_date) the 199 sorted values zq_j are bracketed by the stored integer survival counts N_ik = 199 P(Y >= k): zq_j > (k - 0.5 - c_i) / s_i iff N_ik >= 200 - j; zq_j = midpoint of the bracket (an open end takes the finite bound); q_j = max(c + s zq_j, 0); q25/q50/q75 by linear interpolation at grid positions 49.25 / 99 / 148.75 (0-based)",
    "IRLS logistic, ridge 1.0 on the four non-intercept coefficients (sum-of-losses scale), start at 0, stop when max |step| < 1e-10 or 100 iterations",
    "x = logit(clip(p_model, 0.5/199, 1 - 0.5/199)); every model-derived probability is clipped to the same eps before log loss and Brier; the market de-vig (p_over_mult) is clipped to 1e-9",
    "C0 = the fit rows' over rate (clipped to eps), per book, stat and block; SH_k = the CAL fit after permuting the fit rows' labels with default_rng([1000 + k, stat index, block index, book index]), k = 0..49",
    "CIs: 95% percentile, 2,000 multinomial game resamples (seed 0) shared by every statistic of a season; a date-clustered CI (same construction over game dates) is reported beside the primary; one-sided p for 'arm beats comparator' = (1 + #{boot of (comparator - arm) <= 0}) / 2001",
    "pass contrasts are pooled over the four stats with every leg weighted equally (legs, not stats)",
    "starter = starter10 >= 0.5; confidence bucket on |p_model - 0.5|",
]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lock_note() -> str:
    """This job never takes or removes the heavy lock; it only records whether one was held."""
    if LOCK.exists():
        pid = (LOCK / "pid").read_text().strip() if (LOCK / "pid").exists() else "?"
        return f"heavy.lock held by pid {pid} (not ours; untouched; this job is light)"
    return "heavy.lock absent"


# --------------------------------------------------------------------------- data


def read_pairs(path: Path, season: int, *, holdout_ok: bool = False) -> pl.DataFrame:
    """T-60 legs of one season. Season 2025 only through ``load_holdout`` (after the rule and the logged row)."""
    if season > MAX_SEASON and not holdout_ok:
        raise ValueError(
            "season 2025 is the frozen holdout: only `report --sha` after the logged row may read it"
        )
    d = pl.read_parquet(path, columns=NEED_COLS)
    assert d["season"].n_unique() == 1 and int(d["season"][0]) == season, "one season per frame"
    d = d.filter((pl.col("snapshot_kind") == "t60") & pl.col("book").is_in(list(BOOKS)))
    assert d.group_by(K4).len()["len"].max() == 1, "one main line per game/player/stat/book"
    return d.sort(K4)


def load_holdout(path: Path, grid_path: Path, sha: str) -> tuple[pl.DataFrame, dict[str, Any]]:
    rule.require_ready(sha)
    return attach_grid(
        read_pairs(path, REPORT_SEASON, holdout_ok=True),
        read_grid(grid_path, REPORT_SEASON, holdout_ok=True),
    )


def read_grid(path: Path, season: int, *, holdout_ok: bool = False) -> pl.DataFrame:
    """Stored production OOF rows (c, s, integer survival vector) of one season; no outcome column is read."""
    if season > MAX_SEASON and not holdout_ok:
        raise ValueError("season 2025 is the frozen holdout")
    g = pl.read_parquet(path, columns=GRID_COLS)
    assert g["season"].n_unique() == 1 and int(g["season"][0]) == season
    assert g.group_by(["game_id", "player_id", "stat"]).len()["len"].max() == 1
    assert float(g["s"].min()) > 0  # type: ignore[arg-type]
    return g.with_columns(
        pl.col("player_id").cast(pl.Int64), pl.col("game_date").dt.strftime("%Y-%m").alias("mo")
    )


def block_zq(c: np.ndarray, s: np.ndarray, pge: list[Any]) -> dict[str, Any]:
    """Bracket the block's 199 sorted standardized grid values from the stored integer survival counts."""
    lens = np.array([len(p) for p in pge])
    flat = np.concatenate([np.asarray(p, dtype=float) for p in pge])
    rows = np.repeat(np.arange(len(c)), lens)
    k = np.concatenate([np.arange(1, n + 1) for n in lens])
    N = np.rint(flat * NQ).astype(np.int64)
    assert np.abs(flat * NQ - N).max() < 1e-6, "survival values are not counts out of 199"
    t = (k - 0.5 - c[rows]) / s[rows]
    mx = np.full(NQ + 1, -np.inf)
    np.maximum.at(mx, N, t)
    mn = np.full(NQ + 1, np.inf)
    np.minimum.at(mn, N, t)
    suf = np.maximum.accumulate(mx[::-1])[::-1]  # max t over counts >= n
    pre = np.minimum.accumulate(mn)  # min t over counts <= n
    j = np.arange(1, NQ + 1)
    lo, hi = suf[NQ + 1 - j], pre[NQ - j]
    mid = np.where(
        np.isfinite(lo) & np.isfinite(hi), (lo + hi) / 2, np.where(np.isfinite(lo), lo, hi)
    )
    return {"lo": lo, "hi": hi, "mid": mid, "inconsistent": int(np.sum(hi < lo))}


def _interp(q: np.ndarray, pos: float) -> np.ndarray:
    i = int(np.floor(pos))
    f = pos - i
    return np.asarray(q[:, i] * (1 - f) + q[:, min(i + 1, NQ - 1)] * f)


def attach_grid(pairs: pl.DataFrame, grid: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Add the continuous-grid median, robust spread and continuous P(over line) to each leg."""
    need = [int(np.floor(v)) for v in TAU_POS.values()] + [
        int(np.floor(v)) + 1 for v in TAU_POS.values()
    ]
    legs = pairs.join(
        grid.select("game_id", "player_id", "stat", "mo", "c", "s"),
        on=["game_id", "player_id", "stat"],
        how="left",
    )
    assert legs["c"].null_count() == 0, "a leg without its stored grid row"
    parts: list[pl.DataFrame] = []
    diag: dict[str, Any] = {
        "blocks": 0,
        "inconsistent_blocks": 0,
        "max_width_needed": 0.0,
        "p_cont_mismatch": 0,
    }
    for (stat, mo), gb in grid.group_by(["stat", "mo"], maintain_order=True):
        z = block_zq(gb["c"].to_numpy(), gb["s"].to_numpy(), gb["p_ge"].to_list())
        diag["blocks"] += 1
        diag["inconsistent_blocks"] += int(z["inconsistent"] > 0)
        w = (z["hi"] - z["lo"])[need]
        diag["max_width_needed"] = max(
            diag["max_width_needed"], float(np.nanmax(np.where(np.isfinite(w), w, np.inf)))
        )
        lb = legs.filter((pl.col("stat") == stat) & (pl.col("mo") == mo))
        if lb.is_empty():
            continue
        q = np.maximum(
            lb["c"].to_numpy()[:, None] + lb["s"].to_numpy()[:, None] * z["mid"][None, :], 0.0
        )
        q = np.maximum.accumulate(q, axis=1)
        line = lb["line"].to_numpy()
        p_cont = (q > line[:, None]).sum(axis=1) / NQ
        diag["p_cont_mismatch"] += int(np.sum(np.abs(p_cont - lb["p_model"].to_numpy()) > 1e-9))
        q25, q50, q75 = (_interp(q, TAU_POS[k]) for k in ("q25", "q50", "q75"))
        parts.append(
            lb.with_columns(
                pl.Series("med", q50),
                pl.Series("spr", (q75 - q25) / IQR_TO_SD),
                pl.Series("p_cont", p_cont),
            )
        )
    out = pl.concat(parts).sort(K4)
    assert out.height == pairs.height
    return out, diag


def to_arrays(frames: list[pl.DataFrame]) -> dict[str, Any]:
    d = pl.concat(frames, how="vertical_relaxed")
    D: dict[str, Any] = {
        "n": d.height,
        "season": d["season"].to_numpy().astype(np.int64),
        "date": d["game_date"].to_numpy().astype("datetime64[D]"),
        "book": d["book"].to_numpy().astype(str),
        "stat": d["stat"].to_numpy().astype(str),
        "game_id": d["game_id"].to_numpy().astype(str),
        "pm": d["p_model"].cast(pl.Float64).to_numpy(),
        "line": d["line"].cast(pl.Float64).to_numpy(),
        "med": d["med"].cast(pl.Float64).to_numpy(),
        "spr": d["spr"].cast(pl.Float64).to_numpy(),
        "st10": d["starter10"].cast(pl.Float64).to_numpy(),
        "over": d["over"].cast(pl.Float64).to_numpy(),
        "mult": d["p_over_mult"].cast(pl.Float64).to_numpy(),
        "lead_min": (
            (d["tipoff_utc"] - d["snapshot_ts"]).dt.total_seconds().cast(pl.Float64) / 60.0
        ).to_numpy(),
    }
    D["mstart"] = D["date"].astype("datetime64[M]").astype("datetime64[D]")
    return D


def load_real(
    pairs_dir: Path = PAIRS_DIR, model_dir: Path = MODEL_DIR
) -> tuple[dict[str, Any], dict[str, str]]:
    frames: list[pl.DataFrame] = []
    shas: dict[str, str] = {}
    diags: dict[str, Any] = {}
    for s in FIT_SEASONS:
        f, dg = attach_grid(
            read_pairs(pairs_dir / f"pairs_{s}.parquet", s),
            read_grid(model_dir / f"model_rows_{s}.parquet", s),
        )
        frames.append(f)
        diags[str(s)] = dg
        shas[str(s)] = file_sha(pairs_dir / f"pairs_{s}.parquet")
        shas[f"model_rows_{s}"] = file_sha(model_dir / f"model_rows_{s}.parquet")
    D = to_arrays(frames)
    D["grid_diag"] = diags
    assert int(D["season"].max()) <= MAX_SEASON
    return D, shas


def synthetic_labels(D: dict[str, Any], seed: int) -> np.ndarray:
    """Labels drawn from a known truth (logit p = SYN_A + SYN_B logit p_model); no real outcome is read."""
    p = sigmoid(SYN_A + SYN_B * logit(np.clip(D["pm"], EPS, 1 - EPS)))
    return np.asarray(np.random.default_rng(seed).random(D["n"]) < p, dtype=np.float64)


# --------------------------------------------------------------------------- the map


def logit(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(p) - np.log1p(-p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-x)))


def design(
    pm: np.ndarray, line: np.ndarray, med: np.ndarray, spr: np.ndarray, st10: np.ndarray
) -> np.ndarray:
    """[1, x, z, bench, bench * x]. Takes no price argument by construction (market-free)."""
    x = logit(np.clip(pm, EPS, 1 - EPS))
    z = np.clip((line - med) / np.maximum(spr, SPREAD_MIN), -Z_CLIP, Z_CLIP)
    bench = (st10 < 0.5).astype(np.float64)
    return np.column_stack([np.ones_like(x), x, z, bench, bench * x])


def age_weights(dates: np.ndarray, block_start: np.datetime64) -> np.ndarray:
    age = (block_start - dates).astype("timedelta64[D]").astype(np.float64)
    assert np.all(age > 0), "a fit row on or after the block start"
    return np.asarray(0.5 ** (age / HALF_LIFE_DAYS))


def fit_logistic(
    X: np.ndarray, y: np.ndarray, ridge: float = RIDGE, w: np.ndarray | None = None
) -> np.ndarray:
    w = np.ones(len(y)) if w is None else w
    R = np.eye(X.shape[1]) * ridge
    R[0, 0] = 0.0
    b = np.zeros(X.shape[1])
    for _ in range(100):
        p = sigmoid(X @ b)
        g = X.T @ (w * (y - p)) - R @ b
        H = X.T @ (X * (w * p * (1 - p))[:, None]) + R
        step = np.linalg.solve(H, g)
        b = b + step
        if np.max(np.abs(step)) < 1e-10:
            break
    return b


def walk_forward(
    D: dict[str, Any],
    y: np.ndarray,
    season: int,
    book: str,
    arm: str,
    k: int = 0,
    only_block: np.datetime64 | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Out-of-block probabilities for every leg of (season, book); NaN elsewhere. arm in {cal, base, shuffle}."""
    assert arm in ("cal", "base", "shuffle")
    X = design(D["pm"], D["line"], D["med"], D["spr"], D["st10"])
    out = np.full(D["n"], np.nan)
    info: list[dict[str, Any]] = []
    for si_, stat in enumerate(STATS):
        cell = (D["book"] == book) & (D["stat"] == stat)
        tgt = cell & (D["season"] == season)
        for bi, b0 in enumerate(np.unique(D["mstart"][tgt])):
            if only_block is not None and b0 != only_block:
                continue
            fit = cell & (D["date"] < b0)
            sc = tgt & (D["mstart"] == b0)
            nfit = int(fit.sum())
            rec: dict[str, Any] = {
                "stat": stat,
                "block": str(b0)[:7],
                "n_fit": nfit,
                "n_score": int(sc.sum()),
            }
            if nfit < MIN_FIT:
                out[sc] = np.clip(D["pm"][sc], EPS, 1 - EPS)
                rec["identity"] = True
            else:
                yf = y[fit]
                wf = age_weights(D["date"][fit], b0)
                rec["n_eff"] = float(wf.sum() ** 2 / (wf**2).sum())
                if arm == "shuffle":
                    rng = np.random.default_rng([1000 + k, si_, bi, BOOKS.index(book)])
                    yf = rng.permutation(yf)
                if arm == "base":
                    rate = float(np.sum(wf * yf) / np.sum(wf))
                    out[sc] = float(np.clip(rate, EPS, 1 - EPS))
                    rec["rate"] = rate
                else:
                    beta = fit_logistic(X[fit], yf, w=wf)
                    out[sc] = np.clip(sigmoid(X[sc] @ beta), EPS, 1 - EPS)
                    rec["beta"] = [float(v) for v in beta]
                rec["identity"] = False
                rec["last_fit_date"] = str(D["date"][fit].max())
            info.append(rec)
    return out, info


# --------------------------------------------------------------------------- scoring


def ll(p: np.ndarray, o: np.ndarray, eps: float = EPS) -> np.ndarray:
    pc = np.clip(p, eps, 1 - eps)
    return np.asarray(-np.where(o > 0.5, np.log(pc), np.log1p(-pc)))


def dense(codes: np.ndarray) -> tuple[np.ndarray, int]:
    u, inv = np.unique(codes, return_inverse=True)
    return inv.astype(np.int64), len(u)


def _r(m: dict[str, Any]) -> dict[str, Any]:
    return {k: m.get(k) for k in ("mean", "lo", "hi", "p", "se", "n")}


def reliability(p: np.ndarray, o: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return [
        {
            "bin": b / bins,
            "n": int((idx == b).sum()),
            "pred": float(p[idx == b].mean()),
            "obs": float(o[idx == b].mean()),
        }
        for b in range(bins)
        if (idx == b).any()
    ]


def score_stage(
    D: dict[str, Any], y: np.ndarray, season: int, book: str, k_null: int = K_NULL
) -> dict[str, Any]:
    """Every arm and contrast of one (season, book). y = the labels (real at select/report; synthetic in tests/rehearsal)."""
    p_cal, info_cal = walk_forward(D, y, season, book, "cal")
    p_c0, info_c0 = walk_forward(D, y, season, book, "base")
    m = (D["season"] == season) & (D["book"] == book)
    o = y[m]
    gi, G = dense(D["game_id"][m])
    di, ND = dense(D["date"][m].astype(str))
    bs, bd = Boot(gi, G), Boot(di, ND)
    pm = np.clip(D["pm"][m], EPS, 1 - EPS)
    arms = {"A0": pm, "CAL": p_cal[m], "C0": p_c0[m], "COIN": np.full(len(o), 0.5)}
    L = {a: ll(p, o) for a, p in arms.items()}
    L["MKT"] = ll(D["mult"][m], o, MARKET_EPS)
    sh_ll: list[np.ndarray] = []
    for k in range(k_null):
        p_sh, _ = walk_forward(D, y, season, book, "shuffle", k)
        sh_ll.append(ll(p_sh[m], o))
    every = np.ones(len(o), bool)
    res: dict[str, Any] = {
        "season": season,
        "book": book,
        "n": int(m.sum()),
        "games": G,
        "dates": ND,
        "ll": {a: float(v.mean()) for a, v in L.items()},
        "brier": {a: float(((p - o) ** 2).mean()) for a, p in arms.items()}
        | {"MKT": float(((D["mult"][m] - o) ** 2).mean())},
        "identity_share": float(np.mean([r["identity"] for r in info_cal])),
        "identity_rows": int(sum(r["n_score"] for r in info_cal if r["identity"])),
        "blocks_cal": info_cal,
        "blocks_c0": info_c0,
    }
    # primary and pass contrasts (negative = CAL better); p from the positive-good orientation
    res["d_cal_a0"] = _r(bs.mean(L["CAL"] - L["A0"], every)) | {
        "p": bs.mean(L["A0"] - L["CAL"], every)["p"]
    }
    res["d_cal_a0_date"] = _r(bd.mean(L["CAL"] - L["A0"], every))
    res["d_cal_c0"] = _r(bs.mean(L["CAL"] - L["C0"], every))
    res["brier_cal_a0"] = _r(bs.mean((arms["CAL"] - o) ** 2 - (pm - o) ** 2, every))
    if sh_ll:
        sh_means = np.array([v.mean() for v in sh_ll])
        sh_bar = np.mean(sh_ll, axis=0)
        res["null"] = {
            "k": k_null,
            "sh_minus_a0_mean": float(sh_means.mean() - L["A0"].mean()),
            "sh_minus_c0_mean": float(sh_means.mean() - L["C0"].mean()),
            "sd": float(sh_means.std(ddof=1)) if k_null > 1 else 0.0,
            "sh_means": [float(v) for v in sh_means],
        }
        res["d_cal_sh"] = _r(bs.mean(L["CAL"] - sh_bar, every))
    # reference rows (never pass criteria)
    res["ref"] = {
        "a0_minus_coin": _r(bs.mean(L["A0"] - L["COIN"], every)),
        "cal_minus_coin": _r(bs.mean(L["CAL"] - L["COIN"], every)),
        "cal_minus_market": _r(bs.mean(L["CAL"] - L["MKT"], every)),
        "a0_minus_market": _r(bs.mean(L["A0"] - L["MKT"], every)),
    }
    # calibration in the large, by role
    starter = D["st10"][m] >= 0.5
    res["calib"] = {
        role: {a: _r(bs.mean(arms[a] - o, rm)) for a in ("A0", "CAL", "C0")}
        | {"MKT": _r(bs.mean(D["mult"][m] - o, rm))}
        for role, rm in (("all", every), ("starter", starter), ("bench", ~starter))
    }
    res["reliability"] = {a: reliability(arms[a], o) for a in ("A0", "CAL")} | {
        "MKT": reliability(np.clip(D["mult"][m], 0, 1), o)
    }
    # per-stat secondary family (BH m = 4) and slices
    stat = D["stat"][m]
    per: dict[str, Any] = {}
    for s in STATS:
        sm = stat == s
        per[s] = _r(bs.mean(L["CAL"] - L["A0"], sm)) | {"p": bs.mean(L["A0"] - L["CAL"], sm)["p"]}
        per[s]["cal_minus_c0"] = _r(bs.mean(L["CAL"] - L["C0"], sm))
        per[s]["ll_a0"] = float(L["A0"][sm].mean()) if sm.any() else None
        per[s]["ll_cal"] = float(L["CAL"][sm].mean()) if sm.any() else None
    ps = [per[s]["p"] for s in STATS]
    if all(p is not None for p in ps):
        for s, q in zip(STATS, bh_adjust(np.array(ps)), strict=True):
            per[s]["q_bh"] = float(q)
    res["per_stat"] = per
    conf = np.abs(pm - 0.5)
    month = D["mstart"][m].astype(str)
    sl: dict[str, Any] = {}
    for name, sm in (
        ("starter", starter),
        ("bench", ~starter),
        *((f"conf {b}", (conf >= lo) & (conf < hi)) for b, lo, hi in CONF_BUCKETS),
        *((f"month {mo[:7]}", month == mo) for mo in np.unique(month)),
    ):
        sl[name] = _r(bs.mean(L["CAL"] - L["A0"], sm))
    res["slices"] = sl
    # the same do-no-harm contrasts for each candidate served object (CAL, or C0 if the verdict is shrinkage only)
    res["objects"] = {}
    for a in ("CAL", "C0"):
        res["objects"][a] = {
            "d_a0": _r(bs.mean(L[a] - L["A0"], every)),
            "per_stat_lo": {s_: bs.mean(L[a] - L["A0"], stat == s_)["lo"] for s_ in STATS},
            "slices": {
                nm: bs.mean(L[a] - L["A0"], rm)["mean"]
                for nm, rm in (("starter", starter), ("bench", ~starter))
            },
            "calib": res["calib"]["all"][a]["mean"],
            "ll": float(L[a].mean()),
        }
    return res


def object_checks(primary: dict[str, Any], other: dict[str, Any], arm: str) -> dict[str, bool]:
    """P1 and P4-P7 for a served object (CAL or C0)."""
    o, d = primary["objects"][arm], primary["objects"][arm]["d_a0"]
    return {
        "P1": bool(d["mean"] <= FLOOR and d["hi"] < 0),
        "P4": bool(abs(o["calib"]) <= CAL_ABS_MAX),
        "P5": all(not (lo is not None and lo > 0) for lo in o["per_stat_lo"].values()),
        "P6": all(v is None or v <= 0 for v in o["slices"].values()),
        "P7": bool(other["objects"][arm]["d_a0"]["mean"] < 0),
    }


def evaluate(primary: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """The frozen pass rule, applied to one stage (primary = Pinnacle, other = DK).

    Verdict: CALIBRATED = P1-P7 all pass. SHRINKAGE_ONLY = P1, P4-P7 pass but P2 or P3 fails: the map does not beat
    generic shrinkage; the served object is whichever of CAL and C0 has the lower pooled Pinnacle log loss on this stage,
    and it must itself pass P1 and P4-P7. FAIL otherwise."""
    d = primary["d_cal_a0"]
    c = {
        "P1 CAL-A0 <= floor and CI upper < 0": bool(d["mean"] <= FLOOR and d["hi"] < 0),
        "P2 CAL-C0 CI upper < 0": bool(primary["d_cal_c0"]["hi"] < 0),
        "P3 CAL - mean(SH) <= -2 null sd and CI upper < 0": bool(
            primary["d_cal_sh"]["mean"] <= -NULL_SD_MULT * primary["null"]["sd"]
            and primary["d_cal_sh"]["hi"] < 0
        ),
        "P4 calibration in the large |CAL - realised| <= 0.015": bool(
            abs(primary["calib"]["all"]["CAL"]["mean"]) <= CAL_ABS_MAX
        ),
        "P5 no stat worse (CI lower > 0) on CAL-A0": all(
            not (r["lo"] is not None and r["lo"] > 0) for r in primary["per_stat"].values()
        ),
        "P6 starter and bench slices not worse (point <= 0)": all(
            primary["slices"][s]["mean"] is None or primary["slices"][s]["mean"] <= 0
            for s in ("starter", "bench")
        ),
        "P7 DK direction agrees (CAL-A0 point < 0)": bool(other["d_cal_a0"]["mean"] < 0),
    }
    verdict = "FAIL"
    obj = None
    if all(c.values()):
        verdict, obj = "CALIBRATED", "CAL"
    else:
        harm_ok = [v for k, v in c.items() if not k.startswith(("P2", "P3"))]
        if all(harm_ok):
            obj = min(("C0", "CAL"), key=lambda a: primary["objects"][a]["ll"])
            if all(object_checks(primary, other, obj).values()):
                verdict = "SHRINKAGE_ONLY"
            else:
                obj = None
    return {
        "checks": c,
        "pass": all(c.values()),
        "verdict": verdict,
        "served_object": obj,
        "object_checks": {a: object_checks(primary, other, a) for a in ("CAL", "C0")},
    }


# --------------------------------------------------------------------------- gate


def chk(name: str, value: Any, threshold: str, ok: Any, fatal: bool = True) -> dict[str, Any]:
    return {"test": name, "value": value, "threshold": threshold, "ok": bool(ok), "fatal": fatal}


def planted_future(D: dict[str, Any], y: np.ndarray, season: int) -> dict[str, Any]:
    """For every block: labels dated on/after the block start replaced by garbage leave that block's output identical;
    flipping labels inside the window moves it (positive control). y must be synthetic."""
    probes = changed_ok = identical_ok = 0
    bad: list[str] = []
    for book in BOOKS:
        tgt = (D["season"] == season) & (D["book"] == book)
        for b0 in np.unique(D["mstart"][tgt]):
            sc = tgt & (D["mstart"] == b0)
            later = D["date"] >= b0
            y_g = y.copy()
            y_g[later] = 1.0 - y_g[later]
            y_c = y.copy()
            y_c[~later] = 1.0 - y_c[~later]
            for arm in ("cal", "base", "shuffle"):
                base_p, _ = walk_forward(D, y, season, book, arm, only_block=b0)
                g_p, _ = walk_forward(D, y_g, season, book, arm, only_block=b0)
                c_p, _ = walk_forward(D, y_c, season, book, arm, only_block=b0)
                probes += 1
                same = np.array_equal(base_p[sc], g_p[sc])
                moved = not np.array_equal(base_p[sc], c_p[sc])
                identical_ok += same
                changed_ok += moved
                if not same or not moved:
                    bad.append(f"{book} {str(b0)[:7]} {arm} same={same} moved={moved}")
    return {
        "probes": probes,
        "identical": identical_ok,
        "positive_control_moved": changed_ok,
        "bad": bad,
    }


def a0_reproduction(
    D: dict[str, Any], res_dir: Path = ODDS_RESULTS
) -> tuple[bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    ok = True
    for s in FIT_SEASONS:
        ref = json.loads((res_dir / f"results_{s}.json").read_text())["h1"]
        for book in BOOKS:
            for stat in STATS:
                m = (D["season"] == s) & (D["book"] == book) & (D["stat"] == stat)
                mine = float(ll(np.clip(D["pm"][m], EPS, 1 - EPS), D["over"][m]).mean())
                cell = ref[f"t60/{stat}/{book}"]
                good = abs(mine - cell["ll_model"]) < 1e-9 and int(m.sum()) == int(cell["n"])
                ok &= good
                rows.append(
                    {
                        "season": s,
                        "book": book,
                        "stat": stat,
                        "n": int(m.sum()),
                        "n_ref": cell["n"],
                        "ll": mine,
                        "ll_ref": cell["ll_model"],
                        "ok": good,
                    }
                )
    return ok, rows


def run_gate(
    pairs_dir: Path, sha: str, draft: bool, out_dir: Path = OUT_DIR, k_null: int = K_NULL
) -> dict[str, Any]:
    t0 = time.time()
    _log(lock_note())
    D, shas = load_real(pairs_dir)
    _log(f"loaded {D['n']} t60 legs (2023-2024)")
    tests: list[dict[str, Any]] = []
    counts: dict[str, Any] = {}
    for s in FIT_SEASONS:
        for b in BOOKS:
            m = (D["season"] == s) & (D["book"] == b)
            counts[f"{s} {b}"] = {
                "legs": int(m.sum()),
                "games": int(len(np.unique(D["game_id"][m]))),
                "per_stat": {st: int((m & (D["stat"] == st)).sum()) for st in STATS},
                "months": [str(x)[:7] for x in np.unique(D["mstart"][m])],
            }
    sel = counts[f"{SELECT_SEASON} {PRIMARY_BOOK}"]
    tests.append(
        chk("1 Pinnacle 2024 legs >= 20,000", sel["legs"], ">= 20000", sel["legs"] >= 20000)
    )
    tests.append(
        chk(
            "1 Pinnacle 2024 legs per stat >= 5,000",
            min(sel["per_stat"].values()),
            ">= 5000",
            min(sel["per_stat"].values()) >= 5000,
        )
    )
    # every 2024 block has a full fit window (no identity rows in the select stage), both books
    y0 = synthetic_labels(D, 7)
    ident = {}
    for b in BOOKS:
        _, info = walk_forward(D, y0, SELECT_SEASON, b, "base")
        ident[b] = {
            "identity_blocks": sum(r["identity"] for r in info),
            "blocks": len(info),
            "min_fit": min(r["n_fit"] for r in info),
            "min_n_eff": min(r["n_eff"] for r in info if "n_eff" in r),
        }
    tests.append(
        chk(
            "1 2024 blocks with < 2,000 fit rows (identity), both books",
            sum(v["identity_blocks"] for v in ident.values()),
            "== 0",
            all(v["identity_blocks"] == 0 for v in ident.values()),
        )
    )
    finite = bool(
        np.all(np.isfinite(D["pm"]))
        and np.all(np.isfinite(D["med"]))
        and np.all(np.isfinite(D["spr"]))
        and np.all(np.isfinite(D["line"]))
    )
    n_st_nan = int((~np.isfinite(D["st10"])).sum())
    tests.append(
        chk(
            "2 p_model, continuous median and spread, line finite on every leg",
            finite,
            "True",
            finite,
        )
    )
    gd = D["grid_diag"]
    n_inc = sum(v["inconsistent_blocks"] for v in gd.values())
    wmax = max(v["max_width_needed"] for v in gd.values())
    n_mis = sum(v["p_cont_mismatch"] for v in gd.values())
    tests.append(
        chk(
            "2g continuous grid: blocks whose survival counts admit no single zq grid",
            n_inc,
            "== 0",
            n_inc == 0,
        )
    )
    tests.append(
        chk(
            "2g continuous grid: widest bracket of a needed grid point (z units)",
            round(wmax, 5),
            f"<= {ZQ_WIDTH_MAX}",
            wmax <= ZQ_WIDTH_MAX,
        )
    )
    tests.append(
        chk(
            "2g continuous-grid P(over line) equals the served integer-grid p_model (x.5 lines), legs differing",
            n_mis,
            "== 0",
            n_mis == 0,
        )
    )
    tests.append(chk("2 starter10 finite on every leg", n_st_nan, "== 0", n_st_nan == 0))
    tests.append(
        chk(
            "2 t60 snapshot >= 60 min before the real tip, 100% of legs",
            float(np.mean(D["lead_min"] >= 60)),
            "== 1.0",
            bool(np.all(D["lead_min"] >= 60)),
        )
    )
    tests.append(
        chk(
            "2 every line x.5 (no pushes)",
            bool(np.all(D["line"] % 1 == 0.5)),
            "True",
            bool(np.all(D["line"] % 1 == 0.5)),
        )
    )
    ok3, rows3 = a0_reproduction(D)
    tests.append(
        chk(
            "3 A0 log loss and n equal ODDS_HISTORY h1 (16 cells, |diff| < 1e-9)",
            sum(r["ok"] for r in rows3),
            "== 16",
            ok3,
        )
    )
    _log(f"checks 1-3 done {time.time() - t0:.0f}s")
    pf = planted_future(D, synthetic_labels(D, 11), SELECT_SEASON)
    tests.append(
        chk(
            "4 planted future: later labels flipped leave every block identical (synthetic labels)",
            f"{pf['identical']}/{pf['probes']}",
            "all",
            pf["identical"] == pf["probes"],
        )
    )
    tests.append(
        chk(
            "4 positive control: flipping in-window labels moves every block",
            f"{pf['positive_control_moved']}/{pf['probes']}",
            "all",
            pf["positive_control_moved"] == pf["probes"],
        )
    )
    _log(f"check 4 done {time.time() - t0:.0f}s")
    # 5 power on the real row and game structure, synthetic labels (no real outcome enters a CAL fit)
    ys = synthetic_labels(D, 5)
    syn = score_stage(D, ys, SELECT_SEASON, PRIMARY_BOOK, k_null=0)
    hw = (syn["d_cal_a0"]["hi"] - syn["d_cal_a0"]["lo"]) / 2
    hw_stat = {s: (r["hi"] - r["lo"]) / 2 for s, r in syn["per_stat"].items()}
    # design effect of a contrast of two FIXED arms on REAL labels (coin minus A0; public since T477 / Devin's table)
    m = (D["season"] == SELECT_SEASON) & (D["book"] == PRIMARY_BOOK)
    o = D["over"][m]
    v = ll(np.full(len(o), 0.5), o) - ll(np.clip(D["pm"][m], EPS, 1 - EPS), o)
    gi, G = dense(D["game_id"][m])
    rec = Boot(gi, G).mean(v, np.ones(len(o), bool))
    deff = float(rec["se"] ** 2 / (v.var(ddof=1) / len(v)))
    tests.append(
        chk(
            "5 projected 95% half-width of CAL-A0 (Pinnacle 2024, synthetic truth) <= 0.0015 (half the floor)",
            round(hw, 5),
            "<= 0.0015",
            hw <= 0.0015,
        )
    )
    _log(f"check 5 done {time.time() - t0:.0f}s")
    # 6 permutation null on REAL 2024 Pinnacle labels: shuffled-label fits only (no CAL fit on real labels)
    D_null = score_null(D, SELECT_SEASON, PRIMARY_BOOK, k_null)
    tests.append(
        chk(
            f"6 permutation null measured (K = {k_null}), sd finite",
            D_null["sd"],
            "finite",
            np.isfinite(D_null["sd"]),
        )
    )
    _log(f"check 6 done {time.time() - t0:.0f}s")
    res: dict[str, Any] = {
        "status": "GATE_PASSED"
        if all(t["ok"] or not t["fatal"] for t in tests)
        else "STOPPED_AT_GATE",
        "draft": draft,
        "sha256": sha,
        "pairs_sha256": shas,
        "counts": counts,
        "identity": ident,
        "grid_diag": D["grid_diag"],
        "z_summary": {
            str(s): {
                q: float(
                    np.quantile(
                        ((D["line"] - D["med"]) / np.maximum(D["spr"], SPREAD_MIN))[
                            D["season"] == s
                        ],
                        q,
                    )
                )
                for q in (0.05, 0.25, 0.5, 0.75, 0.95)
            }
            | {"spread_floored_share": float(np.mean(D["spr"][D["season"] == s] < SPREAD_MIN))}
            for s in FIT_SEASONS
        },
        "a0_reproduction": rows3,
        "planted_future": pf,
        "power": {
            "synthetic_truth": f"logit p = {SYN_A} + {SYN_B} logit p_model",
            "syn_d_cal_a0": syn["d_cal_a0"],
            "syn_d_cal_a0_date": syn["d_cal_a0_date"],
            "half_width": hw,
            "half_width_per_stat": hw_stat,
            "syn_d_cal_c0": syn["d_cal_c0"],
            "design_effect_real_coin_minus_a0": deff,
            "legs": int(m.sum()),
            "games": G,
            "legs_per_game": float(m.sum() / G),
        },
        "null": D_null,
        "tests": tests,
        "gate_ok": all(t["ok"] or not t["fatal"] for t in tests),
        "max_season_loaded": int(D["season"].max()),
        "lock": lock_note(),
        "unstated_details": UNSTATED,
        "seconds": time.time() - t0,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ("gate_draft.json" if draft else "gate.json")).write_text(
        json.dumps(res, indent=1, default=str)
    )
    return res


def score_null(D: dict[str, Any], season: int, book: str, k_null: int) -> dict[str, Any]:
    """Shuffled-label CAL fits scored on the season's labels: the null mean and the across-seed sd."""
    y = D["over"]
    m = (D["season"] == season) & (D["book"] == book)
    o = y[m]
    a0 = ll(np.clip(D["pm"][m], EPS, 1 - EPS), o).mean()
    means = []
    for k in range(k_null):
        p, _ = walk_forward(D, y, season, book, "shuffle", k)
        means.append(float(ll(p[m], o).mean()))
    arr = np.array(means)
    return {
        "k": k_null,
        "sh_minus_a0_mean": float(arr.mean() - a0),
        "sd": float(arr.std(ddof=1)),
        "sh_means": means,
    }


# --------------------------------------------------------------------------- doc declarations


def declared(doc: Path) -> dict[str, Any]:
    head = re.split(r"^=== RESULTS BELOW ===$", doc.read_text(), maxsplit=1, flags=re.M)[0]
    out: dict[str, Any] = {}
    for s in FIT_SEASONS:
        mm = re.search(rf"^PAIRS_SHA256_{s}: ([0-9a-f]{{64}})$", head, re.M)
        out[f"pairs_sha_{s}"] = mm.group(1) if mm else None
        mm = re.search(rf"^MODEL_ROWS_SHA256_{s}: ([0-9a-f]{{64}})$", head, re.M)
        out[f"model_rows_sha_{s}"] = mm.group(1) if mm else None
    mm = re.search(r"^NULL_SD_PINNACLE_2024: ([0-9.eE+-]+)$", head, re.M)
    out["null_sd"] = float(mm.group(1)) if mm else None
    return out


# --------------------------------------------------------------------------- stages


def run_stage(
    D: dict[str, Any], y: np.ndarray, season: int, k_null: int = K_NULL
) -> dict[str, Any]:
    t0 = time.time()
    res: dict[str, Any] = {}
    for b in BOOKS:
        res[b] = score_stage(D, y, season, b, k_null)
        _log(f"{season} {b} scored {time.time() - t0:.0f}s")
    res["rule"] = evaluate(res[PRIMARY_BOOK], res["draftkings"])
    return res


def rehearse(
    pairs_dir: Path, out_dir: Path = OUT_DIR / "rehearsal", k_null: int = K_NULL
) -> dict[str, Any]:
    """The select/report code path end to end with 2024 standing in for 2025 and SYNTHETIC labels on every row."""
    _log(lock_note())
    D, shas = load_real(pairs_dir)
    y = synthetic_labels(D, 2025)
    res = run_stage(D, y, SELECT_SEASON, k_null)
    res["labels"] = (
        f"SYNTHETIC (logit p = {SYN_A} + {SYN_B} logit p_model, seed 2025); no real outcome"
    )
    res["pairs_sha256"] = shas
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def select(pairs_dir: Path, doc: Path, sha: str, out_dir: Path = OUT_DIR) -> dict[str, Any]:
    rule.require_frozen(sha, doc)
    g = run_gate(pairs_dir, sha, False, out_dir)
    if not g["gate_ok"]:
        raise SystemExit("GATE FAILED: stop, no CAL result")
    dec = declared(doc)
    for s in FIT_SEASONS:
        if dec[f"pairs_sha_{s}"] != g["pairs_sha256"][str(s)]:
            raise SystemExit(f"pairs_{s}.parquet sha256 does not match the frozen doc")
        if dec[f"model_rows_sha_{s}"] != g["pairs_sha256"][f"model_rows_{s}"]:
            raise SystemExit(f"model_rows_{s}.parquet sha256 does not match the frozen doc")
    if dec["null_sd"] is None or abs(dec["null_sd"] - g["null"]["sd"]) > 1e-12:
        raise SystemExit("the null sd does not reproduce the frozen NULL_SD_PINNACLE_2024 line")
    D, _ = load_real(pairs_dir)
    res = run_stage(D, D["over"], SELECT_SEASON)
    res |= {"sha256": sha, "stage": "select", "season": SELECT_SEASON}
    (out_dir / "select.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def stage2_data_check(D: dict[str, Any]) -> list[str]:
    """Inside the touch, before any arm is scored: the Stage 2 data minimums (no outcome is read)."""
    m = D["season"] == REPORT_SEASON
    pin = m & (D["book"] == PRIMARY_BOOK)
    bad: list[str] = []
    if int(pin.sum()) < 20000:
        bad.append(f"Pinnacle 2025 legs {int(pin.sum())} < 20000")
    if not np.all(D["lead_min"][m] >= 60):
        bad.append("a 2025 T-60 snapshot is less than 60 min before tip")
    if not np.all(D["line"][m] % 1 == 0.5):
        bad.append("a 2025 line is not x.5")
    zeros = np.zeros(D["n"])  # labels are irrelevant to the fit-size count
    for b in BOOKS:
        _, info = walk_forward(D, zeros, REPORT_SEASON, b, "base")
        n_id = sum(r["identity"] for r in info)
        if n_id:
            bad.append(f"{b}: {n_id} identity blocks in 2025")
    return bad


def report(
    pairs_dir: Path,
    pairs_2025: Path,
    doc: Path,
    sha: str,
    out_dir: Path = OUT_DIR,
    grid_2025: Path | None = None,
    model_dir: Path = MODEL_DIR,
) -> dict[str, Any]:
    rule.require_ready(sha, doc=doc)
    sel = json.loads((out_dir / "select.json").read_text())
    if sel.get("sha256") != sha or sel["rule"]["verdict"] not in ("CALIBRATED", "SHRINKAGE_ONLY"):
        raise SystemExit(
            "no CALIBRATED or SHRINKAGE_ONLY select verdict under this sha: the 2025 touch is not earned"
        )
    if grid_2025 is None:
        raise SystemExit("STOP: --grid-2025 <parquet> (production OOF rows of 2025) is required")
    frames = [
        attach_grid(
            read_pairs(pairs_dir / f"pairs_{s}.parquet", s),
            read_grid(model_dir / f"model_rows_{s}.parquet", s),
        )[0]
        for s in FIT_SEASONS
    ]
    f25, diag25 = load_holdout(pairs_2025, grid_2025, sha)
    frames.append(f25)
    D = to_arrays(frames)
    stop = stage2_data_check(D)
    if (
        diag25["inconsistent_blocks"]
        or diag25["p_cont_mismatch"]
        or diag25["max_width_needed"] > ZQ_WIDTH_MAX
    ):
        stop.append(f"2025 continuous grid check failed: {diag25}")
    if stop:
        (out_dir / "report_2025.json").write_text(
            json.dumps({"sha256": sha, "stopped_unscored": stop}, indent=1)
        )
        raise SystemExit(f"STAGE 2 STOPPED UNSCORED (touch spent): {stop}")
    res = run_stage(D, D["over"], REPORT_SEASON)
    obj = sel["rule"]["served_object"]
    oc = object_checks(res[PRIMARY_BOOK], res["draftkings"], obj)
    res["confirmation"] = {
        "stage1_verdict": sel["rule"]["verdict"],
        "served_object": obj,
        "object_checks_2025": oc,
        "confirmed": bool(
            all(oc.values())
            and (
                sel["rule"]["verdict"] == "SHRINKAGE_ONLY" or res["rule"]["verdict"] == "CALIBRATED"
            )
        ),
    }
    res |= {
        "sha256": sha,
        "stage": "report",
        "season": REPORT_SEASON,
        "pairs_2025_sha256": file_sha(pairs_2025),
        "grid_2025_sha256": file_sha(grid_2025),
        "grid_2025_diag": diag25,
    }
    (out_dir / "report_2025.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("stage", choices=["gate", "rehearse", "select", "report"])
    ap.add_argument("--doc", type=Path, default=DOC)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--allow-draft", action="store_true")
    ap.add_argument("--pairs-dir", type=Path, default=PAIRS_DIR)
    ap.add_argument("--pairs-2025", type=Path, default=None)
    ap.add_argument("--grid-2025", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.allow_draft and a.stage != "gate":
        raise SystemExit("STOP: --allow-draft is for the gate stage only")
    if a.stage == "gate":
        sha = rule.frozen_sha(a.doc) if a.allow_draft else rule.require_frozen(a.sha, a.doc)
        g = run_gate(a.pairs_dir, sha, a.allow_draft)
        for t in g["tests"]:
            print(
                ("PASS " if t["ok"] else ("WARN " if not t["fatal"] else "FAIL ")) + t["test"],
                t["value"],
                flush=True,
            )
        print(
            json.dumps(
                {k: g[k] for k in ("sha256", "gate_ok", "pairs_sha256")}
                | {"null_sd": g["null"]["sd"], "half_width": g["power"]["half_width"]},
                indent=1,
            )
        )
        return 0 if g["gate_ok"] else 1
    if a.stage == "rehearse":
        r = rehearse(a.pairs_dir)
        print(json.dumps(r["rule"], indent=1))
        return 0
    if a.stage == "select":
        r = select(a.pairs_dir, a.doc, a.sha)
        print(json.dumps(r["rule"], indent=1))
        return 0
    if a.pairs_2025 is None:
        raise SystemExit("STOP: --pairs-2025 <parquet> is required for the report stage")
    r = report(a.pairs_dir, a.pairs_2025, a.doc, a.sha, grid_2025=a.grid_2025)
    print(json.dumps(r["rule"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
