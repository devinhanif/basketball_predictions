from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from nba.jarvis import imessage

VAR = "JARVIS_IMESSAGE_TO"
FAKE_NUMBER = "+15550100123"  # fictional (555-01xx range), used only in memory in these tests


class Proc:
    def __init__(self, rc: int = 0, err: str = "") -> None:
        self.returncode, self.stderr = rc, err


def test_dry_run_prints_and_never_reads_the_env_or_runs_osascript() -> None:
    out: list[str] = []

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("osascript must not run in dry-run")

    res = imessage.send("hello", env_var=VAR, dry_run=True, env={}, runner=boom, out=out.append)
    assert res.ok and res.mode == "dry_run" and "hello" in out[0]


def test_missing_or_invalid_recipient_fails_without_running() -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("must not run")

    for env in ({}, {VAR: ""}, {VAR: "not a number; rm -rf /"}):
        res = imessage.send("x", env_var=VAR, env=env, runner=boom)
        assert not res.ok and res.mode == "failed" and VAR in res.note


def test_recipient_is_an_argument_never_spliced_into_the_script() -> None:
    seen: dict[str, Any] = {}

    def run(argv: list[str], **kw: Any) -> Proc:
        seen["argv"] = argv
        return Proc(0)

    body = 'quote " and \\ and\nnewline; do shell script "x"'
    res = imessage.send(body, env_var=VAR, env={VAR: FAKE_NUMBER}, runner=run)
    assert res.ok and res.mode == "sent"
    argv = seen["argv"]
    assert argv[0] == "osascript"
    script_lines = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert FAKE_NUMBER not in "".join(script_lines) and body not in "".join(script_lines)
    assert argv[-2:] == [FAKE_NUMBER, body]  # passed as argv items 1 and 2 of `on run argv`
    assert any("on run argv" in ln for ln in script_lines)


def test_failure_note_never_echoes_the_recipient() -> None:
    def run(argv: list[str], **kw: Any) -> Proc:
        return Proc(1, f"Messages got an error: can't send to {FAKE_NUMBER}")

    res = imessage.send("x", env_var=VAR, env={VAR: FAKE_NUMBER}, runner=run)
    assert not res.ok and FAKE_NUMBER not in res.note and "<recipient>" in res.note


def test_timeout_and_oserror_are_failures_not_crashes() -> None:
    def to(argv: list[str], **kw: Any) -> Proc:
        raise subprocess.TimeoutExpired("osascript", 1)

    assert not imessage.send("x", env_var=VAR, env={VAR: FAKE_NUMBER}, runner=to).ok

    def oe(argv: list[str], **kw: Any) -> Proc:
        raise FileNotFoundError("osascript")

    assert not imessage.send("x", env_var=VAR, env={VAR: FAKE_NUMBER}, runner=oe).ok


def test_the_repo_never_contains_a_recipient() -> None:
    """No phone number or email literal in the package, its config, or these tests (except the
    fictional 555-01xx number above, which is in this one file only)."""
    phone = re.compile(r"(?<![\d.])\+?1?[ -.]?\(?\d{3}\)?[ -.]\d{3}[ -.]\d{4}(?!\d)")
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
    files = [
        *Path("nba/jarvis").glob("*.py"),
        Path("configs/jarvis.yaml"),
        *Path("tests/jarvis").glob("*.py"),
    ]
    for f in files:
        txt = f.read_text()
        if f.name != "test_imessage.py":
            assert not phone.search(txt), f
        assert not email.search(txt.replace("devin" + "@", "")), f
    cfg_text = Path("configs/jarvis.yaml").read_text()
    assert "JARVIS_IMESSAGE_TO" in cfg_text  # the NAME of the variable only
