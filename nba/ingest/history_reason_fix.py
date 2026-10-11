"""One-off data fix: strip the trailing ``Previous Status`` words from layout-C ``reason`` rows.

Layout C injury reports (2019-11-20 .. 2019-12-17) print ``Current Status / Reason / Previous
Status`` (2019-12-16 08PM adds ``Previous Reason``). Before the layout-C parser fix the previous
columns were appended to ``reason`` in the HISTORY DB's ``player_availability`` rows. This module
re-parses those cached PDFs and updates ONLY ``reason``, matching stored rows by
(player_id, as_of, game_id, status) paired in insertion order. Dry-run is the default and reads the
DB read-only; ``--apply`` takes the heavy lock, backs up, and writes through ``connect_with_retry``.

    uv run python -m nba.ingest.history_reason_fix            # dry run, read-only
    mkdir data/ops/heavy.lock && uv run python -m nba.ingest.history_reason_fix --apply; \
        rmdir data/ops/heavy.lock
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb
import polars as pl

from nba.ingest.history_injury import (
    HISTORY_DB,
    HISTORY_DIR,
    build_history_resolver,
    report_dt_from_filename,
)
from nba.parse.availability import parse_official_injury_report

START = date(2019, 11, 20)
END = date(2019, 12, 17)
STATUS_WORDS = {"Out", "Doubtful", "Questionable", "Probable", "Available"}
KEY = ("player_id", "as_of", "game_id", "status")


def fingerprint(con: duckdb.DuckDBPyConnection, *, with_reason: bool) -> str:
    cols = "player_id, as_of, game_id, status, source, pulled_at" + (
        ", reason" if with_reason else ""
    )
    rows = con.execute(
        f"SELECT {cols} FROM player_availability ORDER BY player_id, as_of, game_id, status, "
        "source, pulled_at, reason"
    ).fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode())
    return h.hexdigest()


def _is_subsequence(short: list[str], long: list[str]) -> bool:
    it = iter(long)
    return all(tok in it for tok in short)


def compute_plan(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, dict[str, object]]:
    """Return (rowid, old_reason, new_reason) for every stored row in the window + diagnostics."""
    stored = con.execute(
        "SELECT rowid AS rid, player_id, as_of, game_id, status, reason FROM player_availability "
        "WHERE as_of >= ? AND as_of < ? AND source = 'nba_official_report' ORDER BY rowid",
        [datetime(START.year, START.month, START.day), datetime(2019, 12, 18)],
    ).fetchall()
    by_key: dict[tuple[object, ...], list[tuple[int, str | None]]] = defaultdict(list)
    for rid, pid, as_of, gid, status, reason in stored:
        by_key[(pid, as_of, gid, status)].append((rid, reason))

    resolver = build_history_resolver(con, HISTORY_DIR)
    new_by_key: dict[tuple[object, ...], list[str | None]] = defaultdict(list)
    files = sorted((HISTORY_DIR / "availability_official").glob("Injury-Report_*.pdf"))
    n_files = 0
    for path in files:
        rdt = report_dt_from_filename(path.name)
        if not (START <= rdt.date() <= END):
            continue
        n_files += 1
        df = parse_official_injury_report(path, {}, report_dt=rdt, con=con, resolver=resolver)
        for r in df.iter_rows(named=True):
            new_by_key[(r["player_id"], r["as_of"], r["game_id"], r["status"])].append(r["reason"])

    out: list[tuple[int, str | None, str | None]] = []
    removed: Counter[str] = Counter()
    problems: list[str] = []
    for key, olds in by_key.items():
        news = new_by_key.get(key, [])
        if len(news) != len(olds):
            problems.append(f"key {key}: stored {len(olds)} vs parsed {len(news)}")
            continue
        for (rid, old), new in zip(olds, news, strict=True):
            o, n = (old or "").split(), (new or "").split()
            if not _is_subsequence(n, o):
                problems.append(f"rowid {rid}: new reason not a word-subsequence of old")
                continue
            it = iter(o)
            kept = [next(w for w in it if w == tok) for tok in n]
            rem = Counter(o) - Counter(kept)
            removed.update(rem)
            out.append((rid, old, new))
    extra = set(new_by_key) - set(by_key)
    diag: dict[str, object] = {
        "files": n_files,
        "stored_rows": len(stored),
        "planned_rows": len(out),
        "unmatched_new_keys": len(extra),
        "problems": problems[:10],
        "n_problems": len(problems),
        "removed_tokens": removed.most_common(20),
        "old_ended_with_status_word": sum(
            1 for _, o, _ in out if o and o.split()[-1] in STATUS_WORDS
        ),
        "old_ended_with_dash": sum(1 for _, o, _ in out if o and o.split()[-1] == "-"),
    }
    plan = pl.DataFrame(
        out, schema=["rid", "old_reason", "new_reason"], orient="row", infer_schema_length=None
    )
    changed = plan.filter(pl.col("old_reason").fill_null("") != pl.col("new_reason").fill_null(""))
    diag["rows_reason_changed"] = changed.height
    return plan, diag


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.history_reason_fix")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--insert-missing", action="store_true")
    ap.add_argument("--db", type=Path, default=HISTORY_DB)
    args = ap.parse_args(argv)
    if args.insert_missing:
        return insert_main(args.apply)

    ro = duckdb.connect(str(args.db), read_only=True)
    n_before = ro.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
    fp_before = fingerprint(ro, with_reason=False)
    status_before = fingerprint_status(ro)
    plan, diag = compute_plan(ro)
    ro.close()
    print("rows_before", n_before, "fp_no_reason_before", fp_before[:16])
    for k, v in diag.items():
        print(k, v)
    # ``unmatched_new_keys`` are parsed rows with no stored counterpart (players the early-2019
    # aliases resolve now). They are NOT inserted: the fix never changes the row count.
    if diag["n_problems"]:
        print("STOP: plan has problems; nothing written")
        return 2
    if not args.apply:
        print("dry run only")
        return 0

    from nba.db.connect import connect_with_retry

    backup = Path("data/backups") / (
        f"history_player_availability_pre_reasonfix_{datetime.now():%Y%m%d_%H%M%S}.parquet"
    )
    wcon = connect_with_retry(args.db)
    try:
        wcon.execute(f"COPY (SELECT * FROM player_availability) TO '{backup}' (FORMAT PARQUET)")
        wcon.register("plan_df", plan.to_arrow())
        wcon.execute("BEGIN")
        wcon.execute(
            "UPDATE player_availability SET reason = p.new_reason FROM plan_df p "
            "WHERE player_availability.rowid = p.rid"
        )
        n_after = wcon.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
        fp_after = fingerprint(wcon, with_reason=False)
        st_after = fingerprint_status(wcon)
        ok = n_after == n_before and fp_after == fp_before and st_after == status_before
        print("backup", backup)
        print("rows_after", n_after, "fp_no_reason_after", fp_after[:16], "gate_ok", ok)
        wcon.execute("COMMIT" if ok else "ROLLBACK")
        return 0 if ok else 3
    finally:
        wcon.close()


def fingerprint_status(con: duckdb.DuckDBPyConnection) -> str:
    return hashlib.sha256(
        repr(
            con.execute(
                "SELECT status, count(*) FROM player_availability GROUP BY 1 ORDER BY 1"
            ).fetchall()
        ).encode()
    ).hexdigest()


def parse_window(con: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """Fresh full parse of every layout-C PDF (history resolver), all schema columns."""
    resolver = build_history_resolver(con, HISTORY_DIR)
    frames = []
    for path in sorted((HISTORY_DIR / "availability_official").glob("Injury-Report_*.pdf")):
        rdt = report_dt_from_filename(path.name)
        if START <= rdt.date() <= END:
            frames.append(
                parse_official_injury_report(path, {}, report_dt=rdt, con=con, resolver=resolver)
            )
    return pl.concat(frames)


def missing_rows(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, pl.DataFrame]:
    """(rows parsed but not stored -- multiset difference --, all parsed rows)."""
    parsed = parse_window(con)
    cols = parsed.columns
    stored = pl.from_arrow(
        con.execute(
            "SELECT * FROM player_availability WHERE as_of >= '2019-11-20' AND as_of < '2019-12-18'"
        ).arrow()
    )
    assert isinstance(stored, pl.DataFrame)
    stored = stored.select(cols)
    p = parsed.with_columns(_n=pl.int_range(pl.len()).over(cols))
    s = stored.with_columns(_n=pl.int_range(pl.len()).over(cols))
    extra = p.join(s, on=[*cols, "_n"], how="anti").drop("_n")
    return extra, parsed


def insert_main(apply: bool) -> int:
    from nba.db.connect import connect_with_retry

    ro = duckdb.connect(str(HISTORY_DB), read_only=True)
    n_before = ro.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
    fp_full_before = fingerprint(ro, with_reason=True)
    extra, parsed = missing_rows(ro)
    stored_gone = ro.execute(
        "SELECT count(*) FROM player_availability WHERE as_of >= '2019-11-20' "
        "AND as_of < '2019-12-18'"
    ).fetchone()[0]  # type: ignore[index]
    ro.close()
    ids = sorted(extra["player_id"].unique().to_list())
    print("rows_before", n_before, "stored_in_window", stored_gone, "parsed", parsed.height)
    print("to_insert", extra.height, "distinct_players", len(ids))
    print("alias_ids_present", sorted(set(ids) & {202683, 2403, 101123, 1628397, 1628424}))
    print("other_ids", sorted(set(ids) - {202683, 2403, 101123, 1628397, 1628424}))
    print("extra_as_of_range", extra["as_of"].min(), extra["as_of"].max())
    print("extra_null_player", extra["player_id"].null_count())
    if not apply:
        print("dry run only")
        return 0
    backup = Path("data/backups") / (
        f"history_player_availability_pre_insert262_{datetime.now():%Y%m%d_%H%M%S}.parquet"
    )
    w = connect_with_retry(HISTORY_DB)
    try:
        w.execute(f"COPY (SELECT * FROM player_availability) TO '{backup}' (FORMAT PARQUET)")
        w.execute("BEGIN")
        w.register("extra_df", extra.to_arrow())
        cols = ", ".join(extra.columns)
        w.execute(f"INSERT INTO player_availability ({cols}) SELECT {cols} FROM extra_df")
        n_after = w.execute("SELECT count(*) FROM player_availability").fetchone()[0]  # type: ignore[index]
        outside = w.execute(
            "SELECT count(*) FROM player_availability WHERE as_of < '2019-11-20' "
            "OR as_of >= '2019-12-18'"
        ).fetchone()[0]  # type: ignore[index]
        again, _ = missing_rows(w)
        # existing rows unchanged: full fingerprint of pre-existing rows via backup comparison
        pre = w.execute(
            f"SELECT count(*) FROM (SELECT * FROM read_parquet('{backup}') EXCEPT "
            "SELECT * FROM player_availability)"
        ).fetchone()[0]  # type: ignore[index]
        ok = (
            n_after == n_before + extra.height
            and again.height == 0
            and pre == 0
            and outside == 353408
        )
        print("backup", backup)
        print("rows_after", n_after, "still_missing", again.height, "backup_rows_lost", pre)
        print("fp_full_before", fp_full_before[:16], "gate_ok", ok)
        w.execute("COMMIT" if ok else "ROLLBACK")
        return 0 if ok else 3
    finally:
        w.close()


if __name__ == "__main__":
    sys.exit(main())
