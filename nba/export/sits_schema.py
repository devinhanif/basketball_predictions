# ruff: noqa: E501
"""Stdlib-only constants and column list for the 'if a teammate sits' export step.

``nba.export.build`` imports THIS module at load; the scenario code (``teammate_sits``, which pulls in
lightgbm and the context-residual stack) is imported lazily inside a guard, so a broken import there
can never stop the export (adversary review 2026-11-10, B1).
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 1
#: TEAMMATE_SITS frozen rule (docs/prereg/TEAMMATE_SITS.md), body sha256 first 8.
RULE_SHA8 = "c23f7b36"
#: The stat words recorded in T526 / T526-a. Only stats whose word allows a display are exported.
RULE_WORDS: dict[str, str] = {
    "pts": "HELPS",
    "reb": "HELPS",
    "ast": "INCONCLUSIVE",
    "fg3m": "UNRESOLVED",
}
EXPORT_STATS: tuple[str, ...] = ("pts", "reb")
#: Hard ceiling on rows per night (contract: median ~2,000 with four stats, ~5,000 at the largest slate;
#: two stats carry about half). Past it the table is withheld rather than truncated silently.
MAX_ROWS = 12_000
#: Wall-clock allowance for the whole scenario step; checked between stages (a stage is not interrupted).
DEFAULT_BUDGET_S = 150.0

COLUMNS: list[tuple[str, str]] = [
    ("schema_version", "INTEGER"),
    ("made_at", "TIMESTAMP"),
    ("game_id", "VARCHAR"),
    ("team_id", "BIGINT"),
    ("sitter_id", "BIGINT"),
    ("sitter_status", "VARCHAR"),
    ("player_id", "BIGINT"),
    ("stat", "VARCHAR"),
    ("base_mean", "DOUBLE"),
    ("scn_mean", "DOUBLE"),
    ("delta_mean", "DOUBLE"),
    ("base_q10", "DOUBLE"),
    ("base_q50", "DOUBLE"),
    ("base_q90", "DOUBLE"),
    ("scn_q10", "DOUBLE"),
    ("scn_q50", "DOUBLE"),
    ("scn_q90", "DOUBLE"),
    ("p_play", "DOUBLE"),
    ("rule_word", "VARCHAR"),
]


def absent_info(reason: str, seconds: float = 0.0, **extra: Any) -> dict[str, Any]:
    """The manifest block of a step that produced nothing."""
    return {
        "status": "absent", "reason": reason, "n_rows": 0, "n_scenarios": 0, "n_games": 0,
        "rule_sha8": RULE_SHA8, "stats": list(EXPORT_STATS), "seconds": round(seconds, 2), **extra,
    }  # fmt: skip
