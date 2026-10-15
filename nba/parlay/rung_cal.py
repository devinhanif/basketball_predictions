# ruff: noqa: E501
"""ALT_RUNG_CAL CAL-rung map (docs/prereg/ALT_RUNG_CAL.md, frozen 425e4153), served read-only.

The map recalibrates the model's own P(over) at a rung with terms ``[1, x, zm, r, r*x, bench, bench*x]``:
``x = logit(clip(P, EPS))`` where ``P = P(Y >= line + 0.5)`` on the integer grid, ``spread = max(IQR/1.349,
0.5)`` of the continuous 199-point grid, ``zm = clip((main - median) / spread, +-5)`` (where the book's
main line sits against the model's median), ``r = clip((line - main) / spread, +-8)`` (the rung's distance
from the main line), ``bench = starter10 < 0.5``. One weighted ridge logistic per stat, DraftKings only.
No price enters; the book's main line is the only market input and it is a line, not a price.

Display only: this is "calibrated using the book's main line (T494), not model skill". The fit support
was rungs with A0 >= 0.5; ``lines`` below that are extrapolation and are counted, never hidden.

The numerics are the frozen harness's (``research/eval/alt_rung_cal_eval.py``, via ``nba.parlay.line_cal``
which a test pins to ``line_calibration_eval``); ``nba`` never imports ``research``. FanDuel has no map
in v1 (label only). Season 2025 is the frozen holdout: ``fit_cells`` refuses any row after 2025-06-30.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from nba.parlay.line_cal import (
    EPS,
    HALF_LIFE_DAYS,
    IQR_TO_SD,
    MAX_FIT_DATE,
    MIN_FIT,
    RIDGE,
    SPREAD_MIN,
    Z_CLIP,
    age_weights,
    fit_logistic,
    logit,
    sigmoid,
    sorted_grid_quantiles,
)

MAP_PATH = Path(__file__).resolve().parent / "rung_cal_map_v1.json"
MAP_VERSION = "rung_cal_map_v1"
RULE = "ALT_RUNG_CAL"
RULE_SHA8 = "425e4153"
RULE_SHA256 = "425e41539e5d3dcb9525cec11741abc9f5fa51a5992f453215b132f64ded3b30"
HARNESS_PIN8 = "7eb08466"
BOOK = "draftkings"
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
R_CLIP = 8.0
ZM_CLIP = 5.0
TERMS = ("1", "x", "zm", "r", "r*x", "bench", "bench*x")
TERM_DEFS = {
    "x": "logit(clip(P(Y >= line + 0.5), 0.5/199, 1 - 0.5/199))",
    "zm": "clip((main - median) / spread, -5, 5)",
    "r": "clip((line - main) / spread, -8, 8)",
    "bench": "1 if starter10 < 0.5 else 0",
    "spread": "max((q75 - q25) / 1.349, 0.5) of the continuous 199-point grid",
}
FIT_SUPPORT_MIN_P = 0.5  # the harness fit set S holds rungs with A0 >= 0.5 (rule line 1)
LO_PCT, HI_PCT = 0.05, 0.95


def design(
    pm: np.ndarray,
    point: np.ndarray,
    main: np.ndarray,
    med: np.ndarray,
    spr: np.ndarray,
    st10: np.ndarray,
) -> np.ndarray:
    """[1, x, zm, r, r*x, bench, bench*x]; the harness's ``rung_design`` on ``rung_arrays`` inputs."""
    sp = np.maximum(spr, SPREAD_MIN)
    x = logit(np.clip(pm, EPS, 1 - EPS))
    zm = np.clip((main - med) / sp, -ZM_CLIP, ZM_CLIP)
    r = np.clip((point - main) / sp, -R_CLIP, R_CLIP)
    b = (st10 < 0.5).astype(np.float64)
    return np.column_stack([np.ones_like(x), x, zm, r, r * x, b, b * x])


@dataclass(frozen=True)
class RungCell:
    stat: str
    beta: tuple[float, ...] | None  # None = identity (fewer than MIN_FIT fit rows)
    n_fit: int
    n_eff: float | None


def fit_cells(
    D: dict[str, Any], block_start: np.datetime64, stats: tuple[str, ...] = STATS
) -> list[RungCell]:
    """One CAL-rung fit per stat on every rung with a main line dated strictly before ``block_start``
    (the harness's block rule). ``D``: arrays pm, point, main, med, spr, st10, date, stat, hit,
    has_main, season."""
    if np.datetime64(block_start, "D") > np.datetime64(MAX_FIT_DATE, "D") + np.timedelta64(1, "D"):
        raise ValueError("block_start is past the season-2024 cutoff; season 2025 is the holdout")
    if D["date"].size and D["date"].max() > np.datetime64(MAX_FIT_DATE, "D"):
        raise ValueError("fit rows after 2025-06-30: season 2025 is the frozen holdout")
    if D["season"].size and int(D["season"].max()) > 2024:
        raise ValueError("a season-2025 row reached the fit: season 2025 is the frozen holdout")
    X = design(D["pm"], D["point"], D["main"], D["med"], D["spr"], D["st10"])
    out: list[RungCell] = []
    for stat in stats:
        fit = (D["stat"] == stat) & D["has_main"] & (D["date"] < block_start)
        n = int(fit.sum())
        if n < MIN_FIT:
            out.append(RungCell(stat, None, n, None))
            continue
        wf = age_weights(D["date"][fit], block_start)
        beta = fit_logistic(X[fit], D["hit"][fit], w=wf)
        out.append(
            RungCell(stat, tuple(float(v) for v in beta), n, float(wf.sum() ** 2 / (wf**2).sum()))
        )
    return out


def _canonical(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != "map_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def payload_sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class RungCalMap:
    meta: dict[str, Any]
    cells: dict[str, RungCell]

    @property
    def map_sha8(self) -> str:
        return str(self.meta["map_sha256"])[:8]

    def has(self, stat: str) -> bool:
        return stat in self.cells and self.cells[stat].beta is not None

    def apply_many(
        self,
        stat: str,
        pm: np.ndarray,
        point: np.ndarray,
        main: float,
        med: float,
        spr: float,
        st10: float,
    ) -> np.ndarray:
        """Calibrated P(over) at each rung ``point`` of one ladder. Identity cells return clipped A0."""
        cell = self.cells[stat]
        p0 = np.clip(np.asarray(pm, dtype=float), EPS, 1 - EPS)
        if cell.beta is None:
            return np.asarray(p0)
        n = p0.size
        X = design(
            p0,
            np.asarray(point, dtype=float),
            np.full(n, float(main)),
            np.full(n, float(med)),
            np.full(n, float(spr)),
            np.full(n, float(st10)),
        )
        return np.asarray(np.clip(sigmoid(X @ np.asarray(cell.beta)), EPS, 1 - EPS))


def to_payload(
    cells: list[RungCell],
    *,
    fit_through: str,
    block_start: str,
    row_counts: dict[str, Any],
    inputs: dict[str, Any],
    note: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "rung_cal_map/1",
        "map_version": MAP_VERSION,
        "rule": RULE,
        "rule_sha8": RULE_SHA8,
        "rule_sha256": RULE_SHA256,
        "harness_pin8": HARNESS_PIN8,
        "book": BOOK,
        "fit_through": fit_through,
        "block_start": block_start,
        "terms": list(TERMS),
        "term_definitions": TERM_DEFS,
        "constants": {
            "eps": EPS,
            "spread_min": SPREAD_MIN,
            "iqr_to_sd": IQR_TO_SD,
            "zm_clip": ZM_CLIP,
            "r_clip": R_CLIP,
            "z_clip": Z_CLIP,
            "ridge": RIDGE,
            "min_fit": MIN_FIT,
            "half_life_days": HALF_LIFE_DAYS,
            "fit_support_min_p": FIT_SUPPORT_MIN_P,
        },
        "row_counts": row_counts,
        "inputs": inputs,
        "fanduel": "label only in v1; no FanDuel map",
        "note": note,
        "cells": [
            {
                "stat": c.stat,
                "beta": None if c.beta is None else list(c.beta),
                "n_fit": c.n_fit,
                "n_eff": c.n_eff,
            }
            for c in sorted(cells, key=lambda c: c.stat)
        ],
    }
    payload["map_sha256"] = payload_sha(payload)
    return payload


def load_map(path: Path = MAP_PATH) -> RungCalMap:
    """Load and verify: stored sha equals the recomputed one; the fit ends by the season-2024 cutoff."""
    payload = json.loads(path.read_text())
    if payload.get("map_sha256") != payload_sha(payload):
        raise ValueError(f"{path.name}: map_sha256 does not match its content")
    if date.fromisoformat(str(payload["fit_through"])) > MAX_FIT_DATE:
        raise ValueError(f"{path.name}: fit_through is after {MAX_FIT_DATE} (holdout)")
    if payload.get("book") != BOOK:
        raise ValueError(f"{path.name}: not a {BOOK} map")
    cells = {
        c["stat"]: RungCell(
            c["stat"],
            None if c["beta"] is None else tuple(float(v) for v in c["beta"]),
            int(c["n_fit"]),
            c["n_eff"],
        )
        for c in payload["cells"]
    }
    meta = {k: v for k, v in payload.items() if k != "cells"}
    return RungCalMap(meta, cells)


def grid_med_spr(grid199: np.ndarray) -> tuple[float, float]:
    """(median, spread) of the continuous equal-mass 199-point grid, the harness's definition."""
    q25, q50, q75 = sorted_grid_quantiles(np.asarray(grid199, dtype=float))
    return q50, max((q75 - q25) / IQR_TO_SD, SPREAD_MIN)


def _line_range(surv: np.ndarray) -> tuple[float, float]:
    """Half-point lines from the 5th to 95th model percentile, rounded outward to .5.
    ``surv[k-1] = P(Y >= k)``, k = 1..K; the percentile of an integer is the smallest m with
    P(Y <= m) >= tau, so the lines run from ``m05 - 0.5`` to ``m95 + 0.5`` (never below 0.5)."""
    cdf = 1.0 - surv  # cdf[m] = P(Y <= m) = 1 - P(Y >= m + 1), m = 0..K-1; 1 beyond
    ok_lo, ok_hi = cdf >= LO_PCT - 1e-12, cdf >= HI_PCT - 1e-12
    m_lo = int(np.argmax(ok_lo)) if ok_lo.any() else cdf.size
    m_hi = int(np.argmax(ok_hi)) if ok_hi.any() else cdf.size
    return max(m_lo - 0.5, 0.5), m_hi + 0.5


def rung_cal_lines(
    surv: np.ndarray,
    book_main_line: float,
    starter10: float | None,
    stat: str,
    med: float,
    spr: float,
    *,
    rmap: RungCalMap | None = None,
    counts: dict[str, int] | None = None,
    served_only: bool = True,
) -> list[dict[str, float]] | None:
    """The contract's ``lines`` (docs/contracts/rung_cal_export_v1.md), or None.

    ``surv[k-1] = P(Y >= k)`` for k = 1..K (the stored ``p_ge_full`` counts / n); beyond K the survival
    is 0. ``med`` / ``spr`` come from the continuous 199-point grid (``grid_med_spr``). ``starter10``
    None counts as a starter (the harness's fill). Returns None (field dropped) when the stat has no
    map, when no line is inside the fit support, or when the calibrated p_over is not monotone
    non-increasing in line (counted). With ``served_only`` (default, the 2026-10-15 amendment) only lines
    whose raw model P(over) >= ``FIT_SUPPORT_MIN_P`` (the map's fit set) are returned and the monotone
    check runs on those alone; lines below it are extrapolation and are omitted (counted as
    ``below_fit_support``). ``served_only=False`` returns the whole 5th..95th percentile range with the
    check over all of it (diagnostics). ``counts``, when given, is incremented: ``ok``, ``no_map``,
    ``dropped_nonmonotone``, ``below_fit_support`` (lines whose raw A0 < 0.5), ``no_supported_lines``,
    ``lines`` (lines returned).
    """
    c = counts if counts is not None else {}
    for k in (
        "ok",
        "no_map",
        "dropped_nonmonotone",
        "below_fit_support",
        "no_supported_lines",
        "lines",
    ):
        c.setdefault(k, 0)
    m = rmap if rmap is not None else load_map()
    surv = np.asarray(surv, dtype=float)
    if not m.has(stat) or surv.size == 0:
        c["no_map"] += 1
        return None
    lo, hi = _line_range(surv)
    lines = np.arange(lo, hi + 0.25, 1.0)
    idx = np.rint(lines + 0.5).astype(int)  # P(Y >= line + 0.5)
    pm = np.where(idx <= surv.size, surv[np.clip(idx - 1, 0, surv.size - 1)], 0.0)
    pc = m.apply_many(
        stat, pm, lines, book_main_line, med, spr, 1.0 if starter10 is None else starter10
    )
    below = pm < FIT_SUPPORT_MIN_P
    c["below_fit_support"] += int(below.sum())
    if served_only:
        lines, pm, pc = lines[~below], pm[~below], pc[~below]
        if lines.size == 0:
            c["no_supported_lines"] += 1
            return None
    if np.any(np.diff(pc) > 0):
        c["dropped_nonmonotone"] += 1
        return None
    c["ok"] += 1
    c["lines"] += int(lines.size)
    return [
        {"line": float(ln), "p_over": round(float(p), 4)} for ln, p in zip(lines, pc, strict=True)
    ]
