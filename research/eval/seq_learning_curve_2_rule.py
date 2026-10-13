# ruff: noqa: E501
"""SEQ_LEARNING_CURVE_2: the freeze and loader-review refusals shared by the export stager and the Colab run script (no heavy imports).

Rule: docs/prereg/SEQ_LEARNING_CURVE_2.md. The text above ``=== RESULTS BELOW ===`` must hash to the sha of a commit whose subject is
``prereg(SEQ_LEARNING_CURVE_2): <sha256>`` and whose copy of the document hashes to the same sha.

This is gate item D5. It fixes the hole the parent's ``require_review`` had (a later BLOCKED review deleted or renamed reopened an earlier APPROVED
one; the rule name was a substring test, and ``SEQ_LEARNING_CURVE`` is a substring of this id) by reusing the PPLAY_MINUTES_VALID git primitives
(``add_events``, ``head_reviews``, the freeze hash) instead of the parent's file-existence logic. The lineage selection below is the PPLAY one
(S7/S7b/F5/R1), with three differences: this rule's id, glob and reviewed paths; the ``Rule:`` line must equal ``SEQ_LEARNING_CURVE_2 <sha8>`` as a
WHOLE LINE with nothing after it (PPLAY allowed a trailing parenthetical); and ``Verdict:`` / ``Rule:`` / ``Reviewed-commit:`` / ``Exports-manifest:``
must each occur exactly once (two ``Verdict:`` lines are refused, so a second line cannot be read as the first).

The Colab VM has no git: ``stage`` runs ``require_review`` on the Mac and writes ``loader_review_attestation.json``; the notebook refuses without it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from research.eval.pplay_minutes_valid_rule import (
    SHA40,
    add_events,
    file_sha256,
    git_ok,
    head_reviews,
    is_ancestor,
    make_git,
)

ID = "SEQ_LEARNING_CURVE_2"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/SEQ_LEARNING_CURVE_2.md"
REVIEW_DIR_REL = "docs/reviews"
REVIEW_GLOB = "seq_learning_curve_2_loader_*.md"  # the parent's glob (seq_learning_curve_loader_*) cannot see these files
ATTESTATION = "loader_review_attestation.json"
APPROVED = "APPROVED"  # exact; "APPROVED WITH FIXES" and "BLOCKED" never open the gate
HEADER_KEYS = ("Verdict", "Reviewed-commit", "Rule", "Exports-manifest")
EXPORT_NAMES = tuple(f"seq_learning_curve_2_norm_{s}.npz" for s in ("S1", "S2", "S3"))
STAGE_DIR = ROOT / "data/colab/seq_learning_curve_2"
REVIEWED_PATHS = (
    "research/features/seq_learning_curve_2_export.py",
    "research/colab/jobs/seq_learning_curve_2",
    "research/eval/seq_learning_curve_2_eval.py",
    "research/eval/seq_learning_curve_2_rule.py",
    "research/eval/pplay_minutes_valid_rule.py",  # imported git primitives (a committed change there is a different gate)
    "tests/research/seq_learning_curve_2",
)  # any difference between the review's Reviewed-commit and HEAD (or the working tree) voids the review


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


def frozen_sha(root: Path = ROOT) -> str:
    return text_sha((root / DOC_REL).read_text())


def freeze_commit(sha: str, root: Path = ROOT) -> str | None:
    """The commit whose subject is ``prereg(SEQ_LEARNING_CURVE_2): <sha>`` AND whose copy of the document hashes to ``sha``."""
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
    sha = frozen_sha(root)
    fc = freeze_commit(sha, root)
    if fc is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carries this rule text: the rule on disk is not the frozen one; refusing"
        )
    return sha, fc


def parse_header(text: str) -> dict[str, str]:
    """Each header key's value; a key that occurs on more than one line is refused (``Verdict`` twice would otherwise let the first line win)."""
    out: dict[str, str] = {}
    for key in HEADER_KEYS:
        found = re.findall(rf"^{re.escape(key)}:[ \t]*(.*?)[ \t]*$", text, flags=re.MULTILINE)
        if len(found) > 1:
            raise RuntimeError(
                f"the review has {len(found)} `{key}:` lines; exactly one is allowed; refusing"
            )
        if found:
            out[key] = found[0]
    return out


def manifest_sha(files: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{k} {v}" for k, v in sorted(files.items())).encode()
    ).hexdigest()


def exports_manifest_of(export_dir: Path) -> str:
    shas = {}
    for name in EXPORT_NAMES:
        f = export_dir / name
        if not f.exists():
            raise FileNotFoundError(f"{f} missing: build the exports first")
        shas[name] = file_sha256(f)
    return manifest_sha(shas)


def check_review_text(
    text: str, sha: str, fc: str, rc: str, root: Path, export_dir: Path | None
) -> dict[str, str]:
    """The review's own words must open the gate: exact verdict, this rule on a whole line, code identical to the reviewed commit, the exports it reviewed."""
    h = parse_header(text)
    if h.get("Verdict") != APPROVED:
        raise RuntimeError(
            f"the latest loader review's Verdict is {h.get('Verdict')!r}, not exactly {APPROVED!r}: refusing"
        )
    want_rule = f"{ID} {sha[:8]}"
    if h.get("Rule") != want_rule:
        raise RuntimeError(
            f"the review's Rule line {h.get('Rule')!r} is not exactly `Rule: {want_rule}` (substring and trailing text are refused)"
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
    out = {"reviewed_commit": reviewed, "verdict": APPROVED}
    if export_dir is not None:
        got = exports_manifest_of(export_dir)
        if h.get("Exports-manifest", "") != got:
            raise RuntimeError(
                f"the staged exports' manifest {got[:12]} differs from the review's Exports-manifest {h.get('Exports-manifest', '')[:12]!r}; refusing"
            )
        out["exports_manifest"] = got
    return out


def require_review(
    root: Path = ROOT, review_glob: str = REVIEW_GLOB, export_dir: Path | None = STAGE_DIR
) -> dict[str, str]:
    """Refuse unless the LATEST review ever added under the glob (by adding-commit order, deleted or renamed ones included) descends from the freeze,
    still exists, says ``Verdict: APPROVED`` exactly, names ``SEQ_LEARNING_CURVE_2 <sha8>`` on a whole line, covers HEAD's code and matches the staged exports.

    A deletion or rename of the latest review is a revocation: only a strictly later committed APPROVED review replaces it. A review edited after its
    adding commit is refused outright (a review is immutable; add a new one).
    """
    sha, fc = require_frozen(root)
    git = make_git(root)
    rdir = root / REVIEW_DIR_REL
    fs_rel = {str(p.relative_to(root)) for p in rdir.glob(review_glob)} if rdir.exists() else set()
    head = head_reviews(root, review_glob)
    events = add_events(root, review_glob)
    if not fs_rel and not head and not events:
        raise RuntimeError(
            f"no {REVIEW_DIR_REL}/{review_glob}: the adversary's loader review is a hard stop before any run (rule 'Before the run')"
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
                f"{rel}: edited after its adding commit {la[1][:8]} (a review is immutable once committed; add a new review instead); refusing"
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
        raise RuntimeError(
            "no usable loader review: none was added after the freeze commit " + fc[:8]
        )
    top = max(v[0] for v in cands.values())
    group = sorted((v[2], b, v[1]) for b, v in cands.items() if v[0] == top)
    present = {hb: rel for rel, hb in sorted(head.items(), reverse=True)}
    for p, b, c in group:
        if b not in present:
            raise RuntimeError(
                f"the latest loader review ({p}, added in {c[:8]}) is no longer under {REVIEW_DIR_REL}/{review_glob}: deleted or renamed out of the glob. "
                "A removal is a revocation (counts as BLOCKED); only a strictly later committed review saying APPROVED replaces it; refusing"
            )
    infos: list[tuple[str, str, dict[str, str]]] = []
    for _p, b, c in group:  # several reviews added in one commit: every one must pass
        rel = present[b]
        infos.append(
            (rel, c, check_review_text((root / rel).read_text(), sha, fc, c, root, export_dir))
        )
    rel, rc, extra = infos[-1]
    return {
        "rule_sha256": sha,
        "freeze_commit": fc,
        "review_path": rel,
        "review_commit": rc,
        "review_sha256": file_sha256(root / rel),
        **extra,
    }


def stage(out: Path = STAGE_DIR, root: Path = ROOT) -> dict[str, object]:
    """Mac side, before a Colab push: run ``require_review`` and write the attestation the notebook verifies, plus a copy of the review text."""
    rv = require_review(root, export_dir=out)
    shas = {name: file_sha256(out / name) for name in EXPORT_NAMES}
    (out / "loader_review.md").write_text((root / rv["review_path"]).read_text())
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

    ap = argparse.ArgumentParser(description="SEQ_LEARNING_CURVE_2 freeze / loader-review gate")
    ap.add_argument("cmd", choices=("check", "stage"))
    a = ap.parse_args(argv)
    print(json.dumps(require_review() if a.cmd == "check" else stage(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
