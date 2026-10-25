"""Outbound iMessage through macOS Messages (``osascript``). Outbound only; replies are a v2 note.

The recipient comes ONLY from the environment variable named in ``delivery.env_var`` (default
``JARVIS_IMESSAGE_TO``), read at send time. It is never written to the repo, a log, the paper book
or a test, and it is passed to AppleScript as an argument (never spliced into script text), so a
message body cannot break out of the script. ``dry_run`` prints instead of sending and never reads
the variable. Tests inject ``runner`` and never call the real ``osascript``.

macOS asks once for Automation permission (Terminal or the launching app -> Messages); see
docs/DAILY_PIPELINE.md, "JARVIS".
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

SCRIPT = (
    "on run argv",
    "set recipientId to item 1 of argv",
    "set messageText to item 2 of argv",
    'tell application "Messages"',
    "set targetService to 1st service whose service type = iMessage",
    "set targetBuddy to buddy recipientId of targetService",
    "send messageText to targetBuddy",
    "end tell",
    "end run",
)
_PHONE = re.compile(r"^\+?[0-9][0-9 ().\-]{6,19}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Runner = Callable[..., Any]


@dataclass(frozen=True)
class SendResult:
    ok: bool
    mode: str  # sent | dry_run | failed
    note: str = ""


def recipient_from_env(env_var: str, env: Mapping[str, str] | None = None) -> str | None:
    v = (env if env is not None else os.environ).get(env_var, "").strip()
    return v if v and (_PHONE.match(v) or _EMAIL.match(v)) else None


def osascript_argv(recipient: str, text: str) -> list[str]:
    cmd = ["osascript"]
    for line in SCRIPT:
        cmd += ["-e", line]
    return [*cmd, recipient, text]


def send(
    text: str,
    *,
    env_var: str,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
    runner: Runner = subprocess.run,
    out: Callable[[str], None] = print,
    timeout_s: float = 30.0,
) -> SendResult:
    if dry_run:
        out("[dry-run] would text:\n" + text)
        return SendResult(True, "dry_run")
    rcpt = recipient_from_env(env_var, env)
    if rcpt is None:
        return SendResult(False, "failed", f"{env_var} is not set to a phone number or email")
    try:
        proc = runner(
            osascript_argv(rcpt, text),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return SendResult(False, "failed", f"osascript {type(exc).__name__}")
    if proc.returncode != 0:
        # stderr can echo the script arguments; keep only a short, recipient-free reason
        err = str(proc.stderr or "").replace(rcpt, "<recipient>")[:120]
        return SendResult(False, "failed", f"osascript rc={proc.returncode}: {err}")
    return SendResult(True, "sent")
