"""What a player's with/without-teammate history is worth beyond the forecast.

The raw gap ("+4.0 assists a game without Trae, 15 games") is mostly noise and the forecast already
knows the teammate is out, so the number we lead with is the part of the gap that, on past seasons,
carried into his next game: coefficient x n/(n + SHRINK_K) x raw gap.

Descriptive only. This is a display of a measured carry-over, not a claim of edge, and it never
touches a probability. Nothing here reads a season later than 2024: the coefficients below are
fixed numbers from the gate (2020-22 outcomes), copied, not computed from any data in this repo.
"""

from __future__ import annotations

import statistics
from typing import Literal

from pydantic import BaseModel

from .history import Row
from .role_stats import SMALL_N

# ---------------------------------------------------------------------------------------------
# Constants. Source: reports/prereg_wowy_redistribution/gate.json on branch agent/modeler-wowy
# (measured_at "Wed Oct 14 16:37 CDT 2026"; the shrink constant is SHRINK_K = 6 in nba/props/wowy.py
# on that branch). Each coefficient is the share of the shrunk with/without gap (delta * n/(n+6))
# that showed up in the player's next game, beyond what the forecast already does with the team's
# vacated minutes and stats. Clustered by game.
# ---------------------------------------------------------------------------------------------
GATE_SOURCE = (
    "reports/prereg_wowy_redistribution/gate.json, branch agent/modeler-wowy, measured 2026-10-14"
)
SHRINK_K = 6.0

# (coefficient, standard error, window). Points, rebounds, assists, threes: pooled 2020-22, the
# "ANY rotation teammate out" slice (data_capture_audit.kappa.ANY["pooled 2020-2022"], 59,643 rows).
# Minutes: the gate holds only the 2022 estimate (kappa_2022_only.ANY.minutes, 17,433 rows).
Stat = Literal["min", "pts", "reb", "ast", "fg3m"]
COEF: dict[Stat, tuple[float, float, str]] = {
    "pts": (0.03690956195329251, 0.008061539005577573, "2020-22"),
    "reb": (0.03813014869615156, 0.008835185606936484, "2020-22"),
    "ast": (0.04823912334795294, 0.009905065353356632, "2020-22"),
    "fg3m": (0.0011374228909304372, 0.010190798663602512, "2020-22"),
    "min": (0.16978673480560574, 0.014573911891378948, "2022 only"),
}
LABEL: dict[Stat, str] = {
    "min": "Minutes",
    "pts": "Points",
    "reb": "Rebounds",
    "ast": "Assists",
    "fg3m": "Threes",
}
ORDER: tuple[Stat, ...] = ("min", "pts", "reb", "ast", "fg3m")
Z95 = 1.959964

# A "large" gap is in the top tenth by absolute size. The gate stores the standard deviation of the
# shrunk gap per season (by_season[season]["sd_wo_d_<q>(informative)"], 2022/2023/2024) but not its
# deciles; the top tenth of a roughly normal spread begins at 1.645 sd, so that is the cut used.
# Those sd sum over every teammate out in a game, so for one teammate this cut is on the high side:
# it flags fewer gaps, not more.
SD_SHRUNK_GAP: dict[Stat, tuple[float, float, float]] = {
    "min": (5.159710125158258, 7.085240832073468, 7.125763542928135),
    "pts": (3.2657475666450138, 4.052631571725339, 4.1021839246587435),
    "reb": (1.2735936802438923, 1.6791202743842526, 1.682739188018951),
    "ast": (0.9632413467210068, 1.1307709998027442, 1.1387009268191475),
    "fg3m": (0.0, 0.0, 0.0),  # no measurable effect, never flagged
}
TOP_TENTH_Z = 1.6449
# Pooled 2020-22 coefficients inside the top tenth of the gap, own stat (gate.json
# feature_defined_slices.kappa_inside_slices["pooled 2020-22"], 5,330 rows): the slice
# "top10|wo_d_<q>" and the outcome <q>, as (coefficient, standard error). The gate has no minutes
# outcome and none for threes, so those stats have no entry and keep the overall coefficient with
# the upper-bound flag. (The "top10|wo_d_min" slice holds a points, rebounds and assists outcome,
# not a minutes one.)
TOP_TENTH_COEF: dict[Stat, tuple[float, float]] = {
    "pts": (0.0094, 0.0167),
    "reb": (0.0216, 0.0166),
    "ast": (0.0335, 0.0165),
}


class BeyondStat(BaseModel):
    stat: Stat
    label: str
    past_gap: float | None  # games without him minus games with him, per game; the raw history
    expected_extra: float | None  # coefficient x n/(n+6) x past_gap; None when there is no gap
    no_effect: bool  # the coefficient's 95% range spans 0 (threes)
    one_in: int | None  # "about 1 in this many" of a gap carried over; None when no effect
    large_gap: bool
    # large gap, and the coefficient measured inside the biggest gaps spans 0: shown as 0 in words
    no_carry_large: bool
    window: str  # the seasons the coefficient was measured on


class Beyond(BaseModel):
    available: bool  # both sets of games are non-empty
    n_without: int
    n_with: int
    weight: float  # n_without / (n_without + 6)
    small: bool  # fewer games without him than SMALL_N; the existing small-sample flag
    stats: list[BeyondStat]
    flags: list[str]
    note: str
    source: str


def coef_spans_zero(stat: Stat) -> bool:
    c, se, _ = COEF[stat]
    return abs(c) < Z95 * se


def one_in(stat: Stat) -> int | None:
    """The coefficient as a plain fraction ("about 1 in 27"); None when it spans zero."""
    if coef_spans_zero(stat):
        return None
    return round(1.0 / COEF[stat][0])


def top_tenth_spans_zero(stat: Stat) -> bool:
    c, se = TOP_TENTH_COEF[stat]
    return abs(c) < Z95 * se


def large_cut(stat: Stat) -> float:
    """Smallest absolute shrunk gap that counts as a large one (top tenth)."""
    return TOP_TENTH_Z * statistics.fmean(SD_SHRUNK_GAP[stat])


def plain_note() -> str:
    """Short, human. The fractions come from the coefficients; nothing is typed in by hand."""
    counting: list[Stat] = [s for s in ("pts", "reb", "ast") if not coef_spans_zero(s)]
    mean_coef = statistics.fmean(COEF[s][0] for s in counting)
    mins = one_in("min")
    return (
        "The forecast already knows he is out. With and without gaps mostly do not repeat: on past "
        f"seasons, about 1 in {round(1.0 / mean_coef)} of a gap like this carried over for points, "
        f"rebounds and assists, and about 1 in {mins} for minutes. Threes showed nothing."
    )


def _mean(rows: list[Row], stat: Stat) -> float:
    if stat == "min":
        return statistics.fmean(r.minutes for r in rows)
    i = {"pts": 0, "reb": 1, "ast": 2, "fg3m": 3}[stat]
    return statistics.fmean(r.base[i] for r in rows)


def beyond(without: list[Row], with_: list[Row]) -> Beyond:
    n_wo, n_wi = len(without), len(with_)
    weight = n_wo / (n_wo + SHRINK_K)
    ready = n_wo > 0 and n_wi > 0
    stats: list[BeyondStat] = []
    flags: list[str] = []
    for s in ORDER:
        gap = _mean(without, s) - _mean(with_, s) if ready else None
        no_effect = coef_spans_zero(s)
        large = bool(gap is not None and not no_effect and abs(weight * gap) >= large_cut(s))
        coef, window, per = COEF[s][0], COEF[s][2], one_in(s)
        no_carry = False
        if large and s in TOP_TENTH_COEF:  # use the coefficient measured inside the biggest gaps
            coef, window = TOP_TENTH_COEF[s][0], "2020-22, top tenth of gaps"
            no_carry = top_tenth_spans_zero(s)
            per = None if no_carry else round(1.0 / coef)
            what = (
                "no measurable carry-over for gaps this big"
                if no_carry
                else f"about 1 in {per} carried over for gaps this big"
            )
            flags.append(
                f"Large gap in {LABEL[s].lower()}: gaps this big (the top tenth or so) repeat "
                f"least; on past seasons, {what}. The number uses that smaller share."
            )
        elif large:
            flags.append(
                f"Large gap in {LABEL[s].lower()}: gaps this big (the top tenth or so) repeat "
                "least, so treat the number as an upper bound."
            )
        if gap is None:
            extra = None
        elif no_effect or no_carry:
            extra = 0.0
        else:
            extra = coef * weight * gap
        stats.append(
            BeyondStat(
                stat=s,
                label=LABEL[s],
                past_gap=gap,
                expected_extra=extra,
                no_effect=no_effect,
                one_in=per,
                large_gap=large,
                no_carry_large=no_carry,
                window=window,
            )
        )
    return Beyond(
        available=ready,
        n_without=n_wo,
        n_with=n_wi,
        weight=weight,
        small=n_wo < SMALL_N,
        stats=stats,
        flags=flags,
        note=plain_note(),
        source=GATE_SOURCE,
    )
