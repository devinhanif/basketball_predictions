# mypy: disable-error-code="no-untyped-def,arg-type,index,assignment,operator,no-any-return"
# ruff: noqa: E501
"""The loader-review hard stop: the run refuses unless a committed docs/reviews/seq_learning_curve_loader_<date>.md exists whose adding commit descends from the freeze commit
(real throw-away git repositories; the refusal is the thing under test)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from research.eval import seq_learning_curve_rule as R

MARK = "=== RESULTS BELOW ===\n"
BODY = "# SEQ_LEARNING_CURVE\nStatus: **FROZEN**\nrule text\n"
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
    git(root, "commit", "-q", "--allow-empty", "-m", f"prereg(SEQ_LEARNING_CURVE): {sha}")
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


def exports(root: Path, salt: str = "") -> Path:
    d = root / "stage_exports"
    d.mkdir(exist_ok=True)
    for n in R.EXPORT_NAMES:
        (d / n).write_bytes((n + salt).encode())
    return d


def manifest(d: Path) -> str:
    return R.exports_manifest_of(d)


def review(
    root: Path,
    name: str = "seq_learning_curve_loader_2026-10-13.md",
    commit: bool = True,
    verdict: str = "APPROVED",
    reviewed: str | None = None,
    man: str | None = None,
    rule: str | None = None,
) -> Path:
    sha = R.frozen_sha(root)
    reviewed = reviewed or git(root, "rev-parse", "HEAD")
    man = man if man is not None else manifest(exports(root))
    rule = rule if rule is not None else f"SEQ_LEARNING_CURVE {sha[:8]} (freeze)"
    p = root / "docs/reviews" / name
    p.write_text(
        f"# loader review\n\nVerdict: {verdict}\n\nReviewed-commit: {reviewed}\nRule: {rule}\nExports-manifest: {man}\n"
    )
    if commit:
        git(root, "add", str(p.relative_to(root)))
        git(root, "commit", "-q", "-m", "review")
    return p


def test_refuses_without_a_review_and_with_an_uncommitted_or_modified_one(tmp_path: Path) -> None:
    root = repo(tmp_path)
    sha, fc = freeze(root)
    code(root)
    ex = exports(root)
    assert R.frozen_sha(root) == sha and R.freeze_commit(sha, root) == fc
    with pytest.raises(RuntimeError, match="hard stop"):
        R.require_review(root, export_dir=ex)
    p = review(root, commit=False)
    with pytest.raises(RuntimeError, match="not tracked"):
        R.require_review(root, export_dir=ex)
    git(root, "add", str(p.relative_to(root)))
    with pytest.raises(RuntimeError, match="not in any commit|uncommitted"):
        R.require_review(root, export_dir=ex)
    git(root, "commit", "-q", "-m", "review")
    got = R.require_review(root, export_dir=ex)
    assert (
        got["rule_sha256"] == sha
        and got["freeze_commit"] == fc
        and got["review_commit"] != fc
        and got["review_path"].endswith("2026-10-13.md")
        and got["verdict"] == "APPROVED"
        and got["exports_manifest"] == manifest(ex)
    )
    p.write_text(p.read_text() + "edited\n")
    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_review(root, export_dir=ex)


def test_refuses_a_review_that_predates_the_freeze_and_an_edited_or_superseded_rule(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    (root / "docs/prereg/seed.txt").write_text("x")
    git(root, "add", "docs/prereg/seed.txt")
    git(root, "commit", "-q", "-m", "seed")
    early = root / "docs/reviews/seq_learning_curve_loader_2026-10-13.md"
    early.write_text("Verdict: APPROVED\n")  # a review committed BEFORE the freeze
    git(root, "add", str(early.relative_to(root)))
    git(root, "commit", "-q", "-m", "early review")
    sha, fc = freeze(root)
    code(root)
    ex = exports(root)
    with pytest.raises(RuntimeError, match="not a descendant"):
        R.require_review(root, export_dir=ex)
    review(root, "seq_learning_curve_loader_2026-10-14.md")  # a later one descends from the freeze
    assert R.require_review(root, export_dir=ex)["review_path"].endswith("2026-10-14.md")
    # editing the rule above the marker after the freeze: the text no longer matches any freeze commit
    (root / R.DOC_REL).write_text(BODY + "edited\n" + MARK + "(not run)\n")
    git(root, "commit", "-qam", "edit rule")
    with pytest.raises(RuntimeError, match="not the frozen one"):
        R.require_review(root, export_dir=ex)
    # a superseded freeze (different sha) is not a freeze of the current text; re-freezing the new text is
    sha2, fc2 = freeze(root, BODY + "edited\n")
    assert sha2 != sha and R.freeze_commit(sha2, root) == fc2 and R.freeze_commit(sha, root) == fc
    with pytest.raises(
        RuntimeError, match="not a descendant"
    ):  # the old reviews predate the NEW freeze
        R.require_review(root, export_dir=ex)
    code(root, "v2\n")
    review(root, "seq_learning_curve_loader_2026-10-15.md")
    assert R.require_review(root, export_dir=ex)["freeze_commit"] == fc2


def test_results_below_the_marker_may_change_without_unfreezing(tmp_path: Path) -> None:
    root = repo(tmp_path)
    sha, _ = freeze(root)
    code(root)
    ex = exports(root)
    review(root)
    (root / R.DOC_REL).write_text(BODY + MARK + "results: 1\n")
    git(root, "commit", "-qam", "results")
    assert R.require_review(root, export_dir=ex)["rule_sha256"] == sha


def _ready(tmp_path: Path) -> tuple[Path, Path]:
    root = repo(tmp_path)
    freeze(root)
    code(root)
    return root, exports(root)


def test_a_review_that_does_not_say_exactly_approved_never_opens_the_gate(tmp_path: Path) -> None:
    root, ex = _ready(tmp_path)
    review(root, verdict="APPROVED WITH FIXES")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root, export_dir=ex)
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.stage(tmp_path / "stage_exports", root)
    review(root, "seq_learning_curve_loader_2026-10-14.md", verdict="BLOCKED")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root, export_dir=ex)
    review(root, "seq_learning_curve_loader_2026-10-15.md", verdict="Approved")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root, export_dir=ex)
    review(root, "seq_learning_curve_loader_2026-10-16_fixcheck.md", verdict="APPROVED")
    assert R.require_review(root, export_dir=ex)["verdict"] == "APPROVED"


def test_the_latest_review_governs_so_a_later_non_approval_revokes(tmp_path: Path) -> None:
    root, ex = _ready(tmp_path)
    review(root, "seq_learning_curve_loader_2026-10-13.md")
    assert R.require_review(root, export_dir=ex)["review_path"].endswith("10-13.md")
    # a LATER commit adds an alphabetically EARLIER name: history order, not the file name, decides
    review(root, "seq_learning_curve_loader_2026-10-12.md", verdict="BLOCKED")
    with pytest.raises(RuntimeError, match="not exactly 'APPROVED'"):
        R.require_review(root, export_dir=ex)


def test_code_changed_after_the_review_voids_it(tmp_path: Path) -> None:
    root, ex = _ready(tmp_path)
    review(root)
    assert R.require_review(root, export_dir=ex)
    # docs-only commits after the review leave it valid
    (root / "docs/notes.md").write_text("n")
    git(root, "add", "docs/notes.md")
    git(root, "commit", "-q", "-m", "notes")
    assert R.require_review(root, export_dir=ex)
    # a committed change to a reviewed path
    f = root / "research/eval/seq_learning_curve_eval.py"
    f.write_text("v1\nchanged\n")
    with pytest.raises(RuntimeError, match="uncommitted changes in the working tree"):
        R.require_review(root, export_dir=ex)
    git(root, "commit", "-qam", "change code after the review")
    with pytest.raises(RuntimeError, match="reviewed code changed"):
        R.require_review(root, export_dir=ex)
    # a fix-check review naming the new commit restores it
    review(root, "seq_learning_curve_loader_2026-10-14_fixcheck.md")
    assert R.require_review(root, export_dir=ex)["review_path"].endswith("fixcheck.md")


def test_rebuilt_exports_void_the_review(tmp_path: Path) -> None:
    root, ex = _ready(tmp_path)
    review(root)
    assert R.require_review(root, export_dir=ex)
    (ex / R.EXPORT_NAMES[0]).write_bytes(b"rebuilt, different bytes")
    with pytest.raises(RuntimeError, match="Exports-manifest"):
        R.require_review(root, export_dir=ex)
    (ex / R.EXPORT_NAMES[0]).unlink()
    with pytest.raises(FileNotFoundError, match="build the exports"):
        R.require_review(root, export_dir=ex)


def test_a_review_for_another_rule_or_without_a_reviewed_commit_is_refused(tmp_path: Path) -> None:
    root, ex = _ready(tmp_path)
    review(root, rule="SEQ_LEARNING_CURVE deadbeef (other)")
    with pytest.raises(RuntimeError, match="does not name"):
        R.require_review(root, export_dir=ex)
    review(root, "seq_learning_curve_loader_2026-10-14.md", reviewed="f" * 40)
    with pytest.raises(RuntimeError, match="not a commit"):
        R.require_review(root, export_dir=ex)
    review(root, "seq_learning_curve_loader_2026-10-15.md", reviewed="abc")
    with pytest.raises(RuntimeError, match="40-hex"):
        R.require_review(root, export_dir=ex)


def test_a_reviewed_commit_from_before_the_freeze_is_refused(tmp_path: Path) -> None:
    root = repo(tmp_path)
    code(root, "old\n")  # reviewed code that predates the freeze
    pre = git(root, "rev-parse", "HEAD")
    freeze(root)
    ex = exports(root)
    review(root, reviewed=pre)
    with pytest.raises(RuntimeError, match="must descend from the freeze"):
        R.require_review(root, export_dir=ex)


def test_stage_writes_the_attestation_the_notebook_checks(tmp_path: Path) -> None:
    root = repo(tmp_path)
    freeze(root)
    code(root)
    out = tmp_path / "stage"
    out.mkdir()
    with pytest.raises(RuntimeError, match="hard stop"):
        R.stage(out, root)
    for n in R.EXPORT_NAMES:
        (out / n).write_bytes(n.encode())
    review(root, man=R.exports_manifest_of(out))
    att = R.stage(out, root)
    on_disk = json.loads((out / R.ATTESTATION).read_text())
    assert on_disk["review_descends_from_freeze"] is True and set(on_disk["exports"]) == set(
        R.EXPORT_NAMES
    )
    assert (out / "loader_review.md").read_text() == (root / att["review_path"]).read_text()
    assert on_disk["review_sha256"] == R.file_sha256(out / "loader_review.md")
    assert on_disk["exports_manifest_sha256"] == R.manifest_sha(on_disk["exports"])
    (out / R.EXPORT_NAMES[1]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="Exports-manifest"):
        R.stage(out, root)


def test_text_sha_needs_the_marker() -> None:
    with pytest.raises(ValueError):
        R.text_sha("no marker here\n")
