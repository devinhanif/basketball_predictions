"""Research: experiments that reached a verdict, archived under the ledger.

Every module here is importable and its tests run in CI (docs/DECISIONS.md, 2026-10-09),
so every ledger result stays reproducible. Research imports live code (``nba.*``); live code
never imports ``research.*`` (tests/test_layering.py). The map from old path to new path,
with the ledger rows that record each verdict, is research/INDEX.md.
"""
