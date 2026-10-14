# ruff: noqa: E501
"""G1 calibration and the slice guard of docs/prereg/COMBO_STOCKS_SHADOW.md, as the rule's text states them.

The text of rule 6(d) is the specification; ``nba.eval.avg_shadow_live.calibration`` is NOT used because it requires the
0.95 coverage and an absolute bias of 0.5, neither of which the rule says. Pre-freeze review B2 (2026-10-14): the randomised
PIT depends on the row order for a fixed seed, so the order is part of the rule: rows are sorted by
(game_date, game_id, player_id) and ``randomized_pit(q, y, 0)`` is called ONCE per target over that target's scored rows.

Numpy only; imports nothing from ``nba.eval`` (the equality with ``nba.eval.lower_tail_eval.randomized_pit`` is a test).
"""

from __future__ import annotations

from typing import Any

import numpy as np

SEED = 0
REQUIRED_TAUS = (0.10, 0.20, 0.80, 0.90)  # within TOL of nominal; 0.05 and 0.95 are reported only
REPORTED_TAUS = (0.05, 0.95)
TOL = 0.02
COV80_LO, COV80_HI = 0.75, 0.85
REL_BIAS = 0.05  # |mean(y - mean of the grid)| <= 0.05 * mean(y)
MIN_N = 3000
MIN_SLICE_N = 300


def sort_order(game_date: np.ndarray, game_id: np.ndarray, player_id: np.ndarray) -> np.ndarray:
    """Indices that sort rows by (game_date, game_id, player_id): the fixed G1 row order."""
    return np.lexsort((player_id, game_id, game_date))


def randomized_pit(q: np.ndarray, y: np.ndarray, seed: int = SEED) -> np.ndarray:
    """Continuity-corrected randomised PIT on equal-mass integer grids ``q`` (n, g); one uniform per row, in row order."""
    rng = np.random.default_rng(seed)
    f_lo = (q < (y - 0.5)[:, None]).mean(axis=1)
    f_hi = (q <= (y + 0.5)[:, None]).mean(axis=1)
    return np.asarray(f_lo + rng.random(len(y)) * (f_hi - f_lo))


def g1(
    q: np.ndarray,
    y: np.ndarray,
    game_date: np.ndarray,
    game_id: np.ndarray,
    player_id: np.ndarray,
) -> dict[str, Any]:
    """G1 for one target over its scored rows (any input order; sorted here). ``q`` is the stored 199-point integer pmf grid."""
    o = sort_order(game_date, game_id, player_id)
    q, y = np.asarray(q, dtype=float)[o], np.asarray(y, dtype=float)[o]
    n = len(y)
    pit = randomized_pit(q, y, SEED)
    cover = {t: float((pit <= t).mean()) for t in (*REPORTED_TAUS, *REQUIRED_TAUS)}
    cov80 = float(((pit > 0.10) & (pit <= 0.90)).mean())
    bias = float((y - q.mean(axis=1)).mean())
    lim = REL_BIAS * float(y.mean()) if n else float("nan")
    ok_q = all(abs(cover[t] - t) <= TOL for t in REQUIRED_TAUS)
    return {
        "n": n,
        "cover": cover,
        "cov80": cov80,
        "bias": bias,
        "bias_limit": lim,
        "ok": bool(n >= MIN_N and ok_q and COV80_LO <= cov80 <= COV80_HI and abs(bias) <= lim),
        "enough_rows": bool(n >= MIN_N),
    }


def slice_guard(
    delta: np.ndarray, masks: dict[str, np.ndarray], floor: float, min_n: int = MIN_SLICE_N
) -> dict[str, Any]:
    """Rule 6(e): per slice with n >= ``min_n``, the mean of ``delta`` (S minus RECENT, per row) must not exceed ``floor``."""
    rows: dict[str, Any] = {}
    for name, m in masks.items():
        n = int(m.sum())
        if n < min_n:
            rows[name] = {"n": n, "delta": None, "violates": False, "counted": False}
            continue
        d = float(delta[m].mean())
        rows[name] = {"n": n, "delta": d, "violates": bool(d > floor), "counted": True}
    return {"slices": rows, "ok": not any(r["violates"] for r in rows.values())}
