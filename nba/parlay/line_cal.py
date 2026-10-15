"""LINE_CALIBRATION CAL map (docs/prereg/LINE_CALIBRATION.md), served read-only.

The map recalibrates the model's own P(over line) with terms ``[1, x, z, bench, bench * x]``:
``x = logit p_model``, ``z = (line - continuous median) / max((q75 - q25) / 1.349, 0.5)`` clipped to
[-5, 5], ``bench = starter10 < 0.5``. One weighted ridge logistic regression per (book, stat), every
fit row weighted ``0.5 ** (age_days / 365)``. Nothing derived from a price enters a fit. The numeric
conventions below are the frozen harness's (``research/eval/line_calibration_eval.py``); a test pins
this file to it. ``nba`` never imports ``research``, so the few lines are repeated here.

Season 2025 is the frozen holdout: ``fit_cells`` refuses any row dated after ``MAX_FIT_DATE``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

MAP_PATH = Path(__file__).resolve().parent / "line_cal_map_v1.json"
RULE = "LINE_CALIBRATION"
EPS = 0.5 / 199  # production grid resolution; the harness clip for model probabilities
SPREAD_MIN = 0.5
IQR_TO_SD = 1.349
Z_CLIP = 5.0
RIDGE = 1.0
MIN_FIT = 2000
HALF_LIFE_DAYS = 365.0
TERMS = ("1", "x", "z", "bench", "bench*x")
MAX_FIT_DATE = date(2025, 6, 30)  # last day of season 2024; nothing later is ever fitted here


def logit(p: np.ndarray) -> np.ndarray:
    return np.asarray(np.log(p) - np.log1p(-p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-x)))


def z_score(line: np.ndarray, med: np.ndarray, spr: np.ndarray) -> np.ndarray:
    return np.asarray(np.clip((line - med) / np.maximum(spr, SPREAD_MIN), -Z_CLIP, Z_CLIP))


def design(
    pm: np.ndarray, line: np.ndarray, med: np.ndarray, spr: np.ndarray, st10: np.ndarray
) -> np.ndarray:
    """[1, x, z, bench, bench * x]. Takes no price argument by construction (market-free)."""
    x = logit(np.clip(pm, EPS, 1 - EPS))
    z = z_score(line, med, spr)
    bench = (st10 < 0.5).astype(np.float64)
    return np.column_stack([np.ones_like(x), x, z, bench, bench * x])


def age_weights(dates: np.ndarray, block_start: np.datetime64) -> np.ndarray:
    age = (block_start - dates).astype("timedelta64[D]").astype(np.float64)
    if not np.all(age > 0):
        raise ValueError("a fit row on or after the block start")
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


def zq_to_quantiles(
    c: np.ndarray, s: np.ndarray, zq_mid: np.ndarray, pos: dict[str, float]
) -> dict[str, np.ndarray]:
    """Continuous quantiles of the 199-point grid ``max(c + s * zq, 0)`` (cumulative max), linearly
    interpolated at the 0-based grid positions in ``pos`` (harness ``TAU_POS``)."""
    q = np.maximum(c[:, None] + s[:, None] * zq_mid[None, :], 0.0)
    q = np.maximum.accumulate(q, axis=1)
    n = q.shape[1]
    out: dict[str, np.ndarray] = {}
    for k, p in pos.items():
        i = int(np.floor(p))
        f = p - i
        out[k] = np.asarray(q[:, i] * (1 - f) + q[:, min(i + 1, n - 1)] * f)
    return out


def sorted_grid_quantiles(samples: np.ndarray) -> tuple[float, float, float]:
    """(q25, q50, q75) of 199 sorted equal-mass samples at the harness grid positions
    49.25 / 99 / 148.75 (0-based, linear)."""
    s = np.sort(np.asarray(samples, dtype=float))
    n = len(s)
    out = []
    for tau in (0.25, 0.5, 0.75):
        p = tau * n - 0.5
        i = int(np.floor(p))
        f = p - i
        out.append(float(s[i] * (1 - f) + s[min(i + 1, n - 1)] * f))
    return out[0], out[1], out[2]


@dataclass(frozen=True)
class CalCell:
    book: str
    stat: str
    beta: tuple[float, ...] | None  # None = identity (fewer than MIN_FIT fit rows)
    n_fit: int
    n_eff: float | None


def fit_cells(
    D: dict[str, Any], block_start: np.datetime64, books: tuple[str, ...], stats: tuple[str, ...]
) -> list[CalCell]:
    """One CAL fit per (book, stat) on every row dated strictly before ``block_start`` (the
    harness's block rule). ``D`` carries arrays date, book, stat, pm, line, med, spr, st10, over."""
    if np.datetime64(block_start, "D") > np.datetime64(MAX_FIT_DATE, "D") + np.timedelta64(1, "D"):
        raise ValueError("block_start is past the season-2024 cutoff; season 2025 is the holdout")
    if D["date"].size and D["date"].max() > np.datetime64(MAX_FIT_DATE, "D"):
        raise ValueError("fit rows after 2025-06-30: season 2025 is the frozen holdout")
    X = design(D["pm"], D["line"], D["med"], D["spr"], D["st10"])
    out: list[CalCell] = []
    for book in books:
        for stat in stats:
            fit = (D["book"] == book) & (D["stat"] == stat) & (D["date"] < block_start)
            n = int(fit.sum())
            if n < MIN_FIT:
                out.append(CalCell(book, stat, None, n, None))
                continue
            wf = age_weights(D["date"][fit], block_start)
            beta = fit_logistic(X[fit], D["over"][fit], w=wf)
            out.append(
                CalCell(
                    book,
                    stat,
                    tuple(float(v) for v in beta),
                    n,
                    float(wf.sum() ** 2 / (wf**2).sum()),
                )
            )
    return out


def _canonical(payload: dict[str, Any]) -> bytes:
    body = {k: v for k, v in payload.items() if k != "map_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def payload_sha(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class CalMap:
    meta: dict[str, Any]
    cells: dict[tuple[str, str], CalCell]

    @property
    def books(self) -> tuple[str, ...]:
        return tuple(sorted({b for b, _ in self.cells}))

    def has(self, book: str, stat: str) -> bool:
        return (book, stat) in self.cells

    def apply(
        self, book: str, stat: str, pm: float, line: float, med: float, spr: float, st10: float
    ) -> float:
        """Calibrated P(over). Identity cells return the clipped raw probability."""
        cell = self.cells[(book, stat)]
        p0 = float(np.clip(pm, EPS, 1 - EPS))
        if cell.beta is None:
            return p0
        X = design(
            np.array([pm]), np.array([line]), np.array([med]), np.array([spr]), np.array([st10])
        )
        return float(np.clip(sigmoid(X @ np.asarray(cell.beta))[0], EPS, 1 - EPS))

    def apply_many(
        self,
        book: str,
        stat: str,
        pm: np.ndarray,
        line: np.ndarray,
        med: np.ndarray,
        spr: np.ndarray,
        st10: np.ndarray,
    ) -> np.ndarray:
        cell = self.cells[(book, stat)]
        if cell.beta is None:
            return np.asarray(np.clip(pm, EPS, 1 - EPS))
        X = design(pm, line, med, spr, st10)
        return np.asarray(np.clip(sigmoid(X @ np.asarray(cell.beta)), EPS, 1 - EPS))


def to_payload(
    cells: list[CalCell],
    *,
    fit_through: str,
    block_start: str,
    rule_sha8: str,
    inputs: dict[str, str],
    note: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "line_cal_map/1",
        "rule": RULE,
        "rule_sha8": rule_sha8,
        "fit_through": fit_through,
        "block_start": block_start,
        "terms": list(TERMS),
        "half_life_days": HALF_LIFE_DAYS,
        "ridge": RIDGE,
        "min_fit": MIN_FIT,
        "inputs_sha256": inputs,
        "note": note,
        "cells": [
            {
                "book": c.book,
                "stat": c.stat,
                "beta": None if c.beta is None else list(c.beta),
                "n_fit": c.n_fit,
                "n_eff": c.n_eff,
            }
            for c in sorted(cells, key=lambda c: (c.book, c.stat))
        ],
    }
    payload["map_sha256"] = payload_sha(payload)
    return payload


def load_map(path: Path = MAP_PATH) -> CalMap:
    """Load and verify a map file: the stored sha must equal the recomputed one, and the fit must
    end no later than the season-2024 cutoff."""
    payload = json.loads(path.read_text())
    if payload.get("map_sha256") != payload_sha(payload):
        raise ValueError(f"{path.name}: map_sha256 does not match its content")
    if date.fromisoformat(str(payload["fit_through"])) > MAX_FIT_DATE:
        raise ValueError(f"{path.name}: fit_through is after {MAX_FIT_DATE} (holdout)")
    cells = {
        (c["book"], c["stat"]): CalCell(
            c["book"],
            c["stat"],
            None if c["beta"] is None else tuple(float(v) for v in c["beta"]),
            int(c["n_fit"]),
            c["n_eff"],
        )
        for c in payload["cells"]
    }
    meta = {k: v for k, v in payload.items() if k != "cells"}
    return CalMap(meta, cells)
