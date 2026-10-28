"""Halftime scorer, model side: T508 arm M and the player-aware baseline glm_e, scored at e = 24.

Live-path module (nba.* never imports research): the formulas of
``research/eval/in_game_line_rule.py`` (B0, B1, the negative-binomial survival function, Platt)
are re-stated here in numpy, and
``tests/markets/test_halftime_model.py`` pins them to the research functions value for value, so the
live scorer and the harness cannot drift apart. The fitted artifact is JSON (LightGBM text dumps,
Platt pairs, glm_e coefficients); there is no pickle. Fitting happens in
``research/eval/halftime_scorer_train.py`` (the maintainer's run); nothing here fits.

Paper and display only: no order, no credential, no network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats as sps

STATS = ("pts", "reb", "ast", "fg3m")
CHECKPOINT = 24  # halftime, in game minutes
#: T508's trained line grid (Kalshi "k+" strike k); other strikes are never scored.
GRID: dict[str, tuple[int, ...]] = {
    "pts": (10, 15, 20, 25, 30),
    "reb": (2, 4, 6, 8, 10),
    "ast": (2, 4, 6, 8),
    "fg3m": (1, 2, 3, 4),
}

# Constants of the T508 rule (research/eval/in_game_line_rule.py), restated; parity-tested.
BLOWOUT_MARGIN = 20.0
Q4_FACTOR_BLOWOUT = 0.5
Q4_FACTOR_NORMAL = 1.0
SHARE_PRIOR_STARTER = 0.70
SHARE_PRIOR_BENCH = 0.40
SHARE_K = 6.0
RATE_K = 10.0
RATE_PRIOR = {"pts": 0.50, "reb": 0.18, "ast": 0.11, "fg3m": 0.05}
VMR = {"pts": 2.5, "reb": 1.4, "ast": 1.6, "fg3m": 1.3}
MEAN_FLOOR = 0.02
#: glm_e's pseudo-minute weight on the player's own prior rate (the attack's K hit the grid top).
GLM_K = 40.0

FEATURES_A = [
    "e",
    "minutes",
    "starter",
    "fouls",
    "margin",
    "abs_margin",
    "q4_rem",
    "blow_x_q4",
    "rec_minutes",
]
FEATURES_B = [
    "line",
    "need",
    "cur",
    "minutes",
    "e",
    "fouls",
    "margin",
    "abs_margin",
    "starter",
    "pregame_mean",
    "rem_min_hat",
    "q4_rem",
    "blow_x_q4",
    "absm_x_q4",
    "b0_logit",
]
GLM_FEATURES = ["logit_b0p", "logit_b0", "logit_b1", "log1p_need"]
ARTIFACT_VERSION = 1

Cols = dict[str, np.ndarray]


def logit(p: Any) -> Any:
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def sigmoid(z: Any) -> Any:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(z, float), -30.0, 30.0)))


def platt_apply(p: Any, ab: tuple[float, float]) -> Any:
    return np.clip(np.asarray(sigmoid(ab[0] + ab[1] * logit(p))), 1e-4, 1 - 1e-4)


def nb_sf(stat: str, mean: Any, k: Any) -> Any:
    """P(Y >= k), Y ~ NegBin(mean, variance = VMR x mean) on the integer grid (k <= 0 gives 1)."""
    mu = np.maximum(np.asarray(mean, float), MEAN_FLOOR)
    n = mu / (VMR[stat] - 1.0)
    p = n / (n + mu)
    kk = np.asarray(k)
    return np.where(kk <= 0, 1.0, sps.nbinom.sf(np.maximum(kk, 1) - 1, n, p))


def _q4_factor(margin: Any) -> Any:
    blow = np.abs(np.asarray(margin)) >= BLOWOUT_MARGIN
    return np.where(blow, Q4_FACTOR_BLOWOUT, Q4_FACTOR_NORMAL)


def remaining_minutes(e: Any, minutes: Any, share0: Any, margin: Any) -> Any:
    """B0's period-aware rule; ``share0`` is the prior on-court share (a constant for B0)."""
    e = np.asarray(e, float)
    share = (np.asarray(minutes, float) + SHARE_K * np.asarray(share0, float)) / (e + SHARE_K)
    remaining = 48.0 - e
    q4_part = np.minimum(remaining, 12.0)
    return share * ((remaining - q4_part) + _q4_factor(margin) * q4_part)


def b0_prob(stat: str, c: Cols) -> Any:
    share0 = np.where(np.asarray(c["starter"]) > 0, SHARE_PRIOR_STARTER, SHARE_PRIOR_BENCH)
    rem = remaining_minutes(c["e"], c["minutes"], share0, c["margin"])
    rate = (np.asarray(c["cur"], float) + RATE_K * RATE_PRIOR[stat]) / (
        np.asarray(c["minutes"], float) + RATE_K
    )
    return nb_sf(stat, rate * rem, np.asarray(c["line"]) - np.asarray(c["cur"]))


def b1_prob(stat: str, c: Cols) -> Any:
    num = nb_sf(stat, c["pregame_mean"], np.asarray(c["line"]))
    den = np.maximum(nb_sf(stat, c["pregame_mean"], np.asarray(c["cur"])), 1e-12)
    return np.minimum(num / den, 1.0)


def b0p_prob(stat: str, c: Cols, k: float = GLM_K) -> Any:
    """Player-aware pace (T508-attack B0p, reconstructed: the attack scripts were not kept).

    Rate prior = the player's recency per-minute rate (pregame_mean / rec_minutes), weight ``k``
    pseudo-minutes; share prior = rec_minutes / 48 in place of the starter/bench constants.
    """
    rec = np.maximum(np.asarray(c["rec_minutes"], float), 1.0)
    rate_prior = np.asarray(c["pregame_mean"], float) / rec
    rate = (np.asarray(c["cur"], float) + k * rate_prior) / (np.asarray(c["minutes"], float) + k)
    rem = remaining_minutes(c["e"], c["minutes"], np.clip(rec / 48.0, 0.05, 1.0), c["margin"])
    return nb_sf(stat, rate * rem, np.asarray(c["line"]) - np.asarray(c["cur"]))


def derive(c: Cols) -> Cols:
    """Derived columns shared by the trainer and the scorer (train-serve parity by construction)."""
    out = {k: np.asarray(v, float) for k, v in c.items()}
    out["need"] = out["line"] - out["cur"]
    out["abs_margin"] = np.abs(out["margin"])
    out["q4_rem"] = np.minimum(48.0 - out["e"], 12.0)
    out["blow_x_q4"] = (out["abs_margin"] >= BLOWOUT_MARGIN).astype(float) * out["q4_rem"]
    out["absm_x_q4"] = out["abs_margin"] * out["q4_rem"] / 12.0
    return out


def matrix(c: Cols, names: list[str]) -> np.ndarray:
    return np.column_stack([np.asarray(c[n], float) for n in names])


def glm_matrix(stat: str, c: Cols) -> np.ndarray:
    d = {
        "logit_b0p": logit(b0p_prob(stat, c)),
        "logit_b0": logit(b0_prob(stat, c)),
        "logit_b1": logit(b1_prob(stat, c)),
        "log1p_need": np.log1p(
            np.maximum(np.asarray(c["line"], float) - np.asarray(c["cur"], float), 0.0)
        ),
    }
    return matrix(d, GLM_FEATURES)


@dataclass
class Artifact:
    meta: dict[str, Any]
    stage_a: Any  # lightgbm.Booster
    stage_b: dict[str, Any]  # stat -> lightgbm.Booster
    platt: dict[str, tuple[float, float]]
    glm: dict[str, dict[str, Any]]  # stat -> {"coef": [4], "intercept": x}
    digest: str

    def score(self, stat: str, c: Cols) -> tuple[np.ndarray, np.ndarray]:
        """(p_M, p_glm_e) per row for ``stat``; ``c`` holds the raw state columns, e = 24."""
        if stat not in self.stage_b:
            raise KeyError(f"no model for stat {stat!r}")
        d = derive(c)
        d["rem_min_hat"] = np.asarray(self.stage_a.predict(matrix(d, FEATURES_A)), float)
        d["b0_logit"] = logit(b0_prob(stat, d))
        raw = np.asarray(self.stage_b[stat].predict(matrix(d, FEATURES_B)), float)
        p_m = platt_apply(raw, self.platt[stat])
        g = self.glm[stat]
        z = glm_matrix(stat, d) @ np.asarray(g["coef"], float) + float(g["intercept"])
        return np.asarray(p_m), np.asarray(np.clip(sigmoid(z), 1e-4, 1 - 1e-4))


def _digest(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def save_artifact(
    path: Path,
    meta: dict[str, Any],
    stage_a: Any,
    stage_b: dict[str, Any],
    platt: dict[str, tuple[float, float]],
    glm: dict[str, dict[str, Any]],
) -> str:
    payload = {
        "version": ARTIFACT_VERSION,
        "meta": meta,
        "stage_a": stage_a.model_to_string(),
        "stage_b": {s: m.model_to_string() for s, m in stage_b.items()},
        "platt": {s: [float(a), float(b)] for s, (a, b) in platt.items()},
        "glm": glm,
    }
    payload["digest"] = _digest(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"{path} exists; artifacts are write-once")
    path.write_text(json.dumps(payload))
    return str(payload["digest"])


def load_artifact(path: Path) -> Artifact:
    import lightgbm as lgb

    payload = json.loads(Path(path).read_text())
    if payload.get("version") != ARTIFACT_VERSION:
        raise ValueError(f"artifact version {payload.get('version')!r} != {ARTIFACT_VERSION}")
    digest = payload.pop("digest")
    if _digest(payload) != digest:
        raise ValueError(f"artifact digest mismatch in {path}")
    return Artifact(
        meta=payload["meta"],
        stage_a=lgb.Booster(model_str=payload["stage_a"]),
        stage_b={s: lgb.Booster(model_str=m) for s, m in payload["stage_b"].items()},
        platt={s: (float(v[0]), float(v[1])) for s, v in payload["platt"].items()},
        glm=payload["glm"],
        digest=digest,
    )
