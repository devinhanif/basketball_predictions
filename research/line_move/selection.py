"""T510 selection (shared by T510 and T510b, which imports it by reference; see the T510b text).

Definitions transcribed from docs/prereg/LINE_MOVE_TOWARD_MODEL.md "Data" and "Definitions". Nothing
in this module forms a movement statistic: it only chooses which rung / which three snapshots a
player-stat-game is scored on, and records the three mids and the forecasts. The valuation of the
DNP
branch (T510: p_M = p_cond * p_play; T510b: p_M' = p_cond * p_play + (1 - p_play) * mid0) lives in
``valuation`` and is applied after selection; selection itself never reads a valuation.

Times are integer epoch seconds (UTC). A snapshot's time is the UTC timestamp in its capture file
name (enforced by the loader).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from research.line_move import spec

MIN = 60


@dataclass(frozen=True)
class Forecast:
    """One stored forecast row: integer-support survival counts and availability."""

    counts: tuple[int, ...] | None  # p_ge_full "c": c[k-1] of n draws have stat >= k
    n: int
    p_play: float | None  # None = missing or null in the stored row (dropped, counted)

    def p_cond(self, k: int) -> float | None:
        """P(stat >= k | plays): c[k-1]/n, 0 when k-1 >= len(c); None if p_ge_full is null."""
        if self.counts is None:
            return None
        return self.counts[k - 1] / self.n if k - 1 < len(self.counts) else 0.0


@dataclass
class Rung:
    """One Kalshi ticker's capture history: sorted snapshot times and quotes (NaN = missing)."""

    ticker: str
    k: int
    t: np.ndarray  # int64 epoch seconds, ascending
    bid: np.ndarray  # float64 dollars
    ask: np.ndarray


@dataclass
class PSG:
    """A player-stat-game: its forecasts and every captured rung."""

    pid: int
    stat: str
    day: date  # ET calendar date of tipoff
    game_id: str
    tip_s: int
    made_s: int
    m: Forecast
    b: Forecast | None
    rungs: list[Rung] = field(default_factory=list)
    # capture-file times of this series (epoch s, ascending); None = union of the rungs' times
    file_times: np.ndarray | None = None


@dataclass(frozen=True)
class Selected:
    pid: int
    stat: str
    day: date
    game_id: str
    ticker: str
    k: int
    tip_s: int
    made_s: int
    s0_s: int
    s1_s: int
    close_s: int
    mid0: float
    mid1: float
    mid_close: float
    pc_m: float  # P(stat >= k | plays) under M
    play_m: float
    pc_b: float
    play_b: float


@dataclass
class SelectionLog:
    """Drops and totals. Drop sign counters are keyed by (reason, rule) -> {-1, 0, +1, None}."""

    drops: Counter[str] = field(default_factory=Counter)
    drop_signs: dict[tuple[str, str], Counter[int | None]] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    drop_pplay_lt_09: Counter[str] = field(default_factory=Counter)
    psg_seen: int = 0
    psg_with_s0_rung: int = 0


def sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _cents(a: np.ndarray) -> np.ndarray:
    return np.rint(100.0 * a)


def good_quote(bid: float, ask: float, *, float_compat: bool = False) -> bool:
    """Two-sided (0 < bid < ask < 1) with ask - bid <= 0.10, in integer cents.

    ``float_compat`` reproduces the float comparison of the gate scripts (spread 0.10 computed in
    floating point, which rejects some exact 10-cent spreads); it exists only to cross-check counts.
    """
    if np.isnan(bid) or np.isnan(ask):
        return False
    if float_compat:
        return bool(0 < bid < ask < 1 and ask - bid <= spec.MAX_SPREAD)
    bc, ac = int(round(100 * bid)), int(round(100 * ask))
    return 0 < bc < ac < 100 and ac - bc <= round(100 * spec.MAX_SPREAD)


def mid_of(bid: float, ask: float) -> float:
    return (round(100 * bid) + round(100 * ask)) / 200.0


def _file_index(ft: np.ndarray, t: int) -> int | None:
    """Index of the first capture file at or after ``t`` (None if there is none)."""
    i = int(np.searchsorted(ft, t, side="left"))
    return i if i < len(ft) else None


def _quote_at(r: Rung, t: int) -> int | None:
    """Index of the rung's row in the capture file stamped ``t`` (None = absent from that file)."""
    i = int(np.searchsorted(r.t, t, side="left"))
    return i if i < len(r.t) and int(r.t[i]) == t else None


def select_psg(psg: PSG, log: SelectionLog, *, float_compat: bool = False) -> Selected | None:
    """Apply the T510 one-rung rule with decoupled S0 / S1 / close. No fallback to another rung.

    A snapshot is a capture FILE (adversary ruling A1): S0 is the first file at or after
    max(made_at, tip - 150 min), S1 the first file at or after S0 + 15 min, close the last file
    strictly before tipoff. A rung absent from the S0, S1 or close file has no quote there and fails
    the filter (counted as ``rung_absent_at_snapshot``).
    """
    log.psg_seen += 1
    s0_time = max(psg.made_s, psg.tip_s - spec.S0_LEAD_MIN * MIN)
    if psg.file_times is not None:
        ft = psg.file_times
    elif psg.rungs:
        ft = np.unique(np.concatenate([r.t for r in psg.rungs]))
    else:
        ft = np.array([], dtype=np.int64)
    fi0 = _file_index(ft, s0_time)
    if fi0 is None:
        log.drops["no_S0_snapshot"] += len(psg.rungs)
        return None
    t0 = int(ft[fi0])
    cands: list[tuple[Rung, int]] = []
    for r in psg.rungs:
        i0 = _quote_at(r, t0)
        if i0 is None:
            log.drops["rung_absent_at_snapshot"] += 1
            log.drops["S0_quote_fails_filter"] += 1
            continue
        if not good_quote(r.bid[i0], r.ask[i0], float_compat=float_compat):
            log.drops["S0_quote_fails_filter"] += 1
            continue
        cands.append((r, i0))
    if not cands:
        return None
    log.psg_with_s0_rung += 1
    # the rung whose mid0 is closest to 0.50 (ties: lower k); chosen on S0 information only
    r, i0 = min(
        cands,
        key=lambda c: (abs(mid_of(c[0].bid[c[1]], c[0].ask[c[1]]) - spec.TARGET_MID), c[0].k),
    )
    mid0 = mid_of(r.bid[i0], r.ask[i0])
    pc = psg.m.p_cond(r.k)
    play_m = psg.m.p_play

    def drop(reason: str) -> None:
        log.drops[reason] += 1
        for rule in ("T510", "T510b"):
            if pc is None or play_m is None:
                log.drop_signs[(reason, rule)][None] += 1
            else:
                log.drop_signs[(reason, rule)][sign(valuation(rule, pc, play_m, mid0) - mid0)] += 1
        if play_m is not None and play_m < 0.9:
            log.drop_pplay_lt_09[reason] += 1

    fi1 = _file_index(ft, t0 + spec.S1_GAP_MIN * MIN)
    fic = int(np.searchsorted(ft, psg.tip_s, side="left")) - 1  # last file strictly before tip
    if fi1 is None or fic < 0:
        drop("drop_no_S1_or_close")
        return None
    t1, tc = int(ft[fi1]), int(ft[fic])
    i1, ic = _quote_at(r, t1), _quote_at(r, tc)
    if i1 is None or ic is None:
        log.drops["rung_absent_at_snapshot"] += 1
        drop("drop_S1_close_filter")
        return None
    if (
        not good_quote(r.bid[i1], r.ask[i1], float_compat=float_compat)
        or not good_quote(r.bid[ic], r.ask[ic], float_compat=float_compat)
        or tc - t1 < spec.CLOSE_GAP_MIN * MIN
    ):
        drop("drop_S1_close_filter")
        return None
    if pc is None:
        drop("drop_null_p_ge_full")
        return None
    if psg.b is None:
        drop("drop_no_B")
        return None
    pcb = psg.b.p_cond(r.k)
    if pcb is None:
        drop("drop_null_p_ge_full_B")
        return None
    play_b = psg.b.p_play
    if play_m is None or play_b is None:
        drop("drop_null_p_play")
        return None
    return Selected(
        psg.pid,
        psg.stat,
        psg.day,
        psg.game_id,
        r.ticker,
        r.k,
        psg.tip_s,
        psg.made_s,
        t0,
        t1,
        tc,
        mid0,
        mid_of(r.bid[i1], r.ask[i1]),
        mid_of(r.bid[ic], r.ask[ic]),
        pc,
        play_m,
        pcb,
        play_b,
    )


def valuation(rule: str, p_cond: float, p_play: float, mid0: float) -> float:
    """T510: p = p_cond * p_play (DNP = No). T510b: DNP valued at L = mid0 (Kalshi's fair price)."""
    if rule == "T510":
        return p_cond * p_play
    if rule == "T510b":
        return p_cond * p_play + (1.0 - p_play) * mid0
    raise ValueError(rule)


def select_all(
    psgs: list[PSG], *, float_compat: bool = False
) -> tuple[list[Selected], SelectionLog]:
    log = SelectionLog()
    out = []
    for p in psgs:
        s = select_psg(p, log, float_compat=float_compat)
        if s is not None:
            out.append(s)
    return out, log
