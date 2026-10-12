"""claude_code provider against a FAKE ``claude`` executable (no network, no subscription use):
success, schema violation, usage limit, timeout, odds-domain audit, tool-restriction flags, empty
cwd, after-tip refusal, no API fallback, auth preflight, audit-column migration."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba.shadow_llm import __main__ as cli
from nba.shadow_llm.calls import guard, open_shadow
from nba.shadow_llm.claude_code import (
    ALLOWED_TOOLS,
    DENIED_TOOLS,
    ClaudeCodeProvider,
    RunOutcome,
    build_command,
    check_auth,
    child_env,
    launchd_like_env,
    odds_domains_in_text,
    odds_hosts_in_urls,
    parse_stream,
    resolve_claude_bin,
    web_prompt_sha256,
)
from nba.shadow_llm.config import ShadowConfig, load_config
from nba.shadow_llm.provider import AnthropicProvider, prompt_sha256
from nba.shadow_llm.run import run
from nba.shadow_llm.runner import run_game
from tests.shadow_llm.conftest import GAME, NOW, TIP, World

SECRET = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKE123456"


def _ev(**kw: Any) -> str:
    return json.dumps(kw)


def stream(
    text: str,
    *,
    queries: tuple[str, ...] = (),
    result_urls: tuple[str, ...] = (),
    snippet: str = "",
    api_key_source: str = "none",
    tools: tuple[str, ...] = ("WebSearch",),
    extra: tuple[str, ...] = (),
    is_error: bool = False,
    model: str = "claude-opus-5-5",
) -> str:
    lines = [
        _ev(type="system", subtype="init", cwd="/tmp/nba_shadow_x", tools=list(tools),
            mcp_servers=[], model=model, apiKeySource=api_key_source, claude_code_version="9.9"),
    ]  # fmt: skip
    for i, q in enumerate(queries):
        lines.append(
            _ev(type="assistant", message={"model": model, "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "WebSearch", "input": {"query": q}}]})
        )  # fmt: skip
        res = [{"url": u} for u in result_urls]
        links = json.dumps([{"title": "x", **r} for r in res])
        lines.append(
            _ev(type="user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}",
                 "content": f"Links: {links}\n\n{snippet}"}]},
                tool_use_result={"query": q, "results": [{"content": res}]})
        )  # fmt: skip
    lines += list(extra)
    lines.append(
        _ev(type="assistant", message={"model": model, "content": [{"type": "text", "text": text}]})
    )
    lines.append(
        _ev(type="result", subtype="success", is_error=is_error, result=text, num_turns=2,
            modelUsage={model: {"inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 400,
                                "cacheCreationInputTokens": 500},
                        "claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 5}})
    )  # fmt: skip
    return "\n".join(lines) + "\n"


def good_answer(world: World) -> str:
    from nba.shadow_llm.payload import build_payload
    from nba.shadow_llm.provider import StubProvider

    payload = build_payload(GAME, now=NOW, paths=world.paths, cfg=world.cfg)
    return str(StubProvider().forecast(payload)["raw_text"])


def fake_claude(
    tmp: Path, stdout: str, *, rc: int = 0, sleep_s: float = 0.0, stderr: str = ""
) -> Path:
    """An executable that records argv / cwd / env / stdin to a .rec file and prints ``stdout``."""
    exe = tmp / "fake_claude"
    rec = tmp / "fake_claude.rec"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time\n"
        f"stdin = sys.stdin.read()\n"
        f"open({str(rec)!r}, 'w').write(json.dumps({{'argv': sys.argv, 'cwd': os.getcwd(), "
        "'env': dict(os.environ), 'stdin': stdin, 'ls': os.listdir('.')}))\n"
        f"time.sleep({sleep_s})\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({rc})\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


def recorded(tmp: Path) -> dict[str, Any]:
    return json.loads((tmp / "fake_claude.rec").read_text())  # type: ignore[no-any-return]


def make_provider(world: World, exe: Path, at: datetime, **env: str) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(
        world.cfg,
        claude_bin=str(exe),
        environ={"HOME": "/h", "USER": "u", "PATH": "/usr/bin", **env},
        clock=lambda: at,
    )  # fmt: skip


def go(world: World, prov: Any, at: datetime, **kw: Any) -> dict[str, Any]:
    return run_game(
        GAME, provider=prov, now=NOW, paths=world.paths, cfg=kw.pop("cfg", world.cfg),
        clock=lambda: at, sleep=lambda s: None, environ={}, **kw,
    )  # fmt: skip


def calls(
    world: World, cols: str = "status, provider, tokens_in, tokens_out"
) -> list[tuple[Any, ...]]:
    con = duckdb.connect(str(world.paths["shadow"]), read_only=True)
    try:
        return con.execute(f"SELECT {cols} FROM llm_shadow_calls ORDER BY requested_at").fetchall()
    finally:
        con.close()


def first_pred(world: World) -> dict[str, Any]:
    con = duckdb.connect(str(world.paths["forward"]), read_only=True)
    try:
        row = con.execute(
            "SELECT prediction FROM forward_predictions WHERE model_name='llm_shadow' LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    return dict(json.loads(row[0]))


AT = NOW + timedelta(seconds=40)


def test_success_flags_empty_cwd_env_and_audit_row(world: World) -> None:
    exe = fake_claude(
        world.tmp,
        stream(
            good_answer(world),
            queries=("Lakers injury report Oct 28",),
            result_urls=("https://www.nba.com/news/x", "https://www.espn.com/y"),
        ),
    )
    prov = make_provider(world, exe, AT, ANTHROPIC_API_KEY=SECRET, CLAUDE_CODE_SESSION_ID="s")
    out = go(world, prov, AT)
    assert out["status"] == "ok" and out["rows_written"] == 4
    rec = recorded(world.tmp)
    argv = rec["argv"]
    assert argv[1] == "-p" and "--output-format" in argv and "stream-json" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--tools") + 1] == "WebSearch"
    assert argv[argv.index("--allowedTools") + 1] == "WebSearch"
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    for t in ("Bash", "Read", "Write", "Edit", "WebFetch", "Glob", "Grep", "mcp__*"):
        assert t in denied
    assert not set(ALLOWED_TOOLS) & set(DENIED_TOOLS)
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert "--dangerously-skip-permissions" not in argv and "--bare" not in argv
    assert "<data>" in rec["stdin"] and "forecasts" in argv[argv.index("--system-prompt") + 1]
    # empty temp cwd: nothing visible, outside the repo, removed afterwards
    assert rec["ls"] == [] and "nba_shadow_" in rec["cwd"]
    assert not Path(rec["cwd"]).exists()
    assert not str(Path(rec["cwd"]).resolve()).startswith(str(Path(__file__).resolve().parents[2]))
    # subscription billing: the key and inherited CLAUDE_CODE_* never reach the child
    assert "ANTHROPIC_API_KEY" not in rec["env"] and "CLAUDE_CODE_SESSION_ID" not in rec["env"]
    row = calls(
        world,
        "status, provider, model_id, model_requested, n_searches, web_queries, web_urls, "
        "odds_domain_flag, odds_query_flag, api_key_source, tokens_in, tokens_out, wall_s",
    )[0]
    assert row[:3] == ("ok", "claude_code", "claude-opus-5-5") and row[4] == 1
    assert json.loads(row[5]) == ["Lakers injury report Oct 28"]
    assert json.loads(row[6]) == ["https://www.nba.com/news/x", "https://www.espn.com/y"]
    assert row[7] is False and row[8] is False and row[9] == "none"
    assert (row[10], row[11]) == (100 + 400 + 500 + 10, 50 + 5) and row[12] >= 0
    pred = first_pred(world)
    assert pred["provider"] == "claude_code" and pred["odds_domain_flag"] is False
    assert pred["n_searches"] == 1 and "-110" not in json.dumps(pred)


def test_odds_domain_flag_url_and_snippet_and_no_prices_stored(world: World) -> None:
    exe = fake_claude(
        world.tmp,
        stream(
            good_answer(world),
            queries=("Lakers props odds tonight", "Lakers injury news"),
            result_urls=("https://sportsbook.draftkings.com/event/1", "https://www.nba.com/a"),
            snippet="Tatum over 27.5 -115 via fanduel.com",
        ),
    )
    out = go(world, make_provider(world, exe, AT), AT)
    assert out["status"] == "ok"
    r = calls(world, "odds_domain_flag, odds_domains, odds_query_flag, n_searches, web_urls")[0]
    assert r[0] is True and r[2] is True and r[3] == 2
    assert {"draftkings.com", "fanduel.com"} <= set(json.loads(r[1]))
    all_text = json.dumps(
        duckdb.connect(str(world.paths["shadow"]), read_only=True)
        .execute("SELECT * FROM llm_shadow_calls").fetchall(), default=str
    )  # fmt: skip
    assert "-115" not in all_text and "27.5" not in all_text.replace("Tatum", "")  # no price copied
    pred = first_pred(world)
    assert pred["odds_domain_flag"] is True and pred["odds_query_flag"] is True


def test_odds_helpers() -> None:
    assert odds_hosts_in_urls(["https://sportsbook.fanduel.com/x", "https://www.nba.com"]) == [
        "fanduel.com"
    ]
    assert odds_hosts_in_urls(["https://notdraftkings.com/x"]) == []
    assert odds_domains_in_text("see Pinnacle.com and www.covers.com/odds") == [
        "covers.com", "pinnacle.com",
    ]  # fmt: skip
    assert odds_domains_in_text("the Pinnacle of the Caesars Palace") == []


def test_schema_violation_is_final_and_not_retried(world: World) -> None:
    exe = fake_claude(
        world.tmp, stream("```json\n" + good_answer(world) + "\n```\n\nSources: [x](y)")
    )
    out = go(world, make_provider(world, exe, AT), AT)
    assert out["status"] == "schema_violation" and out["rows_written"] == 0
    assert [c[0] for c in calls(world)] == ["schema_violation"]  # one attempt, no retry
    assert world.paths["forward"].exists()
    con = duckdb.connect(str(world.paths["forward"]), read_only=True)
    n = con.execute(
        "SELECT count(*) FROM forward_predictions WHERE model_name='llm_shadow'"
    ).fetchone()
    con.close()
    assert n is not None and n[0] == 0


def test_usage_limit_skips_night_alerts_and_never_falls_back_to_api(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("API provider must never be used as a fallback")

    monkeypatch.setattr(AnthropicProvider, "forecast", boom)
    limit = stream(
        "You've hit your usage limit. Resets at 5pm.",
        extra=(_ev(type="rate_limit_event", rate_limit_info={"status": "rejected"}),),
        is_error=True,
    )
    exe = fake_claude(world.tmp, limit, rc=1)
    prov = make_provider(world, exe, AT, ANTHROPIC_API_KEY=SECRET)
    env = {"NBA_LLM_SHADOW": "1", "ANTHROPIC_API_KEY": SECRET}
    s = run(NOW.date(), provider=prov, cfg=world.cfg, paths=world.paths, environ=env, now=NOW,
            clock=lambda: AT)  # fmt: skip
    assert [r.status for r in s.results] == ["usage_limit"] and s.rc == 4
    alerts = (world.cfg.lock_dir.parent / "ALERTS.md").read_text()
    assert "[llm_shadow]" in alerts and "usage_limit" in alerts and "No API fallback" in alerts
    assert SECRET not in alerts
    first = recorded(world.tmp)
    assert "ANTHROPIC_API_KEY" not in first["env"]
    # next tick, same night: skipped by the guard, claude is not even started
    (world.tmp / "fake_claude.rec").unlink()
    again = go(world, prov, AT + timedelta(minutes=5))
    assert again["status"] == "usage_limit_skip"
    assert not (world.tmp / "fake_claude.rec").exists()
    assert [c[0] for c in calls(world)] == ["usage_limit", "usage_limit_skip"]


def test_usage_limit_by_error_text_without_event(world: World) -> None:
    exe = fake_claude(
        world.tmp, stream("Claude AI usage limit reached|1760000000", is_error=True), rc=1
    )
    out = go(world, make_provider(world, exe, AT), AT)
    assert out["status"] == "usage_limit" and out["night_stop"] is True


def test_auth_failure_is_a_night_stop(world: World) -> None:
    exe = fake_claude(world.tmp, stream("Not logged in · Please run /login", is_error=True), rc=1)
    out = go(world, make_provider(world, exe, AT), AT)
    assert out["status"] == "auth_failed" and out["night_stop"] is True


def test_wall_cap_is_a_logged_skip_and_kills_the_child(world: World) -> None:
    exe = fake_claude(world.tmp, stream(good_answer(world)), sleep_s=30)
    cfg = replace(world.cfg, claude_code_wall_s=2.0)
    prov = ClaudeCodeProvider(
        cfg, claude_bin=str(exe), environ={"PATH": "/usr/bin"}, clock=lambda: AT
    )
    t0 = time.monotonic()
    out = go(world, prov, AT, cfg=cfg)
    assert out["status"] == "wall_cap_skip" and time.monotonic() - t0 < 15
    assert out["rows_written"] == 0 and "night_stop" in out and out["night_stop"] is False
    c = calls(world, "status, wall_s")[0]
    assert c[0] == "wall_cap_skip" and 1.5 <= c[1] < 10


def test_cap_never_runs_past_tip_minus_lead(world: World) -> None:
    exe = fake_claude(world.tmp, stream(good_answer(world)))
    lead = timedelta(minutes=world.cfg.min_lead_min)
    # 0.5 s before the lead limit: claude is never started
    late = TIP - lead - timedelta(seconds=0.5)
    out = go(world, make_provider(world, exe, late), late)
    assert out["status"] == "wall_cap_skip" and not (world.tmp / "fake_claude.rec").exists()
    # after tip: refused, nothing written
    after = TIP + timedelta(minutes=1)
    out = go(world, make_provider(world, exe, after), after)
    assert out["status"] == "wall_cap_skip" and out["rows_written"] == 0
    assert not (world.tmp / "fake_claude.rec").exists()
    # the cap handed to the child is min(wall cap, time to the lead limit)
    seen: list[float] = []

    def spy(
        cmd: list[str], stdin: str, env: dict[str, str], cwd: str, timeout_s: float
    ) -> RunOutcome:
        seen.append(timeout_s)
        return RunOutcome(0, stream(good_answer(world)), "", False, 0.1)

    near = TIP - lead - timedelta(seconds=40)
    prov = ClaudeCodeProvider(
        world.cfg, claude_bin=str(exe), environ={}, clock=lambda: near, runner=spy
    )
    go(world, prov, near)
    assert 35 <= seen[0] <= 40


def test_response_arriving_after_lead_limit_is_post_tip_refused(world: World) -> None:
    exe = fake_claude(world.tmp, stream(good_answer(world)))
    prov = make_provider(world, exe, AT)
    late = TIP - timedelta(minutes=10)  # runner clock when the answer is back
    out = go(world, prov, late)
    assert out["status"] == "post_tip_refused" and out["rows_written"] == 0
    assert calls(world)[0][0] == "post_tip_refused"


def test_tool_use_outside_websearch_or_api_billing_rejects_the_answer(world: World) -> None:
    bad_tool = (
        _ev(type="assistant", message={"model": "claude-opus-5-5", "content": [
            {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "ls"}}]}),
    )  # fmt: skip
    for text in (
        stream(good_answer(world), extra=bad_tool),
        stream(good_answer(world), tools=("WebSearch", "Read")),
        stream(good_answer(world), api_key_source="ANTHROPIC_API_KEY"),
    ):
        exe = fake_claude(world.tmp, text)
        out = go(world, make_provider(world, exe, AT), AT)
        assert out["status"] == "tool_restriction_violated", out  # final: never retried
        assert "tool_restriction_violated" in out["detail"]
        assert [c[0] for c in calls(world)][-1] == "tool_restriction_violated"
    assert out["rows_written"] == 0


def test_model_not_found_uses_configured_fallback_once(world: World) -> None:
    models: list[str] = []

    def runner(cmd: list[str], stdin: str, env: dict[str, str], cwd: str, t: float) -> RunOutcome:
        m = cmd[cmd.index("--model") + 1]
        models.append(m)
        if m == "claude-opus-5-5":
            return RunOutcome(
                1, stream("There's an issue with the selected model", is_error=True), "", False, 0.1
            )
        return RunOutcome(0, stream(good_answer(world), model=m), "", False, 0.1)

    prov = ClaudeCodeProvider(
        world.cfg, claude_bin="/x/claude", environ={}, clock=lambda: AT, runner=runner
    )
    out = go(world, prov, AT)
    assert out["status"] == "ok" and models == ["claude-opus-5-5", "claude-fable-5-1"]
    assert calls(world, "model_id, model_requested")[0] == ("claude-fable-5-1", "claude-fable-5-1")


def test_claude_missing_is_informational(world: World) -> None:
    prov = ClaudeCodeProvider(
        world.cfg, claude_bin=None, environ={"HOME": str(world.tmp), "PATH": ""}, clock=lambda: AT
    )
    out = go(world, prov, AT)
    assert out["status"] == "claude_unavailable"


def test_guard_usd_cap_only_applies_to_anthropic(tmp_path: Path) -> None:
    cfg = replace(ShadowConfig(), monthly_cap_usd=None)
    con = open_shadow(tmp_path / "s.duckdb")
    day = NOW.date()
    assert guard(con, day, cfg, real=True) == "monthly_cap_usd_not_set"
    assert guard(con, day, cfg, real=True, usd_cap_required=False) is None
    tiny = replace(cfg, nightly_token_ceiling=5000)  # the token ceiling still guards claude_code
    assert guard(con, day, tiny, real=True, usd_cap_required=False) == "budget_skip"
    con.close()


def test_audit_columns_migrate_an_existing_table(tmp_path: Path) -> None:
    p = tmp_path / "old.duckdb"
    old = duckdb.connect(str(p))
    old.execute(
        "CREATE TABLE llm_shadow_calls (game_id VARCHAR, slate_date DATE, requested_at TIMESTAMP, "
        "received_at TIMESTAMP, status VARCHAR, model_id VARCHAR, prompt_sha256 VARCHAR, "
        "payload_sha256 VARCHAR, tokens_in INT, tokens_out INT, budget_skip BOOLEAN, "
        "provider_real BOOLEAN, detail VARCHAR)"
    )
    old.execute(
        "INSERT INTO llm_shadow_calls VALUES "
        "('g', '2026-10-28', NULL, NULL, 'ok', 'm', '', '', 1, 2, false, true, '')"
    )
    old.close()
    con = open_shadow(p)
    cols = {r[0] for r in con.execute("DESCRIBE llm_shadow_calls").fetchall()}
    assert {"odds_domain_flag", "web_queries", "web_urls", "wall_s", "provider"} <= cols
    assert con.execute(
        "SELECT count(*), max(odds_domain_flag) FROM llm_shadow_calls"
    ).fetchone() == (1, None)
    con.close()
    open_shadow(p).close()  # idempotent


def test_build_command_and_env_helpers() -> None:
    cmd = build_command("/bin/claude", "m", allow_web=False, system_prompt="s")
    assert cmd[cmd.index("--tools") + 1] == "" and "--allowedTools" not in cmd
    env = child_env({"HOME": "/h", "ANTHROPIC_API_KEY": SECRET, "CLAUDE_CODE_X": "1", "PATH": "/p"})
    assert env == {"HOME": "/h", "PATH": "/p"}
    assert (
        launchd_like_env({"HOME": "/h", "USER": "u", "PATH": "/zzz"})["PATH"]
        == "/usr/bin:/bin:/usr/sbin:/sbin"
    )
    assert web_prompt_sha256() != prompt_sha256()
    assert ClaudeCodeProvider(ShadowConfig(), claude_bin="x").prompt_hash == web_prompt_sha256()


def test_resolve_claude_bin(tmp_path: Path) -> None:
    app = tmp_path / "Library/Application Support/Claude/claude-code"
    for v in ("2.1.9", "2.1.295", "2.1.30"):
        p = app / v / "abc" / "claude.app/Contents/MacOS/claude"
        p.parent.mkdir(parents=True)
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    got = resolve_claude_bin(None, {"HOME": str(tmp_path), "PATH": ""})
    assert got is not None and "/2.1.295/" in got
    explicit = tmp_path / "mine"
    explicit.write_text("#!/bin/sh\n")
    explicit.chmod(0o755)
    assert resolve_claude_bin(
        None, {"HOME": str(tmp_path), "NBA_CLAUDE_BIN": str(explicit)}
    ) == str(explicit)
    assert resolve_claude_bin(None, {"HOME": str(tmp_path / "none"), "PATH": ""}) is None


def test_parse_stream_tolerates_garbage_lines() -> None:
    info = parse_stream(
        "not json\n" + stream("{}", queries=("q",), result_urls=("https://a.com/x",))
    )
    assert info.parse_errors == 1 and info.queries == ["q"] and info.urls == ["https://a.com/x"]


def test_check_auth_ok_and_failure(tmp_path: Path) -> None:
    exe = fake_claude(tmp_path, stream("OK"))
    cfg = replace(ShadowConfig(), claude_bin=str(exe))
    rep = check_auth(cfg, environ={"HOME": "/h", "USER": "u", "PATH": "/usr/bin"})
    assert rep["ok"] and [c["env"] for c in rep["checks"]] == ["current_env", "launchd_like_env"]
    assert all(c["api_key_source"] == "none" for c in rep["checks"])
    assert recorded(tmp_path)["ls"] == []
    bad = fake_claude(tmp_path, stream("Not logged in · Please run /login", is_error=True), rc=1)
    rep = check_auth(replace(cfg, claude_bin=str(bad)), environ={"HOME": "/h", "PATH": "/usr/bin"})
    assert not rep["ok"] and "login" in rep["checks"][0]["detail"].lower() and rep["notes"]
    billed = fake_claude(tmp_path, stream("OK", api_key_source="ANTHROPIC_API_KEY"))
    rep = check_auth(replace(cfg, claude_bin=str(billed)), environ={"HOME": "/h"})
    assert not rep["ok"] and "would bill the API" in rep["checks"][0]["detail"]
    rep = check_auth(ShadowConfig(), environ={"HOME": str(tmp_path / "nowhere"), "PATH": ""})
    assert not rep["ok"] and rep["binary"] is None


def test_cli_check_auth_works_with_flag_off_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("NBA_LLM_SHADOW", raising=False)
    exe = fake_claude(tmp_path, stream("OK"))
    monkeypatch.setenv("NBA_CLAUDE_BIN", str(exe))
    assert cli.main(["--check-auth"]) == 0
    assert "auth check: OK" in capsys.readouterr().out
    assert cli.main(["run", "--date", "2026-10-28"]) == 0  # still off by default
    assert "disabled" in capsys.readouterr().out


def test_repo_config_selects_claude_code_and_stays_off() -> None:
    cfg = load_config()
    assert cfg.provider == "claude_code" and cfg.claude_code_wall_s == 180.0
    assert cfg.monthly_cap_usd is None  # the USD cap is an anthropic-provider requirement only
    assert os.environ.get("NBA_LLM_SHADOW") != "1"
