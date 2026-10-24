# ruff: noqa: E501
"""MARKET_ANCHORED_PROPS: frozen constants and refusals, mirrored from docs/prereg/MARKET_ANCHORED_PROPS.md (frozen f7691bbf).

The doc wins on any disagreement. ``check_mirror`` compares every constant below with the pinned core
(``market_anchored_props_core``) and raises on a difference, so a drift in either is a refusal, not a silent change.

Refusals before any label is read (rule header, "The run refuses ..."):
1. ``require_frozen(sha)``: the doc is not DRAFT, its text above ``=== RESULTS BELOW ===`` hashes to ``sha``, and a commit whose
   subject starts ``prereg(MARKET_ANCHORED_PROPS): <sha>`` is reachable and carries a copy of the doc that hashes to ``sha``
   (the real freeze commit f52a974 has the subject suffix `` FREEZE``; see ``FREEZE_SUBJECT``).
2. ``require_harness_pinned(sha)``: (a) the files that existed at the freeze commit (``FROZEN_HARNESS``) equal their blobs there;
   (b) every harness file (``HARNESS_FILES``, including this file and the eval, written after the freeze) equals the blobs of the
   newest ``harness(MARKET_ANCHORED_PROPS): <pin>`` commit, ``pin`` is ``pin_of`` those blobs, and that commit descends from the
   freeze commit. The pin commit is made by a hand that can commit, after the adversary has checked the eval against the text.
3. ``assert_seasons``: any season > 2024 raises (2025 is the frozen holdout).
4. (in the eval, before any label) the pairs, extras, model_rows and the two label-free extracts (Part L rungs, exchange quotes)
   equal their pins below; the run never opens the odds database (eval review 2026-10-24, F2 / F3).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ID = "MARKET_ANCHORED_PROPS"
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/MARKET_ANCHORED_PROPS.md"
DOC = ROOT / DOC_REL
FROZEN_SHA256 = "f7691bbfade479ef2400faf43820b5e5923c483d9de52d1c4423f50c15aa0091"
FREEZE_SUBJECT = re.compile(rf"^prereg\({ID}\): ([0-9a-f]{{64}})(\s.*)?$")

# ---- seasons (section 6) ----
SELECT_SEASON = 2023
REPORT_SEASON = 2024
MAX_SEASON = 2024  # 2025 is never touched, loaded or counted

# ---- legs and columns (section 3, 14) ----
STATS: tuple[str, ...] = ("pts", "reb", "ast", "fg3m")
HI_LINE = {"pts": 24.5, "reb": 9.5, "ast": 6.5, "fg3m": 2.5}
DCLIP = 3.0
U1CLIP = 1.0
TMOUT_MIN = 3
MODEL_CLIP = 0.5 / 199
P_CLIP = 1e-6

# ---- fitting (section 3, 14) ----
LAM = 50.0
N_MIN_TRAIN = 3000
HALF_LIFE_DAYS = 365.0
K_PLACEBO = 50
PLACEBO_SEED0 = 1000  # draw k uses default_rng(1000 + k)
N_PARAMS = {"B0": 8, "MR": 15, "MD": 7, "MM": 22}

# ---- inference and words (section 4) ----
N_BOOT = 2000
BOOT_SEED = 0
CLUSTERINGS: tuple[str, ...] = ("game", "player_season", "week")
INFO_FLOOR = -0.001
BET_FLOOR = -0.003
WORSE_FLOOR = 0.0005
K1_SELECT_LIMIT = 0.0010
BH_ALPHA = 0.05
FAMILY_SIZE = 10
SLICE_MIN_N = 300
NOT_EVALUATED = "NOT_EVALUATED"
MODEL_WORDS = (
    "NONE",
    "PLACEBO_EXPLAINS",
    "FAIL_SELECT_SIGN",
    "BET_FLOOR_PASS",
    "INFO",
    "DETECTED_BELOW_INFO_FLOOR",
)
MARKET_WORDS = (
    "NONE", "MODEL_EXPLAINS", "FAIL_SELECT_SIGN", "MISCALIBRATION_INFO", "MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR",
)  # fmt: skip
RUN_WORDS = ("NO_RESULT_INVALID_RUN", "KILLED_K1", "WORSE_THAN_MARKET")
MODEL_CONSEQUENCE_WORDS = ("DETECTED_BELOW_INFO_FLOOR", "INFO", "BET_FLOOR_PASS")

# ---- counts the run must reproduce (section 6, 8; MARKET_PRIOR_RESIDUAL G2) ----
LEGS = {
    2023: {"pooled": 35146, "pts": 12189, "reb": 6339, "ast": 6714, "fg3m": 9904},
    2024: {"pooled": 37528, "pts": 13021, "reb": 6645, "ast": 6771, "fg3m": 11091},
}
SCORED = {2023: 28108, 2024: 37528}

# ---- pinned inputs (section 8) ----
PAIRS_PINNED_SHA256 = {
    2023: "050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569",
    2024: "560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c",
}
EXTRAS_PINNED_SHA256 = {
    2023: "9cf98865d6fe7744f73b8532dc1411e39fff5eedbc96e3153e53b34e3ddf6c1f",
    2024: "aac40cd57fff58d6c20b654c44a254cf7959305f844614a0ed07d19d6aa8fabc",
}
MODEL_ROWS_PINNED_SHA256 = {  # review F3 / ruling #3: reports/odds_history/model_rows_{season}.parquet (git-ignored, 2026-10-10)
    2023: "002e53ef1a5fae333e8ff6edc92654de9f449d29c9be1775017e65fc378eea85",
    2024: "ec47dcdf566eac29742f8269b8c3cf4a10845440c9b298b41a40282577b62d70",
}
DESCRIPTIVE_FILES = {  # review F2 / ruling #2: label-free extracts from a byte copy of the odds database, committed
    "part_l_rungs": "reports/prereg_market_anchored_props/part_l_rungs.parquet",
    "exchange_quotes": "reports/prereg_market_anchored_props/exchange_quotes.parquet",
}
DESCRIPTIVE_PINNED_SHA256 = {
    "part_l_rungs": "1412e09de82ea7c0c9ec9600a63fd5da92f65cf01b91392aceb643259b32f278",  # 210,434 rungs
    "exchange_quotes": "befa3c30fc44b950c8e44375f73561d5d915fbef717a2a3ea125a12b0e52b5ae",  # 27,700 quotes
}  # extracted 2026-10-24 01:29 CDT from byte copy 62f68cc0...; a second extraction gave the same bytes
WEEK_DEFINITION = "iso (iso_year-week of game_date; rule sections 4 and 14; review ruling #1)"
GATE_JSON = "reports/prereg_market_anchored_props/gate.json"
GATE_SETTINGS = {"k_null": 20, "boot": 1000, "reps": 10}  # section 8: the committed gate run
GATE_SEEDS = {"world_labels": 20261023, "boot": 0, "placebo": 1000}

# ---- secondary, slices, Part L (section 4) ----
EXCHANGE_BOOKS = ("novig", "prophetx")
EXCHANGE_FIRST_DAY = "2024-12-19"
PART_L_BOOK = "fanduel"
PART_L_MARKETS = {
    "pts": "player_points_alternate",
    "reb": "player_rebounds_alternate",
    "ast": "player_assists_alternate",
    "fg3m": "player_threes_alternate",
}

# ---- harness pin ----
FROZEN_HARNESS = (
    "research/eval/market_anchored_props_core.py",
    "research/eval/market_anchored_props_gate.py",
    "research/eval/market_prior_residual_eval.py",
    "research/eval/market_prior_residual_rule.py",
    "research/eval/model_clv_eval.py",
)
HARNESS_FILES = (
    *FROZEN_HARNESS,
    "research/eval/market_anchored_props_rule.py",
    "research/eval/market_anchored_props_eval.py",
)


def assert_seasons(seasons: tuple[int, ...] | list[int]) -> None:
    for s in seasons:
        if int(s) > MAX_SEASON:
            raise ValueError(
                f"season {s} > {MAX_SEASON}: 2025 is the frozen holdout; this rule never touches it"
            )


def check_mirror(core: Any) -> None:
    """Raise if any constant of the pinned core differs from the frozen text's value mirrored here."""
    pairs = {
        "STATS": (core.STATS, STATS), "HI_LINE": (core.HI_LINE, HI_LINE), "DCLIP": (core.DCLIP, DCLIP),
        "U1CLIP": (core.U1CLIP, U1CLIP), "TMOUT_MIN": (core.TMOUT_MIN, TMOUT_MIN), "LAM": (core.LAM, LAM),
        "N_MIN_TRAIN": (core.N_MIN_TRAIN, N_MIN_TRAIN), "HALF_LIFE_DAYS": (core.HALF_LIFE_DAYS, HALF_LIFE_DAYS),
        "K_PLACEBO": (core.K_PLACEBO, K_PLACEBO), "INFO_FLOOR": (core.INFO_FLOOR, INFO_FLOOR),
        "BET_FLOOR": (core.BET_FLOOR, BET_FLOOR), "WORSE_FLOOR": (core.WORSE_FLOOR, WORSE_FLOOR),
        "MODEL_CLIP": (core.MODEL_CLIP, MODEL_CLIP), "K1_SELECT_LIMIT": (core.K1_SELECT_LIMIT, K1_SELECT_LIMIT),
        "NOT_EVALUATED": (core.NOT_EVALUATED, NOT_EVALUATED),
        "EXTRAS_PINNED_SHA256": (core.EXTRAS_PINNED_SHA256, EXTRAS_PINNED_SHA256),
        "MR columns": (len(core.MR_COLS), N_PARAMS["MR"]), "MD columns": (len(core.MM_COLS), N_PARAMS["MD"]),
        "MM columns": (len(core.MR_COLS) + len(core.MM_COLS), N_PARAMS["MM"]),
        "B0 start": (len(core.BETA0_B0), N_PARAMS["B0"]),
    }  # fmt: skip
    bad = {k: (a, b) for k, (a, b) in pairs.items() if a != b}
    if bad:
        raise RuntimeError(f"core constants differ from the frozen text: {bad}")


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


def git_ok(*args: str) -> bool:
    return (
        subprocess.run(["git", *args], capture_output=True, check=False, cwd=ROOT).returncode == 0
    )


def text_sha(text: str) -> str:
    """sha256 of the text above the marker line (the bytes of ``awk '/^=== RESULTS BELOW ===$/{exit} {print}'``)."""
    keep: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.rstrip("\n") == MARKER:
            break
        keep.append(line)
    return hashlib.sha256("".join(keep).encode()).hexdigest()


def doc_status(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("Status:"):
            return line
    return "Status: MISSING"


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = FREEZE_SUBJECT.match(subj.strip())
        if m and m.group(1) == sha and text_sha(run("show", f"{h}:{DOC_REL}")) == sha:
            return h
    return None


def require_frozen(sha: str | None, doc: Path | None = None, run: Callable[..., str] = git) -> str:
    doc = doc or DOC
    if not sha or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise RuntimeError("--sha <64 hex sha256 of the committed rule> is required")
    text = doc.read_text()
    st = doc_status(text)
    if "DRAFT" in st or "FROZEN" not in st:
        raise RuntimeError(f"{doc.name} is not frozen ({st[:80]!r}): refusing to run")
    got = text_sha(text)
    if got != sha:
        raise RuntimeError(
            f"{doc} text above the marker hashes to {got[:8]}, not {sha[:8]}: not the frozen rule"
        )
    if sha != FROZEN_SHA256:
        raise RuntimeError(
            f"--sha {sha[:8]} is not the frozen sha this harness mirrors ({FROZEN_SHA256[:8]})"
        )
    if freeze_commit(sha, run) is None:
        raise RuntimeError(
            f"no commit `prereg({ID}): {sha}` carrying this rule in git log: the rule is not frozen"
        )
    return sha


def pin_of(blobs: dict[str, str]) -> str:
    """The harness pin: sha256 of the sorted ``path:blob_sha256`` lines."""
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    """(commit, pin) of the newest ``harness(MARKET_ANCHORED_PROPS): <pin>`` commit, or None."""
    for line in run("log", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        m = re.fullmatch(rf"harness\({ID}\): ([0-9a-f]{{64}})", subj.strip())
        if m:
            return h, m.group(1)
    return None


def require_harness_pinned(
    sha: str, run: Callable[..., str] = git, root: Path = ROOT, ok: Callable[..., bool] = git_ok
) -> dict[str, str]:
    """Refuse unless (a) the frozen-era files equal their blobs at the freeze commit and (b) every harness file equals the
    newest harness pin commit, whose subject pin matches the blobs and which descends from the freeze commit."""
    fc = freeze_commit(sha, run)
    if fc is None:
        raise RuntimeError("the rule is not frozen")
    for rel in FROZEN_HARNESS:
        if run("show", f"{fc}:{rel}") != (root / rel).read_text():
            raise RuntimeError(
                f"{rel} differs from its blob at the freeze commit {fc[:8]}: refusing"
            )
    pc = pin_commit(run)
    if pc is None:
        raise RuntimeError(
            f"no `harness({ID}): <pin>` commit: the eval is not pinned (adversary check first)"
        )
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
        raise RuntimeError("the harness pin commit does not descend from the freeze commit")
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m research.eval.market_anchored_props_rule pin``: print the pin of the files on disk and the commit command."""
    args = sys.argv[1:] if argv is None else argv
    if args[:1] != ["pin"]:
        print("usage: python -m research.eval.market_anchored_props_rule pin")
        return 2
    blobs = disk_blobs()
    p = pin_of(blobs)
    for k in sorted(blobs):
        print(f"{k}:{blobs[k]}")
    print(f'\ngit add {" ".join(HARNESS_FILES)} && git commit -m "harness({ID}): {p}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
