"""Watchdog: stale heartbeat, bad rc, launchctl exit 126, dedupe, healthy (no launchctl calls)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from nba.ops.watchdog import (
    JOBS,
    Alert,
    check,
    dedupe,
    evaluate,
    last_due,
    parse_launchctl,
    schedule_tz,
)

NOW = datetime.fromisoformat("2026-10-09T14:35:00-05:00")
HEALTHY_LAUNCHCTL = "-\t0\tlocal.nba.lineups\n-\t0\tlocal.nba.daily-pretip\n123\t0\tcom.apple.x\n"
LAUNCHCTL_126 = "PID\tStatus\tLabel\n-\t126\tlocal.nba.daily-morning\n-\t0\tlocal.nba.lineups\n"


def _hb(d: Path, job: str, start: datetime, rc: int | None, end: bool = True) -> None:
    d.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": job,
        "mode": "x",
        "host": "h",
        "start_ts": start.isoformat(),
        "end_ts": (start + timedelta(minutes=1)).isoformat() if end else None,
        "rc": rc if end else None,
    }
    (d / f"{job}.json").write_text(json.dumps(rec))


def _all_fresh(d: Path, now: datetime = NOW) -> None:
    for spec in JOBS:
        if spec.heartbeat:
            _hb(d, spec.name, last_due(spec, now) + timedelta(seconds=30), 0)


def _kalshi_log(p: Path, now: datetime) -> Path:
    p.write_text("snapshot\n")
    ts = (now - timedelta(minutes=3)).timestamp()
    os.utime(p, (ts, ts))
    return p


def _eval(tmp: Path, launchctl: str = HEALTHY_LAUNCHCTL) -> list[Alert]:
    return evaluate(
        NOW, tmp / "hb", launchctl, NOW - timedelta(days=2), _kalshi_log(tmp / "k.log", NOW)
    )


def test_healthy_no_alerts(tmp_path: Path) -> None:
    _all_fresh(tmp_path / "hb")
    assert _eval(tmp_path) == []


def test_stale_start_alerts(tmp_path: Path) -> None:
    _all_fresh(tmp_path / "hb")
    _hb(tmp_path / "hb", "lineups", NOW - timedelta(hours=2), 0)
    keys = [a.key for a in _eval(tmp_path)]
    assert keys == ["lineups:stale"]


def test_missing_heartbeat_alerts_after_first_seen(tmp_path: Path) -> None:
    _all_fresh(tmp_path / "hb")
    (tmp_path / "hb" / "lineups.json").unlink()
    assert [a.key for a in _eval(tmp_path)] == ["lineups:stale"]
    # a brand-new watchdog gives a never-run job one interval before complaining
    fresh = evaluate(
        NOW, tmp_path / "hb", "", NOW - timedelta(minutes=1), _kalshi_log(tmp_path / "k.log", NOW)
    )
    assert fresh == []


def test_bad_rc_alerts_but_pretip_rc4_ok(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    _all_fresh(hb)
    pretip = next(s for s in JOBS if s.name == "daily-pretip")
    start = last_due(pretip, NOW) + timedelta(seconds=30)
    _hb(hb, "daily-pretip", start, 4)  # informational: tipped games refused
    assert _eval(tmp_path) == []
    _hb(hb, "daily-pretip", start, 2)  # argparse usage error must not be masked
    assert [a.key for a in _eval(tmp_path)] == ["daily-pretip:rc"]
    _hb(hb, "daily-pretip", start, 1)
    assert [a.key for a in _eval(tmp_path)] == ["daily-pretip:rc"]
    _hb(hb, "lineups", NOW - timedelta(minutes=2), 2)
    assert "lineups:rc" in [a.key for a in _eval(tmp_path)]


def test_running_job_with_no_end_yet_is_not_rc_alert(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    _all_fresh(hb)
    _hb(hb, "lineups", NOW - timedelta(minutes=2), None, end=False)
    assert _eval(tmp_path) == []


def test_kalshi_stale_log_alerts(tmp_path: Path) -> None:
    _all_fresh(tmp_path / "hb")
    log = _kalshi_log(tmp_path / "k.log", NOW - timedelta(hours=3))
    out = evaluate(NOW, tmp_path / "hb", "", NOW - timedelta(days=2), log)
    assert [a.key for a in out] == ["kalshi-snapshot:stale"]


def test_launchctl_126_alerts() -> None:
    assert parse_launchctl(LAUNCHCTL_126) == {
        "local.nba.daily-morning": 126,
        "local.nba.lineups": 0,
    }
    # parse is by label, exit codes may be negative (signals); non-local labels ignored
    assert parse_launchctl("-\t-9\tlocal.nba.x\n-\t1\tcom.other\n") == {"local.nba.x": -9}


def test_launchctl_126_alert_end_to_end(tmp_path: Path) -> None:
    _all_fresh(tmp_path / "hb")
    out = _eval(tmp_path, LAUNCHCTL_126)
    assert [a.key for a in out] == ["local.nba.daily-morning:launchctl"]
    assert "126" in out[0].message


def test_dedupe_and_realert_after_6h() -> None:
    a = [Alert("lineups:stale", "m")]
    fresh, st = dedupe(a, {}, NOW)
    assert fresh == a
    fresh2, st2 = dedupe(a, st, NOW + timedelta(minutes=10))
    assert fresh2 == [] and st2 == st
    fresh3, _ = dedupe(a, st2, NOW + timedelta(hours=6, minutes=1))
    assert fresh3 == a
    # resolved condition is forgotten, so a recurrence alerts immediately
    _, cleared = dedupe([], st2, NOW + timedelta(minutes=20))
    assert cleared == {}
    assert dedupe(a, cleared, NOW + timedelta(minutes=30))[0] == a


def test_check_writes_alerts_md_once(tmp_path: Path) -> None:
    ops = tmp_path / "ops"
    _all_fresh(ops / "heartbeat")
    _hb(ops / "heartbeat", "lineups", NOW - timedelta(hours=2), 0)
    log = _kalshi_log(tmp_path / "k.log", NOW)
    sent: list[str] = []
    first = check(ops, NOW, "", log, sent.append)
    again = check(ops, NOW + timedelta(minutes=10), "", log, sent.append)
    assert [a.key for a in first] == ["lineups:stale"] and again == []
    assert len(sent) == 1
    assert (ops / "ALERTS.md").read_text().count("[watchdog] lineups stale") == 1


def test_last_due_slots() -> None:
    pretip = next(s for s in JOBS if s.name == "daily-pretip")
    assert last_due(pretip, NOW).isoformat() == "2026-10-09T14:20:00-05:00"
    early = datetime.fromisoformat("2026-10-09T07:00:00-05:00")
    assert last_due(pretip, early).isoformat() == "2026-10-08T21:50:00-05:00"


CT = ZoneInfo("America/Chicago")
PRETIP = next(s for s in JOBS if s.name == "daily-pretip")


def test_pretip_slots_are_20_and_50() -> None:
    assert PRETIP.minutes == (20, 50)
    assert last_due(PRETIP, datetime(2026, 10, 20, 14, 49, tzinfo=CT)) == datetime(
        2026, 10, 20, 14, 20, tzinfo=CT
    )
    assert last_due(PRETIP, datetime(2026, 10, 20, 14, 50, 1, tzinfo=CT)).minute == 50
    # before the first slot of the day: yesterday's last slot (21:50)
    assert last_due(PRETIP, datetime(2026, 10, 20, 8, 0, tzinfo=CT)) == datetime(
        2026, 10, 19, 21, 50, tzinfo=CT
    )


def test_last_due_is_dst_correct_across_fall_back() -> None:
    # 2026-11-01 02:00 CDT -> 01:00 CST. Yesterday's 21:50 slot (Oct 31, CDT = UTC-5) is 1 h
    # earlier in UTC than a fixed-offset guess built from today's CST offset (UTC-6).
    now = datetime(2026, 11, 1, 8, 5, tzinfo=CT)  # CST
    due = last_due(PRETIP, now)
    assert due == datetime(2026, 10, 31, 21, 50, tzinfo=CT)
    assert due.utcoffset() == timedelta(hours=-5)
    morning = next(s for s in JOBS if s.name == "daily-morning")
    assert last_due(morning, datetime(2026, 11, 1, 7, 59, tzinfo=CT)) == datetime(
        2026, 10, 31, 8, 0, tzinfo=CT
    )
    # a job that ran at its Oct 31 21:50 CDT slot is NOT stale at 08:05 CST on Nov 1
    hb = datetime(2026, 10, 31, 21, 50, 30, tzinfo=CT)
    assert hb.utcoffset() == timedelta(hours=-5)
    # spring forward mirror: Mar 8 2027 02:00 CST -> CDT
    due2 = last_due(PRETIP, datetime(2027, 3, 8, 8, 5, tzinfo=CT))
    assert due2 == datetime(2027, 3, 7, 21, 50, tzinfo=CT) and due2.utcoffset() == timedelta(
        hours=-6
    )


def _skip(d: Path, job: str, n: int) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{job}.skip.json").write_text(
        json.dumps({"job": job, "status": "skipped", "consecutive_skips": n})
    )


def test_consecutive_skips_alert_without_touching_run_heartbeat(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    _all_fresh(hb)
    before = (hb / "daily-pretip.json").read_text()
    _skip(hb, "daily-pretip", 2)
    assert _eval(tmp_path) == []  # below the threshold (3)
    _skip(hb, "daily-pretip", 3)
    out = _eval(tmp_path)
    assert [a.key for a in out] == ["daily-pretip:skipped"] and "3 consecutive" in out[0].message
    assert (hb / "daily-pretip.json").read_text() == before
    (hb / "daily-pretip.skip.json").unlink()
    assert _eval(tmp_path) == []


ET = ZoneInfo("America/New_York")


def test_schedule_tz_env_then_file_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NBA_SCHEDULE_TZ", raising=False)
    assert schedule_tz(tmp_path) == CT
    (tmp_path / "schedule_tz").write_text("America/New_York\n")
    assert schedule_tz(tmp_path) == ET
    monkeypatch.setenv("NBA_SCHEDULE_TZ", "America/Chicago")
    assert schedule_tz(tmp_path) == CT
    monkeypatch.setenv("NBA_SCHEDULE_TZ", "Not/AZone")  # typo: default, never a crash
    assert schedule_tz(tmp_path) == CT


def test_launchd_on_eastern_slots_no_false_stale_after_last_run(tmp_path: Path) -> None:
    """2026-10-11 incident: launchd fires 'Hour 9..21' on Eastern time while the clock is Central.
    The last pretip start is 20:50 CDT (= 21:50 ET) and the morning job starts 07:00 CDT (08:00 ET);
    judged in Central both looked stale every night. Judged in the zone launchd uses, neither is."""
    hb = tmp_path / "hb"
    hb.mkdir()
    for job, mode, start in (
        ("daily-pretip", "pretip", "2026-10-11T20:50:02-05:00"),
        ("daily-morning", "morning", "2026-10-11T07:00:05-05:00"),
        ("lineups", "lineups", "2026-10-11T22:55:00-05:00"),
    ):
        (hb / f"{job}.json").write_text(
            json.dumps({"job": job, "mode": mode, "start_ts": start, "end_ts": start, "rc": 0})
        )
    now = datetime(2026, 10, 11, 22, 18, tzinfo=CT)
    seen = datetime(2026, 10, 9, tzinfo=CT)
    central = {a.key for a in evaluate(now, hb, "", seen, tz=CT)}
    assert {"daily-pretip:stale", "daily-morning:stale"} <= central  # the old false alarms
    assert evaluate(now, hb, "", seen, jobs=JOBS[:3], tz=ET) == []
    # and a genuinely missed slot is still caught in the Eastern frame: 09:50 ET slot missed
    # (last start 08:20 CDT = 09:20 ET), judged after 10:15 CDT
    (hb / "daily-pretip.json").write_text(
        json.dumps(
            {
                "job": "daily-pretip",
                "mode": "pretip",
                "start_ts": "2026-10-12T08:20:03-05:00",
                "end_ts": "2026-10-12T08:20:30-05:00",
                "rc": 0,
            }
        )
    )
    soon = datetime(2026, 10, 12, 9, 40, tzinfo=CT)  # slot due 09:20 CDT, grace not yet over
    assert "daily-pretip:stale" not in {a.key for a in evaluate(soon, hb, "", seen, tz=ET)}
    later = datetime(2026, 10, 12, 10, 16, tzinfo=CT)  # 09:50 CDT slot missed, grace over
    out = evaluate(later, hb, "", seen, tz=ET)
    assert "daily-pretip:stale" in {a.key for a in out}
