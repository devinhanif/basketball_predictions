# ruff: noqa: E501
"""PPLAY_MINUTES_VALID: the freeze and loader-review refusals (no heavy imports).

Rule: docs/prereg/PPLAY_MINUTES_VALID.md. The text above ``=== RESULTS BELOW ===`` must hash to the sha of a commit whose subject is
``prereg(PPLAY_MINUTES_VALID): <sha256>`` and whose copy of the document hashes to the same sha (a subject alone proves nothing; the
superseded first freeze b1be2ea / b3578a9c therefore never matches the rule on disk).

"Before any run" in the rule ("Fixed before the freeze"): an adversary loader review of the harness, committed AFTER the freeze commit as
``docs/reviews/pplay_minutes_valid_loader_<date>.md``. ``require_review`` refuses unless

* at least one such review exists, every candidate is tracked, clean and unedited since the commit that ADDED it (a committed edit after the
  adding commit is refused outright, never skipped: the adversary's R1), and the adding commit is a strict descendant of the freeze;
* the LATEST candidate by adding-commit order (history order, not file name) says ``Verdict: APPROVED`` exactly, names ``PPLAY_MINUTES_VALID <sha8>``
  on its ``Rule:`` line, carries a 40-hex ``Reviewed-commit:`` that descends from the freeze, precedes the review's own commit and is an ancestor
  of HEAD, with no reviewed path changed between that commit and HEAD, and no uncommitted change under a reviewed path.

Modelled on research/eval/seq_learning_curve_rule.py ``require_review`` (F1 version, acc4e61), minus the Colab export manifest, plus the R1 refusal.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ID = "PPLAY_MINUTES_VALID"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/PPLAY_MINUTES_VALID.md"
REVIEW_DIR_REL = "docs/reviews"
REVIEW_GLOB = "pplay_minutes_valid_loader_*.md"
APPROVED = "APPROVED"  # exact; "APPROVED WITH FIXES" and "BLOCKED" never open the gate
SHA40 = re.compile(r"[0-9a-f]{40}")
REVIEWED_PATHS = (
    "research/eval/pplay_minutes_valid.py",
    "research/eval/pplay_minutes_valid_core.py",
    "research/eval/pplay_minutes_valid_rule.py",
    "tests/research/pplay_minutes_valid",
)  # the code the loader review covers: any difference between the review's Reviewed-commit and HEAD (or the working tree) voids it

Runner = Callable[..., str]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (the freeze evidence; the awk command in the rule header)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    else:
        raise ValueError("results marker missing from the rule document")
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def make_git(root: Path = ROOT) -> Runner:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False, cwd=root
        ).stdout

    return git


def git_ok(root: Path, *args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=root).returncode == 0
    )


def is_ancestor(ancestor: str, commit: str, root: Path = ROOT) -> bool:
    """True iff ``ancestor`` is an ancestor of ``commit`` (equal commits count as ancestors)."""
    return git_ok(root, "merge-base", "--is-ancestor", ancestor, commit)


def frozen_sha(root: Path = ROOT) -> str:
    return text_sha((root / DOC_REL).read_text())


def freeze_commit(sha: str, root: Path = ROOT) -> str | None:
    """The commit reachable from HEAD whose subject is ``prereg(ID): <sha>`` AND whose copy of the document hashes to ``sha``."""
    git = make_git(root)
    for line in git("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if subj.strip() == f"prereg({ID}): {sha}":
            try:
                if text_sha(git("show", f"{h}:{DOC_REL}")) == sha:
                    return h
            except ValueError:
                continue
    return None


def require_frozen(sha: str | None = None, root: Path = ROOT) -> tuple[str, str]:
    """(sha, freeze commit). ``sha`` (the ``--sha`` argument) must equal the hash of the document on disk, which must equal a committed freeze."""
    on_disk = frozen_sha(root)
    if sha is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise RuntimeError("--sha must be the full 64-hex freeze hash")
        if sha != on_disk:
            raise RuntimeError(
                f"the rule text above the marker hashes to {on_disk[:8]}, not --sha {sha[:8]}: not the frozen rule; refusing"
            )
    fc = freeze_commit(on_disk, root)
    if fc is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {on_disk}` carries this rule text: the rule on disk is not the frozen one; refusing"
        )
    return on_disk, fc


def parse_header(text: str) -> dict[str, str]:
    """First ``Key: value`` line of each header key anywhere in the review text."""
    out: dict[str, str] = {}
    for key in ("Verdict", "Reviewed-commit", "Rule"):
        m = re.search(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", text, flags=re.MULTILINE)
        if m:
            out[key] = m.group(1)
    return out


def check_review_text(text: str, sha: str, fc: str, rc: str, root: Path) -> dict[str, str]:
    """The review's own words must open the gate: exact verdict, this rule, code identical to the reviewed commit."""
    h = parse_header(text)
    if h.get("Verdict") != APPROVED:
        raise RuntimeError(
            f"the latest loader review's Verdict is {h.get('Verdict')!r}, not exactly {APPROVED!r}: refusing"
        )
    rule_line = h.get("Rule", "")
    if ID not in rule_line or sha[:8] not in rule_line:
        raise RuntimeError(f"the review's Rule line {rule_line!r} does not name {ID} {sha[:8]}")
    reviewed = h.get("Reviewed-commit", "")
    if not SHA40.fullmatch(reviewed):
        raise RuntimeError(f"the review has no 40-hex Reviewed-commit (got {reviewed!r})")
    if not git_ok(root, "cat-file", "-e", f"{reviewed}^{{commit}}"):
        raise RuntimeError(f"Reviewed-commit {reviewed[:8]} is not a commit in this repository")
    if not (
        is_ancestor(fc, reviewed, root)
        and is_ancestor(reviewed, "HEAD", root)
        and is_ancestor(reviewed, rc, root)
    ):
        raise RuntimeError(
            f"Reviewed-commit {reviewed[:8]} must descend from the freeze, precede the review's commit and be an ancestor of HEAD"
        )
    if not git_ok(root, "diff", "--quiet", reviewed, "HEAD", "--", *REVIEWED_PATHS):
        raise RuntimeError(
            f"reviewed code changed between Reviewed-commit {reviewed[:8]} and HEAD ({', '.join(REVIEWED_PATHS)}): the review does not cover this code; refusing"
        )
    if make_git(root)("status", "--porcelain", "--", *REVIEWED_PATHS).strip():
        raise RuntimeError("reviewed code has uncommitted changes in the working tree; refusing")
    return {"reviewed_commit": reviewed, "verdict": APPROVED}


def require_review(root: Path = ROOT, review_glob: str = REVIEW_GLOB) -> dict[str, str]:
    """Refuse unless the LATEST committed review (by adding-commit order) that descends from the freeze says ``Verdict: APPROVED`` and covers HEAD's code.

    Returns {rule_sha256, freeze_commit, review_path, review_commit, review_sha256, reviewed_commit, verdict}.
    """
    sha, fc = require_frozen(None, root)
    git = make_git(root)
    cands = sorted((root / REVIEW_DIR_REL).glob(review_glob))
    if not cands:
        raise RuntimeError(
            f"no {REVIEW_DIR_REL}/{review_glob}: the adversary's loader review is a hard stop before any run (rule 'Fixed before the freeze')"
        )
    order = {
        h: i for i, h in enumerate(git("rev-list", "--topo-order", "--reverse", "HEAD").split())
    }
    eligible: list[tuple[int, str, Path, str]] = []
    pre_freeze: list[str] = []
    for p in cands:
        rel = str(p.relative_to(root))
        if not p.stat().st_size:
            raise RuntimeError(f"{rel}: empty review file; refusing")
        if not git("ls-files", "--error-unmatch", "--", rel).strip():
            raise RuntimeError(f"{rel}: not tracked; commit it or remove it; refusing")
        if git("status", "--porcelain", "--", rel).strip():
            raise RuntimeError(f"{rel}: uncommitted changes; refusing")
        added = git("log", "--diff-filter=A", "--format=%H", "--", rel).split()
        if not added:
            raise RuntimeError(f"{rel}: not in any commit; refusing")
        rc = added[-1]  # the oldest adding commit
        if rc == fc or not is_ancestor(fc, rc, root):
            pre_freeze.append(f"{rel}: commit {rc[:8]} is not a descendant of the freeze {fc[:8]}")
            continue
        if not git_ok(root, "diff", "--quiet", rc, "HEAD", "--", rel):
            raise RuntimeError(
                f"{rel}: edited after its adding commit {rc[:8]} (R1: a review is immutable once committed; add a new review instead); refusing"
            )
        eligible.append((order.get(rc, -1), p.name, p, rc))
    if not eligible:
        raise RuntimeError("no usable loader review: " + "; ".join(pre_freeze))
    _, _, p, rc = max(eligible, key=lambda t: (t[0], t[1]))
    rel = str(p.relative_to(root))
    extra = check_review_text(p.read_text(), sha, fc, rc, root)
    return {
        "rule_sha256": sha,
        "freeze_commit": fc,
        "review_path": rel,
        "review_commit": rc,
        "review_sha256": file_sha256(p),
        **extra,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="PPLAY_MINUTES_VALID freeze / loader-review gate")
    ap.add_argument("cmd", choices=("check",))
    ap.parse_args(argv)
    print(json.dumps(require_review(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
