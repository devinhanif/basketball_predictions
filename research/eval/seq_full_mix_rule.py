# ruff: noqa: E501
"""SEQ_FULL_MIX: the freeze and holdout-row refusals, shared by the 2025 export and the scorer (no heavy imports).

Rule: docs/prereg/SEQ_FULL_MIX.md (DRAFT until the main session commits `prereg(SEQ_FULL_MIX): <sha256>`).
Nothing that can read a season-2025 outcome may run unless (1) the text above `=== RESULTS BELOW ===` hashes to the sha and a commit whose subject is
`prereg(SEQ_FULL_MIX): <sha>` is reachable from HEAD, and (2) `docs/HOLDOUT_ACCESS_LOG.md` carries a COMMITTED, unmodified row naming SEQ_FULL_MIX, the sha8,
`PRE-REGISTERED` and `DEVIN CONFIRMED`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ID = "SEQ_FULL_MIX"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/prereg/SEQ_FULL_MIX.md"
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def frozen_sha(doc: Path = DOC) -> str:
    """sha256 of the text above the results marker line (the freeze evidence)."""
    keep: list[str] = []
    for line in doc.read_text().splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, run: Callable[..., str] = git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, run) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` in git log: the rule is not frozen; nothing may read a 2025 outcome"
        )
    return sha


def require_holdout_row(
    sha: str,
    log: Path | None = None,
    run: Callable[..., str] = git,
    doc: Path | None = None,
) -> str:
    """A committed, unmodified HOLDOUT_ACCESS_LOG row naming this id and sha8 with PRE-REGISTERED and DEVIN CONFIRMED."""
    log = log or HOLDOUT_LOG
    require_frozen(sha, doc, run)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln and sha[:8] in ln and "PRE-REGISTERED" in ln and "DEVIN CONFIRMED" in ln
    ]
    if not rows:
        raise RuntimeError(
            f"no committed holdout row for {ID} (PRE-REGISTERED, DEVIN CONFIRMED, sha8): refusing"
        )
    if run("status", "--porcelain", "--", str(log)).strip():
        raise RuntimeError(
            "HOLDOUT_ACCESS_LOG.md has uncommitted changes: commit the row BEFORE the run"
        )
    if not run("log", "--format=%H", f"-S{sha[:8]}", "--", str(log)).strip():
        raise RuntimeError("the holdout row is not in any commit: refusing")
    return rows[0]


def require_ready(
    sha: str | None, run: Callable[..., str] = git, doc: Path | None = None, log: Path | None = None
) -> str:
    """The single gate every 2025-capable entrypoint calls first."""
    s = require_frozen(sha, doc, run)
    require_holdout_row(s, log, run, doc)
    return s
