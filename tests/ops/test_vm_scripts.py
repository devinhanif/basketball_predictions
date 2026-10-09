"""Sanity tests for the ops shell scripts (syntax, env-overridable paths, cron block)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = sorted(str(p) for p in (REPO / "ops").rglob("*.sh"))
needs_lsof = pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof not installed")


def _fake_uv(tmp: Path) -> Path:
    """Stand-in for uv: records argv and succeeds, except `nba.lineups due` (none due)."""
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        'case "$*" in *"nba.lineups due"*) exit 1;; esac\nexit 0\n'
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    return uv


def _run(script: str, args: list[str], tmp: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp / "root"
    root.mkdir()
    log = tmp / "uv_calls.log"
    env = {
        **os.environ,
        "NBA_ROOT": str(root),
        "NBA_UV": str(_fake_uv(tmp)),
        "FAKE_UV_LOG": str(log),
    }
    res = subprocess.run(
        ["sh", str(REPO / "ops" / script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return res, log


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_parse(script: str) -> None:
    assert subprocess.run(["sh", "-n", script], check=False).returncode == 0


def test_no_hardcoded_mac_values_without_override() -> None:
    for name in ("nba_daily.sh", "nba_lineups.sh"):
        text = (REPO / "ops" / name).read_text()
        assert "ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}" in text
        assert "UV=${NBA_UV:-/opt/homebrew/bin/uv}" in text


@needs_lsof
def test_daily_pretip_uses_env_root_and_uv(tmp_path: Path) -> None:
    res, log = _run("nba_daily.sh", ["pretip"], tmp_path)
    assert res.returncode == 0, res.stderr
    calls = log.read_text()
    assert "run python -m nba.daily run --date" in calls
    assert "run python -m nba.parlay evaluate" in calls
    assert list((tmp_path / "root" / "data" / "ops").glob("pretip_*.log"))


@needs_lsof
def test_daily_bad_mode_exits_64(tmp_path: Path) -> None:
    res, _ = _run("nba_daily.sh", ["bogus"], tmp_path)
    assert res.returncode == 64


def test_lineups_tick_respects_quiet_hours(tmp_path: Path) -> None:
    res, log = _run("nba_lineups.sh", [], tmp_path)
    assert res.returncode == 0, res.stderr
    hour = datetime.now(ZoneInfo("America/New_York")).hour
    if 3 <= hour < 9:
        assert not log.exists()
    else:
        assert "nba.lineups poll" in log.read_text()


def test_cron_block_matches_launchd_schedule() -> None:
    res = subprocess.run(
        ["sh", str(REPO / "ops/vm/install_cron.sh"), "--print"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "NBA_ROOT": "/srv/r", "NBA_UV": "/srv/uv"},
    )
    out = res.stdout
    assert "30 9-21 * * * cd /srv/r && sh ops/nba_daily.sh pretip" in out
    assert "0 8 * * * cd /srv/r && sh ops/nba_daily.sh morning" in out
    assert "*/5 * * * * cd /srv/r && sh ops/nba_lineups.sh" in out
    assert "*/15 * * * *" in out and "nba.kalshi snapshot" in out
    assert "run_ingest_queue" not in out
    withq = subprocess.run(
        ["sh", str(REPO / "ops/vm/install_cron.sh"), "--print", "--with-queue"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "@reboot" in withq and "run_ingest_queue" in withq


def test_sync_requires_host() -> None:
    env = {k: v for k, v in os.environ.items() if k != "NBA_VM_HOST"}
    res = subprocess.run(
        ["sh", str(REPO / "ops/vm/sync_from_mac.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode != 0 and "NBA_VM_HOST" in res.stderr
