"""The game explainer: one self-contained HTML page per game, built from stored rows only.

``python -m nba.explain game --game-id <id> --db <db> --odds-db <odds db> --out <html>``

Read-only by construction: every database is opened with ``read_only=True`` and nothing is ever
refit. What the page shows is what the daily job stored before tip-off (``made_at <= tip - 60``).
Publishing a page anywhere outside this machine is Devin's decision (DECISIONS 2026-10-09).
"""
