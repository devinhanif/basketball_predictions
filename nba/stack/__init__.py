"""Learned routing / stacking across many candidate predictors (rung 6, props-first).

Modules: ``oof`` (out-of-fold prediction store), ``adapters`` (dump candidates
into the store), ``data`` (aligned candidate tensors), ``scoring`` (grid CRPS /
log loss), ``router`` (softmax and LightGBM gates, walk-forward), ``evaluate``
(paired clustered CIs, BH, pre-registered decision rule; see docs/ROUTER_V2.md).
"""

from __future__ import annotations

#: Season held out for the final frozen test; never used for fitting/selection.
FROZEN_SEASON = 2025
