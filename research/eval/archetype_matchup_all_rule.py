# ruff: noqa: E501
"""ARCHETYPE_MATCHUP_ALL rule constants and pure functions (rule: docs/prereg/ARCHETYPE_MATCHUP_ALL.md, DRAFT).

Devin's override of the T506 close (DECISIONS 2026-10-21 20:10): the T506 features and arm (research/eval/archetype_matchup_*) are reused
unchanged, on all four stats at once, with ONE composite primary metric. Nothing here reads an outcome or a database. Season convention:
``season`` = start year (2023 = 2023-24). Season 2025 is the frozen holdout and is never joined to game data by any module of this rule.
Every number the gate compares against is a constant below, fixed in code before the gate was computed.
"""

from __future__ import annotations

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
SHUFFLE_KEEP_MAX = (
    0.5  # the shuffled control may keep strictly less than this share of the composite gain
)

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


def primary_verdict(
    select_cm_point: float,
    report_cm_point: float,
    report_cm_hi: float,
    stat_points_floor_units: dict[str, float],
    top5_share: float,
    shuffled_cm_point: float,
) -> dict[str, Any]:
    """The only claim. Pass on the report season (2024) when every criterion holds; the select season (2023) decides whether 2024 is scored.
    ``report_cm_hi`` is the upper bound of the binding (widest of game, player-season, ISO week) 95% CI of CM."""
    gain = -report_cm_point
    crit = {
        "select_season_point": select_cm_point <= CM_PASS,
        "cm_point": report_cm_point <= CM_PASS,
        "ci_upper_below_zero": report_cm_hi < 0.0,
        "no_stat_hurt": all(v <= HURT_MAX for v in stat_points_floor_units.values()),
        "top5_share": top5_share <= TOP5_SHARE_MAX,
        "shuffled_keeps_under_half": (gain > 0) and (-shuffled_cm_point < SHUFFLE_KEEP_MAX * gain),
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
