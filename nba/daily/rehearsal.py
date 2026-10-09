"""Replay helpers for dress rehearsals on a COPY of the database (never the real file).

``truncate_for_replay`` hides everything on/after a replay date: the ``games`` rows stay
(scores set to NULL, like a scheduled game) so ``--schedule-from-db`` still finds the
slate, while every other table with a ``game_id`` loses its rows for those games and
as-of snapshot tables lose snapshots dated on/after the replay date.
``restore_results`` re-adds the hidden results (scores and per-game rows) up to a date
from the untouched full copy, so settlement can run afterwards.

CLI: ``python -m nba.daily.rehearsal truncate|restore --full F --work W --date D``.
"""

from __future__ import annotations

import argparse
from datetime import date

import duckdb

#: Tables never touched: they hold pre-game information whose own timestamps are filtered
#: by the pipeline, or forward-pipeline outputs.
KEEP_TABLES = frozenset(
    {"player_availability", "forward_predictions", "forward_scores", "forward_scores_elig"}
)
SNAPSHOT_TABLES = {"player_rates": "as_of"}


def _tables(con: duckdb.DuckDBPyConnection, catalog: str = "") -> dict[str, list[str]]:
    q = (
        "SELECT t.table_name, c.column_name FROM information_schema.tables t "
        "JOIN information_schema.columns c USING (table_catalog, table_schema, table_name) "
        "WHERE t.table_schema = 'main' AND t.table_type = 'BASE TABLE'"
    )
    q += (
        f" AND t.table_catalog = '{catalog}'"
        if catalog
        else " AND t.table_catalog = current_database()"
    )
    out: dict[str, list[str]] = {}
    for t, c in con.execute(q + " ORDER BY t.table_name, c.ordinal_position").fetchall():
        out.setdefault(str(t), []).append(str(c))
    return out


def truncate_for_replay(con: duckdb.DuckDBPyConnection, replay_date: date) -> dict[str, int]:
    """Hide results on/after ``replay_date``; returns rows removed per table."""
    removed: dict[str, int] = {}
    ids = "(SELECT game_id FROM games WHERE game_date >= ?)"
    for table, cols in _tables(con).items():
        if table in KEEP_TABLES or table == "games":
            continue
        if "game_id" in cols:
            n = con.execute(f"DELETE FROM {table} WHERE game_id IN {ids}", [replay_date]).fetchone()
        elif table in SNAPSHOT_TABLES:
            col = SNAPSHOT_TABLES[table]
            n = con.execute(f"DELETE FROM {table} WHERE {col} >= ?", [replay_date]).fetchone()
        else:
            continue
        removed[table] = int(n[0]) if n else 0
    n = con.execute(
        "UPDATE games SET home_pts = NULL, away_pts = NULL WHERE game_date >= ?", [replay_date]
    ).fetchone()
    removed["games"] = int(n[0]) if n else 0
    return removed


def restore_results(
    con: duckdb.DuckDBPyConnection, full_path: str, through: date, since: date
) -> dict[str, int]:
    """Re-add results for games with ``since <= game_date <= through`` from ``full_path``."""
    con.execute(f"ATTACH '{full_path}' AS full_db (READ_ONLY)")
    added: dict[str, int] = {}
    try:
        rng = "SELECT game_id FROM full_db.games WHERE game_date >= ? AND game_date <= ?"
        n = con.execute(
            "UPDATE games SET home_pts = f.home_pts, away_pts = f.away_pts "
            "FROM full_db.games f WHERE games.game_id = f.game_id "
            "AND f.game_date >= ? AND f.game_date <= ?",
            [since, through],
        ).fetchone()
        added["games"] = int(n[0]) if n else 0
        full = _tables(con, "full_db")
        for table, cols in _tables(con).items():
            if table in KEEP_TABLES or table == "games" or "game_id" not in cols:
                continue
            if table not in full:
                continue
            shared = ", ".join(c for c in cols if c in full[table])
            con.execute(
                f"DELETE FROM {table} WHERE game_id IN ({rng})", [since, through]
            )  # idempotent re-run
            n = con.execute(
                f"INSERT INTO {table} ({shared}) SELECT {shared} FROM full_db.{table} "
                f"WHERE game_id IN ({rng})",
                [since, through],
            ).fetchone()
            added[table] = int(n[0]) if n else 0
    finally:
        con.execute("DETACH full_db")
    return added


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.daily.rehearsal")
    ap.add_argument("action", choices=["truncate", "restore"])
    ap.add_argument("--work", required=True, help="the COPY to modify")
    ap.add_argument("--full", help="untouched full copy (restore only)")
    ap.add_argument("--date", required=True, type=date.fromisoformat)
    ap.add_argument("--through", type=date.fromisoformat, help="restore up to this date")
    a = ap.parse_args(argv)
    if a.work.rsplit("/", 1)[-1] == "nba.duckdb":
        raise SystemExit("refusing: --work must be a copy, not nba.duckdb")
    con = duckdb.connect(a.work)
    if a.action == "truncate":
        print(truncate_for_replay(con, a.date))
    else:
        if not a.full:
            raise SystemExit("--full required for restore")
        print(restore_results(con, a.full, a.through or a.date, a.date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
