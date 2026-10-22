# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL rule constants and pure functions (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

Devin's override of the T506 close (DECISIONS 2026-10-21 20:10): the T506 features and arm (research/eval/archetype_matchup_*) are reused
unchanged, on all four stats at once, with ONE composite primary metric. Nothing here reads an outcome or a database. Season convention:
``season`` = start year (2023 = 2023-24). Season 2025 is the frozen holdout and is never joined to game data by any module of this rule.
Every number the gate compares against is a constant below, fixed in code before the gate was computed.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from research.eval import archetype_matchup_rule as t506
from research.eval import matchup_availability_rule as t505
from research.eval import role_prior_salary_rule as role_rule

ID = "ARCHETYPE_MATCHUP_ALL"
SELECT = t506.SELECT
REPORT = t506.REPORT
MAX_SEASON = t506.MAX_SEASON
STATS = ("pts", "reb", "ast", "fg3m")
# Project floors. T505/T506 carry pts, ast, fg3m only (no reb); reb is the project floor of role_prior_salary_rule (0.003).
FLOORS = {"pts": 0.005, "reb": 0.003, "ast": 0.002, "fg3m": 0.001}
N_BOOT = t506.N_BOOT
SEED = t506.SEED
ALPHA = t506.ALPHA
NOISE_SIGMA = t506.NOISE_SIGMA
MIN_ROWS = t506.MIN_ROWS
MIN_PLAYER_SEASONS = t506.MIN_PLAYER_SEASONS
MIN_GAMES = t506.MIN_GAMES
MIN_WEEKS = t506.MIN_WEEKS
FEATURES = t506.FEATURES
is_regular = t506.is_regular
check_season = t506.check_season

# --- the primary metric and its pass rule (all in floor units; CM = mean over stats of mean_row(arm - A0) / floor_s)
CM_PASS = -1.0  # composite point must be at most this
HURT_MAX = (
    1.0  # no stat's own delta, in floor units, may exceed this (no stat may be hurt to buy another)
)
TOP5_SHARE_MAX = t506.TOP5_SHARE_MAX  # 0.5: top-5 player-season share of the composite gain
SHUFFLE_KEEP_MAX = 0.5  # the MEAN of the N_SHUFFLE shuffled refits may keep strictly less than this share of the composite gain
# --- Amendment 2 (pre-freeze, from the adversary review ARCHETYPE_MATCHUP_ALL_prefreeze_2026-10-21): every constant below is fixed before any
# outcome of this rule exists.
MAX_STAT_SHARE = 0.5  # M1 (g): no single stat's share of the 2024 CM may exceed this (the composite may not be one stat's claim)
N_SHUFFLE = 5  # S1: shuffle seeds 0..4 of the whole refit, averaged
SHUFFLE_SEEDS = tuple(range(N_SHUFFLE))
IDENT_KEEP_MAX = 0.5  # S2: the identity-only arm {off_arch, e_lg} may keep strictly less than this share of M's 2024 composite gain
KNOCKOUT_KEEP_MIN = 0.5  # M4: a minutes-tracking column whose block knockout keeps less than this share of the gain voids the claim
MINUTES_LEAK_GAP = 0.30  # M4: neutral-share gap, realised minutes < 5 vs >= 28, at which a column "predicts realised minutes"
MINUTES_LEAK_EXCESS = 0.10  # M4: ... AND that gap exceeds the same gap on the pre-tip min10 buckets by this much (tracks tonight, not the pre-game role)
ALIGN_MIN_AGREE = 0.80  # M2: offense archetype labels, 2024 aligned to 2023, must agree for at least this share of players clustered in both seasons

# --- arm M constants (M3): LightGBM layer, one per stat
N_LEAVES = 4
N_TREES = 150
LEARNING_RATE = 0.05
MIN_DATA_IN_LEAF = 20
LGBM_SEED = 0
LAMBDA_CLIP = (
    0.5,
    2.0,
)  # lambda = (mean + prediction) / mean, clipped here, then the grid is re-served on the integer grid
MEAN_FLOOR = 0.05  # rows whose A0 mean is below this get lambda = 1 (a scale change of a near-zero grid moves nothing; avoids dividing by ~0)
MIN_TRAIN_ROWS = (
    200  # a month with fewer earlier-month training rows than this is served as A0 (lambda = 1)
)
CAT_COLS = (
    "off_arch",
)  # passed to LightGBM as a categorical feature; every other feature is numeric
BOOST_FROM_AVERAGE = True  # the layer starts from the training mean of (actual - A0 mean): a global recalibration is part of arm M, by T506's definition
# knockout blocks (refit identically, descriptive except where primary_verdict says otherwise)
KNOCKOUTS: dict[str, tuple[str, ...]] = {
    "identity": ("off_arch", "e_usual"),  # S2: mandatory; e_usual is 96% explained by off_arch
    "shift": ("delta_rate", "shift_l1", "retained"),
    "unknown": ("def_unknown_share",),
    "all_features": FEATURES,  # nothing left: the pure recalibration (constant prediction = training mean residual)
}
IDENTITY_ONLY_COLS = (
    "off_arch",
    "e_lg",
)  # S2 identity-only arm: A0 plus the scorer's role and his league-average expected rate
BLOCK_OF = {
    "off_arch": "identity",
    "e_usual": "identity",
    "delta_rate": "shift",
    "shift_l1": "shift",
    "retained": "shift",
    "def_unknown_share": "unknown",
}

# --- minimum-data gate (label-free; thresholds fixed here before any number of this rule was computed)
N_SEEDS = 20  # independent noise seeds averaged for the detectable effect (issue #25: one seed moves by tens of percent)
GATE_DE_BAR = 1.0  # mean over seeds of the binding 95% half-width of the composite null-noise contrast, floor units, both seasons
POS_SEEDS = 5  # positive control seeds; EVERY one must clear
POS_SIGMA = 0.20  # planted sd of log lambda per player-season, all four stats; oracle arm
POS_SIGMA_DESC = 0.10  # descriptive only


def check_floors() -> None:
    for s in ("pts", "ast", "fg3m"):
        assert FLOORS[s] == t505.FLOORS[s]
    for s in STATS:
        assert FLOORS[s] == role_rule.FLOORS[s]


def noise_seed(stat: str, k: int) -> int:
    """Per-stat, per-seed-index rng seed of the null-noise arm: independent streams across stats."""
    return 1000 * (STATS.index(stat) + 1) + k


def pos_seed(stat: str, k: int) -> int:
    return 5000 + 1000 * STATS.index(stat) + k


def floor_units(delta_mean: float, stat: str) -> float:
    return float(delta_mean) / FLOORS[stat]


def composite_rows(deltas: dict[str, np.ndarray]) -> np.ndarray:
    """Per-row composite: mean over the four stats of (arm - A0) / floor_s. Same rows in every stat (the intersection); the mean of this
    vector is CM, and bootstrapping it resamples the same clusters in all four stats at once."""
    assert tuple(deltas) == STATS, "composite needs exactly the four stats, in rule order"
    lens = {len(v) for v in deltas.values()}
    assert len(lens) == 1, "stats must be scored on the same rows"
    return np.asarray(sum(deltas[s] / FLOORS[s] for s in STATS) / len(STATS))


def stat_parts(deltas: dict[str, np.ndarray]) -> dict[str, float]:
    """Each stat's contribution to CM, in floor units (parts sum to CM)."""
    return {s: float(np.mean(deltas[s]) / FLOORS[s] / len(STATS)) for s in STATS}


def stat_shares(parts: dict[str, float]) -> dict[str, float | None]:
    """Each stat's share of CM; None when CM is zero. Reported so the composite cannot be dominated silently."""
    cm = sum(parts.values())
    return {s: (None if cm == 0 else parts[s] / cm) for s in STATS}


def keep_share(cm_arm: float, cm_m: float) -> float | None:
    """Share of M's composite gain that another arm keeps: cm_arm / cm_m (both negative when both gain). None when M has no gain."""
    return None if cm_m >= 0 else float(cm_arm / cm_m)


def minutes_leak(
    audit: dict[str, dict[str, float]], keeps: dict[str, float | None]
) -> dict[str, Any]:
    """M4 (ii), in numbers. ``audit[col]`` = {gap_realised, gap_pre}: the neutral-share gap of the column between realised minutes < 5 and
    >= 28, and the same gap between pre-tip min10 < 10 and >= 28 (2024 rows). A column PREDICTS REALISED MINUTES (the 10-08 NULL-pattern
    signature) when gap_realised >= MINUTES_LEAK_GAP and gap_realised - gap_pre >= MINUTES_LEAK_EXCESS. It CARRIES THE GAIN when the
    knockout of its block (BLOCK_OF) keeps less than KNOCKOUT_KEEP_MIN of M's 2024 gain. Both together void the claim."""
    flagged = []
    for col, a in audit.items():
        tracks = (
            a["gap_realised"] >= MINUTES_LEAK_GAP
            and a["gap_realised"] - a["gap_pre"] >= MINUTES_LEAK_EXCESS
        )
        k = keeps.get(BLOCK_OF[col])
        carries = k is not None and k < KNOCKOUT_KEEP_MIN
        if tracks and carries:
            flagged.append(col)
    return {"void": bool(flagged), "columns": flagged}


def primary_verdict(
    select_cm_point: float,
    report_cm_point: float,
    report_cm_hi: float,
    report_stat_parts: dict[str, float],
    report_top5_share: float,
    report_shuffled_cm_points: Sequence[float],
    report_identity_only_cm_point: float,
    planted_future_ok: bool,
    minutes_leak_void: bool,
) -> dict[str, Any]:
    """The only claim. Every ``report_*`` argument is measured on the REPORT season (2024); the SELECT season (2023) contributes
    ``select_cm_point`` only and decides whether 2024 is scored. ``report_cm_hi`` is the upper bound of the binding (widest of game,
    player-season, ISO week) 95% CI of CM. ``report_stat_parts`` are ``stat_parts`` (each stat's contribution to CM; its own delta in
    floor units is part * 4). ``report_shuffled_cm_points`` are the CM points of the N_SHUFFLE shuffled refits (seeds 0..4), averaged.
    ``planted_future_ok`` must be True in both seasons; ``minutes_leak_void`` comes from ``minutes_leak``. A failed criterion voids the
    claim; there is no partial pass."""
    assert len(report_shuffled_cm_points) == N_SHUFFLE, (
        "the shuffled control is the mean of exactly N_SHUFFLE refits"
    )
    gain = -report_cm_point
    n = len(STATS)
    shares = stat_shares(report_stat_parts)
    shuf_gain = -float(np.mean(report_shuffled_cm_points))
    crit = {
        "select_season_point": select_cm_point <= CM_PASS,
        "cm_point": report_cm_point <= CM_PASS,
        "ci_upper_below_zero": report_cm_hi < 0.0,
        "no_stat_hurt": all(v * n <= HURT_MAX for v in report_stat_parts.values()),
        "max_stat_share": (gain > 0)
        and all(sh is not None and sh <= MAX_STAT_SHARE for sh in shares.values()),
        "top5_share": report_top5_share <= TOP5_SHARE_MAX,
        "shuffled_keeps_under_half": (gain > 0) and (shuf_gain < SHUFFLE_KEEP_MAX * gain),
        "identity_only_keeps_under_half": (gain > 0)
        and (-report_identity_only_cm_point < IDENT_KEEP_MAX * gain),
        "planted_future_identical": bool(planted_future_ok),
        "no_minutes_leak": not minutes_leak_void,
    }
    return {
        "criteria": crit,
        "pass": all(crit.values()),
        "failed": [k for k, v in crit.items() if not v],
    }


def gate_counts(cell: dict[str, int]) -> dict[str, bool]:
    return {
        "rows": cell["rows"] >= MIN_ROWS,
        "player_seasons": cell["player_seasons"] >= MIN_PLAYER_SEASONS,
        "games": cell["games"] >= MIN_GAMES,
        "weeks": cell["weeks"] >= MIN_WEEKS,
    }


# --------------------------------------------------------------------------- freeze and harness pin (M3; the pattern of preseason_role_prior)
# The freeze pins the TEXT (a commit titled ``prereg(ID): <sha256 of the text above the results marker>``). The harness pin pins the CODE:
# a later commit titled ``harness(ID): <pin>`` where pin = sha256 over "path:sha256(file)" of every file below, and every file must be
# byte-identical at the freeze commit, at the pin commit and on disk when ``run`` starts. Editing any of them after the freeze makes
# ``run`` refuse; a repair is a new, dated amendment and a new freeze, never a silent edit.
MARKER = "=== RESULTS BELOW ==="
ROOT = Path(__file__).resolve().parents[2]
DOC_REL = "docs/prereg/ARCHETYPE_MATCHUP_ALL.md"
HARNESS_FILES: tuple[str, ...] = (
    "research/eval/archetype_matchup_all_rule.py",
    "research/eval/archetype_matchup_all_features.py",
    "research/eval/archetype_matchup_all_arm.py",
    "research/eval/archetype_matchup_all_gate.py",
    "research/eval/archetype_matchup_all_eval.py",
    "research/eval/archetype_matchup_rule.py",
    "research/eval/archetype_matchup_features.py",
    "research/eval/archetype_matchup_gate.py",
    "research/eval/matchup_availability_rule.py",
    "research/eval/matchup_availability_features.py",
    "research/eval/role_prior_salary_rule.py",
    "research/eval/role_prior_salary_eval.py",
    "research/archive/preseason_role_prior/preseason_role_prior.py",
    "research/coldstart/archetypes.py",  # M5: fit_archetypes (KMeans) produces every archetype label
    "nba/props/metrics.py",
    "nba/props/distributions.py",  # imported at load by nba.props.metrics (the pre-existing PRESEASON_ROLE_PRIOR pin set)
    "nba/truth/metrics.py",
)
# M5: modules a number can depend on that are NOT byte-pinned, each covered by a digest asserted in ``run`` before any outcome is read
# (the OUT-set digest covers the production report path without pinning live code; the label digest covers sklearn drift too).
DIGEST_COVERED: dict[str, str] = {
    "nba.props.context_residual": "flagged_from_availability: its output is the per-season OUT-set digest (PIN_OUT_*)",
    "nba.features.injury_report": "ReportTriggerConfig / latest_pretip_flagged: covered by the same OUT-set digest",
    "nba.features.game_tipoff": "real tip times attach inside flagged_from_availability: covered by the same OUT-set digest",
}
# Imported at module load by a pinned file but not called by any code path of this rule (stated, and checked by the coverage test).
EXEMPT_UNUSED: dict[str, str] = {
    "research.features.": "imported by research/coldstart/archetypes.py for functions this rule never calls (only fit_archetypes and encode_position_fractions are used)",
    "research.coldstart.carryover": "reached only through research.features.*; never called",
    "research.coldstart.config": "DEFAULT_N_CLUSTERS only; fit_archetypes receives k explicitly",
    "nba.features.shrinkage": "reached only through research.features.*; never called",
}


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
    return hashlib.sha256(text.split(MARKER)[0].encode()).hexdigest()


def pin_of(blobs: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{k}:{blobs[k]}" for k in sorted(blobs)).encode()).hexdigest()


def disk_blobs(root: Path = ROOT) -> dict[str, str]:
    return {rel: file_sha256(root / rel) for rel in HARNESS_FILES}


def freeze_commit(sha: str, run: Callable[..., str] = git) -> str | None:
    """The commit titled ``prereg(ID): <sha>`` whose committed rule text hashes to ``sha``."""
    for line in run("log", "--all", "--format=%H%x09%s").splitlines():
        h, _, subj = line.partition("\t")
        if (
            subj.strip() == f"prereg({ID}): {sha}"
            and text_sha(run("show", f"{h}:{DOC_REL}")) == sha
        ):
            return h
    return None


def pin_commit(run: Callable[..., str] = git) -> tuple[str, str] | None:
    for line in run("log", "--all", "--format=%H%x09%s").splitlines():
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
    """Refuse unless the rule is frozen, a ``harness(ID): <pin>`` commit descends from the freeze commit, and every harness file on disk is
    byte-identical to its version at both commits and hashes to the pin."""
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
        if run("show", f"{fc}:{rel}") != frozen:
            raise RuntimeError(
                f"{rel} differs between the freeze commit and the pin commit: refusing"
            )
    if pin_of(out) != pin:
        raise RuntimeError("the pin in the commit subject does not match the harness blobs")
    if not ok("merge-base", "--is-ancestor", fc, h):
        raise RuntimeError("pin commit does not descend from the freeze commit")
    return out


# --------------------------------------------------------------------------- M5: declared input pins (asserted by ``run`` before any outcome column is read)
LIB_NAMES = ("python", "sklearn", "lightgbm", "numpy", "pandas")
_DECL = re.compile(r"^((?:PIN|LIB)_[A-Za-z0-9_.]+): (\S+)$")


def versions() -> dict[str, str]:
    import sys

    import lightgbm
    import numpy
    import pandas
    import sklearn

    return {
        "LIB_python": ".".join(map(str, sys.version_info[:3])),
        "LIB_sklearn": sklearn.__version__,
        "LIB_lightgbm": lightgbm.__version__,
        "LIB_numpy": numpy.__version__,
        "LIB_pandas": pandas.__version__,
    }


def declared(doc_text: str) -> dict[str, str]:
    """``PIN_*`` / ``LIB_*`` lines (``NAME: value``) above the results marker of the rule text."""
    out: dict[str, str] = {}
    for line in doc_text.split(MARKER)[0].splitlines():
        m = _DECL.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def input_mismatches(decl: dict[str, str], actual: dict[str, str]) -> list[str]:
    """Every difference between what the frozen rule declares and what was recomputed (a missing declaration counts)."""
    bad = [
        f"{k}: declared {decl.get(k)!r}, recomputed {v!r}"
        for k, v in actual.items()
        if decl.get(k) != v
    ]
    bad += [f"{k}: declared but not recomputed" for k in decl if k not in actual]
    return bad
