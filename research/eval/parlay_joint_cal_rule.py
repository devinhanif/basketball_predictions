# ruff: noqa: E501
"""PARLAY_JOINT_CAL: the freeze and harness-pin refusals (no heavy imports).

Rule: docs/prereg/PARLAY_JOINT_CAL.md. Season 2025 is never read under this rule, so there is no
holdout-row path. Scoring on real outcomes needs (1) the text above ``=== RESULTS BELOW ===`` to hash
to ``--sha`` with a commit ``prereg(PARLAY_JOINT_CAL): <sha>`` reachable from HEAD that carries a copy
of the rule hashing to the same value, and (2) every harness file on disk to equal its blob at that
freeze commit (the rule sha covers the document only). The synthetic-label rehearsal needs neither.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ID = "PARLAY_JOINT_CAL"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/PARLAY_JOINT_CAL.md"
DOC = ROOT / DOC_REL

HARNESS_FILES = (
    "research/eval/parlay_joint_cal_eval.py",
    "research/eval/parlay_joint_cal_rule.py",
    "research/eval/parlay_joint_cal_gate.py",
    "research/eval/alt_harness_common.py",
    "research/eval/line_calibration_eval.py",
    "research/eval/line_calibration_rule.py",
    "research/eval/model_clv_eval.py",
    "nba/parlay/copula.py",
)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (the bytes of
    ``awk '/^=== RESULTS BELOW ===$/{exit} {print}'``)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    """The commit whose subject is ``prereg(PARLAY_JOINT_CAL): <sha>`` AND whose copy of the rule hashes to ``sha``."""
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


def require_harness_pinned(sha: str, run: Callable[..., str] = git) -> dict[str, str]:
    """Every harness file on disk must equal its blob at the freeze commit. Returns the sha256 of each."""
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


def require_ready(sha: str | None, run: Callable[..., str] = git, doc: Path | None = None) -> str:
    """The single gate the real-outcome entrypoint calls first."""
    s = require_frozen(sha, doc, run)
    require_harness_pinned(s, run)
    return s
