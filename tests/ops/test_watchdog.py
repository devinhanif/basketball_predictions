"""Watchdog: stale heartbeat, bad rc, launchctl exit 126, dedupe, healthy (no launchctl calls)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from nba.ops.watchdog import JOBS, Alert, check, dedupe, evaluate, last_due, parse_launchctl

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


def test_bad_rc_alerts_but_pretip_rc2_ok(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    _all_fresh(hb)
    pretip = next(s for s in JOBS if s.name == "daily-pretip")
    start = last_due(pretip, NOW) + timedelta(seconds=30)
    _hb(hb, "daily-pretip", start, 2)
    assert _eval(tmp_path) == []
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
    assert last_due(pretip, NOW).isoformat() == "2026-10-09T14:30:00-05:00"
    early = datetime.fromisoformat("2026-10-09T07:00:00-05:00")
    assert last_due(pretip, early).isoformat() == "2026-10-08T21:30:00-05:00"
