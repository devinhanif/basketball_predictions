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
  leaves yesterday's ``latest`` untouched. The swap is ONE syscall where the OS has one (``renamex_np`` RENAME_SWAP on macOS, ``renameat2``
  RENAME_EXCHANGE on Linux): ``latest`` is never absent. Elsewhere it falls back to two renames.
* **Locked database.** nba.duckdb is single-writer; the build opens it read-only and exits 2 while a pretip run holds it. The job retries a few
  times a minute apart (the pretip runs are short between games), then fails with rc 2 for the watchdog.
* **Heartbeat.** ``data/ops/heartbeat/app-export.json`` is written at START and at END (flat keys of the other wrappers plus ``status`` and
  ``reason``), so a job that never starts, or dies, is visible from the independent watchdog (``nba.ops.watchdog``, JobSpec ``app-export``).
* **Re-export (2026-11-09).** launchd fires two slots an hour (:05 and :35 on the schedule clock, 12-23), between the pretip runs. After today's first
  export a slot rebuilds only when a pretip run has ENDED since the last build started and the last tip of the slate is still ahead; anything
  else is a one-second no-op that says why (an empty slate, ``{}``, builds nothing; unreadable tips fall back to the pretip trigger alone).
  What this buys, honestly: the build pairs a book line with a forecast row only when ``received_at <= made_at`` of that row, and the store
  does not re-insert an unchanged row, so a row unchanged since the morning keeps its morning ``made_at`` and its morning lines. Newer lines
  reach the app only for forecast rows re-inserted after the line arrived (measured 2026-11-09: at best about 165 of 409 keys with posted lines,
  28 at the 10:38 build). The pairing itself is nba.export's and is not changed here.
  Guards: a build whose manifest says lines ``locked``/``absent`` is NOT swapped over a same-day export whose lines were ``ok`` (treated as rc 2:
  retried, then an alert, the last export kept, no state written); one run has a 600 s deadline (an attempt gets min(900, remaining) and none
  starts with under 240 s left, so the job lets go of nba.duckdb before the next pretip run); one instance at a time (``data/ops/app_export_lock``);
  staging directories of dead pids are removed at start; two failed builds for the same pretip run back off (a recorded skip) until a newer
  pretip run ends. A failed re-export keeps the last good export, exits non-zero for the watchdog and appends a line to ``data/ops/ALERTS.md``.
  No credentials, no orders, nothing outward: the export is a local directory.
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

from nba.ops.watchdog import read_heartbeat, todays_tips

JOB = "app-export"
MORNING = "daily-morning"
STEPS_FILE = f"{MORNING}.steps"
PRETIP = "daily-pretip"
STATE_FILE = f"{JOB}.state.json"


#: steps whose failure does not stop the export: the Drive copies only (they never feed it); any other backup_* step blocks
def _is_drive_backup(name: str) -> bool:
    return name.startswith("backup_") and name.endswith("_drive")


#: advisory steps (ops/nba_daily.sh ``advisory_step``): they may alert but never hold back the export
#: coverage_yesterday and data_check_advisory (diff-only flags) were added 2026-10-27; leak/schema flags fail ``data_check`` itself
ADVISORY_STEPS = ("stints_nightly", "coverage_yesterday", "data_check_advisory")

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
DEADLINE_S = 600  # one run, start to end: the pretip runs (:20, :50) must never wait on this job
MIN_ATTEMPT_S = 240  # no attempt starts with less than this left (a build takes 190-500 s)
FAIL_BACKOFF = 2  # failed builds for one pretip run before the next slots stop rebuilding
BACKOFF_PREFIX = "backing off"
FAIL_FILE = f"{JOB}.fail.json"
LOCK_DIR = "app_export_lock"

#: ``build(day, out_dir, timeout_s) -> rc``: injectable for tests
Builder = Callable[[date, Path, float], int]
#: ``tips(data_dir, day) -> {game_id: tip_utc}``: injectable for tests (the watchdog's reader by default)
TipsFn = Callable[[Path, date], dict[str, datetime]]


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
    failed = sorted(
        n
        for n, st in status.items()
        if st != "ok" and not _is_drive_backup(n) and n not in ADVISORY_STEPS
    )
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


def _exchange(a: Path, b: Path) -> bool:
    """Atomically exchange two existing paths (one syscall). False when the OS or filesystem has no such call."""
    import ctypes
    import ctypes.util

    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        if sys.platform == "darwin":
            return bool(libc.renamex_np(os.fsencode(a), os.fsencode(b), 2) == 0)  # RENAME_SWAP
        if sys.platform.startswith("linux"):
            at_fdcwd = -100
            rc = libc.renameat2(
                at_fdcwd, os.fsencode(a), at_fdcwd, os.fsencode(b), 2
            )  # RENAME_EXCHANGE
            return bool(rc == 0)
    except (OSError, AttributeError):
        return False
    return False


def swap_dir(stage: Path, final: Path) -> None:
    """Put ``stage`` in place of ``final``. With an atomic exchange ``final`` is never absent and ``stage`` ends up holding the old
    export (removed); otherwise ``final`` is moved aside, ``stage`` renamed into its place, the old copy removed."""
    if not stage.is_dir():
        raise FileNotFoundError(f"stage {stage} is not a directory")
    if final.is_dir() and not final.is_symlink() and _exchange(stage, final):
        shutil.rmtree(stage, ignore_errors=True)
        return
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


def _utc(t: datetime) -> datetime:
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t


def _pretip_end_str(hb_dir: Path) -> str | None:
    v = (read_heartbeat(hb_dir, PRETIP) or {}).get("end_ts")
    return v if isinstance(v, str) else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, not ours
    return True


def acquire_lock(lock: Path) -> bool:
    """``mkdir`` lock with the owner pid inside (the ``ops/nba_daily.sh`` pattern); a dead owner's lock is cleared."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            lock.mkdir()
        except FileExistsError:
            try:
                owner = int((lock / "pid").read_text().strip())
            except (OSError, ValueError):
                owner = 0
            if owner and _pid_alive(owner):
                return False
            shutil.rmtree(
                lock, ignore_errors=True
            )  # a dead owner (or a lock with no pid): clear and retry once
            continue
        (lock / "pid").write_text(str(os.getpid()))
        return True
    return False


def remove_orphan_stages(export_dir: Path, final_name: str = "latest") -> list[str]:
    """Remove ``.latest.stage.<pid>`` and ``.latest.old.<pid>`` directories whose pid is not alive (a SIGKILLed build)."""
    gone: list[str] = []
    for p in sorted(export_dir.glob(f".{final_name}.*.*")) if export_dir.is_dir() else []:
        kind, _, pid = p.name[len(final_name) + 2 :].partition(".")
        if kind in ("stage", "old") and pid.isdigit() and not _pid_alive(int(pid)):
            shutil.rmtree(p, ignore_errors=True)
            gone.append(p.name)
    return gone


def lines_regression(stage: Path, out: Path, today: date) -> str | None:
    """Why the staged export must not replace the current one, or None. A stage manifest that cannot be read is refused; a stage whose
    lines status is not ``ok`` is refused when the current export is today's and has lines ``ok`` (a locked market store is not an export)."""
    try:
        new = json.loads((stage / "manifest.json").read_text())
        new_status = (new.get("lines") or {}).get("status")
    except (OSError, ValueError, AttributeError):
        return "staged manifest unreadable"
    try:
        cur = json.loads((out / "manifest.json").read_text())
        cur_ok = (
            str(cur.get("export_date")) == today.isoformat()
            and (cur.get("lines") or {}).get("status") == "ok"
        )
    except (OSError, ValueError, AttributeError):
        return None
    if cur_ok and new_status != "ok":
        return (
            f"staged export has lines status {new_status!r} over a same-day export with lines 'ok'"
        )
    return None


def rebuild_reason(
    root: Path,
    out: Path,
    hb_dir: Path,
    today: date,
    morning_end: datetime,
    now: datetime,
    tips_fn: TipsFn = todays_tips,
) -> tuple[bool, str]:
    """``(build?, why)``. Build when today's export does not exist yet (or predates the morning job's end); after that only when a pretip run
    has ended since the last build started (its forecasts and capture are new) and a game of the slate has not tipped yet."""
    try:
        m = json.loads((out / "manifest.json").read_text())
        made = _utc(datetime.fromisoformat(str(m["made_at"])))
    except (OSError, ValueError, KeyError):
        return True, "no export on disk"
    if str(m.get("export_date")) != today.isoformat() or made < morning_end:
        return True, "first export of the day"
    ref = made
    try:
        st = json.loads((hb_dir / STATE_FILE).read_text())
        if st.get("export_date") == today.isoformat():
            started = _ts(st.get("build_start_ts"))
            if started is not None:
                ref = started  # written after a successful swap, so it is never later than the export's own build
    except (OSError, ValueError, AttributeError):
        pass
    end = _ts((read_heartbeat(hb_dir, PRETIP) or {}).get("end_ts"))
    if end is None or end <= ref:
        return False, "nothing newer than the last export (no pretip run has ended since)"
    try:
        tips: dict[str, datetime] | None = tips_fn(root / "data", today)
    except (OSError, ValueError, KeyError):
        tips = None  # unknown tips: the pretip trigger above stands
    if tips is not None and not tips:
        return False, "no games on today's slate"
    if tips and now >= max(tips.values()):
        return False, "every game of the slate has tipped; the last export stands"
    try:
        fail = json.loads((hb_dir / FAIL_FILE).read_text())
        if (
            fail.get("pretip_end") == _pretip_end_str(hb_dir)
            and int(fail.get("count", 0)) >= FAIL_BACKOFF
        ):
            return (
                False,
                f"{BACKOFF_PREFIX}: {fail['count']} builds failed for the pretip run that ended {end:%H:%M}",
            )
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return True, f"pretip run ended {end:%H:%M} after the last build"


def _write_json(path: Path, rec: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec))
    tmp.replace(path)


def _alert(ops: Path, msg: str) -> None:
    """One line in ``data/ops/ALERTS.md`` (the file the other wrappers and the watchdog write); never raises."""
    with contextlib.suppress(OSError), (ops / "ALERTS.md").open("a") as fh:
        fh.write(f"- {datetime.now():%Y-%m-%d %H:%M} [app-export] {msg}\n")


def _build_subprocess(root: Path) -> Builder:
    def build(day: date, out: Path, timeout_s: float) -> int:
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
            timeout=timeout_s,
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
    tips_fn: TipsFn = todays_tips,
    clock: Callable[[], float] = time.monotonic,
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

    def record_skip(why: str) -> None:
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

    lock = ops / LOCK_DIR
    if not acquire_lock(lock):
        say(
            "another app-export run holds the lock; this one does nothing"
        )  # the holder owns the heartbeat
        return 0
    t0 = clock()
    try:
        beat(False, None, "running", "")
        say(f"start export for {today}")
        try:
            return _run_locked(
                root, now, today, ops, hb_dir, out, log, say, beat, record_skip,
                build, sleep, retries, wait_s, tips_fn, clock, t0,
            )  # fmt: skip
        except (
            Exception
        ) as exc:  # whatever happens, the watchdog gets an END heartbeat and an alert
            say(f"FAILED with {type(exc).__name__}: {exc}")
            _alert(
                ops,
                f"run crashed with {type(exc).__name__}: {exc}; the previous export stays in place, see {log}",
            )
            beat(True, 1, "failed", f"crash {type(exc).__name__}")
            return 1
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _run_locked(
    root: Path,
    now: datetime,
    today: date,
    ops: Path,
    hb_dir: Path,
    out: Path,
    log: Path,
    say: Callable[[str], None],
    beat: Callable[[bool, int | None, str, str], None],
    record_skip: Callable[[str], None],
    build: Builder | None,
    sleep: Callable[[float], None],
    retries: int,
    wait_s: float,
    tips_fn: TipsFn,
    clock: Callable[[], float],
    t0: float,
) -> int:
    for name in remove_orphan_stages(out.parent):
        say(f"removed orphaned staging directory {name}")
    ok, why, m_end = morning_gate(hb_dir, today)
    if not ok:
        record_skip(why)
        return 0
    assert m_end is not None
    go, why_build = rebuild_reason(root, out, hb_dir, today, m_end, now, tips_fn)
    if not go:
        if why_build.startswith(BACKOFF_PREFIX):
            record_skip(why_build)  # the watchdog alerts after two skips in a row
            return 0
        (hb_dir / f"{JOB}.skip.json").unlink(missing_ok=True)
        say(f"nothing to do: {why_build}")
        beat(True, 0, "ok", why_build)
        return 0
    say(f"building: {why_build}")
    builder = build or _build_subprocess(root)
    stage = out.with_name(f".{out.name}.stage.{os.getpid()}")
    rc = 1
    for attempt in range(1, retries + 1):
        remaining = DEADLINE_S - (clock() - t0)
        if remaining < MIN_ATTEMPT_S:
            say(
                f"deadline: {remaining:.0f} s left, under {MIN_ATTEMPT_S} s; giving up (the pretip runs come first)"
            )
            rc = RC_LOCKED
            break
        shutil.rmtree(stage, ignore_errors=True)
        stage.parent.mkdir(parents=True, exist_ok=True)
        try:
            rc = builder(today, stage, min(BUILD_TIMEOUT_S, remaining))
        except (
            Exception
        ) as exc:  # a build that cannot run is a failed run, never a crash without a heartbeat
            say(f"build raised {type(exc).__name__}: {exc}")
            rc = 1
        if rc == 0:
            bad = lines_regression(stage, out, today)
            if bad:
                say(f"refusing to swap: {bad}")
                rc = (
                    1 if bad.startswith("staged manifest") else RC_LOCKED
                )  # a locked market store is retried like a locked nba.duckdb
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
        else:  # only a swapped-in export moves the reference: a failed build must not hide a pretip run
            try:
                _write_json(
                    hb_dir / STATE_FILE,
                    {
                        "export_date": today.isoformat(),
                        "build_start_ts": now.astimezone().isoformat(),
                    },
                )
            except OSError as exc:
                say(f"state file not written ({exc}); the manifest's made_at stands in")
    shutil.rmtree(stage, ignore_errors=True)
    if rc == 0:
        (hb_dir / f"{JOB}.skip.json").unlink(missing_ok=True)
        (hb_dir / FAIL_FILE).unlink(missing_ok=True)
        say(f"exported to {out}")
        beat(True, 0, "ok", "")
    else:
        say(f"FAILED rc={rc}; the previous export stays in place")
        _note_failure(hb_dir, today)
        _alert(ops, f"build rc={rc}; the previous export stays in place, see {log}")
        beat(True, rc, "failed", f"build rc={rc}")
    return rc


def _note_failure(hb_dir: Path, today: date) -> None:
    """Count failed builds per pretip run: after ``FAIL_BACKOFF`` the next slots record a skip instead of rebuilding."""
    end = _pretip_end_str(hb_dir)
    n = 1
    with contextlib.suppress(OSError, ValueError, AttributeError, TypeError):
        prev = json.loads((hb_dir / FAIL_FILE).read_text())
        if prev.get("pretip_end") == end and prev.get("export_date") == today.isoformat():
            n = int(prev.get("count", 0)) + 1
    with contextlib.suppress(OSError):
        _write_json(
            hb_dir / FAIL_FILE, {"export_date": today.isoformat(), "pretip_end": end, "count": n}
        )


def main(argv: list[str] | None = None) -> int:
    root = Path(os.environ.get("NBA_ROOT", Path(__file__).resolve().parents[2]))
    os.chdir(root)
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
