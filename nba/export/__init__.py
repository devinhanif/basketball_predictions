"""Read-only export of the forward log for the app (docs/APP_ARCHITECTURE.md s2-s3).

``python -m nba.export build --date <d> --out <dir>`` opens ``nba.duckdb`` with ``read_only=True``
and writes ``export.duckdb``, ``parlay_of_day.json`` and ``manifest.json`` atomically. The app
reads only the output files, never ``nba.duckdb``, and imports ``nba`` only as ``nba.export.*``.

Guarantees (each pinned by tests/export and tests/test_layering.py):
  * no write path to ``nba.duckdb``, no network, no import of ``app`` or ``research``;
  * the 2025 holdout is refused (``guard.ExportRefused``), only season 2026 (and pre-2025
    seasons for fixtures) is exported, filtered in SQL and re-asserted on the rows;
  * the nightly export job is NOT part of the ``ops/*.sh`` live path closure (post-10-20 decision).
"""

from nba.export.guard import ExportRefused, resolve_seasons
from nba.export.parlay import parlay_of_day
from nba.export.reader import (
    ForwardBundle,
    Window,
    box_scores,
    explainer,
    forward_predictions,
    model_stamps,
    settled_scores,
)

__all__ = [
    "ExportRefused",
    "ForwardBundle",
    "Window",
    "box_scores",
    "explainer",
    "forward_predictions",
    "model_stamps",
    "parlay_of_day",
    "resolve_seasons",
    "settled_scores",
]
