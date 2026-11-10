"""The nightly launcher of the 15 s poller: decisions, lock, heartbeat, alerts (fake uv)."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ops" / "nba_kalshi_candles.sh"


def _fake_uv(tmp: Path, plan: str) -> Path:
    uv = tmp / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        f'  *poll15*plan*) echo "{plan}" ;;\n'
        '  *poll15*run*) echo "$*" >> "$FAKE_POLL_LOG"; exit "${FAKE_POLL_RC:-0}" ;;\n'
        "esac\n"
    )
    uv.chmod(0o755)
    cp = tmp / "caffeinate"
    cp.write_text('#!/bin/sh\nshift\nexec "$@"\n')  # drop -i
    cp.chmod(0o755)
    return uv


def _run(tmp: Path, plan: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
    (tmp / "root").mkdir(exist_ok=True)
    env = {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(_fake_uv(tmp, plan)),
        "NBA_CAFFEINATE": str(tmp / "caffeinate"),
        "NBA_TEST_DATE_ET": "2026-11-09",
        "FAKE_POLL_LOG": str(tmp / "poll.log"),
        "FAKE_POLL_RC": str(rc),
    }
    return subprocess.run(
        ["/bin/sh", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60
    )


def _lstart(pid: int) -> str:
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip()


def _launched(tmp: Path) -> list[str]:
    p = tmp / "poll.log"
    return p.read_text().splitlines() if p.exists() else []


@pytest.mark.parametrize("plan", ["early start at 23:50Z", "no_games none", "done x", "over x"])
def test_nothing_runs_when_not_due(tmp_path: Path, plan: str) -> None:
    assert _run(tmp_path, plan).returncode == 0
    assert _launched(tmp_path) == []
    hb = json.loads((tmp_path / "root/data/ops/heartbeat/kalshi-candles-launch.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"] is not None


def test_start_runs_the_poller_once_and_releases_the_lock(tmp_path: Path) -> None:
    assert _run(tmp_path, "start first tip").returncode == 0
    (line,) = _launched(tmp_path)
    assert "nba.kalshi.poll15 run --date 2026-11-09" in line
    assert not (tmp_path / "root/data/ops/kalshi_candles_lock").exists()


def test_a_live_lock_owner_is_never_stolen(tmp_path: Path) -> None:
    lock = tmp_path / "root/data/ops/kalshi_candles_lock"
    lock.mkdir(parents=True)
    pid = os.getpid()  # a live pid: this test process, with its real start time
    (lock / "pid").write_text(str(pid))
    (lock / "start").write_text(_lstart(pid))
    assert _run(tmp_path, "start first tip").returncode == 0
    assert _launched(tmp_path) == []
    assert lock.exists()


def test_a_recycled_pid_with_another_start_time_is_a_stale_lock(tmp_path: Path) -> None:
    """Review F13: `kill -0` alone kept the lock forever when the pid belonged to someone else."""
    lock = tmp_path / "root/data/ops/kalshi_candles_lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text(str(os.getpid()))  # alive, but not the process that took the lock
    (lock / "start").write_text("Mon Jan  1 00:00:00 2001")
    assert _run(tmp_path, "start first tip").returncode == 0
    assert len(_launched(tmp_path)) == 1
    assert not lock.exists()


def test_slate_date_is_the_et_date_of_now_minus_six_hours() -> None:
    """Review F7: a poller that crashed at 00:30 ET must be restarted for the day it belongs to."""
    code = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    assert "TZ=America/New_York date -v-6H +%Y-%m-%d" in code


def test_a_dead_lock_owner_is_cleared(tmp_path: Path) -> None:
    lock = tmp_path / "root/data/ops/kalshi_candles_lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999999")
    assert _run(tmp_path, "start first tip").returncode == 0
    assert len(_launched(tmp_path)) == 1


def test_a_failed_poller_and_an_unknown_schedule_both_alert(tmp_path: Path) -> None:
    assert _run(tmp_path, "start x", rc=2).returncode == 0
    alerts = (tmp_path / "root/data/ops/ALERTS.md").read_text()
    assert "[kalshi-candles] poller for 2026-11-09 exited rc=2" in alerts
    hb = json.loads((tmp_path / "root/data/ops/heartbeat/kalshi-candles-launch.json").read_text())
    assert hb["rc"] == 2
    other = tmp_path / "b"
    other.mkdir()
    assert _run(other, "unknown FileNotFoundError").returncode == 0
    assert _launched(other) == []  # an unknown slate is not guessed
    assert "plan unknown" in (other / "root/data/ops/ALERTS.md").read_text()


def test_launcher_takes_no_shared_lock_and_never_opens_the_db() -> None:
    code = "\n".join(
        ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")
    )
    assert "data/ops/lock" not in code and "heavy.lock" not in code and "nba.duckdb" not in code
    assert "LOCK=$OPS/kalshi_candles_lock" in code


def test_plist_is_valid_launches_through_uv_and_is_not_installed_by_the_repo() -> None:
    p = plistlib.loads((REPO / "ops/launchd/local.nba.kalshi-candles.plist").read_bytes())
    assert p["Label"] == "local.nba.kalshi-candles"
    args = p["ProgramArguments"]
    assert args[0].endswith("/uv") and args[-1].endswith("ops/nba_kalshi_candles.sh")
    assert "sh" in args and args[0] != "/bin/sh"
    assert p["StartInterval"] == 600 and p["RunAtLoad"] is True
    install = (REPO / "ops" / "install_launchd.sh").read_text()
    assert "kalshi-candles" not in install  # installed by hand after the merge, not by the script
