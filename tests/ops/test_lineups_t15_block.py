"""ops/nba_lineups.sh: the T-15 block runs after the T-30 block on the same tick (fall-through)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _env(
    tmp: Path, *, due30: int = 0, due15: int = 0, t30_rc: int = 0, t15_rc: int = 0
) -> dict[str, str]:
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        'case "$*" in\n'
        f"  *lineups\\ due\\ --lead-minutes\\ 15*) exit {due15};;\n"
        f"  *lineups\\ due*) exit {due30};;\n"
        f"  *run-t30*) exit {t30_rc};;\n"
        '  *run-t15*) if [ -d "$NBA_ROOT/data/ops/lineups_lock" ]; then '
        'echo POLL_LOCK_HELD >> "$FAKE_UV_LOG"; fi; '
        'echo "WAIT=$NBA_DB_LOCK_WAIT_S ROSTER_ARGS=$*" >> "$FAKE_UV_LOG"; '
        f"exit {t15_rc};;\n"
        "esac\nexit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return {
        **os.environ,
        "NBA_ROOT": str(tmp / "root"),
        "NBA_UV": str(uv),
        "FAKE_UV_LOG": str(tmp / "uv.log"),
        "NBA_TEST_HOUR_ET": "19",
    }


def _tick(tmp: Path, **kw: int) -> subprocess.CompletedProcess[str]:
    (tmp / "root" / "data" / "ops").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["sh", str(REPO / "ops" / "nba_lineups.sh")],
        env=_env(tmp, **kw),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _hb(tmp: Path) -> Path:
    return tmp / "root" / "data" / "ops" / "heartbeat"


def test_both_arms_run_on_one_tick_t30_first(tmp_path: Path) -> None:
    assert _tick(tmp_path).returncode == 0
    log = (tmp_path / "uv.log").read_text()
    assert "lineups due --lead-minutes 15" in log
    assert log.index("run-t30") < log.index("run-t15")
    assert "POLL_LOCK_HELD" not in log and "WAIT=240" in log
    ops = tmp_path / "root" / "data" / "ops"
    assert not (ops / "lineups_t30_lock").exists() and not (ops / "lineups_t15_lock").exists()


def test_t30_lock_held_falls_through_to_t15(tmp_path: Path) -> None:
    ops = tmp_path / "root" / "data" / "ops"
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (ops / "lineups_t30_lock").mkdir(parents=True)
        (ops / "lineups_t30_lock" / "pid").write_text(str(holder.pid))
        assert _tick(tmp_path).returncode == 0
        log = (tmp_path / "uv.log").read_text()
        assert "run-t30" not in log and "run-t15" in log  # no early exit on the held T-30 lock
        assert (ops / "lineups_t30_lock").exists()  # not stolen
    finally:
        holder.kill()


def test_t30_not_due_still_runs_t15_and_t15_not_due_skips_it(tmp_path: Path) -> None:
    assert _tick(tmp_path, due30=1).returncode == 0
    log = (tmp_path / "uv.log").read_text()
    assert "run-t30" not in log and "run-t15" in log
    (tmp_path / "uv.log").unlink()
    assert _tick(tmp_path, due15=1).returncode == 0
    log = (tmp_path / "uv.log").read_text()
    assert "run-t30" in log and "run-t15" not in log


def test_t15_rc75_writes_its_own_skip_record_and_success_clears_it(tmp_path: Path) -> None:
    for expected in (1, 2):
        assert _tick(tmp_path, t15_rc=75).returncode == 0
        skip = json.loads((_hb(tmp_path) / "lineups_t15.skip.json").read_text())
        assert skip["status"] == "skipped" and skip["consecutive_skips"] == expected
    assert not (_hb(tmp_path) / "lineups_t30.skip.json").exists()
    hb = json.loads((_hb(tmp_path) / "lineups.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"]
    assert _tick(tmp_path).returncode == 0
    assert not (_hb(tmp_path) / "lineups_t15.skip.json").exists()


def test_t15_failure_alerts_and_does_not_block_the_next_tick(tmp_path: Path) -> None:
    assert _tick(tmp_path, t15_rc=3).returncode == 0
    alerts = (tmp_path / "root" / "data" / "ops" / "ALERTS.md").read_text()
    assert "run-t15 failed rc=3" in alerts
    assert not (tmp_path / "root" / "data" / "ops" / "lineups_t15_lock").exists()


def test_watchdog_alerts_on_repeated_t15_deferral(tmp_path: Path) -> None:
    from nba.ops.watchdog import SKIP_ONLY
    from tests.ops.test_watchdog import _all_fresh, _eval

    hb = tmp_path / "hb"
    _all_fresh(hb)
    n = SKIP_ONLY["lineups_t15"]
    (hb / "lineups_t15.skip.json").write_text(
        json.dumps({"status": "skipped", "consecutive_skips": n - 1})
    )
    assert not [a for a in _eval(tmp_path) if a.key == "lineups_t15:skipped"]
    (hb / "lineups_t15.skip.json").write_text(
        json.dumps({"status": "skipped", "consecutive_skips": n})
    )
    assert [a for a in _eval(tmp_path) if a.key == "lineups_t15:skipped"]
