"""Cold-start method 3: rookie priors from draft slot and college stats.

Ridge regression mapping ``(draft_pick, age, height, position,
college_stats...)`` -> first-season rate. Draft slot is the strongest
single input per CLAUDE.md ("do not skip it") -- it is always included and
never dropped even when other inputs are missing. Falls back to the
archetype prior (method 2) for players with no ``college_stats`` at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import Ridge

from nba.coldstart.archetypes import encode_position

#: Column order for the rookie-prior design matrix. ``draft_pick`` is first
#: by convention (CLAUDE.md: "the strongest single input") though Ridge
#: itself does not depend on column order -- this is just documentation.
FEATURE_ORDER = ["draft_pick", "age", "height_in", "position_encoded"]


def build_feature_matrix(
    draft_pick: np.ndarray,
    age: np.ndarray,
    height_in: np.ndarray,
    position: list[str | None],
    college_stats: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Assemble the design matrix. ``college_stats`` is an optional
    ``(n, k)`` array of per-100-possession college rates; rows with any NaN
    there are flagged in the returned ``has_college`` mask so the caller can
    fall back to the archetype prior for those players.

    Missing ``draft_pick`` (undrafted players) is imputed to a documented
    "replacement level" slot (61 -- one past the 60-pick draft) rather than
    dropped, since draft slot must never be skipped per CLAUDE.md.
    """
    draft_pick_f = np.asarray(draft_pick, dtype=float)
    draft_pick_f = np.where(np.isnan(draft_pick_f), 61.0, draft_pick_f)
    age_f = np.asarray(age, dtype=float)
    height_f = np.asarray(height_in, dtype=float)
    pos_f = np.array([encode_position(p) for p in position], dtype=float)

    base = np.column_stack([draft_pick_f, age_f, height_f, pos_f])
    if college_stats is None or college_stats.size == 0:
        has_college = np.zeros(base.shape[0], dtype=bool)
        return base, has_college

    college_stats = np.asarray(college_stats, dtype=float)
    has_college = ~np.isnan(college_stats).any(axis=1)
    college_filled = np.where(np.isnan(college_stats), 0.0, college_stats)
    X = np.column_stack([base, college_filled])
    return X, has_college


@dataclass
class RookiePriorModel:
    ridge: Ridge
    n_features: int
    seed: int


def fit_rookie_prior(
    X: np.ndarray, y: np.ndarray, seed: int = 0, alpha: float = 1.0
) -> RookiePriorModel:
    """Fit ridge regression on rows that have complete features (typically
    the ``has_college`` subset -- callers should filter before calling).
    Deterministic given ``seed`` (ridge closed-form has no randomness, but
    ``seed`` is threaded through for config/logging consistency with the
    other cold-start methods).
    """
    ridge = Ridge(alpha=alpha, random_state=seed)
    ridge.fit(X, y)
    return RookiePriorModel(ridge=ridge, n_features=X.shape[1], seed=seed)


def predict_rookie_prior(
    model: RookiePriorModel,
    X: np.ndarray,
    has_college: np.ndarray,
    archetype_fallback: np.ndarray,
) -> np.ndarray:
    """Blend ridge predictions with the archetype-prior fallback.

    Rows without college_stats (``has_college[i] is False``) use
    ``archetype_fallback[i]`` instead of the ridge model, per CLAUDE.md:
    "Fall back to archetype prior when college data is missing."
    """
    if X.shape[1] != model.n_features:
        raise ValueError(
            f"feature width mismatch: model trained on {model.n_features} columns, got {X.shape[1]}"
        )
    preds = model.ridge.predict(X)
    has_college_a = np.asarray(has_college, dtype=bool)
    out = np.where(has_college_a, preds, archetype_fallback)
    return np.asarray(out, dtype=float)
