"""Qualification, movement, the date-resampled tests, the verdicts and the horizon.

T510 (95%) and T510b (97.5% bounds, 95% reported). The movement ``m_X = (mid_close - mid1) *
sign(p_X -
mid0)`` is formed only in :func:`build_table`, which :func:`score` calls once the horizon is
reached; the
preflight path (``counts_only``) never calls it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy import stats as sps

from research.line_move import spec
from research.line_move.selection import Selected, valuation

EPS = 1e-12  # qualification is |gap| >= 0.05 in exact arithmetic; guard float representation error


@dataclass
class Table:
    """Per-market arrays for one rule, sorted by (date, game_id, ticker)."""

    rule: str
    days: np.ndarray  # object array of date
    game_id: list[str]
    ticker: list[str]
    stat: np.ndarray
    mid0: np.ndarray
    gap_m: np.ndarray  # p_M - mid0 (rule valuation)
    gap_b: np.ndarray
    move: np.ndarray  # mid_close - mid1
    play_m: np.ndarray
    age_h: np.ndarray  # (tip - made_at) in hours
    mid_close: np.ndarray
    pid: np.ndarray

    @property
    def n(self) -> int:
        return len(self.move)

    @property
    def q1(self) -> np.ndarray:
        return np.abs(self.gap_m) >= spec.QUALIFY_GAP - EPS

    @property
    def q2(self) -> np.ndarray:
        return self.q1 | (np.abs(self.gap_b) >= spec.QUALIFY_GAP - EPS)

    @property
    def m_m(self) -> np.ndarray:
        return self.move * np.sign(self.gap_m)

    @property
    def m_b(self) -> np.ndarray:
        return self.move * np.sign(self.gap_b)


def build_table(sel: list[Selected], rule: str, dates: set[date] | None = None) -> Table:
    rows = [s for s in sel if (dates is None or s.day in dates)]
    rows.sort(key=lambda s: (s.day, s.game_id, s.ticker))
    return Table(
        rule=rule,
        days=np.array([s.day for s in rows], dtype=object),
        game_id=[s.game_id for s in rows],
        ticker=[s.ticker for s in rows],
        stat=np.array([s.stat for s in rows], dtype=object),
        mid0=np.array([s.mid0 for s in rows]),
        gap_m=np.array([valuation(rule, s.pc_m, s.play_m, s.mid0) - s.mid0 for s in rows]),
        gap_b=np.array([valuation(rule, s.pc_b, s.play_b, s.mid0) - s.mid0 for s in rows]),
        move=np.array([s.mid_close - s.mid1 for s in rows]),
        play_m=np.array([s.play_m for s in rows]),
        age_h=np.array([(s.tip_s - s.made_s) / 3600.0 for s in rows]),
        mid_close=np.array([s.mid_close for s in rows]),
        pid=np.array([s.pid for s in rows]),
    )


def qualifying_counts(sel: list[Selected], rule: str) -> tuple[int, int, np.ndarray]:
    """Counts only, no movement: (# part-1, # part-2 qualifying, part-1 flag per market)."""
    q1 = np.array(
        [
            abs(valuation(rule, s.pc_m, s.play_m, s.mid0) - s.mid0) >= spec.QUALIFY_GAP - EPS
            for s in sel
        ],
        dtype=bool,
    )
    q2 = np.array(
        [
            abs(valuation(rule, s.pc_m, s.play_m, s.mid0) - s.mid0) >= spec.QUALIFY_GAP - EPS
            or abs(valuation(rule, s.pc_b, s.play_b, s.mid0) - s.mid0) >= spec.QUALIFY_GAP - EPS
            for s in sel
        ],
        dtype=bool,
    )
    return int(q1.sum()), int(q2.sum()), q1


# ---------------------------------------------------------------- horizon


def horizon_date(sel: list[Selected], rule: str) -> date | None:
    """First ET date (>= 2026-10-22) by which the window holds >= 300 part-1 qualifying markets AND
    >= 14 distinct dates. A "date" is an ET date holding at least one part-1 qualifying market."""
    by_day: Counter[date] = Counter()
    for s in sel:
        if s.day < spec.FIRST_DATE:
            continue
        if abs(valuation(rule, s.pc_m, s.play_m, s.mid0) - s.mid0) >= spec.QUALIFY_GAP - EPS:
            by_day[s.day] += 1
    total = 0
    for ndays, d in enumerate(sorted(by_day), start=1):
        total += by_day[d]
        if total >= spec.MIN_QUALIFYING and ndays >= spec.MIN_DATES:
            return d
    return None


def window_end(rule: str, own: date | None, t510: date | None) -> date | None:
    """Scoring date. T510b is never scored before T510 and not at all if T510 cannot be scored."""
    if own is None or t510 is None:
        return None
    return own if rule == "T510" else max(own, t510)


# ---------------------------------------------------------------- inference


def _per_date(values: np.ndarray, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(sum, n) per date, dates ascending."""
    uniq = sorted(set(days.tolist()))
    idx = {d: i for i, d in enumerate(uniq)}
    s = np.zeros(len(uniq))
    n = np.zeros(len(uniq))
    for v, d in zip(values, days, strict=True):
        s[idx[d]] += v
        n[idx[d]] += 1
    return s, n


@dataclass(frozen=True)
class PartResult:
    n: int
    n_dates: int
    mean: float
    pct: dict[float, tuple[float, float]]  # level -> (lower, upper) percentile bounds
    t_lower: dict[float, float]  # level -> lower bound of the t-interval on per-date means
    floor: float
    passed: dict[float, bool]  # level -> mean >= floor and both lower bounds > 0


def resample_dist(sums: np.ndarray, ns: np.ndarray) -> np.ndarray:
    """10,000 resamples of dates with replacement; pooled mean over every market in the resample.

    A fresh ``default_rng(20261022)`` per call; dates are in ascending order (the callers'
    order)."""
    g = len(sums)
    rng = np.random.default_rng(spec.SEED)
    idx = rng.integers(0, g, size=(spec.N_RESAMPLES, g))
    return np.asarray(sums[idx].sum(axis=1) / ns[idx].sum(axis=1))


def part_result(
    values: np.ndarray, days: np.ndarray, floor: float, levels: tuple[float, ...]
) -> PartResult:
    sums, ns = _per_date(values, days)
    g = len(sums)
    if g == 0:
        return PartResult(0, 0, float("nan"), {}, {}, floor, {lv: False for lv in levels})
    dist = resample_dist(sums, ns)
    mean = float(values.mean())
    date_means = sums / ns
    pct, tl, passed = {}, {}, {}
    for lv in levels:
        a = (1.0 - lv) / 2.0 * 100.0
        lo, hi = np.percentile(dist, [a, 100.0 - a])
        pct[lv] = (float(lo), float(hi))
        if g >= 2:
            se = float(date_means.std(ddof=1) / np.sqrt(g))
            tq = float(sps.t.ppf(1.0 - (1.0 - lv) / 2.0, g - 1))
            tl[lv] = float(date_means.mean() - tq * se)
        else:
            tl[lv] = float("nan")
        passed[lv] = bool(mean >= floor and lo > 0 and tl[lv] > 0)
    return PartResult(len(values), g, mean, pct, tl, floor, passed)


@dataclass
class RuleResult:
    rule: str
    level: float  # the rule's decision level
    part1: PartResult
    part2: PartResult
    verdict: str
    passes_at_95: bool
    extras: dict[str, object] = field(default_factory=dict)


def evaluate(table: Table) -> RuleResult:
    """Verdict at the rule's own level: T510 95%; T510b 97.5% (plus the 95% bounds, reported)."""
    level = spec.LEVEL_T510 if table.rule == "T510" else spec.LEVEL_T510B
    levels = (spec.LEVEL_T510,) if table.rule == "T510" else (spec.LEVEL_T510B, spec.LEVEL_T510)
    q1, q2 = table.q1, table.q2
    p1 = part_result(table.m_m[q1], table.days[q1], spec.FLOOR_PART1, levels)
    diff = (table.m_m - table.m_b)[q2]
    p2 = part_result(diff, table.days[q2], spec.FLOOR_PART2, levels)
    pass_lv = p1.passed.get(level, False) and p2.passed.get(level, False)
    at95 = p1.passed.get(spec.LEVEL_T510, False) and p2.passed.get(spec.LEVEL_T510, False)
    if pass_lv:
        verdict = "PASS"
    elif p1.pct and p1.pct[level][1] < 0:
        verdict = "HARM"  # part 1's percentile CI wholly below 0
    else:
        verdict = "NULL"
    return RuleResult(table.rule, level, p1, p2, verdict, at95)


# ---------------------------------------------------------------- reported, not decisive


def reported(table: Table, seed_offset: int = 1, n_shuffle: int = 1000) -> dict[str, object]:
    q1 = table.q1
    out: dict[str, object] = {}
    mm = table.m_m
    for st in sorted(set(table.stat[q1].tolist())):
        m = q1 & (table.stat == st)
        out[f"mean_m_M_{st}"] = (int(m.sum()), float(mm[m].mean()))
    gap = np.abs(table.gap_m)
    for name, m in (("gap_0.05_0.10", q1 & (gap <= 0.10)), ("gap_gt_0.10", q1 & (gap > 0.10))):
        out[name] = (int(m.sum()), float(mm[m].mean()) if m.any() else float("nan"))
    out["toward_away_flat"] = (
        int((mm[q1] > 0).sum()),
        int((mm[q1] < 0).sum()),
        int((mm[q1] == 0).sum()),
    )
    # sign-shuffled placebo: permute the sign vector sign(p_M - mid0) across part-1 markets
    rng = np.random.default_rng(spec.SEED + seed_offset)
    sg = np.sign(table.gap_m[q1])
    mv = table.move[q1]
    pm = (
        np.array([(mv * rng.permutation(sg)).mean() for _ in range(n_shuffle)])
        if q1.any()
        else np.array([np.nan])
    )
    out["placebo_mean_and_95pct_range"] = (
        float(pm.mean()),
        float(np.percentile(pm, 2.5)),
        float(np.percentile(pm, 97.5)),
    )
    for name, m in (
        ("pplay_lt_0.9", q1 & (table.play_m < 0.9)),
        ("pplay_ge_0.9", q1 & (table.play_m >= 0.9)),
    ):
        out[name] = (int(m.sum()), float(mm[m].mean()) if m.any() else float("nan"))
    for name, m in (
        ("M_age_le_3h", q1 & (table.age_h <= 3.0)),
        ("M_age_gt_3h", q1 & (table.age_h > 3.0)),
    ):
        out[name] = (int(m.sum()), float(mm[m].mean()) if m.any() else float("nan"))
    return out


def three_sets(sel: list[Selected], dates: set[date]) -> dict[str, object]:
    """T510b report: {both, only T510, only T510b} with mean m_M and m_M' on each."""
    rows = [s for s in sel if s.day in dates]
    t0, t1 = build_table(rows, "T510"), build_table(rows, "T510b")
    a, b = t0.q1, t1.q1
    out: dict[str, object] = {}
    for name, m in (("both", a & b), ("only_T510", a & ~b), ("only_T510b", b & ~a)):
        out[name] = (
            int(m.sum()),
            float(t0.m_m[m].mean()) if m.any() else float("nan"),
            float(t1.m_m[m].mean()) if m.any() else float("nan"),
        )
    return out


def inactive_mid_close(table: Table, inactive_pids: set[int]) -> tuple[int, float]:
    """Descriptive: mean mid_close of part-1 markets whose player was later inactive."""
    m = table.q1 & np.isin(table.pid, list(inactive_pids))
    return int(m.sum()), float(table.mid_close[m].mean()) if m.any() else float("nan")


__all__ = [
    "PartResult",
    "RuleResult",
    "Table",
    "build_table",
    "evaluate",
    "horizon_date",
    "inactive_mid_close",
    "part_result",
    "qualifying_counts",
    "reported",
    "window_end",
    "resample_dist",
    "three_sets",
]
