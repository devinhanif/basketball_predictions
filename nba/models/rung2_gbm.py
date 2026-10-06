"""Rung 2: LightGBM on the same as-of features + rolling efficiency proxies.

Nonlinear interactions per CLAUDE.md's architecture ladder. "Rolling
efficiency" here means the ORtg/DRtg/pace *proxies* documented in
``nba.features.team_features`` (no possessions table yet, so these are
box-score-derived stand-ins, not true per-100-possession ratings).
"""

from __future__ import annotations

from typing import cast

import numpy as np
import polars as pl
from lightgbm import LGBMClassifier

from nba.features.team_features import MATCHUP_FEATURE_COLUMNS
from nba.models.base import RungModelBase
from nba.models.common import SingleClassFallback, clip_prob

#: Rung 2 adds the raw rolling efficiency proxies (not just home-minus-away
#: diffs) on top of rung 1's feature set, so the GBM can learn nonlinear/
#: interaction effects LogisticRung's diff-only features can't express.
GBM_EXTRA_COLUMNS: list[str] = [
    "home_avg_pts_for_prior",
    "home_avg_pts_allowed_prior",
    "home_pace_proxy_prior",
    "away_avg_pts_for_prior",
    "away_avg_pts_allowed_prior",
    "away_pace_proxy_prior",
    "home_games_played_prior",
    "away_games_played_prior",
]


class LightGBMRung(RungModelBase):
    """Gradient-boosted trees over the matchup feature matrix."""

    def __init__(
        self,
        seed: int = 0,
        feature_columns: list[str] | None = None,
        n_estimators: int = 50,
        max_depth: int = 3,
        learning_rate: float = 0.1,
        min_child_samples: int = 1,
    ) -> None:
        super().__init__(seed=seed)
        self.feature_columns = feature_columns or list(MATCHUP_FEATURE_COLUMNS) + list(
            GBM_EXTRA_COLUMNS
        )
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.min_child_samples = min_child_samples
        self.model_: LGBMClassifier | SingleClassFallback | None = None

    def _matrix(self, df: pl.DataFrame) -> np.ndarray:
        return df.select(self.feature_columns).fill_null(0.0).to_numpy().astype(float)

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        if len(np.unique(y)) < 2:
            self.model_ = SingleClassFallback(float(y.mean()) if len(y) else 0.5)
            return
        X = self._matrix(train_df)
        # min_child_samples is clamped to the fold size so the tiny fixture
        # (a handful of rows per fold) doesn't silently produce a stump
        # that always predicts the training mean.
        min_child = max(1, min(self.min_child_samples, len(y)))
        clf = LGBMClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            min_child_samples=min_child,
            random_state=self.seed,
            deterministic=True,
            verbosity=-1,
        )
        clf.fit(X, y)
        self.model_ = clf

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("LightGBMRung.predict called before fit()")
        if isinstance(self.model_, SingleClassFallback):
            return cast(np.ndarray, self.model_.predict(df.height))
        X = self._matrix(df)
        proba = cast(np.ndarray, self.model_.predict_proba(X))[:, 1]
        return clip_prob(proba)

    def get_config(self) -> dict[str, object]:
        fallback = isinstance(self.model_, SingleClassFallback)
        return {
            "model_name": "rung2_lightgbm",
            "rung": 2,
            "seed": self.seed,
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "min_child_samples": self.min_child_samples,
            "feature_columns": self.feature_columns,
            "single_class_fallback": fallback,
        }
