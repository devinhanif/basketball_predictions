"""ops/nba_daily.sh: skip heartbeat (M6), pid-aware stale lock, informational rc 4."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _env(tmp: Path, daily_run_rc: int = 0) -> dict[str, str]:
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        f'case "$*" in *"nba.daily run "*) exit {daily_run_rc};; esac\nexit 0\n'
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(uv),
        "FAKE_UV_LOG": str(tmp / "uv.log"),
    }


def _pretip(tmp: Path, **kw: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(REPO / "ops" / "nba_daily.sh"), "pretip"],
        env=_env(tmp, **kw),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _ops(tmp: Path) -> Path:
    ops = tmp / "root" / "data" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    return ops


def test_skipped_run_writes_skip_heartbeat_and_keeps_real_heartbeat(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (ops / "lock").mkdir()
        (ops / "lock" / "pid").write_text(str(holder.pid))
        hb = ops / "heartbeat"
        hb.mkdir()
        real = '{"job": "daily-pretip", "start_ts": "T0", "end_ts": "T1", "rc": 0}'
        (hb / "daily-pretip.json").write_text(real)
        for expected in (1, 2):
            res = _pretip(tmp_path)
            assert res.returncode == 0, res.stderr
            skip = json.loads((hb / "daily-pretip.skip.json").read_text())
            assert skip["status"] == "skipped" and skip["consecutive_skips"] == expected
            assert (hb / "daily-pretip.json").read_text() == real  # untouched
        assert not (tmp_path / "uv.log").exists()  # no step ran
    finally:
        holder.kill()


def test_dead_owner_pid_clears_lock_immediately_and_resets_skip_count(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    dead = subprocess.Popen(["true"])
    dead.wait()
    (ops / "lock").mkdir()
    (ops / "lock" / "pid").write_text(str(dead.pid))  # fresh mtime: age rule would not fire
    (ops / "heartbeat").mkdir()
    (ops / "heartbeat" / "daily-pretip.skip.json").write_text('{"consecutive_skips": 5}')
    res = _pretip(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "run --no-sync python -m nba.daily run" in (tmp_path / "uv.log").read_text()
    assert not (ops / "heartbeat" / "daily-pretip.skip.json").exists()
    assert not (ops / "lock").exists()  # released on exit
    hb = json.loads((ops / "heartbeat" / "daily-pretip.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"]
    assert "cleared stale lock (owner pid" in next(ops.glob("pretip_*.log")).read_text()


def test_old_lock_without_live_check_still_clears_after_3h(tmp_path: Path) -> None:
    ops = _ops(tmp_path)
    holder = subprocess.Popen(["sleep", "30"])  # alive pid, but the lock is ancient
    try:
        (ops / "lock").mkdir()
        (ops / "lock" / "pid").write_text(str(holder.pid))
        old = time.time() - 4 * 3600
        os.utime(ops / "lock", (old, old))
        assert _pretip(tmp_path).returncode == 0
        assert (tmp_path / "uv.log").exists()
    finally:
        holder.kill()


def test_rc4_is_informational_but_rc2_and_rc5_alert(tmp_path: Path) -> None:
    for rc, alerts in ((4, False), (2, True), (5, True)):
        d = tmp_path / f"rc{rc}"
        (d / "root").mkdir(parents=True)
        res = _pretip(d, daily_run_rc=rc)
        assert res.returncode == 0
        alert_file = d / "root" / "data" / "ops" / "ALERTS.md"
        assert alert_file.exists() == alerts, rc
        hb = json.loads(
            (d / "root" / "data" / "ops" / "heartbeat" / "daily-pretip.json").read_text()
        )
        assert hb["rc"] == (rc if alerts else 0)
