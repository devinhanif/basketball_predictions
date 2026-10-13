# ruff: noqa: E501
"""Mac preflight of the AVG shadow: the machine must not sleep on AC power (``pmset -g custom`` shows ``sleep 0`` under ``AC Power``).

A sleeping Mac misses the T-60 tick, and a missed pre-tip row can never be backfilled. Devin set sleep 0 on 2026-10-13. The check only
WARNS (an ALERTS line through ``shadow_errors``); it never blocks, changes or retries anything, and it is silent off macOS.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable


def parse_ac_sleep(text: str) -> int | None:
    """The ``sleep`` minutes of the ``AC Power:`` block of ``pmset -g custom`` output; None when absent."""
    block = False
    for line in text.splitlines():
        if re.match(r"^\S.*:\s*$", line):
            block = line.strip().lower().startswith("ac power")
            continue
        m = re.match(r"^\s+sleep\s+(\d+)\b", line)
        if block and m:
            return int(m.group(1))
    return None


def run_pmset() -> str:
    return subprocess.run(
        ["pmset", "-g", "custom"], capture_output=True, text=True, timeout=10, check=False
    ).stdout


def sleep_warning(
    runner: Callable[[], str] = run_pmset, platform: str = sys.platform
) -> str | None:
    """A one-line warning when the Mac can sleep on AC, else None. Never raises."""
    if platform != "darwin":
        return None
    try:
        sleep = parse_ac_sleep(runner())
    except (OSError, subprocess.SubprocessError):
        return "pmset preflight: could not read power settings (AVG shadow cannot confirm the Mac stays awake)"
    if sleep is None:
        return "pmset preflight: no AC sleep setting found (AVG shadow cannot confirm the Mac stays awake)"
    if sleep != 0:
        return f"pmset preflight: Mac sleeps after {sleep} min on AC; a slept Mac misses the T-60 slate (set `sudo pmset -c sleep 0`)"
    return None
