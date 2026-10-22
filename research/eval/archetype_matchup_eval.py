# ruff: noqa: E501
"""ARCHETYPE_MATCHUP inference helpers and the refusing entrypoint (rule: docs/prereg/ARCHETYPE_MATCHUP.md, DRAFT).

Arm M is NOT scored by this file: ``run`` refuses unless the rule is frozen (a commit
``prereg(ARCHETYPE_MATCHUP): <sha256 of the text above the results marker>`` exists). The paired game / player-season / ISO-week
clustered bootstrap, the integer-grid CRPS and the positive-control simulation are reused from ``role_prior_salary_eval`` and
``preseason_role_prior`` (same code, same seeds), not re-implemented.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np

from research.eval import archetype_matchup_rule as rule
from research.eval import role_prior_salary_eval as ev

MARKER = "=== RESULTS BELOW ==="
DOC = Path(__file__).resolve().parents[2] / "docs/prereg/ARCHETYPE_MATCHUP.md"
three_way = ev.three_way
sim_cell = ev.sim_cell
noise_arm = ev.noise_arm
bh_adjust = rule.bh_adjust


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text().split(MARKER)[0])


def require_frozen(sha: str | None, doc: Path = DOC) -> str:
    """Refuse unless the draft's text above the marker hashes to ``sha`` AND a commit titled ``prereg(ARCHETYPE_MATCHUP): <sha>`` exists."""
    if not sha:
        raise SystemExit("ARCHETYPE_MATCHUP is a DRAFT: run refuses without --sha of a frozen rule")
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


def paired_delta(
    arm_crps: np.ndarray, a0_crps: np.ndarray, game: np.ndarray, ps: np.ndarray, week: np.ndarray
) -> dict[str, object]:
    """Paired CRPS delta (arm minus A0) with the three clusterings; the binding one is the widest."""
    return three_way(np.asarray(arm_crps) - np.asarray(a0_crps), game, ps, week)


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
