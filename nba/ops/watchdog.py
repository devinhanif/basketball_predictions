"""Independent "did it run?" watchdog for the launchd jobs.

A job that cannot start cannot alert (macOS TCC once blocked every /bin/sh job with exit 126 and
nothing fired). This checker runs from its own launchd label and judges the jobs from the outside:

* heartbeat files ``data/ops/heartbeat/<job>.json`` written by the shell wrappers at START and END
  (flat keys: ``job, mode, host, start_ts, end_ts, rc``); a stale start or ``rc != 0`` alerts;
* ``launchctl list`` last exit status of every ``local.nba.*`` label (exit-126 launch failures);
* the Kalshi snapshot job is judged from the mtime of its append-only ``snapshot.log`` (read only).

Alerts append to ``data/ops/ALERTS.md`` and raise a guarded macOS notification, de-duplicated:
the same condition is re-alerted only after ``REALERT_AFTER``. Read-only; no credentials.

CLI: ``python -m nba.ops.watchdog check [--no-notify]``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

LABEL_PREFIX = "local.nba."
REALERT_AFTER = timedelta(hours=6)
OK_RC: dict[str, frozenset[int]] = {"daily-pretip": frozenset({0, 2})}


@dataclass(frozen=True)
class JobSpec:
    """Expected schedule of one job. ``kind``: interval | daily | hourly_window."""

    name: str
    label: str
    kind: str
    grace: timedelta
    interval: timedelta | None = None
    at_minute: int = 0
    hours: tuple[int, ...] = ()  # local hours the job fires (daily / hourly_window)
    heartbeat: bool = True  # False: judged from ``log_path`` mtime instead


JOBS: tuple[JobSpec, ...] = (
    JobSpec(
        "daily-pretip",
        "local.nba.daily-pretip",
        "slots",
        timedelta(minutes=25),
        at_minute=30,
        hours=tuple(range(9, 22)),
    ),
    JobSpec(
        "daily-morning",
        "local.nba.daily-morning",
        "slots",
        timedelta(minutes=45),
        at_minute=0,
        hours=(8,),
    ),
    JobSpec(
        "lineups", "local.nba.lineups", "interval", timedelta(minutes=10), timedelta(minutes=5)
    ),
    JobSpec(
        "kalshi-snapshot",
        "local.nba.kalshi-snapshot",
        "interval",
        timedelta(minutes=10),
        timedelta(minutes=15),
        heartbeat=False,
    ),
)


@dataclass(frozen=True)
class Alert:
    key: str  # stable dedupe key, e.g. "lineups:stale"
    message: str


def last_due(spec: JobSpec, now: datetime) -> datetime:
    """Most recent time the job should have started at or before ``now``."""
    if spec.kind == "interval":
        assert spec.interval is not None
        return now - spec.interval - spec.grace  # latest acceptable start, grace included
    slots = [
        now.replace(hour=h, minute=spec.at_minute, second=0, microsecond=0) + timedelta(days=d)
        for d in (0, -1)
        for h in spec.hours
    ]
    return max(s for s in slots if s <= now)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_heartbeat(hb_dir: Path, job: str) -> dict[str, object] | None:
    path = hb_dir / f"{job}.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_launchctl(text: str) -> dict[str, int]:
    """Parse ``launchctl list`` (``PID<TAB>Status<TAB>Label``) into {label: last exit status}."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[2].startswith(LABEL_PREFIX):
            continue
        try:
            out[parts[2]] = int(parts[1])
        except ValueError:
            continue
    return out


def run_launchctl() -> str:
    try:
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout


def evaluate(
    now: datetime,
    hb_dir: Path,
    launchctl_text: str,
    first_seen: datetime,
    kalshi_log: Path | None = None,
    jobs: tuple[JobSpec, ...] = JOBS,
) -> list[Alert]:
    """Pure-ish health check: returns the alerts for the current moment (no dedupe)."""
    alerts: list[Alert] = []
    for spec in jobs:
        due = last_due(spec, now)
        start: datetime | None
        rc: object = None
        if spec.heartbeat:
            hb = read_heartbeat(hb_dir, spec.name)
            start = _parse_ts(hb.get("start_ts")) if hb else None
            end = _parse_ts(hb.get("end_ts")) if hb else None
            rc = hb.get("rc") if hb else None
            if hb and end is not None and start is not None and end >= start and rc is not None:
                ok = OK_RC.get(spec.name, frozenset({0}))
                if rc not in ok:
                    alerts.append(Alert(f"{spec.name}:rc", f"{spec.name} last run exited rc={rc}"))
        else:
            mtime = None
            if kalshi_log is not None and kalshi_log.exists():
                mtime = datetime.fromtimestamp(kalshi_log.stat().st_mtime).astimezone()
            start = mtime
        reference = start if start is not None else first_seen
        if now > due + spec.grace and reference < due:
            if start is None:
                msg = f"{spec.name} has no heartbeat (expected since {due:%Y-%m-%d %H:%M})"
            else:
                msg = (
                    f"{spec.name} stale: last start {start:%Y-%m-%d %H:%M}, expected by {due:%H:%M}"
                )
            alerts.append(Alert(f"{spec.name}:stale", msg))
    status = parse_launchctl(launchctl_text)
    for label, code in sorted(status.items()):
        if code != 0 and label != "local.nba.watchdog":
            alerts.append(
                Alert(f"{label}:launchctl", f"launchctl last exit status {code} for {label}")
            )
    return alerts


def dedupe(
    alerts: list[Alert], state: dict[str, str], now: datetime
) -> tuple[list[Alert], dict[str, str]]:
    """Return alerts due now and the new state (resolved conditions are forgotten)."""
    fresh: list[Alert] = []
    new_state: dict[str, str] = {}
    for a in alerts:
        prev = _parse_ts(state.get(a.key))
        if prev is None or now - prev >= REALERT_AFTER:
            fresh.append(a)
            new_state[a.key] = now.isoformat()
        else:
            new_state[a.key] = state[a.key]
    return fresh, new_state


def notify(message: str) -> None:
    """macOS notification; silently skipped where osascript does not exist."""
    if shutil.which("osascript") is None:
        return
    text = message.replace('"', "'")
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["osascript", "-e", f'display notification "{text}" with title "NBA watchdog"'],
            capture_output=True,
            timeout=10,
        )


def write_heartbeat(hb_dir: Path, job: str, now: datetime, rc: int) -> None:
    hb_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": job,
        "mode": "check",
        "host": socket.gethostname(),
        "start_ts": now.isoformat(),
        "end_ts": now.isoformat(),
        "rc": rc,
    }
    (hb_dir / f"{job}.json").write_text(json.dumps(rec))


def check(
    ops_dir: Path,
    now: datetime | None = None,
    launchctl_text: str | None = None,
    kalshi_log: Path | None = None,
    notifier: Callable[[str], None] | None = notify,
) -> list[Alert]:
    """Run one check; append new alerts to ALERTS.md and notify. Returns the alerts emitted."""
    now = now or datetime.now().astimezone()
    ops_dir.mkdir(parents=True, exist_ok=True)
    state_path = ops_dir / "watchdog_state.json"
    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError):
        state = {}
    first_seen = _parse_ts(state.get("_first_seen")) or now
    alerts_state = {k: v for k, v in state.items() if not k.startswith("_")}
    text = run_launchctl() if launchctl_text is None else launchctl_text
    kl = kalshi_log if kalshi_log is not None else ops_dir.parent / "kalshi" / "snapshot.log"
    current = evaluate(now, ops_dir / "heartbeat", text, first_seen, kl)
    fresh, new_alerts_state = dedupe(current, alerts_state, now)
    if fresh:
        with (ops_dir / "ALERTS.md").open("a") as fh:
            for a in fresh:
                fh.write(f"- {now:%Y-%m-%d %H:%M} [watchdog] {a.message}\n")
        if notifier is not None:
            notifier("; ".join(a.message for a in fresh)[:200])
    state_path.write_text(json.dumps({"_first_seen": first_seen.isoformat(), **new_alerts_state}))
    return fresh


def health_section(ops_dir: Path = Path("data/ops")) -> str:
    """Markdown 'job health' block for ``nba.daily report`` (no alerts emitted, state untouched)."""
    now = datetime.now().astimezone()
    try:
        state = json.loads((ops_dir / "watchdog_state.json").read_text())
    except (OSError, ValueError):
        state = {}
    first_seen = _parse_ts(state.get("_first_seen")) or now
    alerts = evaluate(
        now,
        ops_dir / "heartbeat",
        run_launchctl(),
        first_seen,
        ops_dir.parent / "kalshi" / "snapshot.log",
    )
    lines = ["## Job health", ""]
    if not alerts:
        lines.append("All launchd jobs healthy (heartbeats fresh, no non-zero launchctl status).")
    else:
        lines += [f"- {a.message}" for a in alerts]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nba.ops.watchdog")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check job heartbeats + launchctl status, alert on problems")
    c.add_argument("--ops-dir", default="data/ops")
    c.add_argument("--no-notify", action="store_true")
    args = p.parse_args(argv)
    ops = Path(args.ops_dir)
    now = datetime.now().astimezone()
    fresh = check(ops, now=now, notifier=None if args.no_notify else notify)
    write_heartbeat(ops / "heartbeat", "watchdog", now, 0)
    print(f"watchdog: {len(fresh)} new alert(s)")
    for a in fresh:
        print(f"  {a.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
