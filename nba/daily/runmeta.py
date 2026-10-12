"""Run metadata for the daily summary: which code produced the rows (N15, hardening review M-C).

launchd executes the working tree, not a commit, so a run records the git HEAD **and** whether
tracked files differ from it. The result goes into the printed run summary (hence the job log);
it is never written into ``forward_predictions`` or any scored table, so replays and the oracle
cannot see it."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(args: list[str], root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True, timeout=15
        )
    except Exception:
        return None
    return out.stdout


def git_state(root: Path | None = None) -> tuple[str, bool | None, int]:
    """(HEAD sha or 'unknown', dirty flag or None when unknown, number of modified tracked files).

    Dirty = any tracked file modified or staged (untracked files are ignored: scratch reports are
    not code the live path executes unless imported, and the tracked-file count says so)."""
    root = root or REPO_ROOT
    head = _git(["rev-parse", "HEAD"], root)
    status = _git(["status", "--porcelain", "--untracked-files=no"], root)
    sha = head.strip() if head and head.strip() else "unknown"
    if status is None:
        return sha, None, 0
    n = len([ln for ln in status.splitlines() if ln.strip()])
    return sha, n > 0, n
