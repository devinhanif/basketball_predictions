# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL inference helpers and the refusing entrypoint (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

Arm M (the T506 features in the T506 4-leaf residual layer, per stat) is NOT built or scored by this file: ``run`` refuses unless the rule is
frozen (a commit ``prereg(ARCHETYPE_MATCHUP_ALL): <sha256 of the text above the results marker>`` exists). The composite and the
pass function live in ``archetype_matchup_all_rule``; the clustered bootstrap, integer-grid CRPS are reused (same code, same seeds).
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from research.eval import archetype_matchup_all_rule as rule

MARKER = "=== RESULTS BELOW ==="
DOC = Path(__file__).resolve().parents[2] / "docs/prereg/ARCHETYPE_MATCHUP_ALL.md"


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text().split(MARKER)[0])


def require_frozen(sha: str | None, doc: Path = DOC) -> str:
    """Refuse unless the draft's text above the marker hashes to ``sha`` AND a commit titled ``prereg(ARCHETYPE_MATCHUP_ALL): <sha>`` exists."""
    if not sha:
        raise SystemExit(
            "ARCHETYPE_MATCHUP_ALL is a DRAFT: run refuses without --sha of a frozen rule"
        )
    if MARKER not in doc.read_text():
        raise SystemExit("rule text has no results marker: still a draft")
    if frozen_sha(doc) != sha:
        raise SystemExit("the text above the marker does not hash to --sha")
    found = subprocess.run(
        ["git", "log", "--all", f"--grep=prereg({rule.ID}): {sha}", "--format=%H"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not found:
        raise SystemExit(f"no commit 'prereg({rule.ID}): {sha}' found: not frozen")
    return found.splitlines()[0]


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--sha")
    a = ap.parse_args(argv)
    require_frozen(a.sha)
    raise SystemExit(
        "rule is frozen but the scoring arm is not built in this draft: build it behind the frozen rule"
    )


if __name__ == "__main__":
    raise SystemExit(main())
