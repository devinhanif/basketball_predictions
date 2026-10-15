# ruff: noqa: E501
"""MEDIAN_BIAS: the freeze and harness-pin refusals (no heavy imports).

Rule: docs/prereg/MEDIAN_BIAS.md. The ALT_RUNG_CAL freeze/pin module with the id changed (this rule never loads season 2025), plus a
harness pin that is checked BEFORE any real outcome is read:

1. ``require_frozen(sha)``: the text above ``=== RESULTS BELOW ===`` hashes to ``--sha`` and a commit whose subject is
   ``prereg(MEDIAN_BIAS): <sha>`` is reachable and carries a copy of the rule that hashes to the same value.
2. ``require_harness_pinned(sha)``: a commit whose subject is ``harness(MEDIAN_BIAS): <pin>`` exists, ``pin`` is the sha256
   of the sorted ``path:blob_sha256`` lines of ``HARNESS_FILES`` in that commit, and the files on disk equal that commit.

Pin commit procedure (done by a hand that can commit, after the adversary has read the harness and the synthetic rehearsal):
    .venv/bin/python -m research.eval.median_bias_rule pin           # prints the pin and the commit command
    git commit --allow-empty -m "harness(MEDIAN_BIAS): <pin>"  # the harness files are already committed
The pin commit must come after the ``prereg(MEDIAN_BIAS)`` commit and before any real outcome is read.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ID = "MEDIAN_BIAS"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/MEDIAN_BIAS.md"
DOC = ROOT / DOC_REL
HARNESS_FILES = (
    "research/eval/median_bias_eval.py",
    "research/eval/median_bias_rule.py",
    "research/eval/median_bias_gate.py",
)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout


def text_sha(text: str) -> str:
    """sha256 of the text above the results marker line (same bytes as the awk one-liner in the rule)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def frozen_sha(doc: Path = DOC) -> str:
    return text_sha(doc.read_text())


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, run: Callable[..., str] = git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    got = frozen_sha(doc)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if freeze_commit(sha, run) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carrying this rule in git log: the rule is not frozen"
        )
    return sha


def pin_of(blobs: dict[str, str]) -> str:
    """The pin: sha256 of the sorted ``path:blob_sha256`` lines."""
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=ROOT).returncode == 0
    )


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    """(commit, pin) of the newest ``harness(MEDIAN_BIAS): <pin>`` commit, or None."""
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.fullmatch(rf"harness\({ID}\): ([0-9a-f]{{64}})", subj.strip())
        if m:
            return h, m.group(1)
    return None


def require_harness_pinned(
    sha: str,
    run: Callable[..., str] = git,
    root: Path = ROOT,
    ok: Callable[..., bool] = git_ok,
) -> dict[str, str]:
    """Refuse unless the harness files on disk equal the blobs of the pin commit, the pin hash matches its subject,
    and the pin commit descends from the freeze commit. Returns the blob sha256s."""
    fc = freeze_commit(sha, run)
    if fc is None:
        raise RuntimeError("the rule is not frozen")
    pc = pin_commit(run)
    if pc is None:
        raise RuntimeError(f"no `harness({ID}): <pin>` commit: the harness is not pinned")
    h, pin = pc
    out: dict[str, str] = {}
    for rel in HARNESS_FILES:
        frozen = run("show", f"{h}:{rel}")
        disk = (root / rel).read_text()
        if frozen != disk:
            raise RuntimeError(f"{rel} differs from its version at pin commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(frozen.encode()).hexdigest()
    if pin_of(out) != pin:
        raise RuntimeError("the pin in the commit subject does not match the harness blobs")
    if not ok("merge-base", "--is-ancestor", fc, h):
        raise RuntimeError("pin commit does not descend from the freeze commit")
    return out


def require_ready(
    sha: str | None, run: Callable[..., str] = git, ok: Callable[..., bool] = git_ok
) -> dict[str, str]:
    """The single gate the real-label entrypoint calls before reading any real outcome."""
    s = require_frozen(sha, None, run)
    return require_harness_pinned(s, run, ROOT, ok)


def a0_pins_declared(root: Path = ROOT) -> dict[str, object]:
    """The A0 reproduction numbers of ``reports/prereg_median_bias/gate.json``, after checking that file against the ``GATE_SHA256`` line of the rule."""
    import json

    gj = root / "reports/prereg_median_bias/gate.json"
    want = declared().get("GATE_SHA256")
    if want is None or file_sha256(gj) != want:
        raise RuntimeError("gate.json does not equal the GATE_SHA256 line pinned in the rule")
    pins = json.loads(gj.read_text()).get("a0_pins")
    if not isinstance(pins, dict):
        raise RuntimeError("gate.json carries no a0_pins (run the gate with --a0-labels)")
    return pins


def declared(doc: Path = DOC) -> dict[str, str]:
    """``NAME: <64 hex>`` lines above the marker (the pinned input hashes)."""
    out: dict[str, str] = {}
    for line in doc.read_text().splitlines():
        if line.rstrip() == MARKER:
            break
        m = re.fullmatch(r"([A-Z0-9_]+): ([0-9a-f]{64})", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args[:1] == ["pin"]:
        blobs = disk_blobs()
        pin = pin_of(blobs)
        for k in sorted(blobs):
            print(f"{blobs[k]}  {k}")
        print(f"pin {pin}")
        print(
            f'git commit --allow-empty -m "harness({ID}): {pin}"  # files already committed; no harness edit between freeze and pin'
        )
        return 0
    print("usage: python -m research.eval.median_bias_rule pin")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
