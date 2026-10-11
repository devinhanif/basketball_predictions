"""Flag-off no-op through the CLI and the lineups-tick hook; tick driver; real config file."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from nba.shadow_llm.__main__ import main
from nba.shadow_llm.config import CONFIG_PATH, ROOT, load_config
from nba.shadow_llm.provider import StubProvider
from nba.shadow_llm.run import due_games, run
from tests.shadow_llm.conftest import GAME, NOW, TIP, World

REPO = Path(__file__).resolve().parents[2]


def test_repo_config_loads_with_cap_unset_and_rule_constants() -> None:
    cfg = load_config(CONFIG_PATH)
    assert cfg.monthly_cap_usd is None  # real provider refused until Devin sets MONTHLY_CAP_USD
    assert (cfg.max_tokens, cfg.nightly_token_ceiling, cfg.monthly_token_backstop) == (
        3000, 150_000, 2_500_000,
    )  # fmt: skip
    assert (cfg.planning_tokens_per_game, cfg.input_token_cap) == (8500, 8000)
    assert cfg.model_id == "claude-fable-5-1" and cfg.fallback_model_id == "claude-opus-5-5"


def test_cli_flag_off_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NBA_LLM_SHADOW", raising=False)
    ghost = tmp_path / "never_created.duckdb"
    before = {p for p in ROOT.rglob("llm_shadow*") if ".venv" not in p.parts}
    rc = main(["run", "--date", "2026-10-28", "--db-path", str(ghost), "--provider", "anthropic"])
    assert rc == 0 and "disabled" in capsys.readouterr().out
    assert not ghost.exists()
    assert {p for p in ROOT.rglob("llm_shadow*") if ".venv" not in p.parts} == before


def test_cli_subprocess_flag_off_exit_zero(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("NBA_LLM_SHADOW", "ANTHROPIC_API_KEY")}
    res = subprocess.run(
        [sys.executable, "-m", "nba.shadow_llm", "run", "--date", "2026-10-28"],
        env=env, cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
    )  # fmt: skip
    assert res.returncode == 0 and "disabled" in res.stdout


def test_run_flag_off_does_not_touch_anything(world: World) -> None:
    s = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths, environ={})
    assert s.rc == 0 and s.results == [] and not world.paths["shadow"].exists()
    assert not world.cfg.lock_dir.exists()


def test_stub_refuses_to_write_to_default_paths_without_permission(world: World) -> None:
    s = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths,
            environ={"NBA_LLM_SHADOW": "1"}, now=NOW)  # fmt: skip
    assert s.rc == 2 and not world.paths["shadow"].exists()


def test_tick_driver_window_and_idempotence(world: World) -> None:
    env = {"NBA_LLM_SHADOW": "1"}
    # outside the [60, 70] window: nothing due, nothing sent
    early = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths,
                allow_stub_write=True, environ=env, now=TIP - timedelta(minutes=120))  # fmt: skip
    assert early.results == [] and early.rc == 0
    soon = NOW + timedelta(seconds=9)
    s = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths,
            allow_stub_write=True, environ=env, now=NOW, clock=lambda: soon)  # fmt: skip
    assert [r.status for r in s.results] == ["ok"] and s.results[0].rows_written == 4
    again = run(NOW.date(), provider=StubProvider(), cfg=world.cfg, paths=world.paths,
                allow_stub_write=True, environ=env, now=NOW, clock=lambda: NOW)  # fmt: skip
    assert again.results == []  # already has llm_shadow rows
    assert not world.cfg.lock_dir.exists()  # lock released


def test_due_games_window(world: World) -> None:
    import duckdb

    con = duckdb.connect(str(world.paths["forward"]), read_only=True)
    assert due_games(con, NOW.date(), NOW, (60, 70)) == [(GAME, TIP)]
    assert due_games(con, NOW.date(), TIP - timedelta(minutes=59), (60, 70)) == []
    assert due_games(con, NOW.date(), TIP - timedelta(minutes=71), (60, 70)) == []
    con.close()


def _tick(
    tmp: Path, *, flag: str | None, dotenv: str | None = None, rc: int = 0
) -> tuple[str, str]:
    tmp.mkdir(parents=True, exist_ok=True)
    root = tmp / "root"
    (root / "data" / "ops").mkdir(parents=True, exist_ok=True)
    if dotenv is not None:
        (root / ".env").write_text(dotenv)
    uv = tmp / "fake_uv"
    uv.write_text(
        '#!/bin/sh\necho "$*" >> "$FAKE_UV_LOG"\n'
        f'case "$*" in *"nba.shadow_llm"*) exit {rc};; *"nba.lineups due"*) exit 1;; esac\nexit 0\n'
    )
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    env = {k: v for k, v in os.environ.items() if k not in ("NBA_LLM_SHADOW", "NBA_CLOSE_CAPTURE")}
    env.update(NBA_ROOT=str(root), NBA_UV=str(uv), FAKE_UV_LOG=str(tmp / "uv.log"),
               NBA_TEST_HOUR_ET="19")  # fmt: skip
    if flag is not None:
        env["NBA_LLM_SHADOW"] = flag
    res = subprocess.run(["sh", str(REPO / "ops" / "nba_lineups.sh")], env=env,
                         capture_output=True, text=True, timeout=60, check=False)  # fmt: skip
    assert res.returncode == 0, res.stderr
    log = tmp / "uv.log"
    alerts = root / "data" / "ops" / "ALERTS.md"
    return (log.read_text() if log.exists() else ""), (
        alerts.read_text() if alerts.exists() else ""
    )


def test_hook_off_by_default_runs_nothing(tmp_path: Path) -> None:
    log, alerts = _tick(tmp_path, flag=None)
    assert "shadow_llm" not in log and "llm_shadow" not in alerts


def test_hook_off_when_flag_is_not_exactly_one(tmp_path: Path) -> None:
    log, _ = _tick(tmp_path, flag="0", dotenv="NBA_LLM_SHADOW=0\n")
    assert "shadow_llm" not in log


def test_hook_env_and_dotenv_enable_with_env_file(tmp_path: Path) -> None:
    log, _ = _tick(tmp_path / "a", flag="1", dotenv="ANTHROPIC_API_KEY=x\n")
    assert (
        "nba.shadow_llm run" in log and "--provider anthropic" in log and "--env-file .env" in log
    )
    log2, _ = _tick(tmp_path / "b", flag=None, dotenv="NBA_LLM_SHADOW=1\n")
    assert log2.count("nba.shadow_llm run") == 1


@pytest.mark.parametrize("rc,alert", [(0, False), (4, False), (75, False), (5, True), (1, True)])
def test_hook_alerts_only_on_real_failures(tmp_path: Path, rc: int, alert: bool) -> None:
    _, alerts = _tick(tmp_path, flag="1", rc=rc)
    assert ("llm_shadow rc=" in alerts) is alert
