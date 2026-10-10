"""Rung 1: logistic regression on team-level as-of features.

Cheap, strong, interpretable per CLAUDE.md's architecture ladder. Features
come from :mod:`nba.features.team_features` (``MATCHUP_FEATURE_COLUMNS`` by
default); any subset can be passed in via ``feature_columns`` for ablation.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from sklearn.linear_model import LogisticRegression

from nba.features.team_features import MATCHUP_FEATURE_COLUMNS
from nba.models.base import RungModelBase
from nba.models.common import SingleClassFallback, clip_prob


class LogisticRung(RungModelBase):
    """L2-regularized logistic regression, one head, deterministic given seed."""

    def __init__(
        self,
        seed: int = 0,
        feature_columns: list[str] | None = None,
        C: float = 1.0,
    ) -> None:
        super().__init__(seed=seed)
        self.feature_columns = feature_columns or list(MATCHUP_FEATURE_COLUMNS)
        self.C = C
        self.model_: LogisticRegression | SingleClassFallback | None = None

    def _matrix(self, df: pl.DataFrame) -> np.ndarray:
        return df.select(self.feature_columns).fill_null(0.0).to_numpy().astype(float)

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None:
        if len(np.unique(y)) < 2:
            self.model_ = SingleClassFallback(float(y.mean()) if len(y) else 0.5)
            return
        X = self._matrix(train_df)
        clf = LogisticRegression(C=self.C, random_state=self.seed, max_iter=1000)
        clf.fit(X, y)
        self.model_ = clf

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("LogisticRung.predict called before fit()")
        if isinstance(self.model_, SingleClassFallback):
            return self.model_.predict(df.height)
        X = self._matrix(df)
        proba = self.model_.predict_proba(X)[:, 1]
        return clip_prob(proba)

    def get_config(self) -> dict[str, object]:
        fallback = isinstance(self.model_, SingleClassFallback)
        return {
            "model_name": "rung1_logistic",
            "rung": 1,
            "seed": self.seed,
            "C": self.C,
            "feature_columns": self.feature_columns,
            "single_class_fallback": fallback,
        }
