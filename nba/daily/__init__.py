"""Daily forward-prediction loop (out-of-sample from the first 2026-27 tip).

Every prediction is written to the append-only ``forward_predictions`` table
strictly BEFORE tip-off and scored later into ``forward_scores``; see
``docs/DAILY_PIPELINE.md``. Read-only with respect to markets: no orders, no
credentials.
"""
