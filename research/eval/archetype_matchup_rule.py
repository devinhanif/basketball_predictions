# ruff: noqa: E501
"""ARCHETYPE_MATCHUP rule constants and pure functions (rule: docs/prereg/ARCHETYPE_MATCHUP.md, DRAFT).

The one pre-registered follow-up to T505 MATCHUP_AVAILABILITY. Nothing here reads an outcome or a database. Season convention:
``season`` = start year (2023 = 2023-24). Season 2025 is the frozen holdout and is never joined to game data by any module of this rule.
Every number the gate compares against is a constant below, fixed in code before the gate was computed. Constants shared with T505 are
imported, not re-typed, so the two rules cannot drift apart.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from research.eval import matchup_availability_rule as t505

ID = "ARCHETYPE_MATCHUP"
SELECT = t505.SELECT
REPORT = t505.REPORT
MAX_SEASON = t505.MAX_SEASON
FIRST_MATCHUP_SEASON = t505.FIRST_MATCHUP_SEASON
STATS = t505.STATS
PRIMARY_STAT = t505.PRIMARY_STAT
FLOORS = t505.FLOORS
N_BOOT = t505.N_BOOT
SEED = t505.SEED
ALPHA = t505.ALPHA
BH_Q = t505.BH_Q
TOP5_SHARE_MAX = t505.TOP5_SHARE_MAX
S3_MAX_DEGRADE = (
    t505.S3_MAX_DEGRADE
)  # complement-of-T2 may not degrade by more than this (point and binding upper bound)
DECAY = t505.DECAY
K_OFF = t505.K_OFF
K_DEF = t505.K_DEF
ARCH_MIN_MINUTES = t505.ARCH_MIN_MINUTES
ARCH_SEED = t505.ARCH_SEED
MIN_ARCH_PAIR_POSS = t505.MIN_ARCH_PAIR_POSS
PRIOR_POSS = t505.PRIOR_POSS
MIN_ROWS = t505.MIN_ROWS
MIN_PLAYER_SEASONS = t505.MIN_PLAYER_SEASONS
MIN_GAMES = t505.MIN_GAMES
MIN_WEEKS = t505.MIN_WEEKS
DE_BAR = t505.DE_BAR
NOISE_SIGMA = t505.NOISE_SIGMA
MDE80_FACTOR = t505.MDE80_FACTOR
MIN_BUCKETS = t505.MIN_BUCKETS
check_season = t505.check_season
bh_adjust = t505.bh_adjust
minutes_bucket = t505.minutes_bucket

REGULAR_PREFIX = "002"  # regular-season game ids; the stored A0 files also hold playoff (004) and play-in (005) rows, excluded (Amendment 1)

# categorical layer: archetype index K_OFF / K_DEF is the explicit "unknown" category (archetype -1 in the T505 labelling)
UNK_OFF = K_OFF
UNK_DEF = K_DEF
N_OFF = K_OFF + 1
N_DEF = K_DEF + 1

# roster / utilization construction (fixed, not tuned)
ROSTER_DAYS = 30  # a defender is on tonight's opposing roster if he defended for that team within this many days before tonight
ROSTER_GAMES = 10  # ... and within the team's previous this-many games
MIN_W = 5.0  # a defender's normal defensive minutes per game played (decayed, as-of) must reach this to be in the mix
LIFT_CLIP = (0.1, 10.0)  # clip of the assignment lift A[a, b] / pi[b]

# the large-shift slice
T2_QUANTILE = 0.90  # cut = this quantile of the SELECT season's shift magnitude over its rows; frozen before the report season
SHIFT_FLOOR = 0.05  # T2 is shift >= max(cut, SHIFT_FLOOR): a zero-shift tie mass can never be the "large shift" slice

# audit
MISSING_FLAG_GAP = 0.30  # a new column whose neutral-or-missing share differs by at least this between min10<10 and min10>=28 is flagged

FEATURES = (
    "off_arch",
    "e_usual",
    "delta_rate",
    "shift_l1",
    "retained",
    "def_unknown_share",
)


def t2_mask(shift: np.ndarray, cut: float) -> np.ndarray:
    return np.asarray(shift, float) >= max(float(cut), SHIFT_FLOOR)


def l1_shift(f_tonight: np.ndarray, f_usual: np.ndarray) -> np.ndarray:
    """L1 distance between two archetype mixes (rows are mixes). Range [0, 2]."""
    return np.asarray(np.abs(np.asarray(f_tonight) - np.asarray(f_usual)).sum(axis=1))


def verdict_word(t1_pass: bool, t2_pass: bool) -> str:
    """PASS: T1 and T2 both clear the gate on both seasons. T1_ONLY: T1 clears, T2 does not: T2 is then reported with its CI but is
    descriptive, cannot bear a claim, and leaves the BH family (family size 3 instead of 6). FAIL otherwise (T2 cannot rescue T1: T2 is
    a subset of T1's rows, so its detectable effect is larger)."""
    if t1_pass and t2_pass:
        return "PASS"
    if t1_pass:
        return "T1_ONLY"
    return "FAIL"


def gate_test_checks(cell: dict[str, Any]) -> dict[str, bool]:
    """Counts and power bars for one (season, test) cell of pts rows."""
    return {
        "rows": cell["rows"] >= MIN_ROWS,
        "player_seasons": cell["player_seasons"] >= MIN_PLAYER_SEASONS,
        "games": cell["games"] >= MIN_GAMES,
        "weeks": cell["weeks"] >= MIN_WEEKS,
        "detectable_effect": cell["detectable_effect_pts"] <= DE_BAR["pts"],
    }


def reason_string(
    archetype: str, delta_rate: float, shift: float, out_names: list[str] | None = None
) -> str:
    """Human-readable reason for the app, formatting numbers already computed; it never feeds a score."""
    s = f"as a {archetype}, tonight's defenders allow {delta_rate:+.3f} pts/poss vs his usual mix (mix shift {shift:.2f})"
    if out_names:
        s += "; OUT: " + ", ".join(out_names)
    return s


def is_regular(game_ids: Any) -> np.ndarray:
    """True for regular-season game ids (prefix ``REGULAR_PREFIX``)."""
    return np.array([str(g).startswith(REGULAR_PREFIX) for g in game_ids], dtype=bool)
