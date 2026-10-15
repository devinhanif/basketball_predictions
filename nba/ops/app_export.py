# ruff: noqa: E501
"""The app export as its OWN scheduled job (not a step of ``ops/nba_daily.sh``).

    python -m nba.ops.app_export            # what launchd runs (ops/launchd/local.nba.app-export.plist)

Once the morning job has finished cleanly it runs ``python -m nba.export build --date <today ET> --out data/export/latest``:

* **Gate (DECISIONS 2026-10-14 20:31, A1).** The ``daily-morning`` heartbeat must have an END written today (Eastern date), and every DATA step of
  that run must have succeeded, read from ``daily-morning.steps`` (one ``<step> <rc> <ok|failed>`` line per step, written by ``ops/nba_daily.sh``).
  A failed ``backup_*_drive`` step does NOT block the export; any other failed step does; so does a missing, stale or truncated steps file, or one
  from a different run than the heartbeat (fail closed). A missing, still-running, stale or failing run SKIPS the run with a recorded reason: the reason goes into this job's own heartbeat (``status: skipped``), the log, and
  ``app-export.skip.json`` (consecutive count), which the watchdog turns into an alert after two skips in a row. A skip never exits non-zero.
* **Atomic.** The export is built into a staging directory next to ``latest`` and swapped in only after the build exited 0; a failed build
  leaves yesterday's ``latest`` untouched. (A directory cannot be swapped in one syscall, so there is a window of two renames; the app reads
  ``manifest.json`` last.)
* **Locked database.** nba.duckdb is single-writer; the build opens it read-only and exits 2 while a pretip run holds it. The job retries a few
  times a minute apart (the pretip runs are short between games), then fails with rc 2 for the watchdog.
* **Heartbeat.** ``data/ops/heartbeat/app-export.json`` is written at START and at END (flat keys of the other wrappers plus ``status`` and
  ``reason``), so a job that never starts, or dies, is visible from the independent watchdog (``nba.ops.watchdog``, JobSpec ``app-export``).
* Two launchd slots a day (12:35 and 14:35 on the schedule clock): a second slot that finds today's export already built after the morning job's
  end does nothing and says so. No credentials, no orders, nothing outward: the export is a local directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.ops.watchdog import read_heartbeat

JOB = "app-export"
MORNING = "daily-morning"
STEPS_FILE = f"{MORNING}.steps"


#: steps whose failure does not stop the export: the Drive copies only (they never feed it); any other backup_* step blocks
def _is_drive_backup(name: str) -> bool:
    return name.startswith("backup_") and name.endswith("_drive")


#: data steps that must appear as ok, else the run was cut short (or an older script wrote no record) and the gate fails closed
REQUIRED_STEPS = (
    "settle",
    "checkpoint",
    "report",
    "parlay_settle",
    "data_snapshot",
    "data_diff",
    "data_check",
)
ET = ZoneInfo("America/New_York")
RC_LOCKED = 2  # nba.export: the source database is held by a writer
LOCK_RETRIES = 4
LOCK_WAIT_S = 60
BUILD_TIMEOUT_S = 900

#: ``build(day, out_dir) -> rc``: injectable for tests
Builder = Callable[[date, Path], int]


def _ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo else t.astimezone()  # a naive stamp is local time


def data_step_problem(hb_dir: Path, hb_start: str) -> str | None:
    """None when every data step of the morning run (the one that started at ``hb_start``) succeeded, else the reason."""
    try:
        lines = (hb_dir / STEPS_FILE).read_text().splitlines()
    except OSError:
        return "morning step record missing"
    if not lines or lines[0].split(maxsplit=1) != ["start_ts", hb_start]:
        return "morning step record is from another run"
    status: dict[str, str] = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 3:
            return f"morning step record unreadable: {line!r}"
        if status.get(parts[0], "ok") == "ok":  # sticky: a step once recorded failed stays failed
            status[parts[0]] = parts[2]
    failed = sorted(n for n, st in status.items() if st != "ok" and not _is_drive_backup(n))
    if failed:
        return f"morning data step failed: {', '.join(failed)}"
    missing = [n for n in REQUIRED_STEPS if status.get(n) != "ok"]
    if missing:
        return f"morning data step not recorded ok: {', '.join(missing)}"
    return None


def morning_gate(hb_dir: Path, today: date) -> tuple[bool, str, datetime | None]:
    """``(ok, reason, morning_end)``: ok only when the morning job ended today (ET) and all its data steps succeeded (Drive backup steps may fail)."""
    hb = read_heartbeat(hb_dir, MORNING)
    if hb is None:
        return False, "morning heartbeat missing or unreadable", None
    start, end, rc = _ts(hb.get("start_ts")), _ts(hb.get("end_ts")), hb.get("rc")
    if start is None:
        return False, "morning heartbeat has no start_ts", None
    if end is None or end < start or rc is None:
        return False, f"morning job has not ended (started {start:%Y-%m-%d %H:%M})", None
    if end.astimezone(ET).date() != today:
        return (
            False,
            f"morning heartbeat is stale (ended {end:%Y-%m-%d %H:%M %z}, not {today})",
            end,
        )
    bad = data_step_problem(hb_dir, str(hb.get("start_ts")))
    if bad:
        return False, bad, end
    return True, "ok", end


def already_built(out: Path, today: date, morning_end: datetime) -> bool:
    """True when ``out`` holds today's export built after the morning job ended (the second daily slot has nothing to do)."""
    try:
        m = json.loads((out / "manifest.json").read_text())
        made = datetime.fromisoformat(str(m["made_at"]))
    except (OSError, ValueError, KeyError):
        return False
    made = made.replace(tzinfo=UTC) if made.tzinfo is None else made  # the export stamps naive UTC
    return str(m.get("export_date")) == today.isoformat() and made >= morning_end


def swap_dir(stage: Path, final: Path) -> None:
    """Put ``stage`` in place of ``final``: ``final`` is moved aside, ``stage`` renamed into its place, the old copy removed."""
    old = final.with_name(f".{final.name}.old.{os.getpid()}")
    if final.exists():
        os.replace(final, old)
    try:
        os.replace(stage, final)
    except OSError:
        if old.exists():
            os.replace(old, final)  # put yesterday's export back
        raise
    shutil.rmtree(old, ignore_errors=True)


def _write_json(path: Path, rec: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(path)


def _build_subprocess(root: Path) -> Builder:
    def build(day: date, out: Path) -> int:
        cp = subprocess.run(
            [
                sys.executable,
                "-m",
                "nba.export",
                "build",
                "--date",
                day.isoformat(),
                "--out",
                str(out),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
            check=False,
        )
        sys.stdout.write(cp.stdout)
        sys.stderr.write(cp.stderr)
        return cp.returncode

    return build


def run(
    root: Path,
    *,
    now: datetime | None = None,
    build: Builder | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = LOCK_RETRIES,
    wait_s: float = LOCK_WAIT_S,
) -> int:
    """One run of the job. Returns the process exit code: 0 for ok and for a recorded skip, otherwise the build's rc."""
    now = now or datetime.now().astimezone()
    today = now.astimezone(ET).date()
    ops = root / "data" / "ops"
    hb_dir = ops / "heartbeat"
    out = root / "data" / "export" / "latest"
    log = ops / f"app_export_{today:%Y%m%d}.log"
    host = socket.gethostname().split(".")[0]
    start = now.strftime("%Y-%m-%dT%H:%M:%S%z")

    def say(msg: str) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")

    def beat(end: bool, rc: int | None, status: str, reason: str) -> None:
        _write_json(
            hb_dir / f"{JOB}.json",
            {
                "job": JOB, "mode": "export", "host": host, "start_ts": start,
                "end_ts": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z") if end else None,
                "rc": rc, "status": status, "reason": reason, "export_date": today.isoformat(),
            },
        )  # fmt: skip

    beat(False, None, "running", "")
    say(f"start export for {today}")
    ok, why, m_end = morning_gate(hb_dir, today)
    if not ok:
        skip_path = hb_dir / f"{JOB}.skip.json"
        n = 1
        with contextlib.suppress(OSError, ValueError, AttributeError):
            n = int(json.loads(skip_path.read_text()).get("consecutive_skips", 0)) + 1
        _write_json(
            skip_path,
            {
                "job": JOB,
                "status": "skipped",
                "host": host,
                "ts": start,
                "consecutive_skips": n,
                "reason": why,
            },
        )
        say(f"skipped ({n} consecutive): {why}")
        beat(True, 0, "skipped", why)
        return 0
    assert m_end is not None
    if already_built(out, today, m_end):
        (hb_dir / f"{JOB}.skip.json").unlink(missing_ok=True)
        say("today's export is already built after the morning job; nothing to do")
        beat(True, 0, "ok", "already built")
        return 0
    builder = build or _build_subprocess(root)
    stage = out.with_name(f".{out.name}.stage.{os.getpid()}")
    rc = 1
    for attempt in range(1, retries + 1):
        shutil.rmtree(stage, ignore_errors=True)
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            rc = builder(today, stage)
        except (
            Exception
        ) as exc:  # a build that cannot run is a failed run, never a crash without a heartbeat
            say(f"build raised {type(exc).__name__}: {exc}")
            rc = 1
        say(f"build attempt {attempt}/{retries} rc={rc}")
        if rc != RC_LOCKED or attempt == retries:
            break
        sleep(wait_s)
    if rc == 0:
        try:
            swap_dir(stage, out)
        except OSError as exc:
            say(f"swap failed: {exc}")
            rc = 1
    shutil.rmtree(stage, ignore_errors=True)
    if rc == 0:
        (hb_dir / f"{JOB}.skip.json").unlink(missing_ok=True)
        say(f"exported to {out}")
        beat(True, 0, "ok", "")
    else:
        say(f"FAILED rc={rc}; the previous export stays in place")
        beat(True, rc, "failed", f"build rc={rc}")
    return rc


def main(argv: list[str] | None = None) -> int:
    root = Path(os.environ.get("NBA_ROOT", Path(__file__).resolve().parents[2]))
    os.chdir(root)
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
