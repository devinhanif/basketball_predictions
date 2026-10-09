"""Append-only forward tables, created migration-safely (CREATE IF NOT EXISTS).

Deliberately NOT in ``nba/db/schema.sql`` (owned elsewhere); the DDL lives
here and is idempotent. Timestamps are naive UTC.

``player_id`` is ``-1`` for team/game-level targets so the natural key
``(game_id, model_name, target, player_id)`` is never NULL.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
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


#: Slate-level diagnostics stamped on rows; they vary run to run (games tip off, reports arrive)
#: without changing the forecast, are read by no scorer, and are excluded from the content hash.
VOLATILE_KEYS = frozenset({"n_games_with_report", "n_slate_games"})


def content_hash(tipoff: datetime, prediction: dict[str, object]) -> str:
    """Stable hash of a row's content: tip-off and the canonical (sorted-key) prediction payload.

    ``made_at`` / ``run_id`` are deliberately excluded; ``tipoff`` is included because the
    eligibility cutoffs (tip-60 / tip-30) are measured from it. :data:`VOLATILE_KEYS` are
    ignored."""
    body = {k: v for k, v in prediction.items() if k not in VOLATILE_KEYS}
    h = hashlib.sha256()
    h.update(tipoff.isoformat().encode())
    h.update(b"\x00")
    h.update(json.dumps(body, sort_keys=True).encode())
    return h.hexdigest()


Key = tuple[str, str, str, str, int]  # model_name, version, target, game_id, player_id


def _key(p: ForwardPrediction) -> Key:
    return (p.model_name, p.version, p.target, p.game_id, p.player_id)


def _latest_hashes(
    con: duckdb.DuckDBPyConnection, game_ids: list[str]
) -> dict[Key, tuple[datetime, str]]:
    """(made_at, content hash) of the latest stored row per key, for the given games."""
    if not game_ids:
        return {}
    marks = ",".join("?" for _ in game_ids)
    rows = con.execute(
        f"""
        SELECT model_name, version, target, game_id, player_id, made_at, tipoff, prediction
        FROM (SELECT *, row_number() OVER (
                  PARTITION BY model_name, version, target, game_id, player_id
                  ORDER BY made_at DESC, run_id DESC) rn
              FROM forward_predictions WHERE game_id IN ({marks}))
        WHERE rn = 1
        """,
        game_ids,
    ).fetchall()
    return {
        (str(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4])): (
            r[5],
            content_hash(r[6], json.loads(r[7])),
        )
        for r in rows
    }


def append_predictions_ex(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    preds: list[ForwardPrediction],
    *,
    dedup: bool = False,
) -> tuple[int, int]:
    """Append predictions; returns ``(rows_written, rows_deduped)``.

    The WHOLE batch is refused if any has made_at >= tipoff (checked before any dedup).
    Append-only: no UPDATE/DELETE is ever issued against this table.

    ``dedup=True`` is write-on-change. A prediction *group* is every row sharing
    ``(game_id, player_id, target)`` in this batch (all arms/models of one forecast). A group is
    skipped only when EVERY member equals the latest stored row for its key (same
    :func:`content_hash`, stored ``made_at <= made_at``); if any member is new or changed, the
    whole group is written. Hence for any cutoff the latest stored row at ``made_at <= cutoff`` has
    exactly the content the full log would have had, and arms of one group always share the
    ``run_id`` of their last write (same-run pairing in the checkpoint is unchanged).
    """
    late = [p.game_id for p in preds if not made_at < p.tipoff]
    if late:
        raise LeakageError(
            f"refusing to write: made_at={made_at.isoformat()} is not before tip-off "
            f"for games {sorted(set(late))[:5]}"
        )
    ensure_tables(con)
    payloads = [json.dumps(p.prediction, sort_keys=True) for p in preds]
    skip: set[int] = set()
    if dedup and preds:
        latest = _latest_hashes(con, sorted({p.game_id for p in preds}))
        groups: dict[tuple[str, int, str], list[int]] = defaultdict(list)
        for i, p in enumerate(preds):
            groups[(p.game_id, p.player_id, p.target)].append(i)
        for members in groups.values():
            unchanged = True
            for i in members:
                prev = latest.get(_key(preds[i]))
                if (
                    prev is None
                    or prev[0] > made_at
                    or prev[1] != content_hash(preds[i].tipoff, preds[i].prediction)
                ):
                    unchanged = False
                    break
            if unchanged:
                skip.update(members)
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
            payloads[i],
        )
        for i, p in enumerate(preds)
        if i not in skip
    ]
    if rows:
        con.executemany("INSERT INTO forward_predictions VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows), len(skip)


def append_predictions(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    made_at: datetime,
    preds: list[ForwardPrediction],
) -> int:
    """Append every prediction (no dedup); returns rows written (see ``append_predictions_ex``)."""
    return append_predictions_ex(con, run_id, made_at, preds)[0]
