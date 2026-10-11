"""Separate DuckDB file for lineup snapshots (single writer = the collector).

Kept out of ``nba.duckdb`` so the 5-minute poller never contends with the daily pipeline or
long evals for the single DuckDB writer lock. All timestamps are naive UTC.

Tables
------
``snapshot_log``      one row per HTTP poll (also failures), with the raw file path and sha256.
``lineup_snapshots``  one row per (snapshot, game, team, player) parsed from a 200 response.
``game_tips``         tip-off per game (schedule, or parsed from the feed's status text).
``lineup_snapshots_latest``  VIEW: one row per (snapshot, game, team, player); see ``DDL``.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import duckdb

from nba.ingest.cache import DEFAULT_DATA_DIR
from nba.lineups.source import LineupRow, RawFetch

LINEUPS_DIR = DEFAULT_DATA_DIR / "lineups"
DEFAULT_LINEUPS_DB = LINEUPS_DIR / "lineups.duckdb"
T30_LEAD_MINUTES = 30  # the shadow model's information cutoff: tip-off minus 30 minutes

DDL = """
CREATE TABLE IF NOT EXISTS snapshot_log (
    snapshot_id VARCHAR PRIMARY KEY,   -- fetched_at (iso) + '_' + sha256[:8]
    fetched_at TIMESTAMP,
    game_date DATE,                    -- Eastern game date requested
    url VARCHAR,
    http_status INT,
    last_modified TIMESTAMP,           -- HTTP Last-Modified (source publish time)
    etag VARCHAR,
    sha256 VARCHAR,
    raw_path VARCHAR,
    n_games INT,
    n_rows INT,
    note VARCHAR
);
CREATE TABLE IF NOT EXISTS lineup_snapshots (
    snapshot_id VARCHAR,
    fetched_at TIMESTAMP,
    game_date DATE,
    game_id VARCHAR,
    game_status INT,
    game_status_text VARCHAR,
    team_id INT,
    team_abbr VARCHAR,
    is_home BOOLEAN,
    player_id INT,
    player_name VARCHAR,
    lineup_status VARCHAR,             -- 'Expected' | 'Confirmed'
    announced_starter BOOLEAN,         -- feed position is non-empty
    position VARCHAR,
    roster_status VARCHAR,             -- 'Active' | 'Inactive'
    source_ts TIMESTAMP                -- the feed's own timestamp (converted ET -> UTC)
);
CREATE TABLE IF NOT EXISTS game_tips (
    game_id VARCHAR PRIMARY KEY,
    game_date DATE,
    tipoff TIMESTAMP,
    source VARCHAR,                    -- 'schedule' | 'status_text'
    recorded_at TIMESTAMP
);
-- The feed lists a player twice in one snapshot when he is in the starters block (position set,
-- often 'Expected') and in the confirmed-roster block (position ''): 240 duplicate groups in 20
-- of 39 snapshots on 2026-10-09/10, which made nba/daily/t30.py read a mixed "Confirmed/Expected"
-- status and skip the game. Raw rows stay append-only; consumers read this view:
--   announced_starter = true if ANY row has it (the OR gives exactly 5 starters in 60 of 60
--   duplicated team-snapshots; "latest row wins" gave 0 in 18), lineup_status and the other
--   descriptive columns come from the row with the latest source_ts, position = the non-empty one.
CREATE OR REPLACE VIEW lineup_snapshots_latest AS
SELECT snapshot_id,
       arg_max(fetched_at, source_ts) AS fetched_at,
       arg_max(game_date, source_ts) AS game_date,
       game_id,
       arg_max(game_status, source_ts) AS game_status,
       arg_max(game_status_text, source_ts) AS game_status_text,
       team_id,
       arg_max(team_abbr, source_ts) AS team_abbr,
       arg_max(is_home, source_ts) AS is_home,
       player_id,
       arg_max(player_name, source_ts) AS player_name,
       arg_max(lineup_status, source_ts) AS lineup_status,
       bool_or(announced_starter) AS announced_starter,
       max(position) AS position,
       arg_max(roster_status, source_ts) AS roster_status,
       max(source_ts) AS source_ts
FROM lineup_snapshots
GROUP BY snapshot_id, game_id, team_id, player_id;
"""


def connect_lineups(
    path: Path | str = DEFAULT_LINEUPS_DB, *, read_only: bool = False, retries: int = 10
) -> duckdb.DuckDBPyConnection:
    """Open the lineups DB. DuckDB allows one writer OR many readers across processes, so a
    tick that collides with the poller's brief write lock is retried (``retries`` x 3 s).
    A missing file opened read-only yields an empty in-memory store (no snapshots)."""
    p = Path(path)
    if read_only and not p.exists():
        mem = duckdb.connect(":memory:")
        mem.execute(DDL)
        return mem
    if not read_only:
        p.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            con = duckdb.connect(str(p), read_only=read_only)
            break
        except duckdb.IOException:
            if attempt == retries - 1:
                raise
            time.sleep(3.0)
    if not read_only:
        con.execute(DDL)
    return con


def snapshot_id_for(fetch: RawFetch) -> str:
    return f"{fetch.fetched_at.strftime('%Y%m%dT%H%M%S%f')}_{fetch.sha256[:8]}"


def last_fetch_time(con: duckdb.DuckDBPyConnection, game_date: date) -> datetime | None:
    row = con.execute(
        "SELECT max(fetched_at) FROM snapshot_log WHERE game_date = ?", [game_date]
    ).fetchone()
    return row[0] if row and row[0] is not None else None


def save_raw(fetch: RawFetch, game_date: date, root: Path = LINEUPS_DIR) -> Path:
    """Raw JSON kept for replay: ``raw/<game_date>/<snapshot_id>.json``."""
    path = root / "raw" / game_date.isoformat() / f"{snapshot_id_for(fetch)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fetch.body)
    return path


def record_snapshot(
    con: duckdb.DuckDBPyConnection,
    fetch: RawFetch,
    game_date: date,
    rows: list[LineupRow] | None,
    raw_path: Path | None,
    note: str = "",
) -> str:
    """Idempotent on ``snapshot_id`` (a repeated record of the same fetch is a no-op)."""
    sid = snapshot_id_for(fetch)
    exists = con.execute("SELECT 1 FROM snapshot_log WHERE snapshot_id = ?", [sid]).fetchone()
    if exists:
        return sid
    n_games = len({r.game_id for r in rows}) if rows else 0
    con.execute(
        "INSERT INTO snapshot_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [sid, fetch.fetched_at, game_date, fetch.url, fetch.status, fetch.last_modified,
         fetch.etag, fetch.sha256, str(raw_path) if raw_path else None, n_games,
         len(rows) if rows else 0, note],
    )  # fmt: skip
    if rows:
        con.executemany(
            "INSERT INTO lineup_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (sid, fetch.fetched_at, game_date, r.game_id, r.game_status, r.game_status_text,
                 r.team_id, r.team_abbr, r.is_home, r.player_id, r.player_name, r.lineup_status,
                 r.announced_starter, r.position, r.roster_status, r.source_ts)
                for r in rows
            ],
        )  # fmt: skip
    return sid


def upsert_tip(
    con: duckdb.DuckDBPyConnection,
    game_id: str,
    game_date: date,
    tipoff: datetime,
    source: str,
    recorded_at: datetime,
) -> None:
    """Schedule tips override status-text tips; a status-text tip never overrides a schedule."""
    row = con.execute("SELECT source FROM game_tips WHERE game_id = ?", [game_id]).fetchone()
    if row is not None and row[0] == "schedule" and source != "schedule":
        return
    con.execute("DELETE FROM game_tips WHERE game_id = ?", [game_id])
    con.execute(
        "INSERT INTO game_tips VALUES (?,?,?,?,?)",
        [game_id, game_date, tipoff, source, recorded_at],
    )


def tips_for_date(con: duckdb.DuckDBPyConnection, game_date: date) -> dict[str, datetime]:
    rows = con.execute(
        "SELECT game_id, tipoff FROM game_tips WHERE game_date = ?", [game_date]
    ).fetchall()
    return {str(g): t for g, t in rows}


def latest_snapshot_before(
    con: duckdb.DuckDBPyConnection, game_id: str, before: datetime
) -> tuple[str, datetime] | None:
    """Latest 200-response snapshot that includes ``game_id`` and was FETCHED strictly
    before ``before``. This is the only reader the T-30 path uses (cutoff discipline)."""
    row = con.execute(
        "SELECT snapshot_id, fetched_at FROM lineup_snapshots "
        "WHERE game_id = ? AND fetched_at < ? "
        "GROUP BY snapshot_id, fetched_at ORDER BY fetched_at DESC LIMIT 1",
        [game_id, before],
    ).fetchone()
    return (str(row[0]), row[1]) if row else None


def games_with_tips(
    con: duckdb.DuckDBPyConnection, *, include_unseen: bool = False
) -> list[tuple[str, datetime, int, int]]:
    """``(game_id, tipoff, home_team, away_team)`` for every tip-known game that has at
    least one snapshot (teams come from the snapshots; the feed marks home/away).

    ``include_unseen=True`` also returns tip-known games with NO snapshot at all (the day's
    file was never published or never parsed), with team ids 0. The T-30 run needs them so a
    game the feed never covered is RECORDED as skipped (``no_snapshot_before_t30``) instead of
    vanishing from the shadow arm's denominator."""
    join = "LEFT JOIN" if include_unseen else "JOIN"
    rows = con.execute(
        f"""
        SELECT t.game_id, t.tipoff,
               max(CASE WHEN s.is_home THEN s.team_id END),
               max(CASE WHEN NOT s.is_home THEN s.team_id END)
        FROM game_tips t {join} lineup_snapshots s USING (game_id)
        GROUP BY t.game_id, t.tipoff ORDER BY t.tipoff, t.game_id
        """  # noqa: S608
    ).fetchall()
    if include_unseen:
        return [(str(g), tip, int(h or 0), int(a or 0)) for g, tip, h, a in rows]
    return [
        (str(g), tip, int(h), int(a)) for g, tip, h, a in rows if h is not None and a is not None
    ]
