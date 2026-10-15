# ruff: noqa: E501
"""LINE_CALIBRATION: the freeze and holdout-row refusals (no heavy imports).

Rule: docs/prereg/LINE_CALIBRATION.md. Nothing that can read a season-2025 outcome may run unless
(1) the text above ``=== RESULTS BELOW ===`` hashes to ``--sha`` and a commit whose subject is
``prereg(LINE_CALIBRATION): <sha>`` is reachable from HEAD and carries a copy of the rule that hashes to the same
value, and (2) ``docs/HOLDOUT_ACCESS_LOG.md`` carries a COMMITTED, unmodified row naming LINE_CALIBRATION, the sha8,
``PRE-REGISTERED``, ``DEVIN CONFIRMED`` and Devin's words in double quotes. The select stage (season 2024) needs (1) only; the report stage (season 2025) needs (1) and (2).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ID = "LINE_CALIBRATION"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/LINE_CALIBRATION.md"
DOC = ROOT / DOC_REL
HOLDOUT_LOG = ROOT / "docs/HOLDOUT_ACCESS_LOG.md"
DEVIN_WORDS = re.compile(r'DEVIN CONFIRMED[^"\n]*"[^"\n]{3,}"')


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (same bytes as ``awk '/^=== RESULTS BELOW ===$/{exit} {print}'``)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    """The commit whose subject is ``prereg(LINE_CALIBRATION): <sha>`` AND whose copy of the rule hashes to ``sha``."""
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
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
            f"no commit `prereg({ID}): {sha}` carrying this rule in git log: the rule is not frozen"
        )
    return sha


HARNESS_FILES = (
    "research/eval/line_calibration_eval.py",
    "research/eval/line_calibration_rule.py",
)


def require_harness_pinned(sha: str, run: Callable[..., str] = git) -> dict[str, str]:
    """The harness on disk must equal its blob at the freeze commit (the sha covers the doc only). Returns the blob sha256s."""
    h = freeze_commit(sha, run)
    if h is None:
        raise RuntimeError("the rule is not frozen")
    out: dict[str, str] = {}
    for rel in HARNESS_FILES:
        frozen = run("show", f"{h}:{rel}")
        disk = (ROOT / rel).read_text()
        if frozen != disk:
            raise RuntimeError(f"{rel} differs from its version at freeze commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(disk.encode()).hexdigest()
    return out


def require_holdout_row(
    sha: str, log: Path | None = None, run: Callable[..., str] = git, doc: Path | None = None
) -> str:
    """A committed, unmodified HOLDOUT_ACCESS_LOG row naming this id and sha8 with PRE-REGISTERED, DEVIN CONFIRMED and
    Devin's words in double quotes."""
    log = log or HOLDOUT_LOG
    require_frozen(sha, doc, run)
    text = log.read_text() if log.exists() else ""
    rows = [
        ln
        for ln in text.splitlines()
        if ID in ln
        and sha[:8] in ln
        and "PRE-REGISTERED" in ln
        and DEVIN_WORDS.search(ln) is not None
    ]
    if not rows:
        raise RuntimeError(
            f'no holdout row for {ID} with sha8, PRE-REGISTERED and DEVIN CONFIRMED (... "his words"): refusing'
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
