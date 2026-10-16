# ruff: noqa: E501
"""WOWY_STRUCT: the freeze and harness-pin refusals (no heavy imports).

Rule: docs/prereg/WOWY_STRUCT.md. The F1_WARM_WINDOW freeze/pin module with the id changed, plus a pin over EVERY file that can move a number
(``HARNESS_FILES``: the harness, the gate, the features and the transitive import closure inside ``nba/`` and ``research/``, the two configs the
harness reads, and F1's own files, because arm A0_warm IS F1's arm C). Season 2025 is never loaded by anything pinned here.

1. ``require_frozen(sha)``: the text above ``=== RESULTS BELOW ===`` hashes to ``--sha`` and a commit whose subject is
   ``prereg(WOWY_STRUCT): <sha>`` is reachable and carries a copy of the rule that hashes to the same value.
2. ``require_harness_pinned(sha)``: a commit whose subject is ``harness(WOWY_STRUCT): <pin>`` exists, ``pin`` is the sha256 of the sorted
   ``path:blob_sha256`` lines of ``HARNESS_FILES`` in that commit, the files on disk equal that commit, and it descends from the freeze commit.
3. ``require_ready(sha)``: both, called by the real-label entrypoint BEFORE any outcome is read.

Freeze and pin (a hand that can commit; the adversary re-checks first; the pin commit comes after the prereg commit, before any fit):
    .venv/bin/python -m research.eval.wowy_struct_rule freeze     # prints the rule sha256 and the prereg commit command
    git commit --allow-empty -m "prereg(WOWY_STRUCT): <sha>"      # the rule file is already committed, unchanged above the marker
    .venv/bin/python -m research.eval.wowy_struct_rule pin        # prints the pin and the harness commit command
    git commit --allow-empty -m "harness(WOWY_STRUCT): <pin>"     # no harness edit between the two commits
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ID = "WOWY_STRUCT"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/WOWY_STRUCT.md"
DOC = ROOT / DOC_REL
# Every file whose bytes can move a number of this rule. Kept equal to the import closure of the harness by
# tests/research/wowy_struct/test_wowy_struct_rule.py::test_pin_covers_import_closure.
HARNESS_FILES: tuple[str, ...] = (
    "configs/context_residual.yaml",
    "configs/mov_elo_tuned.yaml",
    "nba/db/connect.py",
    "nba/eval/context_residual_eval.py",
    "nba/eval/lower_tail_eval.py",
    "nba/features/game_context.py",
    "nba/features/game_tipoff.py",
    "nba/features/injury_report.py",
    "nba/features/player_pedigree.py",
    "nba/features/sb_classification.py",
    "nba/features/shrinkage.py",
    "nba/features/team_features.py",
    "nba/ingest/arenas.py",
    "nba/ingest/cache.py",
    "nba/ingest/game_window.py",
    "nba/markets/decision_scoring.py",
    "nba/models/base.py",
    "nba/models/common.py",
    "nba/models/rung0_baselines.py",
    "nba/props/baselines.py",
    "nba/props/config.py",
    "nba/props/conformal.py",
    "nba/props/context_residual.py",
    "nba/props/distributions.py",
    "nba/props/forward.py",
    "nba/props/full_support.py",
    "nba/props/lineup_features.py",
    "nba/props/lower_tail.py",
    "nba/props/metrics.py",
    "nba/props/minutes.py",
    "nba/props/repl_delta.py",
    "nba/props/role_change.py",
    "nba/props/rookie_minutes.py",
    "nba/props/roster_cold.py",
    "nba/props/rosters.py",
    "nba/props/struct_alloc.py",
    "nba/props/wowy.py",
    "nba/props/wowy_repl.py",
    "nba/truth/metrics.py",
    "research/budget/asof.py",
    "research/budget/constraint.py",
    "research/budget/status.py",
    "research/eval/f11_lineup_context.py",
    "research/eval/f1_warm_window_gate.py",
    "research/eval/f1_warm_window_rule.py",
    "research/eval/f1_warm_window_run.py",
    "research/eval/four_factors_eval.py",
    "research/eval/minutes_hazard_eval.py",
    "research/eval/minutes_v2_eval.py",
    "research/eval/pi_recent_role_eval.py",
    "research/eval/pplay_context_eval.py",
    "research/eval/prereg_gates_common.py",
    "research/eval/recent_role_eval.py",
    "research/eval/recent_role_full_eval.py",
    "research/eval/recent_role_holdout.py",
    "research/eval/recent_role_props.py",
    "research/eval/team_budget_eval.py",
    "research/eval/team_budget_gates.py",
    "research/eval/workload_eval.py",
    "research/eval/workload_gates.py",
    "research/eval/wowy_redistribution_gate.py",
    "research/eval/wowy_redistribution_slices.py",
    "research/eval/wowy_struct_crps_null.py",
    "research/eval/wowy_struct_eval.py",
    "research/eval/wowy_struct_gate.py",
    "research/eval/wowy_struct_repl_gate.py",
    "research/eval/wowy_struct_rule.py",
    "research/features/four_factors.py",
    "research/features/game_location_context.py",
    "research/features/injury_reason.py",
    "research/minutes/hazard.py",
    "research/props/minutes_v2.py",
    "research/recent_role/gates.py",
    "research/recent_role/placebo.py",
    "research/recent_role/prod_state.py",
    "research/workload/asof.py",
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
    """(commit, pin) of the newest ``harness(WOWY_STRUCT): <pin>`` commit, or None."""
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
    if args[:1] == ["freeze"]:
        sha = frozen_sha()
        print(f"rule sha256 {sha}")
        print(
            f'git commit --allow-empty -m "prereg({ID}): {sha}"  # {DOC_REL} committed unchanged above the marker'
        )
        return 0
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
    print("usage: python -m research.eval.wowy_struct_rule freeze|pin")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
