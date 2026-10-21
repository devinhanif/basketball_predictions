# ruff: noqa: E501
"""QUESTIONABLE_CASCADE: the rule's constants, refusals and verdict logic (no heavy imports).

Rule: docs/prereg/QUESTIONABLE_CASCADE.md. Everything a result may be judged by lives here and is mirrored from the doc; a test fails if
they disagree. Refusals, in the order the real-label entrypoint applies them:

1. ``require_not_draft(doc)``: the status line of the rule must not say DRAFT (the freeze commit changes it to FROZEN).
2. ``require_frozen(sha)``: the text above ``=== RESULTS BELOW ===`` hashes to ``--sha`` and a commit whose subject is
   ``prereg(QUESTIONABLE_CASCADE): <sha>`` carries a copy of the rule that hashes to the same value.
3. ``require_harness_pinned(sha)``: a commit ``harness(QUESTIONABLE_CASCADE): <pin>`` exists after the freeze and the files on disk equal it.
4. ``require_season(s)``: season 2025 (the frozen holdout) and later are refused everywhere; this rule never earns a 2025 touch.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

ID = "QUESTIONABLE_CASCADE"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/QUESTIONABLE_CASCADE.md"
DOC = ROOT / DOC_REL
HARNESS_FILES = (
    "research/eval/questionable_cascade_eval.py",
    "research/eval/questionable_cascade_features.py",
    "research/eval/questionable_cascade_rule.py",
    "research/eval/questionable_cascade_gate.py",
)

# ------------------------------------------------------------------------------------ the rule's numbers
MAX_SEASON = 2024
SELECT, REPORT = 2023, 2024
SEASONS = (SELECT, REPORT)
STATS = ("pts", "reb", "ast", "fg3m")
FLOOR = -0.005  # integer CRPS, C2 minus A0, affected-teammate rows, per stat
CLOSE_AT = -0.002  # a stat whose report-season point is above this is CLOSED
PL_MARGIN = -0.0025  # C2 minus PL on affected rows must be at least this negative with CI upper < 0
NOHARM_POINT = 0.002  # harm detector on REST rows: harm is declared when the point is >= this AND the binding CI lower bound is > 0 (the gate: REST half-width 0.0056 in one season, so "prove no harm" is unresolvable)
SLICE_TOL = 0.01
MIN_SLICE_N = 300
COV_TAUS = (0.10, 0.20, 0.80, 0.90)
COV_TOL = 0.01
MIN_WEEKS = 20
N_BOOT, SEED = 5000, 2026
BH_Q = 0.05
HW_OWN_DECISIVE = (
    0.005  # own-row contrast is INCONCLUSIVE by construction when its half-width exceeds this
)
#: Permutation-null size of the placebo guard (BEST_PRACTICES 2026-10-12): None until `null --k 50` is run on the select season and the value is
#: written here BEFORE the freeze; the real-label entrypoint refuses while it is None.
PLACEBO_TOL: float | None = None
REPRO_TOL = 1e-6  # heads walk vs stored A0 (the gate's frame reproduction is 1e-9)
DESIGN = "SELECT_REPORT"  # the draft's design; the pooled alternative is an option for Devin (doc, "Options")


# ------------------------------------------------------------------------------------ refusals


def require_season(season: int | float) -> int:
    if int(season) > MAX_SEASON:
        raise RuntimeError(
            f"season {int(season)} > {MAX_SEASON}: season 2025 is the frozen holdout and this rule never touches it"
        )
    return int(season)


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


def status_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Status:"):
            return line
        if line.rstrip() == MARKER:
            break
    return ""


def require_not_draft(doc: Path | None = None) -> None:
    """The status line must START with FROZEN (``Status: **FROZEN ...``); a DRAFT, a missing line or anything else is refused."""
    line = status_line((doc or DOC).read_text())
    if not re.match(r"Status:\s*\**\s*FROZEN", line):
        raise RuntimeError(
            "the rule's status line does not start with FROZEN (a DRAFT is never run on real outcomes)"
        )


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
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=ROOT).returncode == 0
    )


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.fullmatch(rf"harness\({ID}\): ([0-9a-f]{{64}})", subj.strip())
        if m:
            return h, m.group(1)
    return None


def require_harness_pinned(
    sha: str, run: Callable[..., str] = git, root: Path = ROOT, ok: Callable[..., bool] = git_ok
) -> dict[str, str]:
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
        if frozen != (root / rel).read_text():
            raise RuntimeError(f"{rel} differs from its version at pin commit {h[:8]}: refusing")
        out[rel] = hashlib.sha256(frozen.encode()).hexdigest()
    if pin_of(out) != pin:
        raise RuntimeError("the pin in the commit subject does not match the harness blobs")
    if not ok("merge-base", "--is-ancestor", fc, h):
        raise RuntimeError("pin commit does not descend from the freeze commit")
    return out


def require_ready(
    sha: str | None,
    run: Callable[..., str] = git,
    ok: Callable[..., bool] = git_ok,
    doc: Path | None = None,
) -> dict[str, str]:
    """The single gate the real-label entrypoint calls before reading any real outcome."""
    require_not_draft(doc)
    if PLACEBO_TOL is None:
        raise RuntimeError(
            "PLACEBO_TOL is not sized: run `null --k 50`, write it into this module and the doc, then re-pin"
        )
    s = require_frozen(sha, doc, run)
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


# ------------------------------------------------------------------------------------ statistics


class MultiBoot:
    """Paired cluster bootstrap under three clusterings (game, player-season, ISO week) with one multinomial weight matrix each.

    ``ci`` returns the point and, per clustering, the 95% percentile CI and the one-sided p of 'mean < 0'; the BINDING interval is the envelope
    [min lo, max hi] over the DECISIVE clusterings (week only with >= ``MIN_WEEKS`` weeks), and the binding p is the worst (largest)."""

    def __init__(self, keys: dict[str, np.ndarray], seed: int = SEED, b: int = N_BOOT) -> None:
        self.codes: dict[str, np.ndarray] = {}
        self.n: dict[str, int] = {}
        self.W: dict[str, np.ndarray] = {}
        self.B = b
        for i, (name, key) in enumerate(keys.items()):
            u, code = np.unique(np.asarray(key).astype(str), return_inverse=True)
            self.codes[name], self.n[name] = code, len(u)
            self.W[name] = (
                np.random.default_rng([seed, i])
                .multinomial(len(u), np.full(len(u), 1.0 / len(u)), size=b)
                .astype(float)
            )

    def ci(self, v: np.ndarray, m: np.ndarray) -> dict[str, Any]:
        k = m & np.isfinite(v)
        n = int(k.sum())
        if n == 0:
            return {
                "point": float("nan"),
                "lo": float("nan"),
                "hi": float("nan"),
                "p": float("nan"),
                "n": 0,
                "per": {},
            }
        per: dict[str, dict[str, float]] = {}
        for name, code in self.codes.items():
            s = np.bincount(code[k], weights=v[k], minlength=self.n[name])
            c = np.bincount(code[k], minlength=self.n[name]).astype(float)
            den = self.W[name] @ c
            rep = (self.W[name] @ s) / np.where(den > 0, den, np.nan)
            lo, hi = np.nanpercentile(rep, [2.5, 97.5])
            per[name] = {
                "lo": float(lo),
                "hi": float(hi),
                "p": float(max(np.nanmean(rep >= 0.0), 1.0 / self.B)),
                "clusters": float(self.n[name]),
                "decisive": float(name != "week" or self.n[name] >= MIN_WEEKS),
            }
        dec = [x for x in per.values() if x["decisive"]]
        return {
            "point": float(v[k].sum() / n),
            "lo": min(x["lo"] for x in dec),
            "hi": max(x["hi"] for x in dec),
            "p": max(x["p"] for x in dec),
            "n": n,
            "per": per,
        }


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, dtype=float)
    n = len(p)
    order = np.argsort(p)
    adj = np.empty(n)
    run = 1.0
    for rank, idx in zip(range(n, 0, -1), order[::-1], strict=True):
        run = min(run, p[idx] * n / rank)
        adj[idx] = run
    return adj


# ------------------------------------------------------------------------------------ the words


def decide(sc: dict[str, Any]) -> dict[str, Any]:
    """The rule's words from ``score_arms`` output. Per stat, season 2024 is the verdict venue; 2023 only supplies the sign.

    PASS (stat) needs ALL of: (1) C2 minus A0 on affected-teammate rows: point <= FLOOR and binding CI upper < 0; (2) BH over the four stats (one-sided
    binding p) <= BH_Q; (3) the select season's point is negative; (4) C2 minus PL: point <= PL_MARGIN with CI upper < 0; (5) the placebo does not itself
    win (PL minus CTRL not <= PLACEBO_TOL with CI upper < 0); (6) no harm demonstrated: NOT (C2 minus A0 on REST rows has point >= NOHARM_POINT and binding CI lower > 0); (7) mid-coverage at the four taus
    moves by at most COV_TOL against A0 (all rows); (8) no slice with n >= MIN_SLICE_N is worse than +SLICE_TOL.
    CLOSED = report-season point > CLOSE_AT. INCONCLUSIVE = otherwise. GUARD_FAIL = (1)-(3) hold, a guard (4)-(8) fails.
    The own-row contrast (C1 minus A0 on tagged Q/D players' played rows) is reported with its own word and never enters the stat's word:
    OWN_INCONCLUSIVE when its half-width exceeds HW_OWN_DECISIVE, else OWN_PASS / OWN_NULL by the same floor test."""
    words: dict[str, str] = {}
    detail: dict[str, Any] = {}
    stats = [s for s in STATS if s in sc]
    pvals = np.array([sc[s][str(REPORT)]["C2_minus_A0_teammate"]["p"] for s in stats])
    padj = bh_adjust(pvals) if len(stats) else np.array([])
    for s, pa in zip(stats, padj, strict=True):
        r = sc[s][str(REPORT)]
        b = r["C2_minus_A0_teammate"]
        first = b["point"] <= FLOOR and b["hi"] < 0
        sel = sc[s][str(SELECT)]["C2_minus_A0_teammate"]["point"] < 0
        g: dict[str, bool] = {}
        pl_ = r.get("C2_minus_PL_teammate")
        g["placebo_margin"] = bool(pl_ is not None and pl_["point"] <= PL_MARGIN and pl_["hi"] < 0)
        nz = r.get("PL_minus_CTRL_teammate")
        g["placebo_not_winning"] = not (
            nz is not None
            and PLACEBO_TOL is not None
            and nz["point"] <= PLACEBO_TOL
            and nz["hi"] < 0
        )
        g["no_harm_rest"] = not (
            r["C2_minus_A0_rest"]["lo"] > 0 and r["C2_minus_A0_rest"]["point"] >= NOHARM_POINT
        )
        cov = r.get("coverage_A0_C2")
        g["coverage"] = bool(
            cov is None
            or all(abs(v[1] - float(t)) - abs(v[0] - float(t)) <= COV_TOL for t, v in cov.items())
        )
        g["slices"] = not [
            k
            for k, v in r["slices"].items()
            if v.get("reported", True) and v.get("point") is not None and v["point"] > SLICE_TOL
        ]
        if first and pa <= BH_Q and sel:
            word = "PASS" if all(g.values()) else "GUARD_FAIL"
        elif b["point"] > CLOSE_AT:
            word = "CLOSED"
        else:
            word = "INCONCLUSIVE"
        words[s] = word
        own = r["C1_minus_A0_own"]
        hw = (own["hi"] - own["lo"]) / 2.0 if own["n"] else float("inf")
        own_word = (
            "OWN_INCONCLUSIVE"
            if hw > HW_OWN_DECISIVE
            else ("OWN_PASS" if own["point"] <= FLOOR and own["hi"] < 0 else "OWN_NULL")
        )
        detail[s] = {
            "first_condition": bool(first),
            "bh_adjusted_p": float(pa),
            "select_sign_negative": bool(sel),
            "guards": g,
            "own_row_word": own_word,
        }
    overall = (
        "PASS"
        if "PASS" in words.values()
        else (
            "GUARD_FAIL"
            if "GUARD_FAIL" in words.values()
            else ("INCONCLUSIVE" if "INCONCLUSIVE" in words.values() else "CLOSED")
        )
    )
    return {"overall": overall, "words": words, "detail": detail, "design": DESIGN}


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
    print("usage: python -m research.eval.questionable_cascade_rule pin")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
