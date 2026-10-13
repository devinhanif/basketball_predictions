"""Load hand-sourced positions into the history ``players_static`` (SEQ_LEARNING_CURVE_2 gate D4).

Input is a write-once CSV under ``data/raw/manual_positions_*/`` (player_id, source_position,
position, source_url, retrieved_at). A position is written ONLY where the history row exists and
its position is NULL/empty; every write is recorded in the sidecar table
``players_static_position_source`` (player_id, position, source_position, source_url, retrieved_at,
file_sha256, loaded_at). Existing positions are never overwritten.

    uv run python -m nba.ingest.history_positions CSV --sha256 PREFIX --apply   # heavy lock, backup
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from nba.ingest.history_injury import HISTORY_DB
from nba.ingest.history_static_fix import BACKUP_DIR, heavy_lock

SIDECAR = "players_static_position_source"
_VALID = {"Guard", "Forward", "Center"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_positions(csv: Path) -> pl.DataFrame:
    """Parse the CSV; a URL may hold unquoted commas, so the middle fields are re-joined."""
    lines = csv.read_text().splitlines()
    cols = lines[0].split(",")
    n = len(cols)
    rows = []
    for ln in lines[1:]:
        parts = ln.split(",")
        k = len(parts) - n
        if k > 0:  # source_url is the second-to-last field
            parts = [*parts[: n - 2], ",".join(parts[n - 2 : n - 2 + 1 + k]), parts[-1]]
        rows.append(dict(zip(cols, parts, strict=True)))
    return pl.DataFrame(rows).with_columns(pl.col("player_id").cast(pl.Int64))


def apply_positions(
    db: Path,
    csv: Path,
    *,
    expect_sha: str,
    backup_dir: Path = BACKUP_DIR,
    stamp: str | None = None,
) -> dict[str, Any]:
    """Back up, then fill NULL/empty positions and write the sidecar in one transaction."""
    from nba.db.connect import connect_with_retry

    sha = file_sha256(csv)
    if not sha.startswith(expect_sha):
        raise ValueError(f"sha256 {sha[:8]} does not start with {expect_sha}")
    df = read_positions(csv)
    bad = set(df["position"].to_list()) - _VALID
    if bad:
        raise ValueError(f"unmapped positions {bad}")
    stamp = stamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"nba_history_pre_manual_positions_{stamp}.duckdb"
    shutil.copy2(db, backup)
    w = connect_with_retry(db)
    try:
        w.execute("BEGIN")
        w.execute(
            f"CREATE TABLE IF NOT EXISTS {SIDECAR} (player_id INTEGER PRIMARY KEY, "
            "position VARCHAR, source_position VARCHAR, source_url VARCHAR, retrieved_at VARCHAR, "
            "file_sha256 VARCHAR, loaded_at TIMESTAMP)"
        )
        rows_before = w.execute("SELECT count(*) FROM players_static").fetchone()[0]  # type: ignore[index]
        pre = dict(w.execute("SELECT player_id, position FROM players_static").fetchall())
        empty_ids = {i for i, v in pre.items() if not (v or "").strip()}
        todo = df.filter(pl.col("player_id").is_in(list(empty_ids)))
        skipped = sorted(set(df["player_id"].to_list()) - set(todo["player_id"].to_list()))
        t = todo.with_columns(
            pl.lit(sha).alias("file_sha256"), pl.lit(datetime.now()).alias("loaded_at")
        ).select(
            "player_id",
            "position",
            "source_position",
            "source_url",
            "retrieved_at",
            "file_sha256",
            "loaded_at",
        )
        w.register("pos_new", t.to_arrow())
        w.execute(
            "UPDATE players_static SET position = n.position FROM pos_new n "
            "WHERE players_static.player_id = n.player_id "
            "AND (players_static.position IS NULL OR trim(players_static.position) = '')"
        )
        w.execute(f"INSERT OR REPLACE INTO {SIDECAR} SELECT * FROM pos_new")
        w.unregister("pos_new")
        post = dict(w.execute("SELECT player_id, position FROM players_static").fetchall())
        rows_after = w.execute("SELECT count(*) FROM players_static").fetchone()[0]  # type: ignore[index]
        changed = {i for i in post if post[i] != pre.get(i)}
        ok = rows_before == rows_after and changed == set(todo["player_id"].to_list())
        w.execute("COMMIT" if ok else "ROLLBACK")
        return {"backup": str(backup), "ok": ok, "written": todo.height, "skipped_ids": skipped}
    finally:
        w.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.history_positions")
    ap.add_argument("csv", type=Path)
    ap.add_argument("--sha256", required=True, help="expected file sha256 prefix")
    ap.add_argument("--db", type=Path, default=HISTORY_DB)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    sha = file_sha256(args.csv)
    if not sha.startswith(args.sha256):
        print(f"sha mismatch: {sha}")
        return 2
    if not args.apply:
        print("dry run only; sha ok", sha[:8])
        return 0
    with heavy_lock():
        res = apply_positions(args.db, args.csv, expect_sha=args.sha256)
    print(res)
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
