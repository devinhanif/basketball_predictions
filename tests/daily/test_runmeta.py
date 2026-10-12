"""Run metadata: git HEAD + dirty flag in the daily run summary (N15)."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from nba.daily.__main__ import _print_summary
from nba.daily.pipeline import RunSummary
from nba.daily.runmeta import REPO_ROOT, git_state


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root, check=True, capture_output=True,
    )  # fmt: skip


def test_clean_dirty_and_untracked(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / "a.py").write_text("x = 1\n")
    _git(tmp_path, "add", "nba/a.py")
    _git(tmp_path, "commit", "-q", "-m", "c")
    head, dirty, n = git_state(tmp_path)
    assert len(head) == 40 and dirty is False and n == 0
    (tmp_path / "scratch.txt").write_text("untracked")
    assert git_state(tmp_path)[1] is False  # untracked files are not tracked-code drift
    (tmp_path / "nba" / "a.py").write_text("x = 2\n")
    head2, dirty, n = git_state(tmp_path)
    assert head2 == head and dirty is True and n == 1


def test_outside_a_repo_is_unknown(tmp_path: Path) -> None:
    assert git_state(tmp_path) == ("unknown", None, 0)


def test_real_repo_has_a_head() -> None:
    head, dirty, _ = git_state(REPO_ROOT)
    assert len(head) == 40 and dirty is not None


def test_summary_line_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    s = RunSummary(run_id="r", run_date=date(2026, 10, 20), git_head="a" * 40, git_dirty=True,
                   git_n_dirty=3)  # fmt: skip
    _print_summary(s)
    assert (
        "code: git=aaaaaaaaaaaa dirty=true (tracked files modified: 3)" in capsys.readouterr().out
    )
    s2 = RunSummary(run_id="r", run_date=date(2026, 10, 20))
    _print_summary(s2)
    assert "dirty=unknown" in capsys.readouterr().out


def test_generated_reports_do_not_mark_the_run_dirty(tmp_path) -> None:
    import subprocess

    from nba.daily.runmeta import git_state

    def g(*a: str) -> None:
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "nba").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "nba" / "x.py").write_text("a = 1\n")
    (tmp_path / "reports" / "r.md").write_text("old\n")
    g("add", ".")
    g("commit", "-q", "-m", "init")
    (tmp_path / "reports" / "r.md").write_text("new\n")
    assert git_state(tmp_path)[1] is False
    (tmp_path / "nba" / "x.py").write_text("a = 2\n")
    assert git_state(tmp_path)[1] is True
