"""Append-only forward tables, created migration-safely (CREATE IF NOT EXISTS).

Deliberately NOT in ``nba/db/schema.sql`` (owned elsewhere); the DDL lives
here and is idempotent. Timestamps are naive UTC.

``player_id`` is ``-1`` for team/game-level targets so the natural key
``(game_id, model_name, target, player_id)`` is never NULL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import duckdb

NO_PLAYER = -1

DDL = """
CREATE TABLE IF NOT EXISTS forward_predictions (
    run_id VARCHAR,
    made_at TIMESTAMP,
    game_id VARCHAR,
    tipoff TIMESTAMP,
    model_name VARCHAR,
    version VARCHAR,
    target VARCHAR,          -- win_prob_home | pts | reb | ast | fg3m
    player_id INT,           -- -1 for game-level targets
    prediction JSON
);
CREATE TABLE IF NOT EXISTS forward_scores (
    scored_at TIMESTAMP,
    game_id VARCHAR,
    game_date DATE,
    season INT,
    model_name VARCHAR,
    version VARCHAR,
    target VARCHAR,
    player_id INT,
    made_at TIMESTAMP,
    y DOUBLE,
    pred DOUBLE,             -- p(home win) or predictive mean
    log_loss DOUBLE,
    brier DOUBLE,
    crps DOUBLE,
    status VARCHAR           -- 'scored' | 'dnp'
);
"""


class LeakageError(ValueError):
    """A prediction was made at or after tip-off."""


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(DDL)


@dataclass(frozen=True)
class ForwardPrediction:
    game_id: str
    tipoff: datetime
    model_name: str
    version: str
    target: str
    prediction: dict[str, object]
    player_id: int = NO_PLAYER


def append_predictions(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    preds: list[ForwardPrediction],
) -> int:
    """Append predictions; the WHOLE batch is refused if any has made_at >= tipoff.

    Append-only: no UPDATE/DELETE is ever issued against this table.
    """
    late = [p.game_id for p in preds if not made_at < p.tipoff]
    if late:
        raise LeakageError(
            f"refusing to write: made_at={made_at.isoformat()} is not before tip-off "
            f"for games {sorted(set(late))[:5]}"
        )
    ensure_tables(con)
    rows = [
        (
            run_id,
            made_at,
            p.game_id,
            p.tipoff,
            p.model_name,
            p.version,
            p.target,
            p.player_id,
            json.dumps(p.prediction, sort_keys=True),
        )
        for p in preds
    ]
    if rows:
        con.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)
