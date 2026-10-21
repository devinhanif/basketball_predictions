"""Independent "did it run?" watchdog for the launchd jobs.

A job that cannot start cannot alert (macOS TCC once blocked every /bin/sh job with exit 126 and
nothing fired). This checker runs from its own launchd label and judges the jobs from the outside:

* heartbeat files ``data/ops/heartbeat/<job>.json`` written by the shell wrappers at START and END
  (flat keys: ``job, mode, host, start_ts, end_ts, rc``); a stale start or ``rc != 0`` alerts;
* ``launchctl list`` last exit status of every ``local.nba.*`` label (exit-126 launch failures);
* the Kalshi snapshot job is judged from the mtime of its append-only ``snapshot.log`` (read only);
* the game-day probe (``evaluate_game_day``): from the first check at or after 09:00 ET on a day
  with a regular-season game, one alert per day (key ``gameday:<ET date>:<check>``) if the Mac is
  on battery, today's slate is not in the ``games`` table (read-only DuckDB, bounded wait), the
  morning job has no END today, or ``caffeinate`` is not running (the Mac slept on battery on
  opening night 2026-10-20 and nothing told anyone). A probe that cannot compute alerts as itself.

Alerts append to ``data/ops/ALERTS.md`` and raise a guarded macOS notification, de-duplicated:
the same condition is re-alerted only after ``REALERT_AFTER``. Read-only; no credentials.

CLI: ``python -m nba.ops.watchdog check [--no-notify]``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nba.ops.exitcodes import RC_INFORMATIONAL

#: the Mac's launchd calendar runs in local time; slots are rebuilt per local day so a DST change
#: (2026-11-01 fall back) moves yesterday's slots with it
SCHEDULE_TZ = ZoneInfo("America/Chicago")
#: launchd evaluates StartCalendarInterval in the timezone it captured when it started, not the
#: current one. The Mac's launchd has been up for months and still fires on Eastern time while the
#: wall clock is Central (2026-10-11: ``Hour 9`` ran at 08:20 CDT, the morning job at 07:00 CDT), so
#: the slots must be judged in that zone or every night raises two false "stale" alerts. Override:
#: env ``NBA_SCHEDULE_TZ`` or the one-line file ``data/ops/schedule_tz`` (machine-local, untracked;
#: delete it after a reboot/relogin once launchd has picked up the real zone).
TZ_FILE = "schedule_tz"
LABEL_PREFIX = "local.nba."
REALERT_AFTER = timedelta(hours=6)
ET = ZoneInfo("America/New_York")
#: the game-day probe starts at this ET wall-clock time
GAMEDAY_PROBE_FROM = time(9, 0)
#: alert keys with this prefix fire once per ET game day (the date is in the key), never every 6 h
GAMEDAY_PREFIX = "gameday:"
#: regular-season game ids ("002...")
REGULAR_SEASON_PREFIX = "002"
#: bounded wait for a writer on nba.duckdb: the probe never holds up the 10-minute check
GAMEDAY_DB_WAIT_S = 20.0
GAMEDAY_DB_RETRY_S = 5.0
OK_RC: dict[str, frozenset[int]] = {"daily-pretip": frozenset({0, RC_INFORMATIONAL})}


@dataclass(frozen=True)
class JobSpec:
    """Expected schedule of one job. ``kind``: interval | daily | hourly_window."""

    name: str
    label: str
    kind: str
    grace: timedelta
    interval: timedelta | None = None
    minutes: tuple[int, ...] = (0,)  # local minutes past each hour the job fires (slots)
    hours: tuple[int, ...] = ()  # local hours the job fires (slots)
    heartbeat: bool = True  # False: judged from ``log_path`` mtime instead
    #: alert when the wrapper reports this many consecutive skipped runs (lock held)
    max_skips: int | None = None
    #: judged only once the job has ever left a heartbeat or its launchd label is loaded: a job the
    #: maintainer has not installed yet is silent, an installed job that never starts still alerts
    optional: bool = False


JOBS: tuple[JobSpec, ...] = (
    JobSpec(
        "daily-pretip",
        "local.nba.daily-pretip",
        "slots",
        timedelta(minutes=25),
        minutes=(20, 50),  # keep in step with ops/install_launchd.sh and ops/vm/install_cron.sh
        hours=tuple(range(9, 22)),
        max_skips=3,
    ),
    JobSpec(
        "daily-morning",
        "local.nba.daily-morning",
        "slots",
        timedelta(minutes=45),
        minutes=(0,),
        hours=(8,),
        max_skips=1,
    ),
    JobSpec(
        "lineups",
        "local.nba.lineups",
        "interval",
        timedelta(minutes=10),
        timedelta(minutes=5),
        max_skips=6,
    ),
    JobSpec(
        # the app export (nba.ops.app_export, ops/launchd/local.nba.app-export.plist): two slots
        # after the morning job, at :35 so it falls between the pretip runs (:20 + a few minutes,
        # :50). Keep in step with the plist.
        "app-export",
        "local.nba.app-export",
        "slots",
        timedelta(minutes=30),
        minutes=(35,),
        hours=(12, 14),
        max_skips=2,
        optional=True,
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


#: skip-only records written by a sub-step of a job (``<name>.skip.json``, no heartbeat of its own):
#: name -> consecutive skips that raise an alert. ``lineups_t30`` / ``lineups_t15``: run-t30 /
#: run-t15 gave up waiting for nba.duckdb (rc 75); one deferral is normal contention, two in a row
#: means that arm's rows are missing.
SKIP_ONLY: dict[str, int] = {"lineups_t30": 2, "lineups_t15": 2}


@dataclass(frozen=True)
class ShadowJob:
    """A shadow step that is not a launchd job: it writes its own heartbeat ``<name>.json`` from
    inside the pretip run (flat keys of the wrappers plus ``status``, ``consecutive_bad`` and
    ``month_block``). Alert after ``max_consecutive`` failed or skipped slate runs; a missed
    monthly install alerts at once past day ``install_deadline_day`` of the month."""

    name: str
    max_consecutive: int
    install_deadline_day: int | None = None


#: AVG_SHADOW_LIVE_2 (docs/prereg/AVG_SHADOW_LIVE_2.md, "What the steward builds" 6): alert after 3
#: consecutive failed or missing slate heartbeats on game days, 1 for a missed monthly weights
#: install past the 3rd. Silent until the step has written its first heartbeat (flag off = no
#: file = no alert).
#: COMBO_STOCKS_SHADOW (docs/prereg/COMBO_STOCKS_SHADOW.md, "What the steward builds" 1): the
#: same contract, heartbeat ``combo_shadow.json`` written by nba.daily.combo_shadow.run; no
#: monthly install.
SHADOW_JOBS: tuple[ShadowJob, ...] = (ShadowJob("avg_shadow", 3, 3), ShadowJob("combo_shadow", 3))


@dataclass(frozen=True)
class Alert:
    key: str  # stable dedupe key, e.g. "lineups:stale"
    message: str


def schedule_tz(ops_dir: Path | None = None) -> ZoneInfo:
    """Zone the calendar slots are written in: env, then ``<ops_dir>/schedule_tz``, then default."""
    name = os.environ.get("NBA_SCHEDULE_TZ", "").strip()
    if not name and ops_dir is not None:
        try:
            name = (ops_dir / TZ_FILE).read_text().strip()
        except OSError:
            name = ""
    if name:
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            pass  # a typo must not blind the watchdog: fall back to the default
    return SCHEDULE_TZ


def last_due(spec: JobSpec, now: datetime, tz: ZoneInfo = SCHEDULE_TZ) -> datetime:
    """Most recent time the job should have started at or before ``now`` (slots in ``tz``)."""
    if spec.kind == "interval":
        assert spec.interval is not None
        return now - spec.interval - spec.grace  # latest acceptable start, grace included
    local = now.astimezone(tz)
    slots = [
        datetime.combine(local.date() + timedelta(days=d), time(h, m), tzinfo=tz)
        for d in (0, -1)
        for h in spec.hours
        for m in spec.minutes
    ]
    return max(s for s in slots if s <= now)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def read_skips(hb_dir: Path, job: str) -> int:
    """Consecutive skipped runs reported by the wrapper (``<job>.skip.json``); 0 when none.
    The skip record is separate from the run heartbeat so a skip never looks like a run."""
    try:
        data = json.loads((hb_dir / f"{job}.skip.json").read_text())
        return int(data["consecutive_skips"]) if data.get("status") == "skipped" else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0


def read_skip_reason(hb_dir: Path, job: str) -> str | None:
    """The reason the wrapper recorded for its latest skip (``<job>.skip.json``), if any."""
    try:
        reason = json.loads((hb_dir / f"{job}.skip.json").read_text()).get("reason")
    except (OSError, ValueError, AttributeError):
        return None
    return str(reason) if reason else None


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
    tz: ZoneInfo = SCHEDULE_TZ,
) -> list[Alert]:
    """Pure-ish health check: returns the alerts for the current moment (no dedupe)."""
    alerts: list[Alert] = []
    loaded = parse_launchctl(launchctl_text)
    for spec in jobs:
        if spec.optional and read_heartbeat(hb_dir, spec.name) is None and spec.label not in loaded:
            continue  # never installed (no heartbeat, label not loaded): nothing to judge yet
        due = last_due(spec, now, tz)
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
        if spec.max_skips is not None and spec.heartbeat:
            n_skips = read_skips(hb_dir, spec.name)
            if n_skips >= spec.max_skips:
                why = (
                    read_skip_reason(hb_dir, spec.name)
                    or "lock held by another job or a stale lock"
                )
                alerts.append(
                    Alert(
                        f"{spec.name}:skipped",
                        f"{spec.name} skipped {n_skips} consecutive runs ({why}); "
                        "no predictions/ticks in that window",
                    )
                )
        reference = start if start is not None else first_seen
        if now > due + spec.grace and reference < due:
            if start is None:
                msg = f"{spec.name} has no heartbeat (expected since {due:%Y-%m-%d %H:%M})"
            else:
                msg = (
                    f"{spec.name} stale: last start {start:%Y-%m-%d %H:%M}, expected by {due:%H:%M}"
                )
            alerts.append(Alert(f"{spec.name}:stale", msg))
    for name, limit in SKIP_ONLY.items():
        n_skips = read_skips(hb_dir, name)
        if n_skips >= limit:
            alerts.append(
                Alert(
                    f"{name}:skipped",
                    f"{name} deferred {n_skips} consecutive times (nba.duckdb stayed busy past "
                    "the bounded wait); that arm's shadow rows are missing",
                )
            )
    alerts += evaluate_shadow_jobs(now, hb_dir, tz)
    status = parse_launchctl(launchctl_text)
    for label, code in sorted(status.items()):
        if code != 0 and label != "local.nba.watchdog":
            alerts.append(
                Alert(f"{label}:launchctl", f"launchctl last exit status {code} for {label}")
            )
    return alerts


#: a shadow heartbeat whose last activity is older than this is a flag that was switched off: silent
SHADOW_IDLE_AFTER = timedelta(days=3)
#: a run that wrote its START but no END within this long died mid-run
SHADOW_RUN_TIMEOUT = timedelta(minutes=30)


def evaluate_shadow_jobs(
    now: datetime,
    hb_dir: Path,
    tz: ZoneInfo = SCHEDULE_TZ,
    jobs: tuple[ShadowJob, ...] = SHADOW_JOBS,
) -> list[Alert]:
    """Alerts for the in-process shadow steps from their heartbeat files (no file, or no
    activity for ``SHADOW_IDLE_AFTER`` = flag off: silent)."""
    alerts: list[Alert] = []
    for spec in jobs:
        hb = read_heartbeat(hb_dir, spec.name)
        if not hb:
            continue
        start, end = _parse_ts(hb.get("start_ts")), _parse_ts(hb.get("end_ts"))
        last = end or start
        if last is None:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)  # the step writes naive UTC
        if now - last > SHADOW_IDLE_AFTER:
            continue
        if end is None and start is not None and now - last > SHADOW_RUN_TIMEOUT:
            alerts.append(
                Alert(
                    f"{spec.name}:hung",
                    f"{spec.name} started {last:%Y-%m-%d %H:%M} UTC and never finished "
                    "(died mid-run); the shadow is missing rows, production is unaffected",
                )
            )
        bad = hb.get("consecutive_bad")
        if isinstance(bad, int) and bad >= spec.max_consecutive:
            alerts.append(
                Alert(
                    f"{spec.name}:failed",
                    f"{spec.name} failed or skipped {bad} consecutive slate runs (last status "
                    f"{hb.get('status')}); the shadow is missing rows, production is unaffected",
                )
            )
        block, today = hb.get("month_block"), now.astimezone(tz)
        past_deadline = spec.install_deadline_day is not None and (
            today.day > spec.install_deadline_day
        )
        behind = isinstance(block, str) and block < today.strftime("%Y-%m")
        if past_deadline and (behind or hb.get("stale_model") is True):
            alerts.append(
                Alert(
                    f"{spec.name}:install",
                    f"{spec.name} is serving stale weights for {today:%Y-%m} (newest bundle "
                    f"{block}); rows are written stale_model=1 (deadline: the 1st, alert after "
                    f"the {spec.install_deadline_day}th)",
                )
            )
    return alerts


def on_battery() -> bool:
    """True when ``pmset -g batt`` reports battery power ("Battery Power" vs "AC Power")."""
    res = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
    return "Battery Power" in res.stdout


def caffeinate_running() -> bool:
    """True when a ``caffeinate`` process exists (``pgrep -x`` exit 0)."""
    return (
        subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True, timeout=10).returncode
        == 0
    )


def todays_slate(data_dir: Path, today: date) -> list[str]:
    """Regular-season game_ids whose ET date is ``today``: the schedule cache, plus the live tips
    parquet (real tip times, which can move a game across midnight)."""
    ids: set[str] = set()
    y = today.year if today.month >= 8 else today.year - 1
    cache = data_dir / "schedule_cache" / f"schedule_{y}-{(y + 1) % 100:02d}.json"
    try:
        rows = json.loads(cache.read_text())
    except (OSError, ValueError):
        rows = []
    for row in rows:
        gid, tip = str(row[0]), datetime.fromisoformat(row[1]).replace(tzinfo=UTC)
        if gid.startswith(REGULAR_SEASON_PREFIX) and tip.astimezone(ET).date() == today:
            ids.add(gid)
    tips = data_dir / "schedule" / f"live_tips_{y}-{(y + 1) % 100:02d}.parquet"
    if tips.exists():
        import polars as pl

        for r in pl.read_parquet(tips).iter_rows(named=True):
            gid = str(r["gameId"])
            tip = datetime.fromisoformat(str(r["gameDateTimeUTC"]).replace("Z", "+00:00"))
            if gid.startswith(REGULAR_SEASON_PREFIX) and tip.astimezone(ET).date() == today:
                ids.add(gid)
    return sorted(ids)


class DbBusy(Exception):
    """nba.duckdb stayed write-locked past the bounded wait: try again at the next check."""


def games_in_db(db_path: Path, game_ids: Sequence[str]) -> set[str]:
    """The subset of ``game_ids`` present in ``games`` (read-only, bounded wait on a writer)."""
    import duckdb

    from nba.ops.coverage import connect_ro

    try:
        con = connect_ro(db_path, retry_s=GAMEDAY_DB_RETRY_S, max_s=GAMEDAY_DB_WAIT_S)
    except duckdb.IOException as e:
        if "lock" in str(e).lower():
            raise DbBusy(str(e)) from e
        raise
    try:
        ph = ",".join("?" for _ in game_ids)
        rows = con.execute(
            f"SELECT game_id FROM games WHERE game_id IN ({ph})",  # noqa: S608
            list(game_ids),
        ).fetchall()
    finally:
        con.close()
    return {str(r[0]) for r in rows}


def evaluate_game_day(
    now: datetime,
    ops_dir: Path,
    *,
    battery: Callable[[], bool] = on_battery,
    caffeinate: Callable[[], bool] = caffeinate_running,
    slate_fn: Callable[[Path, date], list[str]] = todays_slate,
    present_fn: Callable[[Path, Sequence[str]], set[str]] = games_in_db,
    data_dir: Path | None = None,
    db_path: Path | None = None,
) -> list[Alert]:
    """Game-day probe: silent off game days and before 09:00 ET; otherwise the four checks.

    Every alert key is ``gameday:<ET date>:<check>``. ``battery``, ``caffeinate``, ``slate_fn`` and
    ``present_fn`` are injected (real ``pmset`` / ``pgrep`` / schedule files / read-only DuckDB by
    default). A check that cannot compute becomes its own ``...:probe`` alert; nothing raises."""
    local = now.astimezone(ET)
    if local.time() < GAMEDAY_PROBE_FROM:
        return []
    day = f"{local.date():%Y-%m-%d}"
    prefix = f"{GAMEDAY_PREFIX}{day}"
    data = data_dir if data_dir is not None else ops_dir.parent
    db = db_path if db_path is not None else data.parent / "nba.duckdb"
    try:
        slate = slate_fn(data, local.date())
    except Exception as e:
        return [
            Alert(
                f"{prefix}:probe",
                f"game day {day}: probe cannot read the slate ({type(e).__name__}: {e})",
            )
        ]
    if not slate:
        return []
    alerts: list[Alert] = []

    def fail(check_name: str, e: Exception) -> None:
        alerts.append(
            Alert(
                f"{prefix}:probe-{check_name}",
                f"game day {day}: {check_name} probe could not compute ({type(e).__name__}: {e})",
            )
        )

    try:
        if battery():
            alerts.append(
                Alert(
                    f"{prefix}:battery",
                    f"game day {day}: on battery; plug in and run `caffeinate -dimsu`",
                )
            )
    except Exception as e:
        fail("battery", e)
    try:
        if not caffeinate():
            alerts.append(
                Alert(
                    f"{prefix}:caffeinate",
                    f"game day {day}: caffeinate is not running; run `caffeinate -dimsu`",
                )
            )
    except Exception as e:
        fail("caffeinate", e)
    try:
        have = present_fn(db, slate)
        missing = [g for g in slate if g not in have]
        if missing:
            alerts.append(
                Alert(
                    f"{prefix}:slate",
                    f"game day {day}: games table lacks today's slate: {len(missing)} of "
                    f"{len(slate)} game_ids missing; the schedule fetch does not write "
                    "scheduled games",
                )
            )
    except DbBusy:
        pass  # a writer holds nba.duckdb: judged again at the next check, not an alert
    except Exception as e:
        fail("slate", e)
    try:
        hb = read_heartbeat(ops_dir / "heartbeat", "daily-morning")
        start = _parse_ts(hb.get("start_ts")) if hb else None
        end = _parse_ts(hb.get("end_ts")) if hb else None
        started_today = start is not None and start.astimezone(ET).date() == local.date()
        if not started_today:
            alerts.append(
                Alert(
                    f"{prefix}:morning",
                    f"game day {day}: the morning job has not started today; check "
                    "`launchctl list | grep daily-morning` and data/ops/daily-morning logs",
                )
            )
        elif end is None or (start is not None and end < start):
            alerts.append(
                Alert(
                    f"{prefix}:morning",
                    f"game day {day}: the morning job started but has not ended; check "
                    "data/ops/morning_*.log",
                )
            )
    except Exception as e:
        fail("morning", e)
    return alerts


def dedupe(
    alerts: list[Alert], state: dict[str, str], now: datetime
) -> tuple[list[Alert], dict[str, str]]:
    """Return alerts due now and the new state (resolved conditions are forgotten)."""
    fresh: list[Alert] = []
    # game-day keys stay in the state for the rest of their day even when the condition clears, so a
    # flapping condition does not alert twice; other days' keys are forgotten
    new_state: dict[str, str] = {
        k: v
        for k, v in state.items()
        if k.startswith(GAMEDAY_PREFIX) and k.split(":")[1] == f"{now.astimezone(ET):%Y-%m-%d}"
    }
    for a in alerts:
        prev = _parse_ts(state.get(a.key))
        once_a_day = a.key.startswith(GAMEDAY_PREFIX)  # the ET date is in the key
        if prev is None or (not once_a_day and now - prev >= REALERT_AFTER):
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
    gameday: Callable[[datetime, Path], list[Alert]] = evaluate_game_day,
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
    current = evaluate(now, ops_dir / "heartbeat", text, first_seen, kl, tz=schedule_tz(ops_dir))
    try:
        current += gameday(now, ops_dir)
    except Exception as e:  # the probe must never take the rest of the check down
        current.append(Alert("gameday:probe", f"game-day probe failed: {type(e).__name__}: {e}"))
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
        tz=schedule_tz(ops_dir),
    )
    lines = ["## Job health", ""]
    if not alerts:
        lines.append("All launchd jobs healthy (heartbeats fresh, no non-zero launchctl status).")
    else:
        lines += [f"- {a.message}" for a in alerts]
    return "\n".join(lines) + "\n"


def _write_fake_heartbeat(
    hb_dir: Path, job: str, start: datetime, end: datetime | None, rc: int | None
) -> None:
    hb_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": job,
        "mode": "drill",
        "host": "drill",
        "start_ts": start.isoformat(),
        "end_ts": end.isoformat() if end else None,
        "rc": rc,
    }
    (hb_dir / f"{job}.json").write_text(json.dumps(rec))


def drill(source_hb: Path, now: datetime | None = None) -> dict[str, object]:
    """Fault-injection drill on a TEMPORARY copy of the heartbeat dir (``source_hb`` is only read).

    Builds a healthy baseline for every heartbeat job, then injects (1) a missed pretip (the newest
    pretip start is one slot older than the slot that is due) and (2) a failed morning job (rc 1),
    runs the real ``check`` on the copy with a collecting notifier, and returns what it raised. The
    checker is evaluated just past the pretip grace window of the last due slot, so the missed run
    is overdue by construction. Nothing outside the temporary directory is written; no launchctl,
    no osascript."""
    real_now = now or datetime.now().astimezone()
    tz = schedule_tz(source_hb.parent)
    specs = {s.name: s for s in JOBS}
    pre, mor = specs["daily-pretip"], specs["daily-morning"]
    drill_now = (
        last_due(pre, real_now, tz) + pre.grace + timedelta(minutes=2)
    )  # < the 30 min slot gap
    with tempfile.TemporaryDirectory(prefix="watchdog_drill_") as tmp:
        ops = Path(tmp) / "ops"
        hb = ops / "heartbeat"
        if source_hb.is_dir():
            shutil.copytree(source_hb, hb)
        hb.mkdir(parents=True, exist_ok=True)
        tz_file = source_hb.parent / TZ_FILE
        if tz_file.exists():
            shutil.copy(
                tz_file, ops / TZ_FILE
            )  # the copy is judged in the zone the live checker uses
        for f in hb.glob("*.skip.json"):
            f.unlink()
        for sj in SHADOW_JOBS:
            (hb / f"{sj.name}.json").unlink(missing_ok=True)
        for spec in JOBS:
            if spec.heartbeat and not spec.optional:
                start = last_due(spec, drill_now, tz) + timedelta(seconds=30)
                _write_fake_heartbeat(hb, spec.name, start, start + timedelta(minutes=1), 0)
        (hb / "app-export.json").unlink(missing_ok=True)
        healthy = (
            "-\t0\tlocal.nba.lineups\n-\t0\tlocal.nba.daily-pretip\n-\t0\tlocal.nba.daily-morning\n"
        )
        base = evaluate(drill_now, hb, healthy, drill_now, None, tz=tz)
        missed = last_due(pre, drill_now, tz) - timedelta(minutes=30)
        _write_fake_heartbeat(hb, "daily-pretip", missed, missed + timedelta(minutes=1), 0)
        m_start = last_due(mor, drill_now, tz) + timedelta(seconds=30)
        _write_fake_heartbeat(hb, "daily-morning", m_start, m_start + timedelta(hours=1), 1)
        sent: list[str] = []
        fresh = check(
            ops, now=drill_now, launchctl_text=healthy, kalshi_log=None, notifier=sent.append
        )
        alerts_md = (
            (ops / "ALERTS.md").read_text().splitlines() if (ops / "ALERTS.md").exists() else []
        )
    keys = [a.key for a in fresh]
    return {
        "drill_now": drill_now.isoformat(),
        "baseline_alerts": [a.key for a in base],
        "raised": keys,
        "missed_pretip_raised": "daily-pretip:stale" in keys,
        "failed_morning_raised": "daily-morning:rc" in keys,
        "notifications": sent,
        "alerts_md": alerts_md,
        "source_untouched": str(source_hb),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="nba.ops.watchdog")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check job heartbeats + launchctl status, alert on problems")
    c.add_argument("--ops-dir", default="data/ops")
    c.add_argument("--no-notify", action="store_true")
    d = sub.add_parser(
        "drill", help="fault-injection drill on a temporary copy of the heartbeat dir"
    )
    d.add_argument("--hb-dir", default="data/ops/heartbeat", help="read-only source of the copy")
    args = p.parse_args(argv)
    if args.command == "drill":
        res = drill(Path(args.hb_dir))
        print(json.dumps(res, indent=1))
        return 0 if res["missed_pretip_raised"] and res["failed_morning_raised"] else 1
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
