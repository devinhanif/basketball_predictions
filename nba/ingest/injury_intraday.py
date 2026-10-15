"""Resumable re-pull of the intraday official NBA injury-report PDFs (seasons 2023 and 2024).

Why: ``data/availability_official`` holds two reports per game day, so the fresh-share gate of
``docs/prereg/INJURY_TIMING.md`` fails on the stored table. The pilot probe found an hourly report
12:00-21:00 ET on every sampled 2023-24 date. This module fetches those files and parses them into a
SCRATCH duckdb. It never opens ``nba.duckdb`` for writing.

Commands (``python -m nba.ingest.injury_intraday <cmd> ...``)::

    pull   --seasons 2023 2024 [--dry-run] [--data-dir D] [--db nba.duckdb]
    parse  [--scratch-db data/scratch/injury_intraday.duckdb] [--limit N]
    status

Rules this file enforces:

* Season 2025 (the frozen holdout) and later are refused, in the argument check and again on dates.
* Raw PDFs are write-once: ``<data-dir>/raw/injury_intraday_2026-10-15/<date>/<file>``, written to a
  temp name and renamed; a file already on disk with size > 0 is never fetched or rewritten.
* A slot is fetched only if a ranged 4-byte GET (the probe's ``probe_report_status``) says the PDF
  exists, in either filename form. ``unknown`` (timeout, 5xx, 429) is never cached as a miss.
* Resumable and stoppable: a JSON progress ledger is rewritten atomically; SIGINT/SIGTERM finish the
  current request, flush the ledger and exit 0 (state ``stopped``). N consecutive slot failures
  stop the run (state ``hard_stop``, exit 3). Unresolved slots are simply retried on the next start.
* ``parse`` writes only to the scratch db; ``nba.duckdb`` is attached READ_ONLY to copy ``games``
  and a slim ``player_game_stats`` (season <= 2024) for game-id and name resolution.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import signal
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from nba.db.connect import DEFAULT_DB_PATH, attach_read_only_with_retry, connect_with_retry
from nba.ingest.availability import (
    official_report_url,
    official_report_url_hour_only,
    probe_report_status,
)
from nba.ingest.cache import DEFAULT_DATA_DIR, insert_rows

if TYPE_CHECKING:
    import httpx

ALLOWED_SEASONS = (2023, 2024)
#: The first hold-out season. Anything at or after it is refused outright.
HOLDOUT_SEASON = 2025
#: No date after this can belong to a season <= 2024 (2024-25 finals ended 2025-06-22).
MAX_DATE = date(2025, 7, 31)
MIN_DATE = date(2023, 9, 1)

RAW_SUBDIR = Path("raw") / "injury_intraday_2026-10-15"
LEDGER_NAME = "_ledger.json"
LOCK_NAME = ".pull.lock"
DEFAULT_SCRATCH = "injury_intraday.duckdb"

HOURLY = [(h, 0) for h in range(12, 22)]  # 12:00 .. 21:00, as seen by the pilot probe
QUARTERS = [(h, m) for h in range(12, 22) for m in (0, 15, 30, 45)]  # 12:00 .. 21:45 (probe grid)

MIN_PACE_S, MAX_PACE_S = 0.5, 0.7
MAX_ATTEMPTS = 4
BACKOFF_S = 2.0
DEFAULT_MAX_CONSEC = 25
FLUSH_EVERY = 20

_FILE_RE = re.compile(r"^Injury-Report_(\d{4})-(\d{2})-(\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)\.pdf$")


class SeasonRefused(ValueError):
    """A request touched season 2025 or later (the frozen holdout)."""


class LedgerCorrupt(RuntimeError):
    """The ledger exists but cannot be read; it is never overwritten silently."""


# --------------------------------------------------------------------------------------- guards


def check_seasons(seasons: Sequence[int]) -> list[int]:
    out = sorted(set(int(s) for s in seasons))
    if not out:
        raise ValueError("no seasons given")
    for s in out:
        if s >= HOLDOUT_SEASON:
            raise SeasonRefused(f"season {s} is the frozen holdout (>= {HOLDOUT_SEASON}); refused")
        if s not in ALLOWED_SEASONS:
            raise SeasonRefused(f"season {s} is not one of {ALLOWED_SEASONS}; refused")
    return out


def check_dates(days: Sequence[date]) -> None:
    for d in days:
        if d > MAX_DATE:
            raise SeasonRefused(f"{d} is after {MAX_DATE} (season 2025 or later); refused")
        if d < MIN_DATE:
            raise ValueError(f"{d} is before {MIN_DATE}; outside the 2023-24 re-pull")


def check_scratch_path(path: Path) -> Path:
    """The scratch db must not be nba.duckdb (by name or by resolved path)."""
    p = path.resolve()
    if p.name == "nba.duckdb" or p == DEFAULT_DB_PATH.resolve():
        raise ValueError(f"{path} is the production db; parse writes scratch only")
    return p


# ------------------------------------------------------------------------------------- slots


def slot_times(d: date, quarter_hours: bool = False) -> list[datetime]:
    grid = QUARTERS if quarter_hours else HOURLY
    return [datetime(d.year, d.month, d.day, h, m) for h, m in grid]


def slot_key(ts: datetime) -> str:
    return ts.strftime("%H:%M")


def candidate_urls(ts: datetime, hour_only_first: bool = False) -> list[str]:
    """Both filename forms at the top of the hour (HH_MM and hour-only); HH_MM only otherwise."""
    full = official_report_url(ts)
    if ts.minute != 0:
        return [full]
    hour = official_report_url_hour_only(ts)
    return [hour, full] if hour_only_first else [full, hour]


def parse_report_filename(name: str) -> datetime:
    """``Injury-Report_2024-01-17_05PM.pdf`` or ``..._05_00PM.pdf`` -> naive ET datetime."""
    m = _FILE_RE.match(name)
    if m is None:
        raise ValueError(f"not an official-report filename: {name}")
    y, mo, d, hh, mm, ap = m.groups()
    hour = int(hh) % 12 + (12 if ap == "PM" else 0)
    return datetime(int(y), int(mo), int(d), hour, int(mm or 0))


def existing_file(day_dir: Path, ts: datetime) -> Path | None:
    for u in candidate_urls(ts):
        p = day_dir / u.rsplit("/", 1)[-1]
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


# ------------------------------------------------------------------------------------- ledger


def raw_dir_of(data_dir: Path) -> Path:
    return data_dir / RAW_SUBDIR


def _new_ledger(seasons: Sequence[int], quarter_hours: bool) -> dict[str, Any]:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "version": 1,
        "created": now,
        "updated": now,
        "state": "new",
        "pid": None,
        "seasons": list(seasons),
        "quarter_hours": quarter_hours,
        "game_dates": {},
        "slots": {},
        "failed_slots": {},
        "counts": {
            "requests": 0,
            "present": 0,
            "absent": 0,
            "downloaded": 0,
            "skipped_on_disk": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "bytes": 0,
            "active_seconds": 0.0,
        },
    }


def load_ledger(raw_dir: Path) -> dict[str, Any] | None:
    p = raw_dir / LEDGER_NAME
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise LedgerCorrupt(
            f"{p} is unreadable ({exc}); fix or move it, it is not overwritten"
        ) from exc
    if not isinstance(data, dict) or data.get("version") != 1:
        raise LedgerCorrupt(f"{p} has an unknown shape")
    return data


def flush_ledger(raw_dir: Path, ledger: dict[str, Any]) -> None:
    """Atomic rewrite (tmp + rename), so a kill mid-write leaves the previous ledger intact."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    ledger["updated"] = datetime.now().isoformat(timespec="seconds")
    tmp = raw_dir / (LEDGER_NAME + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, raw_dir / LEDGER_NAME)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(raw_dir: Path) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    lock = raw_dir / LOCK_NAME
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(lock.read_text().strip() or "0")
            except (ValueError, OSError):
                pid = 0
            if pid and _pid_alive(pid):
                raise RuntimeError(f"another pull is running (pid {pid}); lock {lock}") from None
            lock.unlink(missing_ok=True)  # stale
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return lock
    raise RuntimeError(f"could not take {lock}")


def slots_total(ledger: dict[str, Any]) -> int:
    per = len(QUARTERS) if ledger.get("quarter_hours") else len(HOURLY)
    return per * sum(len(v) for v in ledger["game_dates"].values())


def slots_done(ledger: dict[str, Any]) -> int:
    return sum(len(v) for v in ledger["slots"].values())


# ------------------------------------------------------------------------------- the pull loop


class Pacer:
    """0.5-0.7 s (uniform) between requests; clock, rng and sleep are injectable for tests."""

    def __init__(
        self,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        lo: float = MIN_PACE_S,
        hi: float = MAX_PACE_S,
    ) -> None:
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.lo, self.hi = lo, hi
        self._last: float | None = None
        self._clock = time.monotonic

    def wait(self) -> None:
        gap = self._rng.uniform(self.lo, self.hi)
        if self._last is not None:
            remaining = gap - (self._clock() - self._last)
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()


class Stopper:
    """Cooperative stop flag set by SIGINT/SIGTERM."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_: object) -> None:
        self.requested = True


def resolve_game_dates(seasons: Sequence[int], db_path: Path) -> dict[str, list[str]]:
    """Distinct game dates per season from ``games`` (read-only), season <= 2024 in SQL."""
    seasons = check_seasons(seasons)
    con = connect_with_retry(db_path, read_only=True)
    try:
        out: dict[str, list[str]] = {}
        for s in seasons:
            rows = con.execute(
                "SELECT DISTINCT game_date FROM games "
                "WHERE season = ? AND season <= 2024 ORDER BY 1",
                [s],
            ).fetchall()
            out[str(s)] = [r[0].isoformat() for r in rows]
    finally:
        con.close()
    check_dates([date.fromisoformat(x) for v in out.values() for x in v])
    return out


def _probe(
    url: str,
    client: httpx.Client,
    pacer: Pacer,
    ledger: dict[str, Any],
    stop: Stopper,
    sleep: Callable[[float], None],
) -> str:
    """Ranged probe with backoff. Returns present | absent | unknown | stopped."""
    for attempt in range(MAX_ATTEMPTS):
        if stop.requested:
            return "stopped"
        pacer.wait()
        ledger["counts"]["requests"] += 1
        status = probe_report_status(url, client=client)
        if status != "unknown":
            return status
        if attempt < MAX_ATTEMPTS - 1:
            sleep(BACKOFF_S * 2**attempt)
    return "unknown"


def _download(
    url: str,
    dest: Path,
    client: httpx.Client,
    pacer: Pacer,
    ledger: dict[str, Any],
    stop: Stopper,
    sleep: Callable[[float], None],
) -> bool:
    """Full GET with backoff; only a %PDF body of size > 0 is written (tmp + rename, write-once)."""
    for attempt in range(MAX_ATTEMPTS):
        if stop.requested:
            return False
        pacer.wait()
        ledger["counts"]["requests"] += 1
        try:
            resp = client.get(url, timeout=30.0)
            ok = resp.status_code == 200 and resp.content[:4] == b"%PDF" and len(resp.content) > 0
            body = bytes(resp.content)
        except Exception:
            ok, body = False, b""
        if ok:
            if dest.exists() and dest.stat().st_size > 0:
                return True  # write-once: never replace
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(body)
            os.replace(tmp, dest)
            ledger["counts"]["downloaded"] += 1
            ledger["counts"]["bytes"] += len(body)
            return True
        if attempt < MAX_ATTEMPTS - 1:
            sleep(BACKOFF_S * 2**attempt)
    return False


def pull(
    *,
    seasons: Sequence[int],
    data_dir: Path,
    db_path: Path,
    client: httpx.Client | None = None,
    pacer: Pacer | None = None,
    stop: Stopper | None = None,
    sleep: Callable[[float], None] = time.sleep,
    dates: Sequence[date] | None = None,
    quarter_hours: bool = False,
    max_consecutive_failures: int = DEFAULT_MAX_CONSEC,
    max_slots: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run (or resume) the pull: ``(state, ledger)``, state complete | stopped | hard_stop."""
    seasons = check_seasons(seasons)
    raw_dir = raw_dir_of(data_dir)
    ledger = load_ledger(raw_dir) or _new_ledger(seasons, quarter_hours)
    if ledger["quarter_hours"] != quarter_hours:
        raise ValueError("quarter_hours differs from the existing ledger; use a fresh raw dir")
    ledger["seasons"] = sorted(set(ledger["seasons"]) | set(seasons))
    if dates is not None:
        check_dates(dates)
        todo = sorted({d.isoformat() for d in dates})
        ledger["game_dates"].setdefault("explicit", [])
        ledger["game_dates"]["explicit"] = sorted(set(ledger["game_dates"]["explicit"]) | set(todo))
    else:
        missing = [s for s in seasons if str(s) not in ledger["game_dates"]]
        if missing:
            ledger["game_dates"].update(resolve_game_dates(missing, db_path))
        todo = sorted({x for s in seasons for x in ledger["game_dates"][str(s)]})
        check_dates([date.fromisoformat(x) for x in todo])

    owns_client = client is None
    if client is None:
        import httpx

        client = httpx.Client(headers={"User-Agent": "nba-prediction-archivist/1.0"})
    pacer = pacer or Pacer()
    stop = stop or Stopper()
    lock = acquire_lock(raw_dir)
    ledger["state"] = "running"
    ledger["pid"] = os.getpid()
    counts = ledger["counts"]
    t0 = time.monotonic()
    last_flush = counts["requests"]
    form_hint = False  # True once the hour-only filename form has been the one that resolved
    state = "complete"
    processed = 0
    try:
        flush_ledger(raw_dir, ledger)
        for day in todo:
            d = date.fromisoformat(day)
            day_dir = raw_dir / day
            recorded: dict[str, str] = ledger["slots"].setdefault(day, {})
            for ts in slot_times(d, quarter_hours):
                if stop.requested:
                    state = "stopped"
                    break
                key = slot_key(ts)
                on_disk = existing_file(day_dir, ts)
                if on_disk is not None:
                    if recorded.get(key) != "present":
                        recorded[key] = "present"
                        counts["present"] += 1
                    counts["skipped_on_disk"] += 1
                    continue
                if recorded.get(key) == "absent":
                    continue
                if max_slots is not None and processed >= max_slots:
                    state = "stopped"
                    break
                processed += 1
                result = "absent"
                hit_url: str | None = None
                for url in candidate_urls(ts, hour_only_first=form_hint):
                    status = _probe(url, client, pacer, ledger, stop, sleep)
                    if status == "present":
                        hit_url = url
                        result = "present"
                        break
                    if status in ("unknown", "stopped"):
                        result = status
                        break
                if result == "stopped":
                    state = "stopped"
                    break
                if result == "present" and hit_url is not None:
                    dest = day_dir / hit_url.rsplit("/", 1)[-1]
                    if _download(hit_url, dest, client, pacer, ledger, stop, sleep):
                        recorded[key] = "present"
                        counts["present"] += 1
                        form_hint = ts.minute == 0 and hit_url == official_report_url_hour_only(ts)
                        counts["consecutive_failures"] = 0
                        ledger["failed_slots"].pop(f"{day} {key}", None)
                    elif stop.requested:
                        state = "stopped"
                        break
                    else:
                        result = "unknown"
                elif result == "absent":
                    recorded[key] = "absent"
                    counts["absent"] += 1
                    counts["consecutive_failures"] = 0
                    ledger["failed_slots"].pop(f"{day} {key}", None)
                if result == "unknown":
                    counts["failures"] += 1
                    counts["consecutive_failures"] += 1
                    ledger["failed_slots"][f"{day} {key}"] = "unresolved after retries"
                    if counts["consecutive_failures"] >= max_consecutive_failures:
                        state = "hard_stop"
                        break
                if counts["requests"] - last_flush >= FLUSH_EVERY:
                    counts["active_seconds"] += time.monotonic() - t0
                    t0 = time.monotonic()
                    flush_ledger(raw_dir, ledger)
                    last_flush = counts["requests"]
            if state != "complete":
                break
            print(
                f"pull {day}: {slots_done(ledger)}/{slots_total(ledger)} slots, "
                f"present={counts['present']} absent={counts['absent']} "
                f"failures={counts['failures']}",
                flush=True,
            )
        if state == "complete" and max_slots is not None and processed >= max_slots:
            state = "stopped"
    finally:
        counts["active_seconds"] += time.monotonic() - t0
        ledger["state"] = state
        ledger["pid"] = None
        flush_ledger(raw_dir, ledger)
        lock.unlink(missing_ok=True)
        if owns_client:
            client.close()
    return state, ledger


# ------------------------------------------------------------------------------------ status


def status_text(ledger: dict[str, Any] | None) -> str:
    if ledger is None:
        return "no ledger yet (nothing pulled)"
    total, done = slots_total(ledger), slots_done(ledger)
    c = ledger["counts"]
    remaining = max(total - done, 0)
    # seconds per resolved slot observed so far; fall back to a priori 0.6 s x ~3 requests
    per_slot = c["active_seconds"] / done if done and c["active_seconds"] > 0 else 0.6 * 3
    eta_min = remaining * per_slot / 60
    pct = 100.0 * done / total if total else 0.0
    lines = [
        f"state: {ledger['state']}  (updated {ledger['updated']})",
        f"done/total slots: {done}/{total} ({pct:.1f}%)",
        f"reports present: {c['present']}  absent slots: {c['absent']}  "
        f"downloaded: {c['downloaded']}  skipped on disk: {c['skipped_on_disk']}",
        f"requests: {c['requests']}  failures: {c['failures']}  "
        f"consecutive failures: {c['consecutive_failures']}  "
        f"unresolved slots: {len(ledger['failed_slots'])}",
        f"bytes: {c['bytes']}  active time: {c['active_seconds'] / 60:.1f} min",
        f"ETA: {eta_min:.0f} min for {remaining} slots at {per_slot:.2f} s/slot",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------- parse

_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS player_availability (
    player_id INT, as_of TIMESTAMP, game_id VARCHAR, status VARCHAR,
    reason VARCHAR, source VARCHAR, pulled_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS injury_intraday_reports (
    file VARCHAR PRIMARY KEY,
    date_dir DATE,
    report_dt TIMESTAMP,   -- publish time, ET wall clock (event time)
    known_at TIMESTAMP,    -- earliest instant the report content was knowable = report_dt
    n_rows INT,
    n_sections INT,
    status VARCHAR,        -- ok | error
    error VARCHAR,
    parsed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS injury_intraday_team_sections (
    as_of TIMESTAMP, game_id VARCHAR, team_id INT, matchup VARCHAR,
    section_status VARCHAR, n_rows INT, source VARCHAR, pulled_at TIMESTAMP
);
"""


def parse_to_scratch(
    *,
    data_dir: Path,
    scratch_db: Path,
    source_db: Path,
    limit: int | None = None,
    names: Sequence[tuple[str, int]] | None = None,
) -> dict[str, int]:
    """Parse every unparsed PDF under the raw dir into the scratch db. Idempotent per file."""
    from nba.ingest.availability import build_name_resolver
    from nba.parse.availability import (
        AVAILABILITY_SCHEMA,
        TEAM_SECTION_SCHEMA,
        parse_official_injury_report,
        parse_official_injury_report_team_sections,
    )

    scratch = check_scratch_path(scratch_db)
    raw_dir = raw_dir_of(data_dir)
    files = sorted(raw_dir.glob("*/Injury-Report_*.pdf"))
    scratch.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(scratch))
    stats = {"files": len(files), "parsed": 0, "skipped": 0, "errors": 0, "rows": 0}
    try:
        con.execute(_REPORTS_DDL)
        done = {
            r[0]
            for r in con.execute(
                "SELECT file FROM injury_intraday_reports WHERE status = 'ok'"
            ).fetchall()
        }
        if not _has_table(con, "games"):
            attach_read_only_with_retry(con, source_db, "src")
            try:
                con.execute("CREATE TABLE games AS SELECT * FROM src.games WHERE season <= 2024")
                con.execute(
                    "CREATE TABLE player_game_stats AS SELECT s.game_id, s.player_id, s.team_id "
                    "FROM src.player_game_stats s JOIN src.games g USING (game_id) "
                    "WHERE g.season <= 2024"
                )
            finally:
                con.execute("DETACH src")
        max_game = con.execute("SELECT max(game_date) FROM games").fetchone()
        ceiling = max_game[0] if max_game and max_game[0] else MAX_DATE
        resolver = build_name_resolver(
            con, data_dir, date(2023, 9, 1), date(2025, 7, 31), names=names
        )
        for f in files:
            if f.name in done or f.stat().st_size == 0:
                stats["skipped"] += 1
                continue
            if limit is not None and stats["parsed"] + stats["errors"] >= limit:
                break
            day = date.fromisoformat(f.parent.name)
            if day > ceiling or day > MAX_DATE:
                raise SeasonRefused(f"{f} is dated {day}, after the season-2024 games; refused")
            now = datetime.now()
            try:
                rdt = parse_report_filename(f.name)
                if rdt.date() != day:
                    raise ValueError(f"filename date {rdt.date()} != directory {day}")
                df = parse_official_injury_report(
                    f, {}, report_dt=rdt, con=con, resolver=resolver, max_unmatched_rate=0.05
                )
                sec = parse_official_injury_report_team_sections(f, report_dt=rdt, con=con)
            except Exception as exc:  # logged per file; the run continues
                con.execute("DELETE FROM injury_intraday_reports WHERE file = ?", [f.name])
                con.execute(
                    "INSERT INTO injury_intraday_reports VALUES (?, ?, ?, ?, 0, 0, 'error', ?, ?)",
                    [f.name, day, None, None, f"{type(exc).__name__}: {str(exc)[:300]}", now],
                )
                stats["errors"] += 1
                continue
            con.execute("BEGIN")
            con.execute(
                "DELETE FROM player_availability "
                "WHERE source = 'nba_official_report' AND as_of = ?",
                [rdt],
            )
            con.execute("DELETE FROM injury_intraday_team_sections WHERE as_of = ?", [rdt])
            insert_rows(con, "player_availability", AVAILABILITY_SCHEMA, df)
            insert_rows(con, "injury_intraday_team_sections", TEAM_SECTION_SCHEMA, sec)
            con.execute("DELETE FROM injury_intraday_reports WHERE file = ?", [f.name])
            con.execute(
                "INSERT INTO injury_intraday_reports VALUES (?, ?, ?, ?, ?, ?, 'ok', NULL, ?)",
                [f.name, day, rdt, rdt, len(df), len(sec), now],
            )
            con.execute("COMMIT")
            stats["parsed"] += 1
            stats["rows"] += len(df)
            if stats["parsed"] % 25 == 0:
                print(f"parse: {stats['parsed']} files, {stats['errors']} errors", flush=True)
    finally:
        con.close()
    return stats


def _has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


# ------------------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m nba.ingest.injury_intraday")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("pull", "parse", "status"):
        p = sub.add_parser(name)
        p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        if name != "status":
            p.add_argument(
                "--db", type=Path, default=DEFAULT_DB_PATH, help="nba.duckdb (read-only)"
            )
    pp = sub.choices["pull"]
    pp.add_argument("--seasons", nargs="+", type=int, required=True)
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--quarter-hours", action="store_true", help="also probe :15/:30/:45 slots")
    pp.add_argument("--max-consecutive-failures", type=int, default=DEFAULT_MAX_CONSEC)
    pp.add_argument("--max-slots", type=int, default=None, help="stop after N unresolved slots")
    pp.add_argument("--dates", nargs="+", default=None, help="explicit YYYY-MM-DD (smoke runs)")
    ps = sub.choices["parse"]
    ps.add_argument(
        "--scratch-db", type=Path, default=DEFAULT_DATA_DIR / "scratch" / DEFAULT_SCRATCH
    )
    ps.add_argument("--limit", type=int, default=None)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    raw_dir = raw_dir_of(a.data_dir)
    try:
        if a.command == "status":
            print(status_text(load_ledger(raw_dir)))
            return 0
        if a.command == "parse":
            stats = parse_to_scratch(
                data_dir=a.data_dir, scratch_db=a.scratch_db, source_db=a.db, limit=a.limit
            )
            print(f"parse: {stats}")
            return 1 if stats["errors"] else 0
        # pull
        seasons = check_seasons(a.seasons)
        explicit = [date.fromisoformat(x) for x in a.dates] if a.dates else None
        if a.dry_run:
            if explicit is not None:
                check_dates(explicit)
                days = sorted({d.isoformat() for d in explicit})
            else:
                gd = resolve_game_dates(seasons, a.db)
                days = sorted({x for v in gd.values() for x in v})
                print("dates per season: " + ", ".join(f"{k}={len(v)}" for k, v in gd.items()))
            per = len(QUARTERS) if a.quarter_hours else len(HOURLY)
            n_slots = len(days) * per
            on_disk = sum(
                1
                for day in days
                for ts in slot_times(date.fromisoformat(day), a.quarter_hours)
                if existing_file(raw_dir / day, ts) is not None
            )
            print(
                f"dry-run: {len(days)} dates, {per} slots per date, {n_slots} slots; "
                f"requests if every slot exists about {2 * n_slots} (probe + GET), "
                f"ceiling {3 * n_slots} (wrong filename form tried first every time); "
                f"already on disk {on_disk}; no request made"
            )
            return 0
        stop = Stopper()
        signal.signal(signal.SIGINT, stop.request)
        signal.signal(signal.SIGTERM, stop.request)
        state, led = pull(
            seasons=seasons,
            data_dir=a.data_dir,
            db_path=a.db,
            stop=stop,
            dates=explicit,
            quarter_hours=a.quarter_hours,
            max_consecutive_failures=a.max_consecutive_failures,
            max_slots=a.max_slots,
        )
        print(f"pull finished: {state}\n{status_text(led)}")
        return 3 if state == "hard_stop" else 0
    except (SeasonRefused, LedgerCorrupt, ValueError, RuntimeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
