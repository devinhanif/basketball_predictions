"""Returns, the integrity identity, the pooled metric, slices, guards and the verdict.

Primary metric (both rules): ``R`` = mean of ``r_no`` over the pooled eligible settled contracts,
equal
weight per contract, ``r_no = (1 - y) - P_no - fee(P_no)`` with ``P_no = (100 - bid_c) / 100``.
All quantities are exact integer micro-dollars until the final division.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from research.market_forward import stats as st
from research.market_forward.fee import CENT, UNIT, PinnedFee
from research.market_forward.spec import (
    CONCENTRATION_MAX,
    COVERAGE_MIN,
    FLOOR,
    MIN_DATES,
    MIN_GAMES,
    SCALAR_MISSING_MAX,
    SLICE_MIN_GAMES,
    Bucket,
    RuleSpec,
)


def bucket_of(spec: RuleSpec, bid_c: int, ask_c: int) -> str:
    s = bid_c + ask_c
    for b in spec.buckets:
        if b.contains(s):
            return b.name
    return "none"


@dataclass
class Frame:
    """Column arrays for a set of contracts (one row per ticker)."""

    df: pl.DataFrame
    bid_c: np.ndarray
    ask_c: np.ndarray
    y_u: np.ndarray
    fee_no_u: np.ndarray
    fee_yes_u: np.ndarray
    r_no_u: np.ndarray
    r_yes_u: np.ndarray

    @property
    def n(self) -> int:
        return len(self.bid_c)

    @property
    def mid(self) -> np.ndarray:
        return np.asarray((self.bid_c + self.ask_c) / 200.0)

    @property
    def y(self) -> np.ndarray:
        return self.y_u / UNIT

    @property
    def r_no(self) -> np.ndarray:
        return self.r_no_u / UNIT

    @property
    def r_yes(self) -> np.ndarray:
        return self.r_yes_u / UNIT

    @property
    def p_no_u(self) -> np.ndarray:
        return (100 - self.bid_c) * CENT

    @property
    def spread(self) -> np.ndarray:
        return np.asarray((self.ask_c - self.bid_c) / 100.0)

    def col(self, name: str) -> np.ndarray:
        return self.df[name].to_numpy()

    def subset(self, mask: np.ndarray) -> Frame:
        return Frame(
            self.df.filter(pl.Series(mask)),
            self.bid_c[mask],
            self.ask_c[mask],
            self.y_u[mask],
            self.fee_no_u[mask],
            self.fee_yes_u[mask],
            self.r_no_u[mask],
            self.r_yes_u[mask],
        )

    def clusters(self) -> tuple[np.ndarray, int, np.ndarray, int]:
        g, ng = st.codes(self.col("game"))
        d, nd = st.codes(self.col("date"))
        return g, ng, d, nd

    @property
    def n_games(self) -> int:
        return int(self.df["game"].n_unique())

    @property
    def n_dates(self) -> int:
        return int(self.df["date"].n_unique())


def make_frame(df: pl.DataFrame, fee: PinnedFee, *, rounded_fee: bool = False) -> Frame:
    """Attach exact returns. ``rounded_fee`` is sensitivity S1 (whole cent per single contract)."""
    series = df["series"].to_list()
    bid = df["bid_c"].to_numpy().astype(np.int64)
    ask = df["ask_c"].to_numpy().astype(np.int64)
    y_u = df["y_u"].to_numpy().astype(np.int64)
    ffn = fee.fee_micro_rounded_up if rounded_fee else fee.fee_micro
    cache: dict[tuple[str, int], int] = {}

    def f(s: str, c: int) -> int:
        k = (s, c)
        v = cache.get(k)
        if v is None:
            v = cache[k] = ffn(s, c)
        return v

    p_no_c = 100 - bid
    fee_no = np.array([f(s, int(c)) for s, c in zip(series, p_no_c, strict=True)], dtype=np.int64)
    fee_yes = np.array([f(s, int(c)) for s, c in zip(series, ask, strict=True)], dtype=np.int64)
    r_no = (UNIT - y_u) - p_no_c * CENT - fee_no
    r_yes = y_u - ask * CENT - fee_yes
    return Frame(df, bid, ask, y_u, fee_no, fee_yes, r_no, r_yes)


def identity_failures(fr: Frame) -> int:
    """Guard 8.6(a): ``r_yes + r_no == -(ask_c - bid_c)/100 - fee(P_yes) - fee(P_no)`` exactly."""
    lhs = fr.r_yes_u + fr.r_no_u
    rhs = -(fr.ask_c - fr.bid_c) * CENT - fr.fee_yes_u - fr.fee_no_u
    return int(np.count_nonzero(lhs != rhs))


@dataclass(frozen=True)
class Pooled:
    n: int
    n_games: int
    n_dates: int
    n_dnp: int
    R: float
    roi: float
    ci_game: st.Interval
    ci_date: st.Interval
    decision: st.Interval
    slope: float
    intercept: float
    slope_decision: st.Interval
    gap: float  # mean(y) - mean(mid)
    mean_y: float
    mean_mid: float
    mean_spread: float
    mean_fee: float
    mean_r_yes: float


def pooled(fr: Frame, alpha: float, b: int, rng: np.random.Generator) -> Pooled:
    """Pooled R with both clustered intervals, the decision interval and the calibration fit."""
    g, ng, d, nd = fr.clusters()
    x, y, r = fr.mid, fr.y, fr.r_no
    cols = np.column_stack([r, np.ones(fr.n), x, y, x * x, x * y])
    gdist = st.bootstrap_sums(st.cluster_stats(g, cols, ng), b, rng)
    ddist = st.bootstrap_sums(st.cluster_stats(d, cols, nd), b, rng)
    rg, rd = gdist[:, 0] / gdist[:, 1], ddist[:, 0] / ddist[:, 1]
    gi, di, dec = st.decision_interval(rg, rd, alpha)
    _, bg = st.ols_from_sums(gdist[:, 1:])
    _, bd = st.ols_from_sums(ddist[:, 1:])
    sgi, sdi, sdec = st.decision_interval(bg, bd, alpha)
    tot = np.array([[fr.n, x.sum(), y.sum(), (x * x).sum(), (x * y).sum()]])
    a_hat, b_hat = st.ols_from_sums(tot)
    cost = fr.p_no_u + fr.fee_no_u
    return Pooled(
        n=fr.n,
        n_games=fr.n_games,
        n_dates=fr.n_dates,
        n_dnp=int(fr.col("dnp").sum()),
        R=float(r.mean()),
        roi=float(fr.r_no_u.sum() / cost.sum()),
        ci_game=gi,
        ci_date=di,
        decision=dec,
        slope=float(b_hat[0]),
        intercept=float(a_hat[0]),
        slope_decision=sdec,
        gap=float(y.mean() - x.mean()),
        mean_y=float(y.mean()),
        mean_mid=float(x.mean()),
        mean_spread=float(fr.spread.mean()),
        mean_fee=float(fr.fee_no_u.mean() / UNIT),
        mean_r_yes=float(fr.r_yes.mean()),
    )


def mean_r(fr: Frame) -> float:
    return float(fr.r_no.mean()) if fr.n else float("nan")


@dataclass(frozen=True)
class SliceRow:
    name: str
    n: int
    n_games: int
    n_dates: int
    n_dnp: int
    mean_mid: float
    mean_y: float
    mean_spread: float
    mean_fee: float
    mean_r_no: float
    insufficient: bool


def slice_row(name: str, fr: Frame) -> SliceRow:
    if fr.n == 0 or fr.n_games < SLICE_MIN_GAMES:
        return SliceRow(name, fr.n, fr.n_games, fr.n_dates, 0, 0, 0, 0, 0, 0, True)
    return SliceRow(
        name,
        fr.n,
        fr.n_games,
        fr.n_dates,
        int(fr.col("dnp").sum()),
        float(fr.mid.mean()),
        float(fr.y.mean()),
        float(fr.spread.mean()),
        float(fr.fee_no_u.mean() / UNIT),
        mean_r(fr),
        False,
    )


def slices_by(fr: Frame, labels: np.ndarray, order: Sequence[str] | None = None) -> list[SliceRow]:
    names = list(order) if order is not None else sorted({str(v) for v in labels})
    return [slice_row(nm, fr.subset(labels == nm)) for nm in names]


def bucket_labels(spec: RuleSpec, fr: Frame) -> np.ndarray:
    return np.array(
        [bucket_of(spec, int(b), int(a)) for b, a in zip(fr.bid_c, fr.ask_c, strict=True)],
        dtype=object,
    )


def leave_one_out(fr: Frame, labels: np.ndarray) -> dict[str, float]:
    """``R`` recomputed leaving out each label in turn (point estimates; guard 8.2 / S4)."""
    out: dict[str, float] = {}
    for lab in sorted({str(v) for v in labels}):
        sub = fr.subset(labels != lab)
        out[lab] = mean_r(sub)
    return out


def concentrated_games(fr: Frame) -> list[tuple[str, float, float]]:
    """Games above 3% of eligible contracts, with ``R`` shown without each (guard 8.2)."""
    games = fr.col("game")
    uniq, cnt = np.unique(games.astype(str), return_counts=True)
    out = []
    for g, c in zip(uniq, cnt, strict=True):
        share = c / fr.n
        if share > CONCENTRATION_MAX:
            out.append((str(g), float(share), mean_r(fr.subset(games.astype(str) != g))))
    return out


@dataclass
class GuardState:
    coverage: float | None
    coverage_ok: bool
    coverage_gap_dates: list[str]
    scalar_missing_frac: float | None
    settlement_ok: bool
    loo: dict[str, float]
    loo_ok: bool
    selftest_ok: bool
    notes: list[str] = field(default_factory=list)


def coverage_guard(by_date: dict[object, tuple[int, int]]) -> tuple[float | None, bool, list[str]]:
    num = sum(v[0] for v in by_date.values())
    den = sum(v[1] for v in by_date.values())
    if den == 0:
        return None, False, []
    frac = num / den
    gaps = sorted(str(d) for d, (a, b) in by_date.items() if b and a / b < COVERAGE_MIN)
    return frac, frac >= COVERAGE_MIN, gaps


def settlement_guard(scalar_total: int, scalar_missing: int) -> tuple[float | None, bool]:
    if scalar_total == 0:
        return 0.0, True
    frac = scalar_missing / scalar_total
    return frac, frac <= SCALAR_MISSING_MAX


@dataclass(frozen=True)
class Verdict:
    name: str
    detail: str


def decide(
    look: int,
    n_games: int,
    n_dates: int,
    R: float,
    lo: float,
    hi: float,
    g: GuardState,
) -> Verdict:
    """The frozen verdict logic (LONGSHOT Section 6, UNDERS Section 6, guards of Section 8).

    ``lo`` / ``hi`` are the decision interval bounds at the look's one-sided level.
    """
    if not g.selftest_ok:
        return Verdict("SELFTEST_FAILED", "a scorer self-test failed; counts only")
    if not g.coverage_ok:
        return Verdict(
            "INSUFFICIENT_SAMPLE", f"capture: entry coverage below {COVERAGE_MIN:.0%} (guard 8.1)"
        )
    if not g.settlement_ok:
        return Verdict(
            "INSUFFICIENT_SAMPLE",
            f"settlement: scalar values missing above {SCALAR_MISSING_MAX:.0%}",
        )
    if n_games < MIN_GAMES or n_dates < MIN_DATES:
        return Verdict(
            "INSUFFICIENT_SAMPLE",
            f"n games {n_games} (< {MIN_GAMES}) or n dates {n_dates} (< {MIN_DATES})",
        )
    if R >= FLOOR and lo > 0.0:
        if not g.loo_ok:
            return Verdict(
                "KEEP_SHADOWING" if look == 1 else "INCONCLUSIVE",
                "would be PASS, but a leave-one-out R <= 0 (guard 8.2)",
            )
        return Verdict("PASS", "R >= floor and the decision lower bound > 0")
    if hi < FLOOR:
        return Verdict("NO_POSITIVE_EV_FOUND", "the decision upper bound is below the floor")
    return Verdict("KEEP_SHADOWING" if look == 1 else "INCONCLUSIVE", "bound does not clear 0")


@dataclass(frozen=True)
class KellyRow:
    bucket: str
    cost: float
    q_lo: float
    full: float
    half: float


def paper_kelly(
    spec: RuleSpec, fr: Frame, alpha: float, b: int, rng: np.random.Generator
) -> list[KellyRow | str]:
    """UNDERS Section 9, PAPER ONLY. Per bucket (>= 30 games), never per stratum.

    ``c`` = bucket mean of ``P_no + fee``; ``q_lo`` = 10th percentile of the decision-CI bootstrap
    distribution of the bucket's mean NO payoff ``1 - y``; ``f = max(0, (q_lo - c) / (1 - c))``.
    """
    labels = bucket_labels(spec, fr)
    out: list[KellyRow | str] = []
    for bk in spec.buckets:
        sub = fr.subset(labels == bk.name)
        if sub.n == 0 or sub.n_games < SLICE_MIN_GAMES:
            out.append(bk.name)
            continue
        g, ng, d, nd = sub.clusters()
        dist, _ = st.mean_dist_decision(1.0 - sub.y, g, ng, d, nd, alpha, b, rng)
        q_lo = float(np.percentile(dist, 10.0))
        c = float(((sub.p_no_u + sub.fee_no_u) / UNIT).mean())
        full = max(0.0, (q_lo - c) / (1.0 - c))
        out.append(KellyRow(bk.name, c, q_lo, full, full / 2.0))
    return out


def band_mask(spec: RuleSpec, fr: Frame) -> np.ndarray:
    labels = bucket_labels(spec, fr)
    return np.isin(labels, list(spec.band_buckets))


__all__ = [
    "Bucket",
    "Frame",
    "GuardState",
    "KellyRow",
    "Pooled",
    "SliceRow",
    "Verdict",
    "band_mask",
    "bucket_labels",
    "concentrated_games",
    "coverage_guard",
    "decide",
    "identity_failures",
    "leave_one_out",
    "make_frame",
    "mean_r",
    "paper_kelly",
    "pooled",
    "settlement_guard",
    "slice_row",
    "slices_by",
]
