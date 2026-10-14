"""Descriptive role facts for one player, as of before tip: how steady his minutes have been, what
his team did with him on and off the floor, and what he did per 36 minutes in games without a
given teammate.

Rules this module keeps (the same as breakdown.py):
- As-of: callers pass only games strictly before the slate date. Nothing here looks at a date.
- Every number is a count, a mean, a ratio of counts or a sample standard deviation of stored
  box-score or possession rows. Nothing is predicted, and nothing says how accurate our model is.
- Small samples are flagged and printed with their n. The labels are descriptions of the past,
  never a claim that a player is easier or better to bet.

The steadiness cut-offs below are display choices made by us, not a tested rule: the research
note behind the badge (docs/research/player_reliability_2026-10-14.md) found that a player's
minutes stability persists from one season to the next (Spearman 0.81) and that model-accuracy
tiers do not, so only minutes feed the badge.
"""

from __future__ import annotations

import math
import statistics
from typing import Literal

from pydantic import BaseModel

from .history import History, Row

STEADY_WINDOW = 15  # the last this-many games he played
STEADY_MIN_GAMES = 10  # fewer games than this and the badge says "Limited data"
STEADY_CV = 0.20  # minutes spread, as a share of his average, below which we say "Steady"
SMALL_N = 10  # fewer games than this is a small sample
SMALL_POSS = 1000  # fewer possessions than this (both ends added) on either side is a small sample
Z95 = 1.96

SteadyLabel = Literal["Steady", "Mixed", "Limited data"]


# ----------------------------------------------------------------------------- role steadiness


class Steadiness(BaseModel):
    label: SteadyLabel
    n: int  # games he played, out of the window
    window: int
    avg_minutes: float | None
    sd_minutes: float | None  # sample standard deviation of his minutes
    cv: float | None  # sd / avg
    say: str
    caveat: str


STEADY_CAVEAT = (
    "This describes his last games, not the future, and it says nothing about our model's "
    "accuracy or whether a bet is easier. Games he did not play are not counted. "
    f"Steady means his minutes varied by less than {round(STEADY_CV * 100)}% of his average."
)


def steadiness(prior: list[Row]) -> Steadiness:
    """`prior` is every game he played before tonight, oldest first."""
    last = prior[-STEADY_WINDOW:]
    n = len(last)
    if n == 0:
        return Steadiness(
            label="Limited data",
            n=0,
            window=STEADY_WINDOW,
            avg_minutes=None,
            sd_minutes=None,
            cv=None,
            say="No earlier games in our data, so we cannot say how steady his minutes are.",
            caveat=STEADY_CAVEAT,
        )
    minutes = [r.minutes for r in last]
    avg = sum(minutes) / n
    sd = statistics.stdev(minutes) if n > 1 else None
    cv = None if sd is None or avg <= 0 else sd / avg
    if n < STEADY_MIN_GAMES or cv is None or sd is None:
        return Steadiness(
            label="Limited data",
            n=n,
            window=STEADY_WINDOW,
            avg_minutes=avg,
            sd_minutes=sd,
            cv=cv,
            say=(
                f"Only {n} earlier {'game' if n == 1 else 'games'} in our data (fewer than "
                f"{STEADY_MIN_GAMES}): too few to say how steady his minutes are."
            ),
            caveat=STEADY_CAVEAT,
        )
    return Steadiness(
        label="Steady" if cv < STEADY_CV else "Mixed",
        n=n,
        window=STEADY_WINDOW,
        avg_minutes=avg,
        sd_minutes=sd,
        cv=cv,
        say=(
            f"His minutes have varied by about {sd:.1f} either side of an average of "
            f"{avg:.1f} over his last {n} games."
        ),
        caveat=STEADY_CAVEAT,
    )


# ----------------------------------------------------------------------------- per 36 minutes


class Per36(BaseModel):
    """Pooled over the games: total stat divided by total minutes, times 36."""

    n: int
    avg_minutes: float | None
    pts36: float | None
    reb36: float | None
    ast36: float | None
    small: bool


def per36(rows: list[Row]) -> Per36:
    n = len(rows)
    minutes = sum(r.minutes for r in rows)
    if n == 0 or minutes <= 0:
        return Per36(n=n, avg_minutes=None, pts36=None, reb36=None, ast36=None, small=True)

    def rate(i: int) -> float:
        return 36.0 * sum(r.base[i] for r in rows) / minutes

    # base is (pts, reb, ast, fg3m, stl, blk); assists come from the box score, never from
    # possessions (the possession table's passer id is NULL for accented names).
    return Per36(
        n=n,
        avg_minutes=minutes / n,
        pts36=rate(0),
        reb36=rate(1),
        ast36=rate(2),
        small=n < SMALL_N,
    )


# ----------------------------------------------------------------------------- on / off


class Rate(BaseModel):
    """Team points per 100 possessions while he is on (or off) the floor."""

    poss: int  # possessions at both ends added
    off_poss: int
    def_poss: int
    ortg: float | None  # points the team scored per 100 of its possessions
    drtg: float | None  # points it allowed per 100 of the other side's possessions
    net: float | None  # ortg minus drtg


class OnOffWindow(BaseModel):
    games: int
    on: Rate
    off: Rate
    diff: float | None  # net on minus net off
    ci_lo: float | None
    ci_hi: float | None
    small: bool
    note: str


class OnOff(BaseModel):
    available: bool
    this_season: OnOffWindow
    since_last_season: OnOffWindow
    note: str


ONOFF_NOTE = (
    "Team points scored minus points allowed per 100 possessions, in games he played, from the "
    "play-by-play. 'Off' is the same team in the same games while he sat. The range is an "
    "approximate 95% interval that treats each game as one draw and ignores who else was on the "
    "floor. A gap here is a description, not a measure of what he adds: his teammates and "
    "opponents differ between the two."
)
ONOFF_MISSING = "This data holds no play-by-play on/off numbers for this player."

# (own-side possessions, points scored, other-side possessions, points allowed)
Quad = tuple[int, int, int, int]


def _rate(q: Quad) -> Rate:
    o, pf, d, pa = q
    ortg = 100.0 * pf / o if o > 0 else None
    drtg = 100.0 * pa / d if d > 0 else None
    return Rate(
        poss=o + d,
        off_poss=o,
        def_poss=d,
        ortg=ortg,
        drtg=drtg,
        net=None if ortg is None or drtg is None else ortg - drtg,
    )


def _net_variance(games: list[Quad]) -> float | None:
    """Variance of the net rating with games as clusters (linearised ratio estimator)."""
    n = len(games)
    o = sum(g[0] for g in games)
    d = sum(g[2] for g in games)
    if n < 2 or o <= 0 or d <= 0:
        return None
    r_pf = sum(g[1] for g in games) / o
    r_pa = sum(g[3] for g in games) / d
    e = [100.0 * ((g[1] - r_pf * g[0]) / o - (g[3] - r_pa * g[2]) / d) for g in games]
    return n / (n - 1) * sum(x * x for x in e)


def _window(on_games: list[Quad], off_games: list[Quad]) -> OnOffWindow:
    on = _rate(_sum(on_games))
    off = _rate(_sum(off_games))
    diff = None if on.net is None or off.net is None else on.net - off.net
    lo = hi = None
    v_on, v_off = _net_variance(on_games), _net_variance(off_games)
    if diff is not None and v_on is not None and v_off is not None:
        se = math.sqrt(v_on + v_off)
        lo, hi = diff - Z95 * se, diff + Z95 * se
    games = len(on_games)
    small = games < SMALL_N or on.poss < SMALL_POSS or off.poss < SMALL_POSS
    if games == 0:
        note = "No earlier games with play-by-play."
    elif small:
        note = (
            f"Small sample: {games} games, {on.poss:,} possessions on and {off.poss:,} off "
            f"(fewer than {SMALL_POSS:,} on one side). Treat the gap as noise."
        )
    else:
        note = f"{games} games, {on.poss:,} possessions on and {off.poss:,} off."
    return OnOffWindow(
        games=games, on=on, off=off, diff=diff, ci_lo=lo, ci_hi=hi, small=small, note=note
    )


def _sum(games: list[Quad]) -> Quad:
    return (
        sum(g[0] for g in games),
        sum(g[1] for g in games),
        sum(g[2] for g in games),
        sum(g[3] for g in games),
    )


def onoff(hist: History, pid: int, prior: list[Row], season: int) -> OnOff:
    """On/off from the games in `prior` (strictly before tonight). `season` is tonight's."""
    if hist.onoff is None:
        empty = _window([], [])
        return OnOff(
            available=False, this_season=empty, since_last_season=empty, note=ONOFF_MISSING
        )
    on_all: list[tuple[int, Quad, Quad]] = []  # (season, on, off) per game
    for r in prior:
        mine = hist.onoff.player.get(r.game_id, {}).get(pid)
        team = hist.onoff.team.get(r.game_id, {}).get(r.team)
        if mine is None or team is None:
            continue
        off = (team[0] - mine[0], team[1] - mine[1], team[2] - mine[2], team[3] - mine[3])
        on_all.append((r.season, mine, off))
    this = [x for x in on_all if x[0] == season]
    return OnOff(
        available=bool(on_all),
        this_season=_window([x[1] for x in this], [x[2] for x in this]),
        since_last_season=_window([x[1] for x in on_all], [x[2] for x in on_all]),
        note=ONOFF_NOTE if on_all else ONOFF_MISSING,
    )
