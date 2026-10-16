"""Re-stamp stored official injury-report rows with the time printed in each PDF header.

Incident (2026-10-15): hour-only file names (``..._06PM.pdf``) print ``06:30 PM``; ingest stamped
the file name, so 1,379 of 1,682 stored reports were stamped 30 (or 45) minutes early and some
games used a report published after tip-60. This module computes the corrected ``as_of`` for every
stored report from the raw PDFs on disk and applies it to a COPY of the database.

    uv run python -m nba.data.restamp_reports audit --db nba.duckdb      # read-only
    uv run python -m nba.data.restamp_reports apply --src nba.duckdb \\
        --dst data/rehearsal/stampfix.duckdb                              # copy, then re-stamp

``apply`` refuses a destination that is the source (or any ``nba.duckdb``), copies the file, and
only ever opens the copy for writing. Applying to production is a separate, logged act (see
docs/DATA_VERSIONING.md); this module has no such path.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nba.parse.availability import read_report_header_time

SOURCE = "nba_official_report"
_FILE_RE = re.compile(r"^Injury-Report_(\d{4})-(\d{2})-(\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)\.pdf$")


def filename_time(name: str) -> datetime:
    """The (old) stamp: the time encoded in the file name, naive ET."""
    m = _FILE_RE.match(name)
    if m is None:
        raise ValueError(f"not an official-report filename: {name}")
    y, mo, d, hh, mm, ap = m.groups()
    return datetime(int(y), int(mo), int(d), int(hh) % 12 + (12 if ap == "PM" else 0), int(mm or 0))


@dataclass(frozen=True)
class RestampPlan:
    """One row per PDF: ``file, old_stamp, new_stamp`` (``new_stamp`` None = header unreadable)."""

    frame: pl.DataFrame

    @property
    def unreadable(self) -> list[str]:
        return self.frame.filter(pl.col("new_stamp").is_null())["file"].to_list()

    def collisions(self) -> pl.DataFrame:
        """New stamps shared by two files, or old stamps shared by two files: reports that would
        merge (or are ambiguous to map)."""
        f = self.frame
        new = f.group_by("new_stamp").agg(pl.col("file")).filter(pl.col("file").list.len() > 1)
        old = f.group_by("old_stamp").agg(pl.col("file")).filter(pl.col("file").list.len() > 1)
        return pl.concat(
            [
                new.rename({"new_stamp": "stamp"}).with_columns(pl.lit("new").alias("kind")),
                old.rename({"old_stamp": "stamp"}).with_columns(pl.lit("old").alias("kind")),
            ]
        )


def build_plan(raw_dir: Path) -> RestampPlan:
    rows = []
    for p in sorted(raw_dir.glob("Injury-Report_*.pdf")):
        rows.append(
            {
                "file": p.name,
                "old_stamp": filename_time(p.name),
                "new_stamp": read_report_header_time(p),
            }
        )
    frame = pl.DataFrame(
        rows,
        schema={
            "file": pl.Utf8,
            "old_stamp": pl.Datetime("us"),
            "new_stamp": pl.Datetime("us"),
        },
    )
    return RestampPlan(frame)


def _stored_stamps(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    rows = con.execute(
        "SELECT CAST(as_of AS TIMESTAMP), count(*) FROM player_availability "
        "WHERE source = ? GROUP BY 1",
        [SOURCE],
    ).fetchall()
    return pl.DataFrame(rows, schema={"as_of": pl.Datetime("us"), "n_rows": pl.Int64}, orient="row")


def audit(con: duckdb.DuckDBPyConnection, plan: RestampPlan) -> dict[str, Any]:
    """Preflight: every stored stamp must equal some PDF's printed header time.

    ``stale`` = stored stamps that equal a file-name time but not that file's header time (the
    bug); ``orphan`` = stored stamps matching no file at all."""
    stored = _stored_stamps(con)
    ok = set(plan.frame["new_stamp"].drop_nulls().to_list())
    bad = stored.filter(~pl.col("as_of").is_in(list(ok)))
    old_map = {r[0]: r[1] for r in plan.frame.select("old_stamp", "new_stamp").iter_rows()}
    stale = bad.filter(pl.col("as_of").is_in(list(old_map)))
    return {
        "stored_stamps": stored.height,
        "stored_rows": int(stored["n_rows"].sum()),
        "files": plan.frame.height,
        "unreadable_headers": len(plan.unreadable),
        "stamps_equal_to_header": stored.height - bad.height,
        "stamps_not_equal_to_header": bad.height,
        "rows_not_equal_to_header": int(bad["n_rows"].sum()) if bad.height else 0,
        "stale_filename_stamps": stale.height,
        "orphan_stamps": bad.height - stale.height,
        "collisions": plan.collisions().height,
    }


def _guard_destination(src: Path, dst: Path) -> None:
    if dst.name == "nba.duckdb" or dst.resolve() == src.resolve():
        raise ValueError(f"refusing to write {dst}: the re-stamp only ever targets a copy")
    if dst.exists():
        raise FileExistsError(f"{dst} exists; remove it deliberately or pick a new name")


def apply_to_copy(src: Path, dst: Path, plan: RestampPlan) -> dict[str, Any]:
    """Copy ``src`` to ``dst`` and re-stamp the copy. Returns before/after counts."""
    _guard_destination(src, dst)
    if plan.unreadable:
        raise ValueError(f"{len(plan.unreadable)} report headers unreadable: {plan.unreadable[:5]}")
    coll = plan.collisions()
    if coll.height:
        raise ValueError(f"stamp collisions would merge reports: {coll.head(5).to_dicts()}")
    # lock probe: a read-only open fails if a writer holds the file
    duckdb.connect(str(src), read_only=True).close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    con = duckdb.connect(str(dst))
    try:
        before = audit(con, plan)
        changed = plan.frame.filter(pl.col("old_stamp") != pl.col("new_stamp"))
        con.register("restamp_map", changed.select("old_stamp", "new_stamp").to_arrow())
        con.execute("BEGIN")
        n = con.execute(
            "UPDATE player_availability a SET as_of = m.new_stamp, pulled_at = m.new_stamp "
            "FROM restamp_map m WHERE a.source = ? AND a.as_of = m.old_stamp",
            [SOURCE],
        ).fetchone()
        con.execute("COMMIT")
        after = audit(con, plan)
    finally:
        con.close()
    return {"before": before, "after": after, "rows_updated": n[0] if n else None}


def change_table(src_con: duckdb.DuckDBPyConnection, plan: RestampPlan) -> pl.DataFrame:
    """Rows, reports and games whose stamp changes, by season (2025 appears as counts only)."""
    changed = plan.frame.filter(pl.col("old_stamp") != pl.col("new_stamp"))
    src_con.register("restamp_changed", changed.select("old_stamp", "new_stamp").to_arrow())
    rows = src_con.execute(
        "SELECT g.season, count(*) AS rows_changed, count(DISTINCT a.as_of) AS reports_changed, "
        "count(DISTINCT a.game_id) AS games_changed, "
        "avg(date_diff('minute', m.old_stamp, m.new_stamp)) AS mean_shift_min "
        "FROM player_availability a JOIN restamp_changed m ON a.as_of = m.old_stamp "
        "LEFT JOIN games g ON g.game_id = a.game_id WHERE a.source = ? GROUP BY 1 ORDER BY 1",
        [SOURCE],
    ).fetchall()
    return pl.DataFrame(
        rows,
        schema={
            "season": pl.Int64,
            "rows_changed": pl.Int64,
            "reports_changed": pl.Int64,
            "games_changed": pl.Int64,
            "mean_shift_min": pl.Float64,
        },
        orient="row",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit", help="read-only: stored stamps vs PDF headers")
    a.add_argument("--db", type=Path, default=Path("nba.duckdb"))
    a.add_argument("--raw", type=Path, default=Path("data/availability_official"))
    b = sub.add_parser("apply", help="copy --src to --dst and re-stamp the copy")
    b.add_argument("--src", type=Path, default=Path("nba.duckdb"))
    b.add_argument("--dst", type=Path, required=True)
    b.add_argument("--raw", type=Path, default=Path("data/availability_official"))
    args = ap.parse_args(argv)
    plan = build_plan(args.raw)
    if args.cmd == "audit":
        con = duckdb.connect(str(args.db), read_only=True)
        try:
            print(json.dumps(audit(con, plan), indent=2, default=str))
            print(change_table(con, plan))
        finally:
            con.close()
        return 0
    res = apply_to_copy(args.src, args.dst, plan)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
