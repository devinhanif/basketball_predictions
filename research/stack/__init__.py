"""Learned routing / stacking across many candidate predictors (rung 6, props-first).

Archived under the ledger (docs/ROUTER_V2.md, T028-T033); nothing in ``nba/`` imports it. Modules:
``oof`` (the out-of-fold prediction store in its own DuckDB file), ``adapters`` (dump candidates
into the store), ``data`` (aligned candidate tensors), ``scoring`` (grid CRPS / log loss),
``router`` (softmax and LightGBM gates, walk-forward), ``evaluate`` (paired clustered CIs, BH,
pre-registered decision rule), ``populate`` (fill the store from the frozen artifacts).

This package owns ``FROZEN_SEASON``; it used to be defined in ``nba/stack/__init__.py`` and
re-exported here.
"""

from __future__ import annotations

#: Season held out for the final frozen test; never used for fitting/selection.
FROZEN_SEASON = 2025
