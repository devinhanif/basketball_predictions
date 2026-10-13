# mypy: disable-error-code="no-untyped-def,arg-type,index,assignment,operator,no-any-return"
# ruff: noqa: E501
"""The freeze and loader-review hard stop (real throw-away git repositories; the refusal is the thing under test).

Modelled on tests/research/seq_learning_curve/test_rule.py (F1 version) plus the adversary's R1: a review edited after its adding commit is refused."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research.eval import pplay_minutes_valid_rule as R

MARK = "=== RESULTS BELOW ===\n"
BODY = "# PPLAY_MINUTES_VALID\nStatus: **FROZEN**\nrule text\n"
ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "HOME": "/nonexistent",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


def git(root: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env=ENV,
        check=True,
    )
    return r.stdout.strip()


def repo(tmp: Path) -> Path:
    git(tmp, "init", "-q", "-b", "main")
    (tmp / "docs/prereg").mkdir(parents=True)
    (tmp / "docs/reviews").mkdir(parents=True)
    return tmp


def freeze(root: Path, body: str = BODY) -> tuple[str, str]:
    (root / R.DOC_REL).write_text(body + MARK + "(not run)\n")
    sha = R.text_sha(body + MARK)
    git(root, "add", R.DOC_REL)
    git(root, "commit", "-q", "--allow-empty", "-m", f"prereg(PPLAY_MINUTES_VALID): {sha}")
    return sha, git(root, "rev-parse", "HEAD")


def code(root: Path, text: str = "v1\n") -> str:
    """Commit one file under each reviewed path; returns the commit (the Reviewed-commit a review names)."""
    for rel in R.REVIEWED_PATHS:
        f = root / rel if rel.endswith(".py") else root / rel / "x.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        git(root, "add", str(f.relative_to(root)))
    git(root, "commit", "-q", "-m", "code " + text.strip())
    return git(root, "rev-parse", "HEAD")


def review(
    root: Path,
    name: str = "pplay_minutes_valid_loader_2026-10-13.md",
    commit: bool = True,
    verdict: str = "APPROVED",
    reviewed: str | None = None,
    rule: str | None = None,
) -> Path:
    sha = R.frozen_sha(root)
    reviewed = reviewed or git(root, "rev-parse", "HEAD")
    rule = rule if rule is not None else f"PPLAY_MINUTES_VALID {sha[:8]} (freeze)"
    p = root / "docs/reviews" / name
    p.write_text(
        f"# loader review\n\nVerdict: {verdict}\n\nReviewed-commit: {reviewed}\nRule: {rule}\n"
    )
    if commit:
        git(root, "add", str(p.relative_to(root)))
        git(root, "commit", "-q", "-m", "review")
    return p


def _ready(tmp_path: Path) -> Path:
    root = repo(tmp_path)
    freeze(root)
    code(root)
    return root


def test_refuses_without_a_review_and_with_an_uncommitted_or_modified_one(tmp_path: Path) -> None:
    root = repo(tmp_path)
    sha, fc = freeze(root)
    code(root)
    assert R.frozen_sha(root) == sha and R.freeze_commit(sha, root) == fc
    with pytest.raises(RuntimeError, match="hard stop"):
        R.require_review(root)
    p = review(root, commit=False)
    with pytest.raises(RuntimeError, match="not tracked"):
        R.require_review(root)
    git(root, "add", str(p.relative_to(root)))
    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_review(root)
    git(root, "commit", "-q", "-m", "review")
    got = R.require_review(root)
    assert (
        got["rule_sha256"] == sha
        and got["freeze_commit"] == fc
        and got["review_commit"] != fc
        and got["verdict"] == "APPROVED"
    )
    p.write_text(p.read_text() + "edited\n")
    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_review(root)


def test_r1_a_review_edited_after_its_adding_commit_is_refused(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    p = review(root)
    assert R.require_review(root)
    p.write_text(p.read_text() + "\nSoften: actually fine.\n")
    git(
        root, "commit", "-qam", "edit the review after approval"
    )  # committed edit: the tree is clean, the review is not the one that was added
    with pytest.raises(RuntimeError, match="edited after its adding commit"):
        R.require_review(root)
    # an older approved review cannot stand in for the edited one
    review(root, "pplay_minutes_valid_loader_2026-10-12.md")
    with pytest.raises(RuntimeError, match="edited after its adding commit"):
        R.require_review(root)


def test_refuses_a_review_that_predates_the_freeze_and_an_edited_or_superseded_rule(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    (root / "docs/prereg/seed.txt").write_text("x")
    git(root, "add", "docs/prereg/seed.txt")
    git(root, "commit", "-q", "-m", "seed")
    early = root / "docs/reviews/pplay_minutes_valid_loader_2026-10-13.md"
    early.write_text("Verdict: APPROVED\n")  # a review committed BEFORE the freeze
    git(root, "add", str(early.relative_to(root)))
    git(root, "commit", "-q", "-m", "early review")
    sha, fc = freeze(root)
    code(root)
    with pytest.raises(RuntimeError, match="not a descendant"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-14.md")
    assert R.require_review(root)["review_path"].endswith("2026-10-14.md")
    # editing the rule above the marker after the freeze: the text no longer matches any freeze commit
    (root / R.DOC_REL).write_text(BODY + "edited\n" + MARK + "(not run)\n")
    git(root, "commit", "-qam", "edit rule")
    with pytest.raises(RuntimeError, match="not the frozen one"):
        R.require_review(root)
    # a superseded freeze (other sha) is not a freeze of the current text
    sha2, fc2 = freeze(root, BODY + "edited\n")
    assert sha2 != sha and R.freeze_commit(sha2, root) == fc2 and R.freeze_commit(sha, root) == fc
    with pytest.raises(RuntimeError, match="not a descendant"):
        R.require_review(root)
    code(root, "v2\n")
    review(root, "pplay_minutes_valid_loader_2026-10-15.md")
    assert R.require_review(root)["freeze_commit"] == fc2


def test_the_subject_alone_proves_nothing(tmp_path: Path) -> None:
    """The first, superseded freeze (b1be2ea / b3578a9c) had a subject sha that does not hash the document it carried."""
    root = repo(tmp_path)
    (root / R.DOC_REL).write_text(BODY + MARK)
    git(root, "add", R.DOC_REL)
    git(root, "commit", "-q", "-m", "prereg(PPLAY_MINUTES_VALID): " + "b3578a9c" + "0" * 56)
    assert R.freeze_commit(R.frozen_sha(root), root) is None
    with pytest.raises(RuntimeError, match="not the frozen one"):
        R.require_frozen(None, root)
    sha, _ = freeze(root, BODY + "x\n")
    with pytest.raises(RuntimeError, match="not --sha"):
        R.require_frozen("0" * 64, root)
    with pytest.raises(RuntimeError, match="64-hex"):
        R.require_frozen("abc", root)
    assert R.require_frozen(sha, root)[0] == sha


def test_results_below_the_marker_may_change_without_unfreezing(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    sha = R.frozen_sha(root)
    review(root)
    (root / R.DOC_REL).write_text(BODY + MARK + "results: 1\n")
    git(root, "commit", "-qam", "results")
    assert R.require_review(root)["rule_sha256"] == sha


def test_a_review_that_does_not_say_exactly_approved_never_opens_the_gate(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    review(root, verdict="APPROVED WITH FIXES")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-14.md", verdict="BLOCKED")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-15.md", verdict="Approved")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-16_fixcheck.md", verdict="APPROVED")
    assert R.require_review(root)["verdict"] == "APPROVED"


def test_the_latest_review_governs_so_a_later_non_approval_revokes(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    review(root, "pplay_minutes_valid_loader_2026-10-13.md")
    assert R.require_review(root)["review_path"].endswith("10-13.md")
    # a LATER commit adds an alphabetically EARLIER name: history order, not the file name, decides
    review(root, "pplay_minutes_valid_loader_2026-10-12.md", verdict="BLOCKED")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root)


def test_code_changed_after_the_review_voids_it(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    review(root)
    assert R.require_review(root)
    (root / "docs/notes.md").write_text("n")  # docs-only commits leave the review valid
    git(root, "add", "docs/notes.md")
    git(root, "commit", "-q", "-m", "notes")
    assert R.require_review(root)
    f = root / "research/eval/pplay_minutes_valid_core.py"
    f.write_text("v1\nchanged\n")
    with pytest.raises(RuntimeError, match="uncommitted changes in the working tree"):
        R.require_review(root)
    git(root, "commit", "-qam", "change code after the review")
    with pytest.raises(RuntimeError, match="reviewed code changed"):
        R.require_review(root)
    review(
        root, "pplay_minutes_valid_loader_2026-10-14_fixcheck.md"
    )  # a fix-check naming the new commit restores it
    assert R.require_review(root)["review_path"].endswith("fixcheck.md")


def test_a_test_file_change_after_the_review_also_voids_it(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    review(root)
    f = root / "tests/research/pplay_minutes_valid/x.py"
    f.write_text("v1\nweaker test\n")
    git(root, "commit", "-qam", "edit a test")
    with pytest.raises(RuntimeError, match="reviewed code changed"):
        R.require_review(root)


def test_a_review_for_another_rule_or_without_a_reviewed_commit_is_refused(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    review(root, rule="PPLAY_MINUTES_VALID deadbeef (other)")
    with pytest.raises(RuntimeError, match="does not name"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-14.md", reviewed="f" * 40)
    with pytest.raises(RuntimeError, match="not a commit"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-15.md", reviewed="abc")
    with pytest.raises(RuntimeError, match="40-hex"):
        R.require_review(root)
    review(root, "pplay_minutes_valid_loader_2026-10-16.md", rule="SEQ_LEARNING_CURVE ad0408a7")
    with pytest.raises(RuntimeError, match="does not name"):
        R.require_review(root)


def test_a_reviewed_commit_from_before_the_freeze_is_refused(tmp_path: Path) -> None:
    root = repo(tmp_path)
    code(root, "old\n")  # reviewed code that predates the freeze
    pre = git(root, "rev-parse", "HEAD")
    freeze(root)
    review(root, reviewed=pre)
    with pytest.raises(RuntimeError, match="must descend from the freeze"):
        R.require_review(root)


def test_a_reviewed_commit_that_is_not_an_ancestor_of_head_is_refused(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    later = code(root, "v2\n")  # a commit the review will name ...
    git(
        root, "reset", "-q", "--hard", "HEAD~1"
    )  # ... but which is not in the history that carries the review
    review(root, reviewed=later)
    with pytest.raises(RuntimeError, match="must descend from the freeze"):
        R.require_review(root)


def test_a_missing_marker_is_an_error() -> None:
    with pytest.raises(ValueError):
        R.text_sha("no marker here\n")


def test_the_real_rule_document_hashes_to_the_frozen_sha() -> None:
    """The document on disk is the frozen rule (91d82de7) and the freeze commit exists in this repository's history."""
    sha = R.frozen_sha()
    assert sha.startswith("91d82de7")
    fc = R.freeze_commit(sha)
    assert fc is not None and fc.startswith("8e15012")
