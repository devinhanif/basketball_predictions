"""Shared helpers for the sklearn/LightGBM-backed rungs (1-2)."""

from __future__ import annotations

from typing import cast

import numpy as np

#: Probability clip so single-class training folds (degenerate on the tiny
#: fixture, e.g. 2 training games that both happen to be home wins) don't
#: produce 0/1 predictions and infinite log loss.
PROB_CLIP = (0.02, 0.98)


def clip_prob(p: np.ndarray) -> np.ndarray:
    return cast(np.ndarray, np.clip(p, PROB_CLIP[0], PROB_CLIP[1]))


class SingleClassFallback:
    """Fallback used when a training fold has only one outcome class.

    sklearn's ``LogisticRegression`` and LightGBM's binary objective both
    require >= 2 classes to fit. Walk-forward folds on small data can
    legitimately have a single-class training slice (e.g. the 2-game
    fixture before the only test game). Rather than crash, fall back to
    predicting the (clipped) empirical training rate for every row -- this
    is reported explicitly in ``get_config()`` so it's never mistaken for a
    real fit.
    """

    def __init__(self, rate: float) -> None:
        self.rate = float(np.clip(rate, *PROB_CLIP))

    def predict(self, n_rows: int) -> np.ndarray:
        return np.full(n_rows, self.rate, dtype=float)
