# ruff: noqa: E501
"""PPLAY_MINUTES_VALID: the freeze and loader-review refusals (no heavy imports).

Rule: docs/prereg/PPLAY_MINUTES_VALID.md. The text above ``=== RESULTS BELOW ===`` must hash to the sha of a commit whose subject is
``prereg(PPLAY_MINUTES_VALID): <sha256>`` and whose copy of the document hashes to the same sha (a subject alone proves nothing; the
superseded first freeze b1be2ea / b3578a9c therefore never matches the rule on disk).

"Before any run" in the rule ("Fixed before the freeze"): an adversary loader review of the harness, committed AFTER the freeze commit as
``docs/reviews/pplay_minutes_valid_loader_<date>.md``. ``require_review`` refuses unless

* at least one such review exists, every file under the glob is tracked, clean and unedited since the commit that ADDED it (a committed edit after
  the adding commit is refused outright, never skipped: the adversary's R1), and the adding commit is a strict descendant of the freeze;
* the candidates are every review CONTENT (git blob) ever added under the glob in HEAD's history, from ``git log``, not from the files that happen to
  exist now (F5: a later BLOCKED review removed with ``git rm`` or renamed out of the glob does not reopen the gate). A blob is dated by its EARLIEST
  adding commit, so renaming an old review never makes it newer. The LATEST candidate by adding-commit order (history order, not file name; several in
  one commit must all pass) must still be present under the glob (a deletion is a revocation; only a later APPROVED review replaces it), say
  ``Verdict: APPROVED`` exactly, carry a ``Rule:`` line that is exactly ``PPLAY_MINUTES_VALID <sha8>`` (optionally followed by a parenthetical), carry a
  40-hex ``Reviewed-commit:`` that descends from the freeze, precedes the review's own commit and is an ancestor of HEAD, with no reviewed path
  changed between that commit and HEAD, and no uncommitted change under a reviewed path.
* ``REVIEWED_PATHS`` includes the modules the harness imports (S9c, strict reading): a committed change to one voids the review. ``module_blobs`` stamps
  their git blob shas into the results (R2).

Modelled on research/eval/seq_learning_curve_rule.py ``require_review`` (F1 version, acc4e61), minus the Colab export manifest, plus the R1 refusal.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
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
    # imported dependencies (S9c / R2): parity and the R22 reproduction catch drift in P0 and R, but a committed change here is still a different harness
    "research/eval/pplay_context_eval.py",
    "research/eval/recent_role_eval.py",
    "research/eval/minutes_v2_eval.py",
    "research/eval/history_window_eval.py",
    "research/eval/workload_eval.py",
    "research/eval/pplay_into_props_live.py",
    "research/props/minutes_v2.py",
    "research/recent_role/prod_state.py",
    "research/workload/asof.py",
    "nba/props/minutes.py",
    "nba/props/forward.py",
    "nba/props/context_residual.py",
    "nba/eval/context_residual_eval.py",
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
    if not re.fullmatch(rf"{re.escape(ID)} {re.escape(sha[:8])}(?: \([^)]*\))?", rule_line):
        raise RuntimeError(
            f"the review's Rule line {rule_line!r} does not name {ID} {sha[:8]} exactly (substring matches are refused, F5/S12b)"
        )
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


def _pathspec(review_glob: str) -> str:
    return f":(glob){REVIEW_DIR_REL}/{review_glob}"


def head_reviews(root: Path, review_glob: str) -> dict[str, str]:
    """{path: blob sha} of the review files in HEAD's tree under the glob."""
    out: dict[str, str] = {}
    prefix = REVIEW_DIR_REL + "/"
    for line in make_git(root)("ls-tree", "-r", "HEAD", REVIEW_DIR_REL).splitlines():
        meta, _, path = line.partition("\t")
        rest = path[len(prefix) :]
        if path.startswith(prefix) and "/" not in rest and fnmatch.fnmatch(rest, review_glob):
            out[path] = meta.split()[2]
    return out


def add_events(root: Path, review_glob: str) -> list[tuple[str, str, str]]:
    """Every (commit, path, blob) that ADDED a file under the glob anywhere in HEAD's history (renames are an add plus a delete, never a continuation)."""
    txt = make_git(root)(
        "log",
        "-m",
        "--no-renames",
        "--diff-filter=A",
        "--raw",
        "--no-abbrev",
        "--format=commit %H",
        "--",
        _pathspec(review_glob),
    )
    out: list[tuple[str, str, str]] = []
    cur = ""
    for line in txt.splitlines():
        if line.startswith("commit "):
            cur = line.split()[1]
        elif line.startswith(":") and cur:
            meta, _, path = line.partition("\t")
            out.append((cur, path, meta.split()[3]))
    return out


def require_review(root: Path = ROOT, review_glob: str = REVIEW_GLOB) -> dict[str, str]:
    """Refuse unless the LATEST committed review (by adding-commit order) that descends from the freeze says ``Verdict: APPROVED``, still exists, and covers HEAD's code.

    Returns {rule_sha256, freeze_commit, review_path, review_commit, review_sha256, reviewed_commit, verdict}.
    """
    sha, fc = require_frozen(None, root)
    git = make_git(root)
    rdir = root / REVIEW_DIR_REL
    fs_rel = {str(p.relative_to(root)) for p in rdir.glob(review_glob)} if rdir.exists() else set()
    head = head_reviews(root, review_glob)
    events = add_events(root, review_glob)
    if not fs_rel and not head and not events:
        raise RuntimeError(
            f"no {REVIEW_DIR_REL}/{review_glob}: the adversary's loader review is a hard stop before any run (rule 'Fixed before the freeze')"
        )
    for rel in sorted(fs_rel):
        if not (root / rel).stat().st_size:
            raise RuntimeError(f"{rel}: empty review file; refusing")
        if not git("ls-files", "--error-unmatch", "--", rel).strip():
            raise RuntimeError(f"{rel}: not tracked; commit it or remove it; refusing")
        if rel not in head:
            raise RuntimeError(f"{rel}: uncommitted changes (not in any commit); refusing")
        if git("status", "--porcelain", "--", rel).strip():
            raise RuntimeError(f"{rel}: uncommitted changes; refusing")
    for rel in sorted(set(head) - fs_rel):
        raise RuntimeError(f"{rel}: deleted in the working tree, uncommitted changes; refusing")
    order = {
        h: i for i, h in enumerate(git("rev-list", "--topo-order", "--reverse", "HEAD").split())
    }
    ev = [(order.get(c, -1), c, p, b) for c, p, b in events]
    latest_by_path: dict[str, tuple[int, str, str]] = {}
    for o, c, p, b in ev:
        if p not in latest_by_path or o > latest_by_path[p][0]:
            latest_by_path[p] = (o, c, b)
    for rel, hb in sorted(head.items()):
        la = latest_by_path.get(rel)
        if la is None:
            raise RuntimeError(f"{rel}: not in any commit; refusing")
        if la[2] != hb:
            raise RuntimeError(
                f"{rel}: edited after its adding commit {la[1][:8]} (R1: a review is immutable once committed; add a new review instead); refusing"
            )
    first: dict[
        str, tuple[int, str, str]
    ] = {}  # blob -> earliest add (a rename or copy never makes a review newer)
    for o, c, p, b in ev:
        if b not in first or (o, c) < first[b][:2]:
            first[b] = (o, c, p)
    anc: dict[str, bool] = {}

    def after_freeze(c: str) -> bool:
        if c not in anc:
            anc[c] = c != fc and is_ancestor(fc, c, root)
        return anc[c]

    cands = {b: v for b, v in first.items() if after_freeze(v[1])}
    if not cands:
        pre = [
            f"{p}: commit {first[hb][1][:8]} is not a descendant of the freeze {fc[:8]}"
            for p, hb in sorted(head.items())
            if hb in first
        ]
        raise RuntimeError("no usable loader review: " + "; ".join(pre))
    top = max(v[0] for v in cands.values())
    group = sorted((v[2], b, v[1]) for b, v in cands.items() if v[0] == top)
    present = {hb: rel for rel, hb in sorted(head.items(), reverse=True)}
    for p, b, c in group:
        if b not in present:
            raise RuntimeError(
                f"the latest loader review ({p}, added in {c[:8]}) is no longer under {REVIEW_DIR_REL}/{review_glob}: deleted or renamed out of the glob. "
                "A removal is a revocation, not a withdrawal of its verdict; only a later committed review saying APPROVED replaces it (F5); refusing"
            )
    infos: list[tuple[str, str, dict[str, str]]] = []
    for _p, b, c in group:  # several reviews added in one commit: every one must pass
        rel = present[b]
        infos.append((rel, c, check_review_text((root / rel).read_text(), sha, fc, c, root)))
    rel, rc, extra = infos[-1]
    return {
        "rule_sha256": sha,
        "freeze_commit": fc,
        "review_path": rel,
        "review_commit": rc,
        "review_sha256": file_sha256(root / rel),
        **extra,
    }


def module_blobs(root: Path = ROOT, paths: tuple[str, ...] = REVIEWED_PATHS) -> dict[str, str]:
    """{path: git blob sha at HEAD} for every file under the reviewed paths (R2: stamped into the results; with the review's clean-tree check these are the code that ran)."""
    out: dict[str, str] = {}
    for line in make_git(root)("ls-tree", "-r", "HEAD", "--", *paths).splitlines():
        meta, _, path = line.partition("\t")
        out[path] = meta.split()[2]
    return out


def code_sha(blobs: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(blobs, sort_keys=True).encode()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PPLAY_MINUTES_VALID freeze / loader-review gate")
    ap.add_argument("cmd", choices=("check",))
    ap.parse_args(argv)
    print(json.dumps(require_review(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
