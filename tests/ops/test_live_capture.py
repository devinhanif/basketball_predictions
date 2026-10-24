"""Nightly live-capture launcher: plan, watchdog check, wrapper behaviour (fake uv, no network)."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from nba.ops import live_capture as lc
from nba.ops.watchdog import evaluate_game_day, live_capture_problem

REPO = Path(__file__).resolve().parents[2]
DAY = date(2026, 10, 24)
FIRST = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # 18:00 ET
LAST = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)
TIPS = [FIRST, FIRST + timedelta(hours=1), LAST]


def _hb(status: str, day: str = "2026-10-24") -> dict[str, object]:
    return {"job": "live_capture", "date": day, "status": status}


# --- plan


def test_plan_decisions() -> None:
    f = lc.plan
    assert f(FIRST, [], None, DAY)[0] == "no_games"
    assert f(FIRST - timedelta(minutes=151), TIPS, None, DAY)[0] == "early"
    assert f(FIRST - timedelta(minutes=150), TIPS, None, DAY)[0] == "start"  # first tip - 150 min
    assert f(FIRST - timedelta(minutes=30), TIPS, _hb("running"), DAY)[0] == "start"
    assert f(FIRST, TIPS, _hb("done"), DAY)[0] == "done"
    assert f(FIRST, TIPS, _hb("done", "2026-10-23"), DAY)[0] == "start"  # yesterday's beat
    assert f(LAST + timedelta(hours=4, minutes=1), TIPS, None, DAY)[0] == "over"


def test_plan_cli_reads_the_schedule_cache_and_fails_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    (data / "schedule_cache").mkdir(parents=True)
    rows = [["0022600104", "2026-10-24T22:00:00"], ["0022600105", "2026-10-25T01:00:00"]]
    (data / "schedule_cache" / "schedule_2026-27.json").write_text(json.dumps(rows))
    base = ["plan", "--date", "2026-10-24", "--data-dir", str(data)]
    assert lc.main([*base, "--now", "2026-10-24T18:00:00Z"]) == 0
    assert capsys.readouterr().out.startswith("early start at 19:30Z")  # 22:00Z - 150 min
    assert lc.main([*base, "--now", "2026-10-24T19:30:00Z"]) == 0
    assert capsys.readouterr().out.startswith("start ")
    assert lc.main(["plan", "--date", "2026-10-24", "--data-dir", str(tmp_path / "none")]) == 0
    assert capsys.readouterr().out.startswith("unknown")


# --- watchdog


def test_problem_only_inside_the_window_and_when_stale() -> None:
    opens = FIRST - lc.WINDOW_LEAD
    t = opens + timedelta(minutes=20)
    fresh = t - timedelta(seconds=40)
    assert lc.problem(opens + timedelta(minutes=5), TIPS, None, None, DAY) is None  # grace
    assert lc.problem(opens - timedelta(hours=1), TIPS, None, None, DAY) is None  # not due
    assert "no heartbeat" in (lc.problem(t, TIPS, None, None, DAY) or "")
    assert "2026-10-23" in (lc.problem(t, TIPS, _hb("done", "2026-10-23"), fresh, DAY) or "")
    assert lc.problem(t, TIPS, _hb("running"), fresh, DAY) is None
    assert lc.problem(t, TIPS, _hb("waiting"), fresh, DAY) is None
    stale = lc.problem(t, TIPS, _hb("running"), t - timedelta(minutes=9), DAY)
    assert stale and "9 min old" in stale
    assert lc.problem(t, TIPS, _hb("done"), t - timedelta(hours=1), DAY) is None
    assert lc.problem(LAST + timedelta(hours=5), TIPS, None, None, DAY) is None  # window over
    assert lc.problem(t, [], None, None, DAY) is None  # no games


def _gameday(ops: Path, now: datetime, **kw: object) -> list[str]:
    alerts = evaluate_game_day(
        now,
        ops,
        battery=lambda: False,
        caffeinate=lambda: True,
        slate_fn=lambda d, day: ["0022600104"],
        present_fn=lambda db, ids: set(ids),
        tips_fn=lambda d, day: {"0022600104": FIRST, "0022600105": LAST},
        kalshi_fn=lambda d: (now, []),
        **kw,  # type: ignore[arg-type]
    )
    return [a.key for a in alerts if "livecap" in a.key]


def _morning_ok(ops: Path, now: datetime) -> None:
    hb = ops / "heartbeat"
    hb.mkdir(parents=True, exist_ok=True)
    rec = {
        "job": "daily-morning",
        "start_ts": (now - timedelta(hours=8)).isoformat(),
        "end_ts": (now - timedelta(hours=7)).isoformat(),
        "rc": 0,
    }
    (hb / "daily-morning.json").write_text(json.dumps(rec))


def test_watchdog_alerts_in_window_and_stays_silent_when_never_installed(tmp_path: Path) -> None:
    now = FIRST - timedelta(minutes=90)  # inside the window, before the first tip
    _morning_ok(tmp_path, now)
    assert _gameday(tmp_path, now) == []  # no capture or launcher heartbeat ever: silent
    (tmp_path / "heartbeat" / lc.LAUNCH_HEARTBEAT).write_text("{}")  # launcher installed
    keys = _gameday(tmp_path, now)
    assert len(keys) == 1 and keys[0].startswith("gameday:2026-10-24:livecap@")


def test_watchdog_silent_with_a_fresh_capture_heartbeat(tmp_path: Path) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    hb = tmp_path / "heartbeat"
    hb.mkdir()
    (hb / lc.HEARTBEAT).write_text(json.dumps(_hb("running", f"{now:%Y-%m-%d}")))
    tips = [now + timedelta(minutes=100)]
    assert live_capture_problem(tmp_path, now, tips, now.date()) is None
    old = time.time() - 900
    os.utime(hb / lc.HEARTBEAT, (old, old))
    assert "old" in (live_capture_problem(tmp_path, now, tips, now.date()) or "")


# --- wrapper


def _fake_uv(tmp: Path, plan: str) -> Path:
    uv = tmp / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *nba.ops.live_capture*) echo "{plan}" ;;\n'
        '  *nba.live*) echo "$*" >> "$FAKE_CAPTURE_LOG"; exit "${FAKE_CAPTURE_RC:-0}" ;;\n'
        "esac\n"
    )
    uv.chmod(0o755)
    cp = tmp / "caffeinate"
    cp.write_text('#!/bin/sh\nshift\nexec "$@"\n')  # drop -i
    cp.chmod(0o755)
    return uv


def _run_wrapper(tmp: Path, plan: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
    (tmp / "root").mkdir(exist_ok=True)
    env = {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(_fake_uv(tmp, plan)),
        "NBA_CAFFEINATE": str(tmp / "caffeinate"),
        "NBA_TEST_DATE_ET": "2026-10-24",
        "FAKE_CAPTURE_LOG": str(tmp / "capture.log"),
        "FAKE_CAPTURE_RC": str(rc),
    }
    return subprocess.run(
        ["/bin/sh", str(REPO / "ops" / "nba_live_capture.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _launched(tmp: Path) -> list[str]:
    p = tmp / "capture.log"
    return p.read_text().splitlines() if p.exists() else []


@pytest.mark.parametrize("plan", ["early start at 19:30Z", "no_games none", "done x", "over x"])
def test_wrapper_does_nothing_when_not_due(tmp_path: Path, plan: str) -> None:
    assert _run_wrapper(tmp_path, plan).returncode == 0
    assert _launched(tmp_path) == []
    hb = json.loads((tmp_path / "root/data/ops/heartbeat/live-capture-launch.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"] is not None


def test_wrapper_starts_the_capture_once_and_releases_the_lock(tmp_path: Path) -> None:
    assert _run_wrapper(tmp_path, "start first tip").returncode == 0
    (line,) = _launched(tmp_path)
    assert "nba.live capture --date 2026-10-24" in line
    assert not (tmp_path / "root/data/ops/live_capture_lock").exists()


def test_wrapper_start_is_idempotent_while_a_capture_is_running(tmp_path: Path) -> None:
    lock = tmp_path / "root/data/ops/live_capture_lock"
    lock.mkdir(parents=True)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (lock / "pid").write_text(str(sleeper.pid))
        assert _run_wrapper(tmp_path, "start first tip").returncode == 0
        assert _launched(tmp_path) == []
        assert lock.exists() and (lock / "pid").read_text().strip() == str(sleeper.pid)
    finally:
        sleeper.kill()
        sleeper.wait()


def test_wrapper_clears_a_dead_owners_lock_and_starts(tmp_path: Path) -> None:
    lock = tmp_path / "root/data/ops/live_capture_lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999")
    assert _run_wrapper(tmp_path, "start first tip").returncode == 0
    assert len(_launched(tmp_path)) == 1


def test_wrapper_fails_open_on_an_unreadable_schedule_and_alerts_on_capture_failure(
    tmp_path: Path,
) -> None:
    assert _run_wrapper(tmp_path, "unknown FileNotFoundError", rc=2).returncode == 0
    assert len(_launched(tmp_path)) == 1
    alerts = (tmp_path / "root/data/ops/ALERTS.md").read_text()
    assert "[live-capture] capture for 2026-10-24 exited rc=2" in alerts
    hb = json.loads((tmp_path / "root/data/ops/heartbeat/live-capture-launch.json").read_text())
    assert hb["rc"] == 2


def test_wrapper_takes_no_daily_or_heavy_lock_and_never_opens_the_db() -> None:
    text = (REPO / "ops" / "nba_live_capture.sh").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "data/ops/lock" not in code and "heavy.lock" not in code and "nba.duckdb" not in code
    assert "LOCK=$OPS/live_capture_lock" in code


def test_plist_is_valid_and_launches_through_uv() -> None:
    import plistlib

    p = plistlib.loads((REPO / "ops/launchd/local.nba.live-capture-start.plist").read_bytes())
    assert p["Label"] == "local.nba.live-capture-start"
    args = p["ProgramArguments"]
    assert args[0].endswith("/uv") and args[-1].endswith("ops/nba_live_capture.sh")
    assert "sh" in args and args[0] != "/bin/sh"
    assert p["StartInterval"] == 600 and p["RunAtLoad"] is True
    assert (REPO / "ops" / "nba_live_capture.sh").exists()
    assert timedelta(minutes=150) == lc.START_LEAD
