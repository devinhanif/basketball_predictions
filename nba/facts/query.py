"""As-of reads of the fact store: everything knowable about a game at time ``t``.

Mirrors the forward path's "stamped before tip" rule: asking for a time after the game's tip
raises unless ``allow_post_tip=True`` (backtest/audit use only).
"""

from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl

#: Per-game player predicates; their ``object`` is the game entity, which also defines the roster.
#: Reporter claims (``claimed_<type>``, nba/ingest/reporter_facts.py) are game-scoped the same way
#: and count too, so "what was known at T-60" includes them, marked by source and confidence.
GAME_PREDICATES = ("listed_status", "announced_starter", "played_minutes")
CLAIMED_PREFIX = "claimed_"


class PostTipError(ValueError):
    """Raised when an as-of read is requested after tip-off (or tip is unknown)."""


def _naive_utc(t: dt.datetime) -> dt.datetime:
    if t.tzinfo is not None:
        t = t.astimezone(dt.UTC).replace(tzinfo=None)
    return t


def _game_entity(game_id: str) -> str:
    return game_id if game_id.startswith("game:") else f"game:{game_id}"


def game_tip(con: duckdb.DuckDBPyConnection, game_id: str) -> dt.datetime | None:
    """Tip-off (naive UTC) from the latest-known ``tips_at`` fact, or None."""
    row = con.execute(
        "SELECT valid_from FROM facts WHERE subject = ? AND predicate = 'tips_at' "
        "ORDER BY known_at DESC, fact_id DESC LIMIT 1",
        [_game_entity(game_id)],
    ).fetchone()
    return row[0] if row else None


_SQL = """
WITH teams AS (
    SELECT DISTINCT object AS id FROM facts
    WHERE subject = $g AND predicate IN ('home_team', 'away_team') AND known_at <= $t
), players AS (
    SELECT DISTINCT subject AS id FROM facts
    WHERE object = $g
      AND (predicate IN ('listed_status', 'announced_starter', 'played_minutes')
           OR predicate LIKE 'claimed%')
      AND known_at <= $t
)
SELECT * FROM facts
WHERE known_at <= $t
  AND (
    subject = $g
    OR (subject IN (SELECT id FROM teams)
        AND (valid_from IS NULL OR valid_from <= $ref) AND (valid_to IS NULL OR valid_to > $ref))
    OR (subject IN (SELECT id FROM players)
        AND (object IS NULL OR object NOT LIKE 'game:%' OR object = $g)
        AND (valid_from IS NULL OR valid_from <= $ref) AND (valid_to IS NULL OR valid_to > $ref))
  )
ORDER BY known_at, fact_id
"""


def as_of(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    t: dt.datetime,
    *,
    allow_post_tip: bool = False,
) -> pl.DataFrame:
    """Every fact about the game, its two teams and the players with a game-scoped fact known
    by ``t``, with ``known_at <= t``. Players and teams appear only once a fact known by ``t``
    ties them to the game (the roster is never inferred from later facts)."""
    t = _naive_utc(t)
    tip = game_tip(con, game_id)
    if not allow_post_tip:
        if tip is None:
            raise PostTipError(f"{game_id}: no tips_at fact, cannot verify t is before tip")
        if t > tip:
            raise PostTipError(f"{game_id}: t={t} is after tip {tip}; pass allow_post_tip=True")
    ref = tip if tip is not None else t
    return con.execute(_SQL, {"g": _game_entity(game_id), "t": t, "ref": ref}).pl()


def known_before_tip(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    minutes_before: float,
    *,
    allow_post_tip: bool = False,
) -> pl.DataFrame:
    """:func:`as_of` at ``tip - minutes_before`` (the tip is read from the facts)."""
    if minutes_before < 0 and not allow_post_tip:
        raise PostTipError("minutes_before < 0 is after tip; pass allow_post_tip=True")
    tip = game_tip(con, game_id)
    if tip is None:
        raise PostTipError(f"{game_id}: no tips_at fact")
    return as_of(
        con, game_id, tip - dt.timedelta(minutes=minutes_before), allow_post_tip=allow_post_tip
    )
