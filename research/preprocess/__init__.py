"""Preprocessing / representation-learning layer between ingest and models.

Owned by the data-innovator role (see ``.claude/agents/data-innovator.md``):
leakage-safe, best-practice preprocessing plus novel representations
(co-occurrence embeddings, depth-chart / minutes-ceiling signals, etc.)
that the obvious rollups in ``nba/features/`` miss. Every builder here
follows the same strictly as-of discipline as ``nba/features/`` --
``ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`` ordered by
``(game_date, game_id)`` -- and ships a planted-future-game no-leakage test.
"""

from __future__ import annotations
