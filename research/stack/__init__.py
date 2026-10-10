"""Learned routing / stacking across many candidate predictors (rung 6, props-first).

Modules: ``adapters`` (dump candidates into the OOF store), ``data`` (aligned candidate
tensors), ``scoring`` (grid CRPS / log loss), ``router`` (softmax and LightGBM gates,
walk-forward), ``evaluate`` (paired clustered CIs, BH, pre-registered decision rule; see
docs/ROUTER_V2.md). The OOF store itself (``nba.stack.oof``) stays live: the production
backtest can write to it (``--write-oof``).
"""

from __future__ import annotations

from nba.stack import FROZEN_SEASON

__all__ = ["FROZEN_SEASON"]
