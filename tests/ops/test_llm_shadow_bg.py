"""ops/nba_lineups.sh launches the LLM shadow step DETACHED (ops/llm_shadow_bg.sh): a slow shadow
run never delays the tick or its T-30 / T-15 blocks, runs in its own process group, holds its own
lock (pid inside, released on exit), never runs twice at once, clears only its own stale lock, and
logs to data/ops/llm_shadow_YYYYMMDD.log. No network, no real uv: a fake uv records its calls."""

from __future__ import annotations

import os
import stat
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TAG = "nba_llm_shadow_bg"


def _fake_uv(tmp: Path, shadow_sleep: float, shadow_rc: int = 0) -> Path:
    uv = tmp / "fake_uv"
    uv.write_text(
        "#!/bin/sh\n"
        'echo "$(date +%s.%N 2>/dev/null) $*" >> "$FAKE_UV_LOG"\n'
        'case "$*" in\n'
        "  *nba.shadow_llm*) "
        'echo "SHADOW_PGID=$(ps -o pgid= -p $$ | tr -d " ")" >> "$FAKE_UV_LOG"; '
        f'sleep {shadow_sleep}; echo SHADOW_DONE >> "$FAKE_UV_LOG"; exit {shadow_rc};;\n'
        '  *"lineups due --lead-minutes 15"*) exit 1;;\n'
        '  *"lineups due"*) exit 0;;\n'
        '  *run-t30*) echo "T30_RAN" >> "$FAKE_UV_LOG"; exit 0;;\n'
        "esac\nexit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return uv


def _env(tmp: Path, uv: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NBA_LLM_SHADOW", "NBA_CLOSE_CAPTURE")}
    env.update(NBA_ROOT=str(tmp / "root"), NBA_UV=str(uv), FAKE_UV_LOG=str(tmp / "uv.log"),
               NBA_TEST_HOUR_ET="19", NBA_LLM_SHADOW="1")  # fmt: skip
    return env


def _ops(tmp: Path) -> Path:
    p = tmp / "root" / "data" / "ops"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _uvlog(tmp: Path) -> str:
    p = tmp / "uv.log"
    return p.read_text() if p.exists() else ""


def _shadow_log(tmp: Path) -> str:
    return "".join(p.read_text() for p in _ops(tmp).glob("llm_shadow_*.log"))


def _wait(cond: object, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():  # type: ignore[operator]
            return
        time.sleep(0.1)
    raise AssertionError("condition not reached")


def _tick(tmp: Path, uv: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    _ops(tmp)
    t0 = time.monotonic()
    res = subprocess.run(["sh", str(REPO / "ops" / "nba_lineups.sh")], env=_env(tmp, uv),
                         capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    return res, time.monotonic() - t0


def test_slow_shadow_does_not_block_the_tick_or_t30(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=6)
    res, wall = _tick(tmp_path, uv)
    assert res.returncode == 0, res.stderr
    assert wall < 5, wall  # the 6 s shadow run did not hold the tick
    log = _uvlog(tmp_path)
    assert "T30_RAN" in log and "SHADOW_DONE" not in log  # T-30 ran while the shadow still runs
    ops = _ops(tmp_path)
    lock = ops / "llm_shadow_bg.lock"
    _wait(lambda: (lock / "pid").exists())
    assert (lock / "owner").read_text().strip() == TAG
    lines = "".join(p.read_text() for p in ops.glob("lineups_*.log"))
    assert "llm_shadow launched in the background" in lines
    # own process group: not the tick's
    _wait(lambda: "SHADOW_PGID=" in _uvlog(tmp_path))
    pgid = int(_uvlog(tmp_path).split("SHADOW_PGID=")[1].split()[0])
    assert pgid != os.getpgid(0)
    _wait(lambda: "llm_shadow rc=0" in _shadow_log(tmp_path))
    _wait(lambda: not lock.exists())  # released on exit
    hb = (ops / "heartbeat" / "llm_shadow.json").read_text()
    assert '"rc": 0' in hb and '"end_ts": "' in hb


def test_second_tick_never_starts_a_second_run(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=5)
    _tick(tmp_path, uv)
    lock = _ops(tmp_path) / "llm_shadow_bg.lock"
    _wait(lambda: (lock / "pid").exists())
    res, _ = _tick(tmp_path, uv)
    assert res.returncode == 0
    lines = "".join(p.read_text() for p in _ops(tmp_path).glob("lineups_*.log"))
    assert "still running in the background" in lines
    _wait(lambda: "llm_shadow rc=0" in _shadow_log(tmp_path))
    _wait(lambda: not lock.exists())
    assert _uvlog(tmp_path).count("nba.shadow_llm run") == 1


def test_background_script_refuses_a_live_lock_even_when_started_directly(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=0)
    lock = _ops(tmp_path) / "llm_shadow_bg.lock"
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock.mkdir()
        (lock / "pid").write_text(str(holder.pid))
        (lock / "owner").write_text(TAG)
        res = subprocess.run(["sh", str(REPO / "ops" / "llm_shadow_bg.sh"), "2026-10-28"],
                             env=_env(tmp_path, uv), capture_output=True, text=True, timeout=30,
                             check=False)  # fmt: skip
        assert res.returncode == 0 and "nba.shadow_llm" not in _uvlog(tmp_path)
        assert "already running" in _shadow_log(tmp_path)
        assert (lock / "pid").read_text() == str(holder.pid)  # untouched
    finally:
        holder.kill()


def test_stale_own_lock_is_cleared_but_a_foreign_lock_is_left_alone(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=0)
    lock = _ops(tmp_path) / "llm_shadow_bg.lock"
    dead = subprocess.Popen(["true"])
    dead.wait()
    env = _env(tmp_path, uv)
    script = str(REPO / "ops" / "llm_shadow_bg.sh")
    # foreign: dead pid but no owner tag -> left alone, nothing runs
    lock.mkdir()
    (lock / "pid").write_text(str(dead.pid))
    subprocess.run(["sh", script, "2026-10-28"], env=env, timeout=30, check=False)
    assert lock.exists() and "nba.shadow_llm" not in _uvlog(tmp_path)
    # ours and dead -> cleared, the run proceeds, the lock is released afterwards
    (lock / "owner").write_text(TAG)
    subprocess.run(["sh", script, "2026-10-28"], env=env, timeout=30, check=False)
    assert "nba.shadow_llm run --date 2026-10-28" in _uvlog(tmp_path)
    assert "cleared stale" in _shadow_log(tmp_path) and not lock.exists()


def test_failure_alerts_from_the_background_run(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=0, shadow_rc=5)
    _tick(tmp_path, uv)
    alerts = _ops(tmp_path) / "ALERTS.md"
    _wait(lambda: alerts.exists() and "llm_shadow rc=5" in alerts.read_text())
    _wait(lambda: not (_ops(tmp_path) / "llm_shadow_bg.lock").exists())


def test_flag_off_launches_nothing(tmp_path: Path) -> None:
    uv = _fake_uv(tmp_path, shadow_sleep=0)
    env = _env(tmp_path, uv)
    env.pop("NBA_LLM_SHADOW")
    _ops(tmp_path)
    subprocess.run(["sh", str(REPO / "ops" / "nba_lineups.sh")], env=env, timeout=60, check=False)
    time.sleep(0.5)
    assert "nba.shadow_llm" not in _uvlog(tmp_path)
    assert not list(_ops(tmp_path).glob("llm_shadow_*.log"))
