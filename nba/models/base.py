"""The 3-method contract every architecture-ladder rung implements.

Per CLAUDE.md "Registry retrofit" -> "Patterns to port": mirrors the work
registry's ``ForecastModel`` ABC (``predict()``, ``get_metrics()``,
``get_config()``), re-implemented from scratch against plain
numpy/polars -- no Snowflake, no employer code.

``fit`` is deliberately *not* part of the frozen 3-method contract (it is
training-time plumbing used by the walk-forward harness), but every rung
provides it so :mod:`nba.eval` can retrain on each walk-forward slice.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np
import polars as pl


class RungModel(Protocol):
    """Protocol satisfied by every rung in ``nba/models/``.

    ``predict`` takes a polars DataFrame of as-of features (whatever
    columns that rung needs -- see each model's ``get_config()`` for its
    expected feature list) and returns a 1-D array of calibrated P(home
    win) in ``[0, 1]``.
    """

    def fit(self, train_df: pl.DataFrame, y: np.ndarray) -> None: ...

    def predict(self, df: pl.DataFrame) -> np.ndarray: ...

    def get_metrics(self) -> dict[str, object]: ...

    def get_config(self) -> dict[str, object]: ...

    def record_metrics(self, metrics: dict[str, object]) -> None: ...


class RungModelBase:
    """Shared ``get_metrics``/``record_metrics`` bookkeeping for all rungs."""

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed
        self._metrics: dict[str, object] = {}

    def record_metrics(self, metrics: dict[str, object]) -> None:
        """Store the harness's latest walk-forward evaluation for this rung."""
        self._metrics = dict(metrics)

    def get_metrics(self) -> dict[str, object]:
        """Return the most recently recorded evaluation metrics (``{}`` if none yet)."""
        return dict(self._metrics)
