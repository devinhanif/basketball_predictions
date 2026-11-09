# ruff: noqa: E501
"""ops/nba_pulls.sh and `python -m nba.pulls tick` on a fake uv and a tmp root: gate, lock, heartbeat, ALERTS.
No network, no real data directory, no launchd."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "ops" / "nba_pulls.sh"


def _fake_uv(tmp: Path) -> Path:
    uv = tmp / "uv"
    uv.write_text('#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\nexit "${FAKE_UV_RC:-0}"\n')
    uv.chmod(0o755)
    return uv


def _run(
    tmp: Path, *, gate: str | None, rc: int = 0, env_file: str | None = None
) -> subprocess.CompletedProcess[str]:
    root = tmp / "root"
    root.mkdir(exist_ok=True)
    if env_file is not None:
        (root / ".env").write_text(env_file)
    env = {
        k: v for k, v in os.environ.items() if k not in ("NBA_CLOSE_CAPTURE", "THE_ODDS_API_KEY")
    }
    env.update(
        NBA_ROOT=str(root),
        NBA_UV=str(_fake_uv(tmp)),
        FAKE_UV_LOG=str(tmp / "uv.log"),
        FAKE_UV_RC=str(rc),
    )
    if gate is not None:
        env["NBA_CLOSE_CAPTURE"] = gate
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), "pulls"], env=env, capture_output=True, text=True, timeout=60
    )


def _calls(tmp: Path) -> list[str]:
    p = tmp / "uv.log"
    return p.read_text().splitlines() if p.exists() else []


def _hb(tmp: Path) -> dict[str, object]:
    return json.loads((tmp / "root/data/ops/heartbeat/pulls.json").read_text())


def test_gate_off_runs_nothing_exits_zero_and_beats(tmp_path: Path) -> None:
    assert _run(tmp_path, gate=None).returncode == 0
    assert _calls(tmp_path) == []
    hb = _hb(tmp_path)
    assert hb["rc"] == 0 and hb["end_ts"] is not None and "disabled" in str(hb["detail"])
    assert not (tmp_path / "root/data/ops/pulls_lock").exists()


def test_gate_on_runs_one_tick_with_the_env_file_and_releases_the_lock(tmp_path: Path) -> None:
    assert _run(tmp_path, gate="1", env_file="THE_ODDS_API_KEY=x\n").returncode == 0
    (line,) = _calls(tmp_path)
    assert "run --no-sync --env-file .env python -m nba.pulls tick" in line
    assert _hb(tmp_path)["rc"] == 0
    assert not (tmp_path / "root/data/ops/pulls_lock").exists()
    assert not (tmp_path / "root/data/ops/ALERTS.md").exists()


def test_gate_can_come_from_the_dotenv_line(tmp_path: Path) -> None:
    assert _run(tmp_path, gate=None, env_file="NBA_CLOSE_CAPTURE=1\n").returncode == 0
    assert len(_calls(tmp_path)) == 1


def test_a_failing_tick_alerts_and_the_script_still_exits_zero(tmp_path: Path) -> None:
    assert _run(tmp_path, gate="1", rc=4).returncode == 0
    alerts = (tmp_path / "root/data/ops/ALERTS.md").read_text()
    assert "[pulls] pulls tick rc=4" in alerts
    assert _hb(tmp_path)["rc"] == 4


def test_a_live_owner_keeps_the_lock_and_a_dead_one_is_cleared(tmp_path: Path) -> None:
    lock = tmp_path / "root/data/ops/pulls_lock"
    lock.mkdir(parents=True)
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        (lock / "pid").write_text(str(sleeper.pid))
        assert _run(tmp_path, gate="1").returncode == 0
        assert _calls(tmp_path) == [] and lock.exists()
    finally:
        sleeper.kill()
        sleeper.wait()
    (lock / "pid").write_text("999999")
    assert _run(tmp_path, gate="1").returncode == 0
    assert len(_calls(tmp_path)) == 1


def test_the_script_never_takes_the_heavy_lock_or_opens_the_db() -> None:
    text = SCRIPT.read_text()
    code = "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("#"))
    assert "heavy.lock" not in code and "nba.duckdb" not in code and "ops/lock" not in code


def test_python_tick_with_the_gate_off_is_a_noop_and_exits_zero(tmp_path: Path) -> None:
    env = {
        k: v for k, v in os.environ.items() if k not in ("NBA_CLOSE_CAPTURE", "THE_ODDS_API_KEY")
    }
    out = subprocess.run(
        [sys.executable, "-m", "nba.pulls", "tick"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0 and "no-op" in out.stdout
