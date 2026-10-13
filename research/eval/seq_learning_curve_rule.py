# ruff: noqa: E501
"""SEQ_LEARNING_CURVE: the freeze and loader-review refusals shared by the export stager, the Colab run script and the scorer (no heavy imports).

Rule: docs/prereg/SEQ_LEARNING_CURVE.md. The text above ``=== RESULTS BELOW ===`` must hash to the sha of a commit whose subject is
``prereg(SEQ_LEARNING_CURVE): <sha256>`` and whose copy of the document hashes to the same sha (a subject alone proves nothing).

"Before the run" in the rule: an adversary loader review of the export, the notebook and the scorer, committed as
``docs/reviews/seq_learning_curve_loader_<date>.md`` AFTER the freeze commit and BEFORE any Colab run. ``require_review`` refuses unless such a file
exists at HEAD, is tracked and unmodified, and the commit that ADDED it is a strict descendant of the freeze commit; the LATEST such review must also say ``Verdict: APPROVED`` (exactly), name
this rule, carry a ``Reviewed-commit`` whose reviewed paths equal HEAD's, and an ``Exports-manifest`` equal to the staged exports' (fix F1 of the 2026-10-12 loader review). The Colab VM has no git: the Mac-side
``stage`` command writes ``loader_review_attestation.json`` after this check, and the notebook refuses without a matching attestation (see the job tail).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

ID = "SEQ_LEARNING_CURVE"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/SEQ_LEARNING_CURVE.md"
REVIEW_DIR_REL = "docs/reviews"
REVIEW_GLOB = "seq_learning_curve_loader_*.md"
ATTESTATION = "loader_review_attestation.json"

Runner = Callable[..., str]
EXPORT_NAMES = tuple(
    f"seq_learning_curve_{v}_{s}.npz" for v in ("norm", "raw") for s in ("S1", "S2", "S3")
)
STAGE_DIR = ROOT / "data/colab/seq_learning_curve"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (the freeze evidence)."""
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


def is_ancestor(ancestor: str, commit: str, root: Path = ROOT) -> bool:
    """True iff ``ancestor`` is an ancestor of ``commit`` (git merge-base --is-ancestor; equal commits count as ancestors)."""
    r = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, commit],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )
    return r.returncode == 0


def frozen_sha(root: Path = ROOT) -> str:
    return text_sha((root / DOC_REL).read_text())


def freeze_commit(sha: str, root: Path = ROOT) -> str | None:
    """The commit whose subject is ``prereg(SEQ_LEARNING_CURVE): <sha>`` AND whose copy of the document hashes to ``sha``. A superseded freeze (other sha) never matches."""
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


def require_frozen(root: Path = ROOT) -> tuple[str, str]:
    """(sha, freeze commit). The document on disk must still hash to the committed sha."""
    sha = frozen_sha(root)
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("bad rule sha")
    fc = freeze_commit(sha, root)
    if fc is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carries this rule text: the rule on disk is not the frozen one; refusing"
        )
    return sha, fc


REVIEWED_PATHS = (
    "research/features/seq_learning_curve_export.py",
    "research/colab/jobs/seq_learning_curve",
    "research/eval/seq_learning_curve_eval.py",
    "research/eval/seq_learning_curve_rule.py",
    "tests/research/seq_learning_curve",
)  # the code the loader review covers: any difference between the review's Reviewed-commit and HEAD (or the working tree) voids it
APPROVED = "APPROVED"  # exact; "APPROVED WITH FIXES" and "BLOCKED" never open the gate
SHA40 = re.compile(r"[0-9a-f]{40}")


def parse_header(text: str) -> dict[str, str]:
    """First ``Key: value`` line of each header key (Verdict, Reviewed-commit, Rule, Exports-manifest) anywhere in the review text."""
    out: dict[str, str] = {}
    for key in ("Verdict", "Reviewed-commit", "Rule", "Exports-manifest"):
        m = re.search(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", text, flags=re.MULTILINE)
        if m:
            out[key] = m.group(1)
    return out


def git_ok(root: Path, *args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=root).returncode == 0
    )


def exports_manifest_of(export_dir: Path) -> str:
    """The manifest sha of the six staged export npz files (the same formula as ``stage`` and the exporter)."""
    shas = {}
    for name in EXPORT_NAMES:
        f = export_dir / name
        if not f.exists():
            raise FileNotFoundError(
                f"{f} missing: build the exports first (`python -m research.features.seq_learning_curve_export build`)"
            )
        shas[name] = file_sha256(f)
    return manifest_sha(shas)


def check_review_text(
    text: str, sha: str, fc: str, rc: str, root: Path, export_dir: Path
) -> dict[str, str]:
    """The review's own words must open the gate: exact verdict, this rule, code identical to the reviewed commit, the exports it reviewed."""
    h = parse_header(text)
    if h.get("Verdict") != APPROVED:
        raise RuntimeError(
            f"the latest loader review's Verdict is {h.get('Verdict')!r}, not exactly {APPROVED!r}: refusing (a fix-check review with Verdict: APPROVED is required)"
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
    want = h.get("Exports-manifest", "")
    got = exports_manifest_of(export_dir)
    if want != got:
        raise RuntimeError(
            f"the staged exports' manifest {got[:12]} differs from the review's Exports-manifest {want[:12]!r}: the exports are not the ones reviewed; refusing"
        )
    return {"reviewed_commit": reviewed, "exports_manifest": got, "verdict": APPROVED}


def require_review(
    root: Path = ROOT, review_glob: str = REVIEW_GLOB, export_dir: Path = STAGE_DIR
) -> dict[str, str]:
    """Refuse unless the LATEST committed ``docs/reviews/seq_learning_curve_loader_*.md`` that descends from the freeze says ``Verdict: APPROVED`` (exactly), names this rule,
    its ``Reviewed-commit`` matches HEAD on every reviewed path, and its ``Exports-manifest`` equals the staged exports'.

    "Latest" = the review whose adding commit is newest in history (name breaks ties), so a later non-approving review revokes an earlier approval.
    Returns {rule_sha256, freeze_commit, review_path, review_commit, review_sha256, reviewed_commit, exports_manifest, verdict}.
    """
    sha, fc = require_frozen(root)
    git = make_git(root)
    cands = sorted((root / REVIEW_DIR_REL).glob(review_glob))
    if not cands:
        raise RuntimeError(
            f"no {REVIEW_DIR_REL}/{review_glob}: the adversary's loader review is a hard stop before any run (rule 'Before the run')"
        )
    order = {
        h: i for i, h in enumerate(git("rev-list", "--topo-order", "--reverse", "HEAD").split())
    }
    problems: list[str] = []
    eligible: list[tuple[int, str, Path, str]] = []
    for p in cands:
        rel = str(p.relative_to(root))
        if not p.stat().st_size:
            problems.append(f"{rel}: empty")
            continue
        if not git("ls-files", "--error-unmatch", "--", rel).strip():
            problems.append(f"{rel}: not tracked")
            continue
        if git("status", "--porcelain", "--", rel).strip():
            problems.append(f"{rel}: uncommitted changes")
            continue
        added = git("log", "--diff-filter=A", "--format=%H", "--", rel).split()
        if not added:
            problems.append(f"{rel}: not in any commit")
            continue
        rc = added[-1]  # the oldest adding commit
        if rc == fc or not is_ancestor(fc, rc, root):
            problems.append(f"{rel}: commit {rc[:8]} is not a descendant of the freeze {fc[:8]}")
            continue
        eligible.append((order.get(rc, -1), p.name, p, rc))
    if not eligible:
        raise RuntimeError("no usable loader review: " + "; ".join(problems))
    _, _, p, rc = max(eligible, key=lambda t: (t[0], t[1]))
    rel = str(p.relative_to(root))
    extra = check_review_text(p.read_text(), sha, fc, rc, root, export_dir)
    return {
        "rule_sha256": sha,
        "freeze_commit": fc,
        "review_path": rel,
        "review_commit": rc,
        "review_sha256": file_sha256(p),
        **extra,
    }


def manifest_sha(files: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()


def stage(out: Path = STAGE_DIR, root: Path = ROOT) -> dict[str, object]:
    """Mac side, before a Colab push: run ``require_review`` (raises without a committed, post-freeze loader review) and write the attestation the notebook verifies,
    plus a copy of the review text (``loader_review.md``). Records the sha256 of every staged export."""
    rv = require_review(root, export_dir=out)
    shas = {name: file_sha256(out / name) for name in EXPORT_NAMES}
    review_text = (root / rv["review_path"]).read_text()
    (out / "loader_review.md").write_text(review_text)
    att: dict[str, object] = {
        **rv,
        "review_file": "loader_review.md",
        "review_descends_from_freeze": True,
        "exports": shas,
        "exports_manifest_sha256": manifest_sha(shas),
    }
    (out / ATTESTATION).write_text(json.dumps(att, indent=2))
    return att


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="SEQ_LEARNING_CURVE freeze / loader-review gate")
    ap.add_argument("cmd", choices=("check", "stage"))
    a = ap.parse_args(argv)
    res = require_review() if a.cmd == "check" else stage()
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
