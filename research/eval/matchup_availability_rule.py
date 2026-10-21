# ruff: noqa: E501
"""MATCHUP_AVAILABILITY rule constants and pure functions (rule: docs/prereg/MATCHUP_AVAILABILITY.md, DRAFT).

Nothing here reads an outcome or a database. Season convention: ``season`` = start year (2023 = 2023-24). Season 2025 is the frozen
holdout and is never joined to game data by any module of this rule. Every number the gate compares against is a constant below,
fixed in code before the gate was computed.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

ID = "MATCHUP_AVAILABILITY"
SELECT = 2023
REPORT = 2024
MAX_SEASON = 2024
FIRST_MATCHUP_SEASON = 2022  # first season with player_game_matchups rows (nba.duckdb; the history db holds none for 2019-2021)
STATS = ("pts", "ast", "fg3m")
PRIMARY_STAT = "pts"
FLOORS = {"pts": 0.005, "ast": 0.002, "fg3m": 0.001}
N_BOOT = 2000  # equals role_prior_salary_rule.N_BOOT (asserted in tests): the reused power method reads that constant
SEED = 0
ALPHA = 0.05
BH_Q = 0.10
S3_MAX_DEGRADE = 0.002
TOP5_SHARE_MAX = 0.50

# as-of construction (fixed, not tuned)
DECAY = (
    0.97  # weight per intervening game of the entity (player), window: current and previous season
)
PRIMARY_SHARE_MIN = 0.25
MIN_HIST_GAMES = 5  # prior games with matchup rows in the window before a share is trusted
PRIOR_POSS = 200.0  # partial possessions of shrinkage toward the archetype-pair rate
K_OFF = 6  # offensive archetypes (fixed; the project default of 4 is too coarse to separate bigs / wings / guards by style)
K_DEF = 6
ARCH_MIN_MINUTES = (
    300.0  # minutes in the PRIOR season to be clustered; below it the archetype is -1 (unknown)
)
ARCH_SEED = 0
MIN_ARCH_PAIR_POSS = 300.0  # partial possessions in a (scorer type, defender type) cell before its rate is used; else league

# minimum-data gate bars (label-free; per season, regular season, for the slice, pts rows)
MIN_ROWS = 300
MIN_PLAYER_SEASONS = 50
MIN_GAMES = 150
MIN_WEEKS = 20
DE_BAR = {
    "pts": 0.005
}  # detectable effect (binding half-width of the null-noise contrast) must be <= the pts floor
NOISE_SIGMA = 0.05  # sd of log lambda of the null-noise arm (role_prior_salary G6)
MDE80_FACTOR = (
    2.8 / 1.96
)  # descriptive: minimum detectable effect at 80% power from a 95% half-width

MIN_BUCKETS = (
    ("lt10", 0.0, 10.0),
    ("10to20", 10.0, 20.0),
    ("20to28", 20.0, 28.0),
    ("ge28", 28.0, 1e9),
)


def check_season(season: int) -> int:
    if season > MAX_SEASON:
        raise ValueError(f"season {season}: 2025 is the frozen holdout, never loaded by this rule")
    return season


def bh_adjust(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        adj[i] = prev
    return adj


def minutes_bucket(m: float) -> str:
    for name, lo, hi in MIN_BUCKETS:
        if lo <= m < hi:
            return name
    return "lt10"


def pick_primary(share: pd.DataFrame) -> pd.DataFrame:
    """share: columns qidx, oth, w (as-of decayed minutes). Returns one row per qidx: primary other, its share, total weight.
    Ties broken by the smaller id (deterministic). Pure function of as-of weights."""
    s = share.copy()
    tot = s.groupby("qidx").w.transform("sum")
    s["share"] = s.w / tot
    s = s.sort_values(["qidx", "share", "oth"], ascending=[True, False, True])
    top = s.drop_duplicates("qidx")[["qidx", "oth", "share"]].rename(
        columns={"oth": "primary", "share": "primary_share"}
    )
    return top.reset_index(drop=True)


def slice_labels(primary_def_out: np.ndarray, primary_asg_out: np.ndarray) -> np.ndarray:
    """S1 = primary defender faced is OUT; S2 = primary assignment is OUT (not already S1); S12 both; S3 = everything else.
    The pass tests use S1 and S2 as (possibly overlapping) row sets; this label is for the control and the reporting only."""
    d = np.asarray(primary_def_out, bool)
    a = np.asarray(primary_asg_out, bool)
    return np.where(d & a, "S12", np.where(d, "S1", np.where(a, "S2", "S3")))


def reason_string(
    side: str,
    name: str,
    archetype: str,
    delta_rate: float | None,
    freed_share: float | None = None,
    fallback: bool = False,
) -> str:
    """Human-readable reason for the app. Pure formatting of already computed numbers; it never feeds a score."""
    if side == "D":
        s = f"primary defender {name} ({archetype}) OUT"
        if delta_rate is not None and np.isfinite(delta_rate):
            s += f"; replacement mix allows {delta_rate:+.2f} pts/poss vs his"
    elif side == "O":
        s = f"usual assignment {name} ({archetype}) OUT"
        if freed_share is not None and np.isfinite(freed_share):
            s += f"; {100 * freed_share:.0f}% of his defensive time freed"
    else:
        raise ValueError(side)
    if fallback:
        s += " (archetype fallback: little direct history)"
    return s


def verdict_word(s1_pass: bool, s2_pass: bool) -> str:
    if s1_pass and s2_pass:
        return "PASS"
    if s1_pass:
        return "S1_ONLY"
    if s2_pass:
        return "S2_ONLY"
    return "FAIL"


def gate_slice_checks(cell: dict[str, Any]) -> dict[str, bool]:
    """Counts and power bars for one (season, slice) cell of pts rows."""
    return {
        "rows": cell["rows"] >= MIN_ROWS,
        "player_seasons": cell["player_seasons"] >= MIN_PLAYER_SEASONS,
        "games": cell["games"] >= MIN_GAMES,
        "weeks": cell["weeks"] >= MIN_WEEKS,
        "detectable_effect": cell["detectable_effect_pts"] <= DE_BAR["pts"],
    }
