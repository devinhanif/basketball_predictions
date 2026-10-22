"""Push to Devin: iMessage to self via osascript (recipient from NBA_IMESSAGE_TO), else a macOS
notification (same pattern as nba/ops/watchdog.py), and always a line in the log file.
The recipient is never written to the repo, the log or a message."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

ENV_VAR = "NBA_IMESSAGE_TO"
_IMESSAGE = [
    "on run argv",
    'tell application "Messages"',
    "set svc to 1st service whose service type = iMessage",
    "set bud to buddy (item 1 of argv) of svc",
    "send (item 2 of argv) to bud",
    "end tell",
    "end run",
]


def send_imessage(text: str, to: str) -> bool:
    if shutil.which("osascript") is None:
        return False
    cmd = ["osascript"]
    for line in _IMESSAGE:
        cmd += ["-e", line]
    try:
        r = subprocess.run([*cmd, to, text], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def notification(text: str) -> bool:
    if shutil.which("osascript") is None:
        return False
    body = text.replace('"', "'").replace("\\", "/")
    try:
        r = subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "NBA ride"'],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


class Notifier:
    def __init__(
        self,
        log_path: Path,
        *,
        push: bool = True,
        imessage: Callable[[str, str], bool] = send_imessage,
        notify: Callable[[str], bool] = notification,
        env: dict[str, str] | None = None,
    ) -> None:
        self.log_path = log_path
        self.push = push
        self._imessage, self._notify = imessage, notify
        self._env = os.environ if env is None else env

    def log(self, kind: str, text: str, *, now: datetime | None = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        one_line = text.replace("\n", " / ")
        with self.log_path.open("a") as f:
            f.write(f"{stamp} {kind} {one_line}\n")

    def alert(self, kind: str, text: str, *, now: datetime | None = None) -> str:
        """Push + log. Returns the channel used: imessage | notification | none."""
        channel = "none"
        if self.push:
            to = self._env.get(ENV_VAR, "")
            if to and self._imessage(text, to):
                channel = "imessage"
            elif self._notify(text):
                channel = "notification"
        self.log(kind, f"[push={channel}] {text}", now=now)
        return channel
