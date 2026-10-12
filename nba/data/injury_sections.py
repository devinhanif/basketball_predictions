"""Team-section coverage of the stored official injury-report PDFs (N11): counts only.

For every (game, team) in ``games`` it asks, over the stored snapshots of that game, whether the
team was ``filed`` (at least one player row), ``not_yet_submitted`` (placeholder only), or absent
from every snapshot (no section at all -- which is NOT the same as "filed with nobody listed").

Read-only on ``nba.duckdb``. Parsing 1,700 PDFs takes minutes, so the per-file result is cached
write-once under ``--cache-dir`` and the run is resumable; use ``--max-files`` to bound one call.
No outcomes are read, so season 2025 contributes counts and coverage only.

    uv run python -m nba.data.injury_sections --max-files 300      # repeat until "pending 0"
    uv run python -m nba.data.injury_sections --report              # counts per season
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from nba.ingest.cache import DEFAULT_DATA_DIR
from nba.parse.availability import parse_official_injury_report_team_sections

PDF_DIR = DEFAULT_DATA_DIR / "availability_official"
CACHE_DIR = DEFAULT_DATA_DIR / "ops" / "injury_sections_cache"
_NAME_RE = re.compile(r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)\.pdf$")


def report_dt(name: str) -> datetime:
    """Publish time from a report filename (``..._05PM.pdf`` or ``..._05_45PM.pdf``)."""
    m = _NAME_RE.match(name)
    if m is None:
        raise ValueError(name)
    d, hh, mm, ap = m.groups()
    return datetime.strptime(f"{d} {hh}:{mm or '00'} {ap}", "%Y-%m-%d %I:%M %p")


def scan(db: Path, pdf_dir: Path, cache_dir: Path, max_files: int) -> tuple[int, int]:
    """Parse up to ``max_files`` uncached PDFs; returns (parsed, pending after)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db), read_only=True)
    try:
        todo = [
            p
            for p in sorted(pdf_dir.glob("Injury-Report_*.pdf"))
            if not (cache_dir / (p.stem + ".json")).exists()
        ]
        done = 0
        for p in todo[:max_files]:
            try:
                df = parse_official_injury_report_team_sections(
                    p, report_dt=report_dt(p.name), con=con
                )
                rows = [
                    {
                        k: (str(v) if k == "as_of" else v)
                        for k, v in r.items()
                        if k in ("as_of", "game_id", "team_id", "section_status", "n_rows")
                    }
                    for r in df.to_dicts()
                ]
                out: dict[str, Any] = {"file": p.name, "rows": rows}
            except Exception as exc:  # recorded, never silently dropped
                out = {"file": p.name, "error": f"{type(exc).__name__}: {exc}"[:200], "rows": []}
            tmp = cache_dir / (p.stem + ".json.tmp")
            tmp.write_text(json.dumps(out))
            tmp.rename(cache_dir / (p.stem + ".json"))
            done += 1
        return done, len(todo) - done
    finally:
        con.close()


def report(cache_dir: Path, games: list[tuple[Any, ...]]) -> dict[str, Any]:
    snaps: dict[tuple[str, int], list[tuple[str, str, int]]] = defaultdict(list)
    game_seen: set[str] = set()
    errors = unresolved = nfiles = 0
    for f in sorted(cache_dir.glob("*.json")):
        d = json.loads(f.read_text())
        nfiles += 1
        if d.get("error"):
            errors += 1
        for r in d["rows"]:
            if r["game_id"] is None or r["team_id"] is None:
                unresolved += 1
                continue
            snaps[(r["game_id"], int(r["team_id"]))].append(
                (r["as_of"], r["section_status"], r["n_rows"])
            )
            game_seen.add(r["game_id"])
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for gid, season, home, away in games:
        for team in (int(home), int(away)):
            c = out[str(season)]
            c["team_games"] += 1
            s = snaps.get((gid, team), [])
            if not s:
                c["absent_game_visible" if gid in game_seen else "absent_game_invisible"] += 1
                continue
            c["filed_ever" if any(x[1] == "filed" for x in s) else "nys_only_ever"] += 1
            last = max(s)
            c["last_snapshot_" + last[1]] += 1
    return {
        "files": nfiles,
        "parse_errors": errors,
        "unresolved_sections": unresolved,
        "by_season": {k: dict(v) for k, v in sorted(out.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m nba.data.injury_sections")
    ap.add_argument("--db", default="nba.duckdb")
    ap.add_argument("--pdf-dir", default=str(PDF_DIR))
    ap.add_argument("--cache-dir", default=str(CACHE_DIR))
    ap.add_argument("--max-files", type=int, default=300)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.report:
        con = duckdb.connect(a.db, read_only=True)
        try:
            games = con.execute(
                "SELECT game_id, season, home_team, away_team FROM games"
            ).fetchall()
        finally:
            con.close()
        print(json.dumps(report(Path(a.cache_dir), games), indent=1))
        return 0
    done, pending = scan(Path(a.db), Path(a.pdf_dir), Path(a.cache_dir), a.max_files)
    print(f"parsed {done}; pending {pending}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
