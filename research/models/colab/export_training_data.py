"""Export the rung-4 possession-step training set to a local parquet file.

This is the artifact the maintainer uploads to Google Drive/Colab (per
``research/models/colab/README.md``) -- everything downstream (the training
notebook) consumes only this parquet, never the live DuckDB file (which
is never committed/shipped anywhere per CLAUDE.md).

Memory discipline (this box is 8GB and GPU-less, see ``NEXT_SESSION.md``):
the join between ``possessions``/``games`` and the as-of team-ratings
table is issued as a single DuckDB ``COPY ... TO ... (FORMAT PARQUET)``
statement (see ``research.features.possession_step_features.
possession_step_join_sql``) so DuckDB's own streaming/spilling execution
engine does the work -- the full result set is never materialized as a
Python/polars object in this process. The only thing held in memory
up front is the small team-ratings table (one row per team-game, a few
thousand rows even over multiple seasons), registered back into DuckDB
as a view so the join can reference it.

Usage (exact maintainer command, see README):

    uv run python -m research.models.colab.export_training_data \\
        --db nba/db/nba.duckdb --out research/models/colab/possession_steps.parquet

Opens the real DB ``read_only=True`` (CLAUDE.md "DuckDB is single-writer")
-- this script never writes to ``nba.duckdb``, only to the output parquet
file on local disk.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

from nba.db.connect import connect
from research.features.possession_features import build_team_possession_rates
from research.features.possession_step_features import possession_step_join_sql


def export_training_parquet(
    con: duckdb.DuckDBPyConnection,
    out_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    """Streams the possession-step join to ``out_path`` as parquet; returns the row count.

    ``start_date``/``end_date`` (inclusive, ``YYYY-MM-DD``) optionally
    restrict the export to a date range -- useful for a quick Colab smoke
    run before committing to the full multi-season export.
    """
    team_ratings = build_team_possession_rates(con)
    if team_ratings.height == 0:
        return 0
    con.register("team_ratings", team_ratings)
    try:
        sql = possession_step_join_sql()
        filters = []
        if start_date is not None:
            filters.append(f"poss.game_date >= DATE '{start_date}'")
        if end_date is not None:
            filters.append(f"poss.game_date <= DATE '{end_date}'")
        if filters:
            # The join's outer SELECT has no WHERE clause to hook into
            # directly, so wrap it in a filtering subquery instead of
            # string-splicing into the middle of the CTE -- simpler and
            # just as streaming (DuckDB pushes the filter down).
            where_clause = " AND ".join(filters)
            sql = f"SELECT * FROM ({sql}) WHERE {where_clause.replace('poss.', '')}"
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY ({sql}) TO '{out_path}' (FORMAT PARQUET)")
        (n_rows,) = con.execute(f"SELECT COUNT(*) FROM '{out_path}'").fetchone()  # type: ignore[misc]
        return int(n_rows)
    finally:
        con.unregister("team_ratings")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="nba/db/nba.duckdb", help="Path to the DuckDB file.")
    parser.add_argument(
        "--out",
        default="research/models/colab/possession_steps.parquet",
        help="Output parquet path (upload this to Drive/Colab).",
    )
    parser.add_argument("--start-date", default=None, help="Optional YYYY-MM-DD lower bound.")
    parser.add_argument("--end-date", default=None, help="Optional YYYY-MM-DD upper bound.")
    args = parser.parse_args(argv)

    con = connect(args.db, read_only=True)
    try:
        n_rows = export_training_parquet(con, args.out, args.start_date, args.end_date)
    finally:
        con.close()

    print(f"Wrote {n_rows} possession rows to {args.out}")
    if n_rows == 0:
        print(
            "WARNING: 0 rows exported -- the possessions table is empty "
            "(e.g. the parser hasn't been run against the real DB yet).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
