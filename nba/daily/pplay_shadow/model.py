# ruff: noqa: E501
"""Fit and apply the recent-role P(play) model R (docs/prereg/RECENT_ROLE_HOLDOUT.md, ledger T458).

Frozen recipe, no tuning: LightGBM binary classifier with ``nba.props.context_residual._params(cfg,
"binary")`` from ``configs/context_residual.yaml``, seed 0, on the :data:`FEATURES` columns, refit daily
on every eligible played / DNP row dated strictly before the slate date (the holdout procedure refit each
calendar month on every earlier row; a daily refit is the same rule with a finer block).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl
import yaml

from nba.daily.pplay_shadow.features import FEATURES
from nba.props.context_residual import ContextResidualConfig, _matrix, _params

CONFIG = Path("configs/context_residual.yaml")
MIN_TRAIN = 1500  # the holdout walk's floor; fewer rows and no row is written
MODEL_NAME = "pplay_r"
#: bump when the feature set, the learner or the config recipe changes
MODEL_VERSION = "pplay_r_v1"


def load_cfg(path: Path = CONFIG) -> ContextResidualConfig:
    raw = yaml.safe_load(path.read_text()).get("model", {})
    return ContextResidualConfig(**raw)


def feature_set_sha(cfg: ContextResidualConfig) -> str:
    """Fingerprint of the frozen recipe: ordered feature names plus the learner parameters and the
    lightgbm version. Stored on every row; a change means a different model."""
    body = {"features": FEATURES, "params": _params(cfg, "binary"), "lightgbm": lgb.__version__}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def row_fingerprints(frame: pl.DataFrame) -> list[str]:
    """Per-row sha256 (first 16 hex) of the feature values the model saw, in :data:`FEATURES` order."""
    x = _matrix(frame, FEATURES)
    return [
        hashlib.sha256(np.ascontiguousarray(row, dtype=np.float64).tobytes()).hexdigest()[:16]
        for row in x
    ]


@dataclass
class Fit:
    clf: lgb.LGBMClassifier
    n_train: int
    train_through: dt.date
    n_positive: int


def fit(
    frame: pl.DataFrame, as_of: dt.date, cfg: ContextResidualConfig, min_train: int = MIN_TRAIN
) -> Fit | None:
    """Fit on labelled rows with ``game_date < as_of``; ``None`` when fewer than ``min_train`` rows."""
    train = frame.filter((pl.col("game_date") < as_of) & pl.col("played").is_not_null())
    if train.height < min_train:
        return None
    y = train["played"].to_numpy().astype(int)
    clf: Any = lgb.LGBMClassifier(**_params(cfg, "binary")).fit(_matrix(train, FEATURES), y)  # type: ignore[arg-type]
    return Fit(clf, train.height, train["game_date"].max(), int(y.sum()))  # type: ignore[arg-type]


def predict(model: Fit, rows: pl.DataFrame) -> np.ndarray:
    return np.asarray(model.clf.predict_proba(_matrix(rows, FEATURES)))[:, 1]
