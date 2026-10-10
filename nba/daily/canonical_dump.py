"""Canonical dump of a replay: what the daily process produced, minus when it ran.

The safety oracle for restructuring (docs/DESIGN_RESTRUCTURE.md s8.1): replay a season through
the real daily process on a DB copy, dump every row of ``forward_predictions`` and
``forward_scores`` in a fixed order with the run-time columns removed, and hash the result.
Two replays of the same code must give the same hash; a refactor is safe when its hash equals
HEAD's. Day 0 proves the oracle itself is deterministic by replaying HEAD twice.

Usage::

    python -m nba.daily.canonical_dump --db data/rehearsal/oracle_a.duckdb --out dump_a.csv
    python -m nba.daily.canonical_dump --db A.duckdb --db B.duckdb   # compare two replays
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import duckdb
import polars as pl

#: Columns kept, in order. ``run_id``, ``made_at``, ``scored_at`` say when the code ran, not what
#: it decided, and are excluded. ``tipoff`` is data, not run time, and stays.
PREDICTION_COLUMNS = (
    "game_id",
    "tipoff",
    "model_name",
    "version",
    "target",
    "player_id",
    "prediction",
)
SCORE_COLUMNS = (
    "game_id",
    "game_date",
    "season",
    "model_name",
    "version",
    "target",
    "player_id",
    "y",
    "pred",
    "log_loss",
    "brier",
    "crps",
    "status",
)
SORT_KEY = ("game_id", "model_name", "version", "target", "player_id")


def canonical_frames(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, pl.DataFrame]:
    """The two tables, projected to their canonical columns and sorted on the canonical key."""

    def select(table: str, columns: tuple[str, ...]) -> pl.DataFrame:
        # Order on EVERY kept column: a player-game has one row per pre-tip snapshot, and the key
        # alone leaves those rows tied (2,118 tied groups in a 20-date replay), so the hash would
        # depend on insertion order rather than on content.
        order = ", ".join(SORT_KEY + tuple(c for c in columns if c not in SORT_KEY))
        sql = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}"
        return con.execute(sql).pl()

    return select("forward_predictions", PREDICTION_COLUMNS), select(
        "forward_scores", SCORE_COLUMNS
    )


def canonical_csv(con: duckdb.DuckDBPyConnection) -> str:
    """Both tables as one CSV text: a header line naming the table, then its rows."""
    preds, scores = canonical_frames(con)
    return (
        "# forward_predictions\n"
        + preds.write_csv(float_precision=None)
        + "# forward_scores\n"
        + scores.write_csv(float_precision=None)
    )


def dump(db_path: Path, out: Path | None = None) -> tuple[str, int, int]:
    """Write the canonical CSV (if ``out``) and return ``(sha256, n_predictions, n_scores)``."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        text = canonical_csv(con)
        preds, scores = canonical_frames(con)
    finally:
        con.close()
    if out is not None:
        out.write_text(text)
    return hashlib.sha256(text.encode()).hexdigest(), preds.height, scores.height


def first_difference(a: Path, b: Path) -> str | None:
    """A one-line description of the first differing row between two replays, or None."""
    ca, cb = duckdb.connect(str(a), read_only=True), duckdb.connect(str(b), read_only=True)
    try:
        for name, (fa, fb) in zip(
            ("forward_predictions", "forward_scores"),
            zip(canonical_frames(ca), canonical_frames(cb), strict=True),
            strict=True,
        ):
            if fa.height != fb.height:
                return f"{name}: {fa.height} rows vs {fb.height}"
            if fa.equals(fb):
                continue
            for i, (ra, rb) in enumerate(zip(fa.iter_rows(), fb.iter_rows(), strict=True)):
                if ra != rb:
                    return f"{name} row {i}: {ra} vs {rb}"
    finally:
        ca.close()
        cb.close()
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.daily.canonical_dump", description=__doc__)
    ap.add_argument(
        "--db", type=Path, action="append", required=True, help="replay DB (repeat to compare)"
    )
    ap.add_argument("--out", type=Path, help="write the canonical CSV here (single --db only)")
    args = ap.parse_args(argv)
    if len(args.db) > 2 or (args.out and len(args.db) != 1):
        ap.error("give one --db with --out, or exactly two --db to compare")
    hashes = []
    for db in args.db:
        sha, n_pred, n_score = dump(db, args.out)
        hashes.append(sha)
        print(f"{db}: sha256 {sha}  predictions {n_pred}  scores {n_score}")
    if len(hashes) == 2:
        if hashes[0] == hashes[1]:
            print("IDENTICAL")
            return 0
        print(f"DIFFERENT: {first_difference(args.db[0], args.db[1])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
