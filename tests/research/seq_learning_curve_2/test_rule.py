# mypy: disable-error-code="no-untyped-def,arg-type,index,assignment,operator,no-any-return"
# ruff: noqa: E501
"""D5: the SEQ_LEARNING_CURVE_2 review gate in throw-away git repositories (the refusal is the thing under test)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from research.eval import seq_learning_curve_2_rule as R

MARK = "=== RESULTS BELOW ===\n"
BODY = "# SEQ_LEARNING_CURVE_2\nStatus: **FROZEN**\nrule text\n"
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
        ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        cwd=root,
        capture_output=True,
        text=True,
        env=ENV,
        check=True,
    )
    return r.stdout.strip()


def repo(tmp: Path) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    git(tmp, "init", "-q", "-b", "main")
    (tmp / "docs/prereg").mkdir(parents=True)
    (tmp / "docs/reviews").mkdir(parents=True)
    return tmp


def _ready(tmp: Path) -> Path:
    root = repo(tmp)
    (root / R.DOC_REL).write_text(BODY + MARK + "(not run)\n")
    sha = R.text_sha(BODY + MARK)
    git(root, "add", R.DOC_REL)
    git(root, "commit", "-q", "-m", f"prereg(SEQ_LEARNING_CURVE_2): {sha}")
    for rel in R.REVIEWED_PATHS:
        f = root / rel if rel.endswith(".py") else root / rel / "x.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("v1\n")
        git(root, "add", str(f.relative_to(root)))
    git(root, "commit", "-q", "-m", "code")
    return root


def review(
    root: Path, name: str, verdict: str = "APPROVED", rule: str | None = None, extra: str = ""
) -> Path:
    sha = R.frozen_sha(root)
    rule = rule if rule is not None else f"{R.ID} {sha[:8]}"
    p = root / "docs/reviews" / name
    p.write_text(
        f"# loader review\n\nVerdict: {verdict}\n\nReviewed-commit: {git(root, 'rev-parse', 'HEAD')}\nRule: {rule}\n{extra}"
    )
    git(root, "add", str(p.relative_to(root)))
    git(root, "commit", "-q", "-m", "review " + name)
    return p


def check(root: Path):
    return R.require_review(root, export_dir=None)


def test_approved_review_opens_the_gate_and_none_does_not(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    with pytest.raises(RuntimeError, match="hard stop"):
        check(root)
    review(root, "seq_learning_curve_2_loader_2026-10-14.md")
    got = check(root)
    assert got["verdict"] == "APPROVED" and got["rule_sha256"] == R.frozen_sha(root)


def test_deleted_or_renamed_blocked_review_after_an_approved_one_blocks(tmp_path: Path) -> None:
    for how in ("rm", "mv"):
        root = _ready(tmp_path / how)
        review(root, "seq_learning_curve_2_loader_2026-10-14.md")
        assert check(root)
        blocked = review(root, "seq_learning_curve_2_loader_2026-10-15.md", verdict="BLOCKED")
        with pytest.raises(RuntimeError, match="Verdict"):
            check(root)
        rel = str(blocked.relative_to(root))
        if how == "rm":
            git(root, "rm", "-q", rel)
        else:
            git(root, "mv", rel, "docs/reviews/notes_blocked.md")
        git(root, "commit", "-q", "-m", "make the blocked review disappear")
        with pytest.raises(RuntimeError, match="deleted or renamed"):
            check(root)
        review(
            root, "seq_learning_curve_2_loader_2026-10-16.md"
        )  # only a strictly later APPROVED review reopens it
        assert check(root)["review_path"].endswith("2026-10-16.md")


def test_rule_line_must_be_a_whole_line_not_a_substring_or_the_parent_id(tmp_path: Path) -> None:
    sha8 = R.text_sha(BODY + MARK)[:8]
    for rule in (
        f"SEQ_LEARNING_CURVE {sha8}",  # the parent id
        f"XSEQ_LEARNING_CURVE_2 {sha8}",  # substring
        f"SEQ_LEARNING_CURVE_2 {sha8} (freeze)",  # trailing text
        f"SEQ_LEARNING_CURVE_2 {sha8}extra",
        f"SEQ_LEARNING_CURVE_2 {'0' * 8}",  # wrong sha
        "",
    ):
        root = _ready(tmp_path / f"r{abs(hash(rule))}")
        review(root, "seq_learning_curve_2_loader_2026-10-14.md", rule=rule)
        with pytest.raises(RuntimeError, match="Rule line"):
            check(root)
    root = _ready(tmp_path / "ok")
    review(root, "seq_learning_curve_2_loader_2026-10-14.md", rule=f"SEQ_LEARNING_CURVE_2 {sha8}")
    assert check(root)


def test_the_parent_glob_does_not_see_these_files_and_these_do_not_see_the_parent(
    tmp_path: Path,
) -> None:
    root = _ready(tmp_path)
    review(root, "seq_learning_curve_loader_2026-10-14.md")  # the parent's naming: invisible here
    with pytest.raises(RuntimeError, match="hard stop"):
        check(root)


def test_editing_an_earlier_reviews_header_later_is_refused(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    p = review(root, "seq_learning_curve_2_loader_2026-10-14.md", verdict="BLOCKED")
    review(root, "seq_learning_curve_2_loader_2026-10-15.md")
    assert check(root)
    p.write_text(p.read_text().replace("Verdict: BLOCKED", "Verdict: APPROVED"))
    git(root, "commit", "-qam", "rewrite the old review header")
    with pytest.raises(RuntimeError, match="edited after its adding commit"):
        check(root)


def test_two_verdict_or_rule_lines_are_refused(tmp_path: Path) -> None:
    root = _ready(tmp_path / "v")
    review(root, "seq_learning_curve_2_loader_2026-10-14.md", extra="Verdict: BLOCKED\n")
    with pytest.raises(RuntimeError, match="2 `Verdict:` lines"):
        check(root)
    root = _ready(tmp_path / "r")
    review(root, "seq_learning_curve_2_loader_2026-10-14.md", extra=f"Rule: {R.ID} 00000000\n")
    with pytest.raises(RuntimeError, match="2 `Rule:` lines"):
        check(root)


def test_a_review_older_than_the_freeze_and_changed_reviewed_code_are_refused(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    p = root / "docs/reviews/seq_learning_curve_2_loader_2026-10-01.md"
    p.write_text("Verdict: APPROVED\n")
    git(root, "add", str(p.relative_to(root)))
    git(root, "commit", "-q", "-m", "early review")
    (root / R.DOC_REL).write_text(BODY + MARK)
    sha = R.text_sha(BODY + MARK)
    git(root, "add", R.DOC_REL)
    git(root, "commit", "-q", "-m", f"prereg({R.ID}): {sha}")
    with pytest.raises(RuntimeError, match="after the freeze"):
        check(root)
    root = _ready(tmp_path / "c")
    review(root, "seq_learning_curve_2_loader_2026-10-14.md")
    assert check(root)
    f = root / "research/eval/seq_learning_curve_2_rule.py"
    f.write_text("v2\n")
    git(root, "commit", "-qam", "change reviewed code")
    with pytest.raises(RuntimeError, match="reviewed code changed"):
        check(root)


def test_the_rule_on_disk_must_be_the_committed_freeze(tmp_path: Path) -> None:
    root = _ready(tmp_path)
    (root / R.DOC_REL).write_text(BODY.replace("rule text", "edited rule") + MARK)
    with pytest.raises(RuntimeError, match="not the frozen one"):
        check(root)
