# ruff: noqa: E501
"""TAIL_LEG_CAL harness (rule: docs/prereg/TAIL_LEG_CAL.md).

    check     counts-only preflight on the real legs (no tail-stage label is attached)
    rehearse  synthetic tail-stage labels only; allowed on the DRAFT; writes a rehearsal report
    run       real labels; refuses unless the rule is frozen AND the harness is pinned (``tail_leg_cal_rule.require_ready``)

    <python> -m research.eval.tail_leg_cal_eval check    --data-root <main checkout>
    <python> -m research.eval.tail_leg_cal_eval rehearse --data-root <main checkout> --truth smooth|step|calibrated|flat
    <python> -m research.eval.tail_leg_cal_eval run      --data-root <main checkout> --sha <64 hex>

What this implements, sentence by sentence of the rule:
* the legs are LINE_CALIBRATION's frozen CAL legs exactly as PARLAY_JOINT_CAL built them (``parlay_joint_cal_eval.load_cal``
  and ``book_legs``): the CAL-served side of every DraftKings / Pinnacle T-60 leg with q = max(p_cal, 1 - p_cal);
* the tail stage is a second walk-forward by calendar month on those out-of-block q values: fit rows are earlier tail legs
  (q >= 0.60) of the same book, weighted 0.5 ** (age_days / 365), identity below ``MIN_TAIL`` fit rows;
* arms: CAL (frozen), A0 (raw production on the CAL side), TAIL_H (hinge on the logit above the knee), TAIL_I (isotonic,
  floored at the knee), and two reference rows SHIFT and CONST that issue no word;
* guards raise (never ``assert``), season 2025 is never loaded, nothing but the legs' own q enters a map (no price, no stat).

Honest disclosure of the one label read before the tail stage: the CAL probabilities p_cal are produced by LINE_CALIBRATION's
walk-forward on the leg labels of seasons 2023-24 (already scored in T493 and T495). The tail stage cannot see a label of its own
until ``attach_labels``, which ``run`` calls only after ``require_ready`` and the count preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from research.eval import alt_harness_common as ahc
from research.eval import parlay_joint_cal_eval as pj
from research.eval import parlay_joint_cal_gate as pgate
from research.eval import tail_leg_cal_rule as rule

MAX_SEASON = 2024
SEASONS = (2023, 2024)
BOOKS = ("draftkings", "pinnacle")
PRIMARY = "draftkings"
KNEE = 0.60  # tail = CAL q >= KNEE (Devin's "q >= 0.60")
HIGH = 0.65  # the band the T495 attack measured
HALF_LIFE = 365.0
MIN_TAIL = 1000  # fewer earlier tail legs than this: identity (arm = CAL)
RIDGE = 1.0
BETA_MIN, BETA_MAX = (
    -0.95,
    1.0,
)  # slope above the knee stays in (0.05, 2.0): monotone by construction
QCLIP = 1e-6
K_NULL = 50
B_BOOT = 5000
SEED_GAME = 2026
SEED_DATE = 2027
GAP_LIMIT = 0.015  # P1: |gap| at q >= 0.60 (LINE_CALIBRATION P4 margin)
GAP_MARGIN = 0.03  # P1: clustered interval inside +- this (ALT_LADDER_HIST margin)
TOP_GAP_LO, TOP_GAP_HI = (
    -0.02,
    0.03,
)  # P3: gap at q >= 0.65, point only (the interval cannot resolve it; see the gate)
FLOOR_LL_65 = -0.002  # P2b: log loss on the q >= 0.65 band, arm minus CAL
FLOOR_LL = -0.0005  # P2: tail-row log loss, arm minus CAL
NULL_SD_MULT = 2.0  # P5
ALPHA_ONE = 0.025
ARMS = ("A0", "CAL", "TAIL_H", "TAIL_I", "SHIFT", "CONST")
WORD_ARMS = ("TAIL_H",)  # TAIL_I, SHIFT and CONST are labels
LABEL_ARMS_SLICE = ("TAIL_H", "TAIL_I")
MIN_STAT_TAIL = 400  # stats with fewer scored tail legs are not tested (pts has a handful)
NI_MARGIN = 0.0005  # P2n: the log-loss contrast must be non-inferior to CAL by this margin
SIDE_HARM = 0.0005
TOP_N = 3
STEP_GAPS = (
    (0.60, 0.65, -0.007),
    (0.65, 1.01, -0.040),
)  # T495 published, DraftKings pooled 2023-24 (the misspecified stress truth)
SMOOTH_SLOPE = -0.5  # the main oracle: gap = -0.5 * (q - 0.60) above the knee: -0.010 at q 0.62, -0.045 at q 0.69, close to T495
INPUT_PINS = dict(
    pj.INPUT_PINS
)  # pairs and model_rows (the gate.json pin of PARLAY_JOINT_CAL is dropped)
INPUT_PINS.pop("reports/prereg_parlay_joint_cal/gate.json", None)
OUT_DIR = Path("reports/prereg_tail_leg_cal")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------ guards (raise, never assert)


def guard_season(seasons: Any) -> None:
    if max(seasons) > MAX_SEASON:
        raise RuntimeError(
            "season 2025 is the frozen holdout and is never loaded under TAIL_LEG_CAL"
        )


def guard_fit_before(dates: np.ndarray, block_start: np.datetime64, what: str) -> None:
    if dates.size and bool((dates >= block_start).any()):
        raise RuntimeError(f"{what}: a fit row is dated on or after the block start {block_start}")


# ------------------------------------------------------------------------------ maps


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, QCLIP, 1 - QCLIP)
    return np.asarray(np.log(p / (1 - p)))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-x)))


K_LOGIT = float(np.log(KNEE / (1 - KNEE)))


def hinge_u(q: np.ndarray) -> np.ndarray:
    return np.maximum(0.0, logit(q) - K_LOGIT)


def fit_hinge(q: np.ndarray, hit: np.ndarray, w: np.ndarray) -> float:
    """One-parameter offset logistic: logit P(hit) = logit q + beta * max(0, logit q - logit KNEE); ridge on beta;
    beta clipped to [BETA_MIN, BETA_MAX]. Continuous at the knee and monotone for beta > -1."""
    off = logit(q)
    u = hinge_u(q)
    b = 0.0
    for _ in range(100):
        p = sigmoid(off + b * u)
        g = float(np.sum(w * u * (hit - p))) - RIDGE * b
        h = float(np.sum(w * u * u * p * (1 - p))) + RIDGE
        step = g / h
        b += step
        if abs(step) < 1e-12:
            break
    return float(np.clip(b, BETA_MIN, BETA_MAX))


def apply_hinge(q: np.ndarray, beta: float) -> np.ndarray:
    return np.asarray(np.where(q >= KNEE, sigmoid(logit(q) + beta * hinge_u(q)), q))


def pav(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators for a non-decreasing fit of ``y`` already ordered by the regressor."""
    vals: list[float] = []
    wts: list[float] = []
    cnt: list[int] = []
    for yi, wi in zip(y.tolist(), w.tolist(), strict=True):
        vals.append(yi)
        wts.append(wi)
        cnt.append(1)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            tw = wts[-2] + wts[-1]
            vals[-2] = (vals[-2] * wts[-2] + vals[-1] * wts[-1]) / tw
            wts[-2] = tw
            cnt[-2] += cnt[-1]
            vals.pop()
            wts.pop()
            cnt.pop()
    return np.repeat(np.array(vals), cnt)


def fit_iso(q: np.ndarray, hit: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic hit-on-q: knots (weighted mean q of each pooled block, fitted value). Ties in q are ordered by index."""
    order = np.lexsort((np.arange(len(q)), q))
    qs, ys, ws = q[order], hit[order], w[order]
    fit = pav(ys, ws)
    # collapse equal fitted values into knots at their weighted mean q
    kx: list[float] = []
    ky: list[float] = []
    i = 0
    while i < len(fit):
        j = i
        while j + 1 < len(fit) and fit[j + 1] == fit[i]:
            j += 1
        kx.append(float(np.sum(qs[i : j + 1] * ws[i : j + 1]) / np.sum(ws[i : j + 1])))
        ky.append(float(fit[i]))
        i = j + 1
    return np.array(kx), np.array(ky)


def apply_iso(q: np.ndarray, kx: np.ndarray, ky: np.ndarray) -> np.ndarray:
    """Linear interpolation between knots (flat beyond the ends), then floored at KNEE: q' = max(iso(q), KNEE) for q >= KNEE,
    q elsewhere. Non-decreasing overall, so the order of legs is never reversed."""
    v = np.full(len(q), ky[0]) if len(kx) == 1 else np.interp(q, kx, ky)
    return np.asarray(np.where(q >= KNEE, np.clip(np.maximum(v, KNEE), KNEE, 1 - QCLIP), q))


def age_weights(dates: np.ndarray, block_start: np.datetime64) -> np.ndarray:
    age = (block_start - dates).astype("timedelta64[D]").astype(np.float64)
    if not np.all(age > 0):
        raise RuntimeError("a fit row on or after the block start")
    return np.asarray(0.5 ** (age / HALF_LIFE))


def walk_forward_tail(
    date: np.ndarray, q: np.ndarray, hit: np.ndarray, k: int | None = None
) -> dict[str, Any]:
    """Second-stage walk-forward over calendar months of one book's legs (sorted by date). Returns the arm arrays
    (same length as ``q``), the scored mask (tail rows in non-identity blocks) and one record per block.

    ``k`` not None: SH_k, the fit labels of each block permuted among the fit tail legs (``default_rng([1000 + k, block])``)."""
    n = len(q)
    mstart = date.astype("datetime64[M]").astype("datetime64[D]")
    out = {a: q.copy() for a in ("TAIL_H", "TAIL_I", "SHIFT", "CONST")}
    scored = np.zeros(n, dtype=bool)
    recs: list[dict[str, Any]] = []
    tail = q >= KNEE
    for bi, b0 in enumerate(np.unique(mstart)):
        sc = (mstart == b0) & tail
        fit = (date < b0) & tail
        guard_fit_before(date[fit], b0, "tail fit")
        nfit = int(fit.sum())
        rec: dict[str, Any] = {"block": str(b0)[:7], "n_fit": nfit, "n_score": int(sc.sum())}
        if nfit < MIN_TAIL or sc.sum() == 0:
            rec["identity"] = True
            recs.append(rec)
            continue
        qf, yf, wf = q[fit], hit[fit], age_weights(date[fit], b0)
        if k is not None:
            yf = np.random.default_rng([1000 + k, bi]).permutation(yf)
        beta = fit_hinge(qf, yf, wf)
        kx, ky = fit_iso(qf, yf, wf)
        g = float(np.sum(wf * (yf - qf)) / np.sum(wf))
        c = float(np.sum(wf * yf) / np.sum(wf))
        out["TAIL_H"][sc] = apply_hinge(q[sc], beta)
        out["TAIL_I"][sc] = apply_iso(q[sc], kx, ky)
        out["SHIFT"][sc] = np.clip(q[sc] + g, QCLIP, 1 - QCLIP)
        out["CONST"][sc] = float(np.clip(c, QCLIP, 1 - QCLIP))
        scored |= sc
        rec.update(
            {"identity": False, "beta": beta, "n_knots": int(len(kx)), "shift": g, "const": c}
        )
        recs.append(rec)
    return {"arms": out, "scored": scored, "blocks": recs}


# ------------------------------------------------------------------------------ legs


def legs_prefit(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, np.ndarray]]:
    """The CAL legs per book: arrays sorted by (game_date, game_id, player_id, stat), WITHOUT any outcome column, and the
    leg outcomes returned separately (``hits``): only ``attach_labels(mode='real')`` hands them on.
    The CAL probabilities are LINE_CALIBRATION's frozen walk-forward output (its own fit labels are the T493 leg labels)."""
    guard_season(SEASONS)
    lab, ident = pj.load_cal(root, True, None)
    out: dict[str, dict[str, Any]] = {}
    hits: dict[str, np.ndarray] = {}
    for book in BOOKS:
        legs, _ = pj.book_legs(root, book, lab, ident)
        df = pl.concat([legs[s] for s in SEASONS], how="vertical_relaxed").sort(
            ["game_date", "game_id", "player_id", "stat"]
        )
        d: dict[str, Any] = {
            "date": df["game_date"].to_numpy().astype("datetime64[D]"),
            "game": df["game_id"].to_numpy(),
            "pid": df["player_id"].to_numpy(),
            "stat": df["stat"].to_numpy().astype(str),
            "season": df["season"].to_numpy().astype(int),
            "q": df["q_cal"].to_numpy().astype(float),
            "qraw": df["q_raw_side"].to_numpy().astype(float),
            "over": df["side_over"].to_numpy().astype(bool),
        }
        out[book] = d
        hits[book] = df["hit_leg"].to_numpy().astype(float)
    return out, hits


def fingerprint(q: np.ndarray) -> str:
    return hashlib.sha256(np.round(q, 9).astype("<f8").tobytes()).hexdigest()


def counts(legs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for book, d in legs.items():
        for s in SEASONS:
            m = d["season"] == s
            q = d["q"][m]
            out[f"{book}|{s}"] = {
                "legs": int(m.sum()),
                "ge60": int((q >= KNEE).sum()),
                "ge65": int((q >= HIGH).sum()),
                "ge70": int((q >= 0.70).sum()),
                "games": int(len(np.unique(d["game"][m]))),
                "dates": int(len(np.unique(d["date"][m]))),
                "q_fingerprint": fingerprint(q),
            }
    return out


def attach_labels(
    legs: dict[str, dict[str, Any]],
    hits: dict[str, np.ndarray],
    mode: str,
    truth: str | None = None,
) -> dict[str, np.ndarray]:
    """The tail-stage outcome per book. ``real``: the leg labels. ``synthetic``: one uniform per leg, hit = U < truth(q).
    Only ``run`` (after ``require_ready``) may call ``mode='real'``."""
    out: dict[str, np.ndarray] = {}
    for book, d in legs.items():
        if mode == "real":
            out[book] = hits[book].astype(float)
        else:
            out[book] = synthetic_hit(d, truth or "calibrated", seed=[2026, 31, BOOKS.index(book)])
    return out


def truth_prob(q: np.ndarray, truth: str) -> np.ndarray:
    """The synthetic hit probability given the CAL shown probability q (rehearsal and gate only; no real outcome)."""
    if truth == "calibrated":
        return q
    if truth == "smooth":
        return np.asarray(np.where(q >= KNEE, q + SMOOTH_SLOPE * (q - KNEE), q))
    if truth == "step":
        t = q.copy()
        for lo, hi, gap in STEP_GAPS:
            m = (q >= lo) & (q < hi)
            t[m] = q[m] + gap
        return t
    if truth == "flat":
        return np.asarray(np.where(q >= KNEE, 0.585, q))  # no information inside the tail
    raise RuntimeError(f"unknown truth {truth}")


def synthetic_hit(d: dict[str, Any], truth: str, seed: list[int]) -> np.ndarray:
    q = d["q"]
    u = np.random.default_rng(seed).random(len(q))
    return np.asarray((u < truth_prob(q, truth)).astype(float))


# ------------------------------------------------------------------------------ statistics


def ll(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    pc = np.clip(p, QCLIP, 1 - QCLIP)
    return np.asarray(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)))


class Clusters:
    """Game and date resample matrices, one per season (strata), shared by every statistic."""

    def __init__(
        self, game: np.ndarray, date: np.ndarray, season: np.ndarray, b: int = B_BOOT
    ) -> None:
        self.season = season
        self.mat_game: dict[int, np.ndarray] = {}
        self.mat_date: dict[int, np.ndarray] = {}
        self.idx: dict[str, dict[int, np.ndarray]] = {"game": {}, "date": {}}
        self.n: dict[str, dict[int, int]] = {"game": {}, "date": {}}
        for name, key, seed in (("game", game, SEED_GAME), ("date", date, SEED_DATE)):
            sizes = {}
            for s in SEASONS:
                m = season == s
                u, inv = np.unique(key[m], return_inverse=True)
                self.idx[name][s] = inv
                self.n[name][s] = len(u)
                sizes[s] = len(u)
            if name == "game":
                self.mat_game = ahc.make_boot(sizes, b, seed)
            else:
                self.mat_date = ahc.make_boot(sizes, b, seed)

    def stat(self, mask: np.ndarray, values: np.ndarray, name: str) -> tuple[float, np.ndarray]:
        parts = []
        mats = self.mat_game if name == "game" else self.mat_date
        for s in SEASONS:
            ms = self.season == s
            sel = mask[ms]
            ix = self.idx[name][s]
            v = values[ms]
            num = ahc.cluster_totals(ix, self.n[name][s], np.where(sel, v, 0.0))
            den = ahc.cluster_totals(ix, self.n[name][s], sel.astype(float))
            parts.append((mats[s], num, den))
        return ahc.boot_ratio(parts)

    def summary(self, mask: np.ndarray, values: np.ndarray) -> dict[str, Any]:
        pg, bg = self.stat(mask, values, "game")
        pd_, bd = self.stat(mask, values, "date")
        lg = ahc.percentile_interval(bg)
        ld = ahc.percentile_interval(bd)
        return {
            "n": int(mask.sum()),
            "point": pg,
            "game": [lg[0], lg[1]],
            "date": [ld[0], ld[1]],
            "lo": float(min(lg[0], ld[0])),
            "hi": float(max(lg[1], ld[1])),
            "p_neg": float(
                max(ahc.p_le0(-bg), ahc.p_le0(-bd))
            ),  # one-sided p for "the statistic is < 0"
            "p_pos": float(max(ahc.p_le0(bg), ahc.p_le0(bd))),
        }

    def slope(self, mask: np.ndarray, x: np.ndarray, y: np.ndarray) -> dict[str, Any]:
        """OLS slope of the hit on the shown probability over ``mask``, game-clustered (seasons are strata); label only."""
        parts = []
        for s in SEASONS:
            ms = self.season == s
            sel = mask[ms]
            ix = self.idx["game"][s]
            xs, ys = x[ms], y[ms]
            cols = [
                ahc.cluster_totals(ix, self.n["game"][s], np.where(sel, c, 0.0))
                for c in (np.ones_like(xs), xs, ys, xs * xs, xs * ys)
            ]
            parts.append((self.mat_game[s], cols))
        tot = [sum(float(c[i].sum()) for _, c in parts) for i in range(5)]
        n, sx, sy, sxx, sxy = tot
        point = float((sxy - sx * sy / n) / (sxx - sx * sx / n)) if n > 0 else float("nan")
        b = parts[0][0].shape[0]
        boot = np.zeros((b, 5))
        for m, cols in parts:
            for i in range(5):
                boot[:, i] += m @ cols[i]
        with np.errstate(divide="ignore", invalid="ignore"):
            sb = (boot[:, 4] - boot[:, 1] * boot[:, 2] / boot[:, 0]) / (
                boot[:, 3] - boot[:, 1] ** 2 / boot[:, 0]
            )
        lo, hi = ahc.percentile_interval(sb)
        return {"point": point, "lo": lo, "hi": hi, "n": int(mask.sum())}


def bh_adjust(p: list[float]) -> list[float]:
    return ahc.bh_q(p)


# ------------------------------------------------------------------------------ scoring one book


def shown(arms: dict[str, np.ndarray], d: dict[str, Any]) -> dict[str, np.ndarray]:
    return {
        "A0": np.clip(d["qraw"], QCLIP, 1 - QCLIP),
        "CAL": d["q"],
        "TAIL_H": arms["TAIL_H"],
        "TAIL_I": arms["TAIL_I"],
        "SHIFT": arms["SHIFT"],
        "CONST": arms["CONST"],
    }


def construction_checks(d: dict[str, Any], wf: dict[str, Any]) -> dict[str, Any]:
    """Properties that hold by construction; the run refuses if any fails."""
    q = d["q"]
    res: dict[str, Any] = {}
    below = q < KNEE
    for a in ("TAIL_H", "TAIL_I"):
        res[f"{a}_below_knee_unchanged"] = bool(np.array_equal(wf["arms"][a][below], q[below]))
        # monotone: sort by q, the arm must be non-decreasing within each block (one map per block)
        mono = True
        mstart = d["date"].astype("datetime64[M]")
        for b0 in np.unique(mstart):
            mk = mstart == b0
            o = np.argsort(q[mk], kind="stable")
            v = wf["arms"][a][mk][o]
            if np.any(np.diff(v) < -1e-12):
                mono = False
        res[f"{a}_monotone_within_block"] = mono
    if not all(res.values()):
        raise RuntimeError(f"construction check failed: {res}")
    return res


def score_book(
    d: dict[str, Any], hit: np.ndarray, cl: Clusters, k_null: int, label: str
) -> dict[str, Any]:
    wf = walk_forward_tail(d["date"], d["q"], hit)
    cons = construction_checks(d, wf)
    sc = wf["scored"]
    sh = shown(wf["arms"], d)
    q = d["q"]
    r60 = sc
    r65 = sc & (q >= HIGH)
    rband = sc & (q < HIGH)
    res: dict[str, Any] = {
        "label": label,
        "construction": cons,
        "blocks": wf["blocks"],
        "n_scored_tail": int(sc.sum()),
    }
    res["n_dropped_tail_identity"] = int(((q >= KNEE) & ~sc).sum())
    sets = {"r60": r60, "r65": r65, "r60_65": rband}
    dll = {a: ll(sh[a], hit) - ll(sh["CAL"], hit) for a in ARMS}
    out: dict[str, Any] = {}
    for sname, m in sets.items():
        o: dict[str, Any] = {}
        for a in ARMS:
            o[a] = {
                "gap": cl.summary(m, hit - sh[a]),
                "mean_shown": float(np.mean(sh[a][m])) if m.any() else float("nan"),
                "mean_hit": float(np.mean(hit[m])) if m.any() else float("nan"),
                "ll": float(np.mean(ll(sh[a], hit)[m])) if m.any() else float("nan"),
            }
            if a != "CAL":
                o[a]["ll_minus_cal"] = cl.summary(m, dll[a])
        out[sname] = o
    res["sets"] = out
    # everything in the blocks where the tail map was fit: the rest is unchanged by construction, so this is the tail contrast diluted
    ok_blocks = {r["block"] for r in wf["blocks"] if not r["identity"]}
    blk = np.isin(d["date"].astype("datetime64[M]").astype(str), list(ok_blocks))
    res["all_legs_in_scored_blocks"] = {
        a: cl.summary(blk, dll[a]) for a in ARMS if a not in ("CAL",)
    }
    sl: dict[str, Any] = {}
    for s in SEASONS:
        for band, mb in (("r60", r60), ("r65", r65)):
            m = mb & (d["season"] == s)
            sl[f"season_{s}_{band}"] = {
                a: {
                    "gap": float(np.mean((hit - sh[a])[m])) if m.any() else float("nan"),
                    "ll_minus_cal": float(np.mean(dll[a][m])) if m.any() else float("nan"),
                    "n": int(m.sum()),
                }
                for a in ("CAL", *LABEL_ARMS_SLICE)
            }
    for name, key in (("stat", d["stat"]), ("side", np.where(d["over"], "over", "under"))):
        for v in np.unique(key):
            m = r60 & (key == v)
            entry: dict[str, Any] = {
                a: {
                    "gap": float(np.mean((hit - sh[a])[m])) if m.any() else float("nan"),
                    "ll_minus_cal": float(np.mean(dll[a][m])) if m.any() else float("nan"),
                    "n": int(m.sum()),
                }
                for a in ("CAL", *LABEL_ARMS_SLICE)
            }
            if int(m.sum()) >= MIN_STAT_TAIL:
                entry["TAIL_H_contrast"] = cl.summary(m, dll["TAIL_H"])
            sl[f"{name}_{v}"] = entry
    res["slices"] = sl
    res["reliability_r60"] = reliability(sh, hit, r60)
    res["slope_r60"] = {a: cl.slope(r60, sh[a], hit) for a in ("CAL", "TAIL_H")}
    if k_null > 0:
        null: dict[str, list[float]] = {a: [] for a in ("TAIL_H", "TAIL_I")}
        nulls_gap: dict[str, list[float]] = {a: [] for a in ("TAIL_H", "TAIL_I")}
        for k in range(k_null):
            w2 = walk_forward_tail(d["date"], d["q"], hit, k=k)
            if not np.array_equal(w2["scored"], sc):
                raise RuntimeError("the shuffled null changed the scored rows")
            for a in null:
                null[a].append(float(np.mean((ll(w2["arms"][a], hit) - ll(sh["CAL"], hit))[sc])))
                nulls_gap[a].append(float(np.mean((hit - w2["arms"][a])[sc])))
            if (k + 1) % 10 == 0:
                _log(f"{label}: shuffled null {k + 1}/{k_null}")
        res["null"] = {
            a: {
                "mean_ll_minus_cal": float(np.mean(null[a])),
                "sd": float(np.std(null[a], ddof=1)) if len(null[a]) > 1 else float("nan"),
                "mean_gap": float(np.mean(nulls_gap[a])),
            }
            for a in null
        }
    res["top"] = top_slice(d, hit, wf, cl, label)
    return res


def reliability(sh: dict[str, np.ndarray], hit: np.ndarray, m: np.ndarray) -> dict[str, Any]:
    edges = [KNEE, 0.62, 0.64, 0.66, 0.70, 0.75, 1.01]
    out: dict[str, Any] = {}
    for a in ("CAL", *WORD_ARMS):
        rows = []
        for lo, hi in itertools.pairwise(edges):
            mm = m & (sh[a] >= lo) & (sh[a] < hi)
            rows.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "n": int(mm.sum()),
                    "shown": float(np.mean(sh[a][mm])) if mm.any() else None,
                    "hit": float(np.mean(hit[mm])) if mm.any() else None,
                }
            )
        out[a] = rows
    return out


def top_slice(
    d: dict[str, Any], hit: np.ndarray, wf: dict[str, Any], cl: Clusters, label: str
) -> dict[str, Any]:
    """Secondary, label only: the cross-game top-3 independent tickets per date and size, ranked by each arm's own product of
    leg probabilities (PARLAY_JOINT_CAL's construction; the CAL-served legs of scored blocks only). Ticket gap = hit minus shown."""
    sh = shown(wf["arms"], d)
    ok_blocks = {r["block"] for r in wf["blocks"] if not r["identity"]}
    sel = np.isin(d["date"].astype("datetime64[M]").astype(str), list(ok_blocks))
    ix = np.flatnonzero(sel)
    out: dict[str, Any] = {}
    for m_size in (2, 3):
        for a in ("CAL", *WORD_ARMS):
            idx, tdate = pgate.top_candidates(
                d["date"][ix], d["game"][ix], sh[a][ix], m_size, "cross"
            )
            if len(idx) == 0:
                continue
            p = np.prod(sh[a][ix][idx], axis=1)
            kp = pgate.keep_top(idx, tdate, p)
            ti, td = idx[kp], tdate[kp]
            gl = ix[ti]  # global leg indices
            tickets_hit = np.prod(hit[gl], axis=1)
            tshown = np.prod(sh[a][gl], axis=1)
            # cluster by date only (tickets span games); season of the ticket from its first leg
            seas = d["season"][gl[:, 0]]
            parts = []
            for s in SEASONS:
                ms = seas == s
                dates = np.unique(d["date"][d["season"] == s])
                pos = np.searchsorted(dates, td[ms])
                num = ahc.cluster_totals(pos, len(dates), (tickets_hit - tshown)[ms])
                den = ahc.cluster_totals(pos, len(dates))
                parts.append((cl.mat_date[s], num, den))
            pt, bt = ahc.boot_ratio(parts)
            lo, hi = ahc.percentile_interval(bt)
            out[f"{m_size}leg_{a}"] = {
                "n": int(len(kp)),
                "ticket_gap": pt,
                "lo": lo,
                "hi": hi,
                "mean_shown": float(np.mean(tshown)),
                "mean_hit": float(np.mean(tickets_hit)),
            }
    return out


# ------------------------------------------------------------------------------ checks and words


def evaluate(primary: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Fixed-order words from the pre-declared checks. ``primary`` is DraftKings, ``other`` Pinnacle. Only TAIL_H issues a word."""
    a = "TAIL_H"
    s60, s65 = primary["sets"]["r60"][a], primary["sets"]["r65"][a]
    g60, g65, l60 = s60["gap"], s65["gap"], s60["ll_minus_cal"]
    sl = primary["slices"]
    # P7: stats with enough scored tail legs, BH over them, "worse" one-sided; sides at the point
    stat_keys = [k for k in sl if k.startswith("stat_") and "TAIL_H_contrast" in sl[k]]
    pw = [sl[k]["TAIL_H_contrast"]["p_pos"] for k in stat_keys]
    qw = dict(zip(stat_keys, bh_adjust(pw), strict=True)) if pw else {}
    p7_stats = all(v >= 0.05 for v in qw.values())
    p7_sides = all(
        sl[f"side_{v}"][a]["ll_minus_cal"] <= SIDE_HARM
        for v in ("over", "under")
        if f"side_{v}" in sl
    )
    o60 = other["sets"]["r60"][a]
    cal65 = primary["sets"]["r65"]["CAL"]["gap"]
    checks = {
        "P1_gap60": bool(
            abs(g60["point"]) <= GAP_LIMIT and g60["lo"] >= -GAP_MARGIN and g60["hi"] <= GAP_MARGIN
        ),
        "P2n_ll60_noninferior": bool(l60["point"] <= 0 and l60["hi"] <= NI_MARGIN),
        "P3_gap65": bool(g65["lo"] <= 0 <= g65["hi"] and TOP_GAP_LO <= g65["point"] <= TOP_GAP_HI),
        "P4_seasons": all(
            sl[f"season_{s}_r60"][a]["ll_minus_cal"] <= 0
            and abs(sl[f"season_{s}_r60"][a]["gap"]) <= GAP_MARGIN
            and sl[f"season_{s}_r65"][a]["gap"] >= -GAP_MARGIN
            for s in SEASONS
        ),
        "P6_pinnacle": bool(
            o60["ll_minus_cal"]["point"] <= 0 and abs(o60["gap"]["point"]) <= GAP_MARGIN
        ),
        "P7_no_stat_or_side_harmed": bool(p7_stats and p7_sides),
    }
    sup = bool(l60["point"] <= FLOOR_LL and l60["p_neg"] < ALPHA_ONE)
    res: dict[str, Any] = {
        "checks": checks,
        "P2_ll60_superiority": sup,
        "P2b_ll65_superiority_label": bool(s65["ll_minus_cal"]["point"] <= FLOOR_LL_65 and s65["ll_minus_cal"]["p_neg"] < ALPHA_ONE),
        "gap60": g60, "gap65": g65, "ll_minus_cal_60": l60, "stat_bh_q_worse": qw,
    }  # fmt: skip
    res["P0_problem_exists"] = bool(
        cal65["hi"] < 0
    )  # CAL's own gap at q >= 0.65 on the scored rows has its upper bound below 0
    res["CAL_gap65"] = cal65
    if not res["P0_problem_exists"]:
        res["word"] = "NO_TAIL_PROBLEM"
    elif all(checks.values()) and sup:
        res["word"] = "TAIL_FIXES"
    elif all(checks.values()):
        res["word"] = "TAIL_GAP_FIXED_LL_NOT_SHOWN"
    else:
        res["word"] = "NOT_FIXED"
    return res


# ------------------------------------------------------------------------------ pipeline


def run_all(root: Path, mode: str, truth: str | None, k_null: int) -> dict[str, Any]:
    t0 = time.time()
    legs, hits = legs_prefit(root)
    _log(f"legs built ({time.time() - t0:.1f}s)")
    pre = counts(legs)
    labels = attach_labels(legs, hits, mode, truth)
    del hits
    cl_by: dict[str, Clusters] = {
        b: Clusters(legs[b]["game"], legs[b]["date"], legs[b]["season"]) for b in BOOKS
    }
    per: dict[str, Any] = {}
    for b in BOOKS:
        _log(f"scoring {b}")
        per[b] = score_book(legs[b], labels[b], cl_by[b], k_null if b == PRIMARY else 0, b)
    ev = evaluate(per[PRIMARY], per["pinnacle"])
    return {
        "mode": mode,
        "truth": truth,
        "counts": pre,
        "per_book": per,
        "evaluation": ev,
        "seconds": time.time() - t0,
    }


REQUIRED_DECLARED = (
    "PAIRS_SHA256_2023", "PAIRS_SHA256_2024", "MODEL_ROWS_SHA256_2023", "MODEL_ROWS_SHA256_2024", "GATE_SHA256",
    "QCAL_FP_DRAFTKINGS_2023", "QCAL_FP_DRAFTKINGS_2024", "QCAL_FP_PINNACLE_2023", "QCAL_FP_PINNACLE_2024",
)  # fmt: skip
DECLARED_FILES = {
    "PAIRS_SHA256_2023": "data/ops/model_clv_pairs/pairs_2023.parquet",
    "PAIRS_SHA256_2024": "data/ops/model_clv_pairs/pairs_2024.parquet",
    "MODEL_ROWS_SHA256_2023": "reports/odds_history/model_rows_2023.parquet",
    "MODEL_ROWS_SHA256_2024": "reports/odds_history/model_rows_2024.parquet",
}
PUBLISHED_TAIL = {  # T495 attack, docs/reviews/T495_post_result_2026-10-15.md: (legs at q >= 0.60, legs at q >= 0.65)
    "draftkings|2023": (4677, 1246),
    "draftkings|2024": (3906, 853),
    "pinnacle|2023": (2013, 415),
    "pinnacle|2024": (2486, 422),
}


def preflight(
    root: Path, require_declared: bool = False, doc: Path | None = None
) -> dict[str, Any]:
    """Counts only (no tail-stage label): the pinned input hashes, T495's published tail counts, and the rule's own pins.
    ``require_declared``: the real run needs every pin line in the frozen rule (the draft may lack them)."""
    guard_season(SEASONS)
    for rel, want in INPUT_PINS.items():
        got = rule.file_sha256(root / rel)
        if got != want:
            raise RuntimeError(f"input {rel} hashes to {got[:12]}, pinned {want[:12]}: refusing")
    decl = rule.declared(doc) if doc is not None else rule.declared()
    if require_declared:
        missing = [k for k in REQUIRED_DECLARED if k not in decl]
        if missing:
            raise RuntimeError(f"the frozen rule lacks pin lines {missing}")
    for name, rel in DECLARED_FILES.items():
        if name in decl and rule.file_sha256(root / rel) != decl[name]:
            raise RuntimeError(f"{rel} differs from the rule's {name}")
    if "GATE_SHA256" in decl:
        gate = Path(rule.ROOT) / "reports/prereg_tail_leg_cal/gate.json"
        if rule.file_sha256(gate) != decl["GATE_SHA256"]:
            raise RuntimeError(
                "reports/prereg_tail_leg_cal/gate.json differs from the rule's GATE_SHA256"
            )
    legs, _hits = legs_prefit(root)
    del _hits
    c = counts(legs)
    bad = [k for k, v in PUBLISHED_TAIL.items() if (c[k]["ge60"], c[k]["ge65"]) != v]
    if bad:
        raise RuntimeError(
            f"tail counts differ from the T495 attack's published counts in {bad}: {c}"
        )
    for k, v in c.items():
        fp = decl.get("QCAL_FP_" + k.replace("|", "_").upper())
        if fp is not None and fp != v["q_fingerprint"]:
            raise RuntimeError(f"q_cal fingerprint of {k} differs from the rule's pin")
    return {"counts": c, "published_equal": True}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "rehearse", "run"])
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--truth", default="smooth", choices=["smooth", "step", "calibrated", "flat"])
    ap.add_argument("--k", type=int, default=K_NULL)
    ap.add_argument("--sha", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    root = Path(a.data_root)
    if a.cmd == "check":
        print(json.dumps(preflight(root), indent=1))
        return 0
    if a.cmd == "rehearse":
        pre = preflight(root)
        res = run_all(root, "synthetic", a.truth, a.k)
        res["preflight"] = pre
        out = Path(a.out) if a.out else OUT_DIR / "rehearsal" / a.truth / "results.json"
    else:
        sha = rule.require_frozen(a.sha)
        blobs = rule.require_harness_pinned(sha)  # before any tail-stage label is attached
        pre = preflight(root, require_declared=True)
        res = run_all(root, "real", None, a.k)
        res["preflight"] = pre
        res["rule_sha256"] = sha
        res["harness_blobs"] = blobs
        out = Path(a.out) if a.out else OUT_DIR / "results" / "results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=float))
    ev = res["evaluation"]
    _log(f"word {ev['word']}; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
