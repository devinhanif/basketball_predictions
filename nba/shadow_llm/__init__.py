"""LLM shadow forecaster (docs/LLM_SHADOW.md): a nightly, flag-gated comparison arm.

Never a production input. One request per game at T-60 carrying only as-of facts the pipeline
already holds; rows go to ``forward_predictions`` as ``llm_shadow`` and are refused after tip like
every other forward prediction. Off unless ``NBA_LLM_SHADOW=1``.
"""
