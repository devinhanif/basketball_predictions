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


def review(
    root: Path, name: str = "seq_learning_curve_loader_2026-10-13.md", commit: bool = True
) -> Path:
    p = root / "docs/reviews" / name
    p.write_text("# loader review\nverdict: ok\n")
    if commit:
        git(root, "add", str(p.relative_to(root)))
        git(root, "commit", "-q", "-m", "review")
    return p


def test_refuses_without_a_review_and_with_an_uncommitted_or_modified_one(tmp_path: Path) -> None:
    root = repo(tmp_path)
    sha, fc = freeze(root)
    assert R.frozen_sha(root) == sha and R.freeze_commit(sha, root) == fc
    with pytest.raises(RuntimeError, match="hard stop"):
        R.require_review(root)
    p = review(root, commit=False)
    with pytest.raises(RuntimeError, match="not tracked"):
        R.require_review(root)
    git(root, "add", str(p.relative_to(root)))
    with pytest.raises(RuntimeError, match="not in any commit|uncommitted"):
        R.require_review(root)
    git(root, "commit", "-q", "-m", "review")
    got = R.require_review(root)
    assert (
        got["rule_sha256"] == sha
        and got["freeze_commit"] == fc
        and got["review_commit"] != fc
        and got["review_path"].endswith("2026-10-13.md")
    )
    p.write_text(p.read_text() + "edited\n")
    with pytest.raises(RuntimeError, match="uncommitted"):
        R.require_review(root)


def test_refuses_a_review_that_predates_the_freeze_and_an_edited_or_superseded_rule(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path)
    review(root)  # the review is committed BEFORE the freeze
    sha, fc = freeze(root)
    with pytest.raises(RuntimeError, match="not a descendant"):
        R.require_review(root)
    review(root, "seq_learning_curve_loader_2026-10-14.md")  # a later one descends from the freeze
    assert R.require_review(root)["review_path"].endswith("2026-10-14.md")
    # editing the rule above the marker after the freeze: the text no longer matches any freeze commit
    (root / R.DOC_REL).write_text(BODY + "edited\n" + MARK + "(not run)\n")
    git(root, "commit", "-qam", "edit rule")
    with pytest.raises(RuntimeError, match="not the frozen one"):
        R.require_review(root)
    # a superseded freeze (different sha) is not a freeze of the current text; re-freezing the new text is
    sha2, fc2 = freeze(root, BODY + "edited\n")
    assert sha2 != sha and R.freeze_commit(sha2, root) == fc2 and R.freeze_commit(sha, root) == fc
    with pytest.raises(
        RuntimeError, match="not a descendant"
    ):  # the old reviews predate the NEW freeze
        R.require_review(root)
    review(root, "seq_learning_curve_loader_2026-10-15.md")
    assert R.require_review(root)["freeze_commit"] == fc2


def test_results_below_the_marker_may_change_without_unfreezing(tmp_path: Path) -> None:
    root = repo(tmp_path)
    sha, _ = freeze(root)
    review(root)
    (root / R.DOC_REL).write_text(BODY + MARK + "results: 1\n")
    git(root, "commit", "-qam", "results")
    assert R.require_review(root)["rule_sha256"] == sha


def test_stage_writes_the_attestation_the_notebook_checks(tmp_path: Path) -> None:
    root = repo(tmp_path)
    freeze(root)
    out = tmp_path / "stage"
    out.mkdir()
    with pytest.raises(RuntimeError, match="hard stop"):
        R.stage(out, root)
    review(root)
    with pytest.raises(FileNotFoundError, match="build the exports"):
        R.stage(out, root)
    for n in R.EXPORT_NAMES:
        (out / n).write_bytes(n.encode())
    att = R.stage(out, root)
    on_disk = json.loads((out / R.ATTESTATION).read_text())
    assert on_disk["review_descends_from_freeze"] is True and set(on_disk["exports"]) == set(
        R.EXPORT_NAMES
    )
    assert (out / "loader_review.md").read_text() == (root / att["review_path"]).read_text()
    assert on_disk["review_sha256"] == R.file_sha256(out / "loader_review.md")
    assert on_disk["exports_manifest_sha256"] == R.manifest_sha(on_disk["exports"])


def test_text_sha_needs_the_marker() -> None:
    with pytest.raises(ValueError):
        R.text_sha("no marker here\n")
