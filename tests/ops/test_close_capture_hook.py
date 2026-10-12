"""The closing-line hook in ops/nba_lineups.sh: off by default, flag from env or .env, failures
alert without touching the poll heartbeat. A fake uv records its argv; nothing real runs."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _tick(tmp: Path, *, flag: str | None, dotenv: str | None = None, odds_rc: int = 0) -> str:
    root = tmp / "root"
    (root / "data" / "ops").mkdir(parents=True, exist_ok=True)
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        f'case "$*" in *"nba.odds capture"*) exit {odds_rc};; *"nba.lineups due"*) exit 1;; esac\n'
        "exit 0\n"
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items() if k != "NBA_CLOSE_CAPTURE"}
    env.update(
        NBA_ROOT=str(root), NBA_UV=str(uv), FAKE_UV_LOG=str(tmp / "uv.log"), NBA_TEST_HOUR_ET="19"
    )
    if flag is not None:
        env["NBA_CLOSE_CAPTURE"] = flag
    res = subprocess.run(
        ["sh", str(REPO / "ops" / "nba_lineups.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    log = tmp / "uv.log"
    return log.read_text() if log.exists() else ""


def test_off_by_default_never_calls_odds(tmp_path: Path) -> None:
    assert "nba.odds" not in _tick(tmp_path, flag=None)


def test_off_when_flag_is_not_exactly_one(tmp_path: Path) -> None:
    assert "nba.odds" not in _tick(tmp_path, flag="0", dotenv="NBA_CLOSE_CAPTURE=0\n")


def test_env_flag_runs_one_all_capture_with_env_file(tmp_path: Path) -> None:
    log = _tick(tmp_path, flag="1", dotenv="THE_ODDS_API_KEY=x\n").splitlines()
    odds = [ln for ln in log if "nba.odds capture" in ln]
    # one call walks the whole configs/odds_live.yaml schedule, close first (f014b9c)
    assert len(odds) == 1 and "--kind all" in odds[0] and "--date " in odds[0]
    assert "--env-file .env" in odds[0]


def test_flag_in_dotenv_enables(tmp_path: Path) -> None:
    log = _tick(tmp_path, flag=None, dotenv="NBA_CLOSE_CAPTURE=1\n")
    assert log.count("nba.odds capture --kind all") == 1


def test_failure_alerts_but_poll_heartbeat_stays_clean(tmp_path: Path) -> None:
    _tick(tmp_path, flag="1", odds_rc=4)
    ops = tmp_path / "root" / "data" / "ops"
    alerts = (ops / "ALERTS.md").read_text()
    assert alerts.count("[lineups] odds capture all rc=4") == 1
    hb = json.loads((ops / "heartbeat" / "lineups.json").read_text())
    assert hb["rc"] == 0 and hb["end_ts"]
