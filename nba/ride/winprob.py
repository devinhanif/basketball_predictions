"""In-game win probability: a small logistic model on score margin and time left.

Fit on regular-season play-by-play of seasons 2022-2024 (start years; season 2025 is the frozen
holdout and is never loaded). Features depend only on (home margin, seconds left): possession is
not in the ESPN live feed in a form we trust, so it is omitted and disclosed (a possession is worth
about one point; the 98% gate and the ESPN cross-check absorb it).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

COEF_PATH = Path(__file__).with_name("winprob_coefs.json")
REG_SEASON_SECONDS = 2880.0
ALLOWED_SEASON_PREFIXES = ("00222", "00223", "00224")  # regular seasons 2022-23 .. 2024-25


def features(margin: np.ndarray, secs_left: np.ndarray) -> np.ndarray:
    """Design matrix [1, m/sqrt(t+1), m, m*log1p(t)] with t in minutes, m = home - away."""
    m = np.asarray(margin, dtype=float)
    t = np.maximum(np.asarray(secs_left, dtype=float), 0.0) / 60.0
    return np.column_stack([np.ones_like(m), m / np.sqrt(t + 1.0), m, m * np.log1p(t)])


def sigmoid(x: np.ndarray) -> np.ndarray:
    return np.asarray(1.0 / (1.0 + np.exp(-np.clip(x, -40.0, 40.0))))


@dataclass(frozen=True)
class WinProbModel:
    coefs: tuple[float, ...]

    def prob_home(self, margin: np.ndarray, secs_left: np.ndarray) -> np.ndarray:
        return sigmoid(features(margin, secs_left) @ np.asarray(self.coefs))

    def p_home(self, margin: float, secs_left: float) -> float:
        return float(self.prob_home(np.array([margin]), np.array([secs_left]))[0])

    def save(self, path: Path = COEF_PATH, meta: dict[str, object] | None = None) -> None:
        path.write_text(json.dumps({"coefs": list(self.coefs), "meta": meta or {}}, indent=1))

    @classmethod
    def load(cls, path: Path = COEF_PATH) -> WinProbModel:
        return cls(tuple(float(c) for c in json.loads(path.read_text())["coefs"]))


def fit_logistic(
    x: np.ndarray, y: np.ndarray, *, l2: float = 1e-6, iters: int = 50
) -> WinProbModel:
    """Newton-Raphson logistic regression (tiny ridge for stability)."""
    beta = np.zeros(x.shape[1])
    for _ in range(iters):
        p = sigmoid(x @ beta)
        g = x.T @ (y - p) - l2 * beta
        w = p * (1.0 - p)
        h = (x * w[:, None]).T @ x + l2 * np.eye(x.shape[1])
        step = np.linalg.solve(h, g)
        beta = beta + step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return WinProbModel(tuple(float(b) for b in beta))


def parse_clock(clock: str) -> float:
    """'PT05M12.00S' (nba.com) -> seconds; 'm:ss' / 'ss.s' (ESPN) -> seconds."""
    c = clock.strip()
    if c.startswith("PT"):
        mm, rest = c[2:].split("M")
        return float(mm) * 60.0 + float(rest.rstrip("S") or 0.0)
    if ":" in c:
        mm, ss = c.split(":")
        return float(mm) * 60.0 + float(ss)
    return float(c) if c else 0.0


def seconds_left(period: int, clock_secs: float) -> float:
    """Regulation seconds left; in overtime, seconds left in that overtime period."""
    if period <= 4:
        return (4 - period) * 720.0 + clock_secs
    return clock_secs


def leader_prob(p_home: float) -> tuple[str, float]:
    return ("home", p_home) if p_home >= 0.5 else ("away", 1.0 - p_home)


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    q = np.clip(p, 1e-12, 1 - 1e-12)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)
