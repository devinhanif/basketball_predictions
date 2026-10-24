"""One look of one rule: universe -> self-tests -> pooled R -> guards -> verdict -> tables.

The same function scores real and synthetic universes. Real-data safety lives in the CLI
(``__main__``):
reads refuse before their date, and the pre-window run uses :func:`preflight`, which computes no
return on real outcomes that it prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

from research.market_forward import stats as st
from research.market_forward.data import Universe
from research.market_forward.fee import PinnedFee
from research.market_forward.score import (
    Frame,
    GuardState,
    KellyRow,
    Pooled,
    SliceRow,
    Verdict,
    band_mask,
    bucket_labels,
    concentrated_games,
    coverage_guard,
    decide,
    leave_one_out,
    make_frame,
    paper_kelly,
    pooled,
    settlement_guard,
    slice_row,
    slices_by,
)
from research.market_forward.selftest import SelfTestResult, run_selftests
from research.market_forward.spec import (
    BOOTSTRAP_B,
    BOOTSTRAP_SEED,
    LOOK_ALPHA,
    MIN_DATES,
    MIN_GAMES,
    S2_MAX_AGE_MIN,
    RuleSpec,
)
from research.market_forward.strata import StrataCounts, StrataInputs, assign_strata


@dataclass(frozen=True)
class SensRow:
    name: str
    n: int
    n_games: int
    mean: float
    lo: float
    hi: float


@dataclass
class LookResult:
    spec: RuleSpec
    look: int
    alpha: float
    seed: int
    b: int
    fee_desc: str
    universe: Universe
    selftest: SelfTestResult
    guards: GuardState
    verdict: Verdict
    counts_only: bool
    n_pool: int = 0
    n_games: int = 0
    n_dates: int = 0
    n_dnp: int = 0
    pooled: Pooled | None = None
    buckets: list[SliceRow] = field(default_factory=list)
    kinds: list[SliceRow] = field(default_factory=list)
    stats_axis: list[SliceRow] = field(default_factory=list)
    spread_desc: SliceRow | None = None
    spread_overlap_n: int = 0
    r_band: Pooled | None = None
    sens: list[SensRow] = field(default_factory=list)
    concentration: list[tuple[str, float, float]] = field(default_factory=list)
    kelly: list[KellyRow | str] = field(default_factory=list)
    strata: list[tuple[str, list[SliceRow]]] = field(default_factory=list)
    strata_counts: StrataCounts | None = None
    strata_notes: list[str] = field(default_factory=list)
    cross_first: SliceRow | None = None
    age30_frac: float = 0.0
    projected: date | None = None


def project_date(
    sched_dates: list[date], window_start: date, cutoff: date, scored_games: int, scored_dates: int
) -> date | None:
    """First schedule date after the cutoff at which the observed scoring rates reach 300 / 50."""
    inwin = [d for d in sched_dates if window_start <= d <= cutoff]
    if not inwin or scored_games == 0:
        return None
    games_by_date: dict[date, int] = {}
    for d in sched_dates:
        games_by_date[d] = games_by_date.get(d, 0) + 1
    n_sched_games = sum(games_by_date[d] for d in games_by_date if window_start <= d <= cutoff)
    gfrac = scored_games / n_sched_games
    dfrac = scored_dates / len(set(inwin))
    eg, ed = float(scored_games), float(scored_dates)
    for d in sorted(x for x in games_by_date if x > cutoff):
        eg += gfrac * games_by_date[d]
        ed += dfrac
        if eg >= MIN_GAMES and ed >= MIN_DATES:
            return d
    return None


def _sens(
    name: str, fr: Frame, alpha: float, b: int, rng: np.random.Generator, col: str = "r_no"
) -> SensRow:
    if fr.n == 0:
        return SensRow(name, 0, 0, float("nan"), float("nan"), float("nan"))
    g, ng, d, nd = fr.clusters()
    v = fr.r_yes if col == "r_yes" else fr.r_no
    m = st.mean_with_ci(v, g, ng, d, nd, alpha, b, rng)
    return SensRow(name, fr.n, fr.n_games, m.mean, m.decision.lo, m.decision.hi)


def score_look(
    spec: RuleSpec,
    universe: Universe,
    fee: PinnedFee,
    look: int,
    *,
    b: int = BOOTSTRAP_B,
    seed: int = BOOTSTRAP_SEED,
    selftest_k: int = 200,
    selftest_b: int = 10_000,
    strata: StrataInputs | None = None,
    tips: dict[str, datetime] | None = None,
    schedule_dates: list[date] | None = None,
    window_start: date | None = None,
    cutoff: date | None = None,
) -> LookResult:
    """Score one look. Seed ``default_rng(2026)`` is created here: re-seeded at each read."""
    alpha = LOOK_ALPHA[look]
    rng = np.random.default_rng(seed)
    df = universe.df
    allf = make_frame(df, fee)
    pool = allf.subset(np.asarray(df["in_pool"].to_numpy(), dtype=bool))
    st_res = run_selftests(allf, pool, k=selftest_k, b=selftest_b, seed=seed)

    cov, cov_ok, gaps = coverage_guard(universe.coverage_by_date)  # type: ignore[arg-type]
    miss_frac, set_ok = settlement_guard(universe.scalar_total, len(universe.scalar_missing))
    axis_labels = pool.col("series") if spec.loo_axis == "series" else pool.col("stat")
    loo = leave_one_out(pool, axis_labels) if pool.n else {}
    loo_ok = all(v > 0.0 for v in loo.values())
    guards = GuardState(
        coverage=cov,
        coverage_ok=cov_ok,
        coverage_gap_dates=gaps,
        scalar_missing_frac=miss_frac,
        settlement_ok=set_ok,
        loo=loo,
        loo_ok=loo_ok,
        selftest_ok=st_res.ok,
    )
    res = LookResult(
        spec=spec,
        look=look,
        alpha=alpha,
        seed=seed,
        b=b,
        fee_desc=fee.describe(),
        universe=universe,
        selftest=st_res,
        guards=guards,
        verdict=Verdict("SELFTEST_FAILED", "counts only"),
        counts_only=True,
        n_pool=pool.n,
        n_games=pool.n_games,
        n_dates=pool.n_dates,
        n_dnp=int(pool.col("dnp").sum()) if pool.n else 0,
    )
    if not st_res.ok or pool.n == 0:
        if pool.n == 0 and st_res.ok:
            res.verdict = Verdict("INSUFFICIENT_SAMPLE", "no eligible settled contracts")
        return res

    # C3: both rules print counts and the projected date only below the frozen minimum; the
    # INSUFFICIENT_SAMPLE verdict is settled BEFORE any pooled R exists, so none can be shown.
    if pool.n_games < MIN_GAMES or pool.n_dates < MIN_DATES:
        res.verdict = decide(look, pool.n_games, pool.n_dates, 0.0, 0.0, 0.0, guards)
        if schedule_dates and window_start is not None and cutoff is not None:
            res.projected = project_date(
                schedule_dates, window_start, cutoff, pool.n_games, pool.n_dates
            )
        return res
    pl_ = pooled(pool, alpha, b, rng)
    res.pooled = pl_
    res.verdict = decide(
        look, pool.n_games, pool.n_dates, pl_.R, pl_.decision.lo, pl_.decision.hi, guards
    )
    res.counts_only = (
        res.verdict.name == "INSUFFICIENT_SAMPLE"
    )  # guards 8.1 / 8.3 can also land here
    if res.counts_only:
        res.pooled = None
    if schedule_dates and window_start is not None and cutoff is not None:
        res.projected = project_date(
            schedule_dates, window_start, cutoff, pool.n_games, pool.n_dates
        )
    if res.counts_only:
        return res

    labels = bucket_labels(spec, pool)
    res.buckets = slices_by(pool, labels, [x.name for x in spec.buckets])
    res.kinds = slices_by(pool, pool.col("kind"))
    res.stats_axis = slices_by(pool, pool.col("stat"))
    res.concentration = concentrated_games(pool)
    res.age30_frac = float(np.mean(pool.col("age_min") <= S2_MAX_AGE_MIN))
    # descriptive slice and band
    desc = allf.subset(~np.asarray(df["in_pool"].to_numpy(), dtype=bool))
    if desc.n:
        res.spread_desc = slice_row("KXNBASPREAD (descriptive)", desc)
        res.spread_overlap_n = desc.n
    bm = band_mask(spec, pool)
    if spec.rule_id == "UNDERS_FORWARD" and bm.any():
        res.r_band = pooled(pool.subset(bm), alpha, b, rng)
    # sensitivities
    s1 = make_frame(df, fee, rounded_fee=True).subset(
        np.asarray(df["in_pool"].to_numpy(), dtype=bool)
    )
    res.sens = [
        _sens("S1 fee rounded up per single contract", s1, alpha, b, rng),
        _sens(
            "S2 entry age <= 30 min only",
            pool.subset(pool.col("age_min") <= S2_MAX_AGE_MIN),
            alpha,
            b,
            rng,
        ),
        _sens("S3 YES-side mirror (r_yes)", pool, alpha, b, rng, col="r_yes"),
        _sens(
            "S5 DNP contracts excluded", pool.subset(~pool.col("dnp").astype(bool)), alpha, b, rng
        ),
    ]
    if spec.rule_id == "UNDERS_FORWARD":
        res.kelly = paper_kelly(spec, pool, alpha, b, rng)
        if strata is not None and tips is not None:
            _add_strata(res, spec, pool, strata, tips)
    return res


def _add_strata(
    res: LookResult, spec: RuleSpec, pool: Frame, inp: StrataInputs, tips: dict[str, datetime]
) -> None:
    # props and combos both go through the strata; combos have no stored forecast or Pinnacle
    # line and land in `unknown` / `no_pinnacle`, counted, never imputed.
    if pool.n == 0:
        return
    df2, sc = assign_strata(pool.df, tips, inp)
    res.strata_counts = sc
    fr2 = Frame(
        df2,
        pool.bid_c,
        pool.ask_c,
        pool.y_u,
        pool.fee_no_u,
        pool.fee_yes_u,
        pool.r_no_u,
        pool.r_yes_u,
    )
    res.cross_first = slice_row("z>0.5 and D=0", fr2.subset(fr2.col("cross").astype(bool)))
    res.strata = [
        ("z (vs our mean)", slices_by(fr2, fr2.col("z_bin"))),
        ("D (vs Pinnacle)", slices_by(fr2, fr2.col("d_bin"))),
    ]
    zb, lq = fr2.col("z_bin"), df2["line_minus_q50"].to_numpy()
    for lab in ("z<=-0.5", "-0.5<z<=0.5", "z>0.5"):
        m = (zb == lab) & ~np.isnan(lq)
        if m.any():
            res.strata_notes.append(
                f"mean (line - q50) in {lab}: {float(lq[m].mean()):+.2f} (n={int(m.sum())})"
            )
    pg = df2["pin_gap"].to_numpy()
    mg = ~np.isnan(pg)
    if mg.any():
        res.strata_notes.append(
            "D = 0: mean devigged Pinnacle over-probability minus Kalshi YES-mid "
            f"{float(pg[mg].mean()):+.4f} (n={int(mg.sum())}; descriptive only)"
        )


@dataclass(frozen=True)
class PreflightResult:
    """What the pre-window run may print: counts and pass/fail of integrity checks, no returns."""

    n_contracts: int
    n_pool: int
    n_games: int
    n_dates: int
    n_dnp: int
    identity_failures: int
    selftest_null_in_band: bool
    selftest_plant_ok: bool
    selftest_ok: bool
    coverage_ok: bool
    settlement_ok: bool


def preflight(
    spec: RuleSpec,
    universe: Universe,
    fee: PinnedFee,
    *,
    selftest_k: int = 200,
    selftest_b: int = 10_000,
    seed: int = BOOTSTRAP_SEED,
) -> PreflightResult:
    """Pipeline end to end on pre-window data. No R, no mean y, no CI is computed or returned."""
    df = universe.df
    allf = make_frame(df, fee)
    pool = allf.subset(np.asarray(df["in_pool"].to_numpy(), dtype=bool))
    t = run_selftests(allf, pool, k=selftest_k, b=selftest_b, seed=seed)
    _, cov_ok, _ = coverage_guard(universe.coverage_by_date)  # type: ignore[arg-type]
    _, set_ok = settlement_guard(universe.scalar_total, len(universe.scalar_missing))
    return PreflightResult(
        n_contracts=allf.n,
        n_pool=pool.n,
        n_games=pool.n_games if pool.n else 0,
        n_dates=pool.n_dates if pool.n else 0,
        n_dnp=int(pool.col("dnp").sum()) if pool.n else 0,
        identity_failures=t.identity_failures,
        selftest_null_in_band=t.null_in_band,
        selftest_plant_ok=t.plant_ok,
        selftest_ok=t.ok,
        coverage_ok=cov_ok,
        settlement_ok=set_ok,
    )


def schedule_dates_from_tips(tips: set) -> list[date]:  # type: ignore[type-arg]
    from research.market_forward.data import et_date

    return sorted(et_date(t) for t in tips)


__all__ = [
    "LookResult",
    "PreflightResult",
    "SensRow",
    "preflight",
    "project_date",
    "schedule_dates_from_tips",
    "score_look",
]
