# ruff: noqa: E501
"""INJURY_TIMING rev 3, B1: re-parse the intraday official-report PDFs with known-at = the time PRINTED in the PDF header.

Why: hour-only filenames (``..._05PM.pdf``) hold a report headed ``HH:30``; the archivist's parse stamped ``HH:00`` (30 minutes of future).
This module reads the same raw PDFs (``data/raw/injury_intraday_2026-10-15``, never modified) and writes a NEW scratch duckdb:

    .venv/bin/python -W ignore -m research.eval.injury_timing_reparse run  --data-root /Users/devin/Downloads/nba-prediction [--limit N]
    .venv/bin/python -W ignore -m research.eval.injury_timing_reparse audit --data-root /Users/devin/Downloads/nba-prediction

Tables: ``player_availability`` (as_of = header time), ``injury_intraday_reports`` (file, filename_dt, header_dt, report_dt, n_rows,
n_sections (counted, not stored), n_unmatched, status), ``stored_report_headers`` (header audit of the STORED production PDFs dated <= 2025-07-31, used to shift
the stored 11:00 / 17:00 rows), ``games``, ``player_game_stats`` (ids only, season <= 2024; copied from the v1 scratch db). A file whose header is
missing, or whose header minus filename time is not +30 (hour-only form) or 0 (HH_MM form), is recorded as an error and NOT loaded.
Resumable and idempotent per file. Writes only the new scratch db; ``nba.duckdb`` and the raw PDFs are never opened for writing; no PDF dated
after 2025-07-31 is opened (season 2025 is the holdout; such files are skipped).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

MAX_DATE = date(2025, 7, 31)
HEADER_RE = re.compile(r"Injury Report:\s*(\d{2})/(\d{2})/(\d{2})\s+(\d{1,2}):(\d{2})\s*([AP]M)")
FILE_RE = re.compile(r"Injury-Report_(\d{4})-(\d{2})-(\d{2})_(\d{2})(?:_(\d{2}))?([AP]M)\.pdf$")
NEW_DB = "data/scratch/injury_intraday_v2.duckdb"
V1_DB = "data/scratch/injury_intraday.duckdb"
RAW = "data/raw/injury_intraday_2026-10-15"
STORED = "data/availability_official"

DDL = """
CREATE TABLE IF NOT EXISTS player_availability (player_id INT, as_of TIMESTAMP, game_id VARCHAR, status VARCHAR, reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS injury_intraday_reports (file VARCHAR PRIMARY KEY, date_dir DATE, filename_dt TIMESTAMP, header_dt TIMESTAMP, report_dt TIMESTAMP, known_at TIMESTAMP, form VARCHAR, n_rows INT, n_sections INT, n_unmatched INT, status VARCHAR, error VARCHAR, parsed_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS stored_report_headers (file VARCHAR PRIMARY KEY, filename_dt TIMESTAMP, header_dt TIMESTAMP, form VARCHAR, status VARCHAR, error VARCHAR);
"""


def filename_dt(name: str) -> tuple[datetime, str]:
    m = FILE_RE.search(name)
    if m is None:
        raise ValueError(f"not an official-report filename: {name}")
    y, mo, d, hh, mm, ap = m.groups()
    hour = int(hh) % 12 + (12 if ap == "PM" else 0)
    return datetime(int(y), int(mo), int(d), hour, int(mm or 0)), ("hhmm" if mm else "hour_only")


def header_dt(pdf_path: Path) -> datetime:
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    m = HEADER_RE.search(text[:400])
    if m is None:
        raise ValueError("no 'Injury Report: MM/DD/YY HH:MM AM|PM' header on page 1")
    mo, d, yy, hh, mm, ap = m.groups()
    hour = int(hh) % 12 + (12 if ap == "PM" else 0)
    return datetime(2000 + int(yy), int(mo), int(d), hour, int(mm))


def check_convention(fname_dt: datetime, hdr: datetime, form: str) -> None:
    want = 30 if form == "hour_only" else 0
    got = int((hdr - fname_dt).total_seconds() // 60)
    if got != want:
        raise ValueError(f"header minus filename = {got} min, convention for {form} is {want}")


def stamp_for(fname_dt: datetime, form: str, hdr: datetime) -> datetime:
    check_convention(fname_dt, hdr, form)
    return hdr


def audit_stored(con: duckdb.DuckDBPyConnection, root: Path) -> dict[str, int]:
    con.execute(DDL)
    done = {r[0] for r in con.execute("SELECT file FROM stored_report_headers").fetchall()}
    n = bad = 0
    for f in sorted((root / STORED).glob("Injury-Report_*.pdf")):
        fd, form = filename_dt(f.name)
        if fd.date() > MAX_DATE or f.name in done:
            continue
        try:
            h = header_dt(f)
            check_convention(fd, h, form)
            con.execute(
                "INSERT INTO stored_report_headers VALUES (?,?,?,?,'ok',NULL)",
                [f.name, fd, h, form],
            )
        except Exception as exc:
            bad += 1
            con.execute(
                "INSERT INTO stored_report_headers VALUES (?,?,?,?,'error',?)",
                [f.name, fd, None, form, str(exc)[:200]],
            )
        n += 1
        if n % 100 == 0:
            print(f"audit stored: {n} files, {bad} bad", flush=True)
    return {"checked": n, "bad": bad}


def run(root: Path, limit: int | None) -> dict[str, int]:
    from nba.ingest.availability import build_name_resolver
    from nba.ingest.cache import insert_rows
    from nba.parse.availability import (
        AVAILABILITY_SCHEMA,
        parse_official_injury_report,
        parse_official_injury_report_team_sections,
    )

    db = root / NEW_DB
    if db.name == "nba.duckdb":
        raise ValueError("refusing to write the production db")
    db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db))
    stats = {"files": 0, "parsed": 0, "errors": 0, "skipped": 0, "rows": 0}
    try:
        con.execute(DDL)
        have = {
            r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        if "games" not in have:
            con.execute(f"ATTACH '{root / V1_DB}' AS v1 (READ_ONLY)")
            con.execute("CREATE TABLE games AS SELECT * FROM v1.games WHERE season <= 2024")
            con.execute("CREATE TABLE player_game_stats AS SELECT * FROM v1.player_game_stats")
            con.execute("DETACH v1")
        done = {
            r[0]
            for r in con.execute(
                "SELECT file FROM injury_intraday_reports WHERE status = 'ok'"
            ).fetchall()
        }
        resolver = build_name_resolver(con, root / "data", date(2023, 9, 1), date(2024, 7, 31))
        files = sorted((root / RAW).glob("*/Injury-Report_*.pdf"))
        stats["files"] = len(files)
        for f in files:
            if f.name in done or f.stat().st_size == 0:
                stats["skipped"] += 1
                continue
            if limit is not None and stats["parsed"] + stats["errors"] >= limit:
                break
            day = date.fromisoformat(f.parent.name)
            if day > MAX_DATE:  # season 2025 (holdout): the file is neither opened nor parsed
                stats["skipped"] += 1
                continue
            now = datetime.now()
            fd, form = filename_dt(f.name)
            try:
                if fd.date() != day:
                    raise ValueError("filename date != directory")
                hdr = header_dt(f)
                rdt = stamp_for(fd, form, hdr)
                n0 = len(resolver.unmatched)
                df = parse_official_injury_report(
                    f, {}, report_dt=rdt, con=con, resolver=resolver, max_unmatched_rate=0.05
                )
                sec = parse_official_injury_report_team_sections(f, report_dt=rdt, con=con)
                n_un = len(resolver.unmatched) - n0
            except Exception as exc:
                con.execute("DELETE FROM injury_intraday_reports WHERE file = ?", [f.name])
                con.execute(
                    "INSERT INTO injury_intraday_reports VALUES (?,?,?,?,NULL,NULL,?,0,0,0,'error',?,?)",
                    [f.name, day, fd, None, form, f"{type(exc).__name__}: {str(exc)[:300]}", now],
                )
                stats["errors"] += 1
                continue
            con.execute("BEGIN")
            con.execute(
                "DELETE FROM player_availability WHERE source = 'nba_official_report' AND as_of = ?",
                [rdt],
            )
            insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)
            con.execute("DELETE FROM injury_intraday_reports WHERE file = ?", [f.name])
            con.execute(
                "INSERT INTO injury_intraday_reports VALUES (?,?,?,?,?,?,?,?,?,?,'ok',NULL,?)",
                [f.name, day, fd, hdr, rdt, rdt, form, len(df), len(sec), n_un, now],
            )
            con.execute("COMMIT")
            stats["parsed"] += 1
            stats["rows"] += len(df)
            if stats["parsed"] % 25 == 0:
                print(f"reparse: {stats['parsed']} files, {stats['errors']} errors", flush=True)
    finally:
        con.close()
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("run", "audit"))
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    if a.cmd == "audit":
        con = duckdb.connect(str(a.data_root / NEW_DB))
        try:
            print(audit_stored(con, a.data_root))
        finally:
            con.close()
        return 0
    s = run(a.data_root, a.limit)
    print(s)
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
