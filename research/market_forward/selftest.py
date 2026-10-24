"""Guard 8.6: the scorer's self-tests, run before every look (both rules, identical text).

(a) The Section 4 identity on every contract, in exact integers.
(b) Calibrated-null coverage: K = 200 synthetic outcome sets on the look's own contracts. Each
    ladder (one player-stat, one team's spread or team total, one game total) gets one uniform
    draw ``u`` and a rung is YES iff ``u < mid``, so ladders stay nested. DNPs are not simulated
    (they keep their real, fixed scalar payoff). The game and date structure is the real one. The
    decision-CI one-sided 5% bound must exclude the known true ``E[r_no]`` in 2% to 9% of the sets.
(c) Planted edge: the same sets with every YES probability multiplied by 0.80. The point estimate
    must fall within 0.5c of the known true ``R`` and the decision CI must cover it.

Interpretations the text leaves open (all listed in the hand-off): the 5% bound is the LOWER bound
(the side a PASS uses; the upper-side rate is reported too); (c) is judged on the mean over the
sets (a per-set 0.5c test fails routinely at the stated SE) and "the CI covers" is judged as the
lower bound exceeding the truth in at most 9% of sets.

The self-test never prints a return on real outcomes: it draws its own outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.market_forward import stats as st
from research.market_forward.fee import UNIT
from research.market_forward.score import Frame, identity_failures
from research.market_forward.spec import (
    SELFTEST_ALPHA,
    SELFTEST_K,
    SELFTEST_NULL_BAND,
    SELFTEST_PLANT_FACTOR,
    SELFTEST_PLANT_TOL,
)


@dataclass(frozen=True)
class SetOutcome:
    point: float
    truth: float
    lo: float
    hi: float


@dataclass(frozen=True)
class SelfTestResult:
    identity_failures: int
    identity_n: int
    k: int
    null_lo_excl_rate: float
    null_hi_excl_rate: float
    null_in_band: bool
    plant_mean_point: float
    plant_mean_truth: float
    plant_abs_diff: float
    plant_frac_sets_within: float
    plant_lo_excl_rate: float
    plant_hi_excl_rate: float
    plant_ok: bool
    seeds: str
    ok: bool


def _run_sets(
    fr: Frame,
    factor: float,
    u: np.ndarray,
    ladder_codes: np.ndarray,
    boot_rng: np.random.Generator,
    b: int,
) -> list[SetOutcome]:
    g, ng, d, nd = fr.clusters()
    dnp = fr.col("dnp")
    p_yes = np.where(dnp, fr.y, np.minimum(1.0, factor * fr.mid))
    # known true E[r_no] per contract: DNPs keep their real fixed payoff
    exp_r = (1.0 - p_yes) - fr.p_no_u / UNIT - fr.fee_no_u / UNIT
    truth = float(exp_r.mean())
    base = (-(fr.p_no_u + fr.fee_no_u) / UNIT).astype(float)
    ones = np.ones(fr.n)
    out: list[SetOutcome] = []
    for s in range(u.shape[0]):
        yes = (u[s][ladder_codes] < p_yes) & ~dnp
        y = np.where(dnp, fr.y, yes.astype(float))
        r = (1.0 - y) + base
        cols = np.column_stack([r, ones])
        gd = st.bootstrap_sums(st.cluster_stats(g, cols, ng), b, boot_rng)
        dd = st.bootstrap_sums(st.cluster_stats(d, cols, nd), b, boot_rng)
        _, _, dec = st.decision_interval(gd[:, 0] / gd[:, 1], dd[:, 0] / dd[:, 1], SELFTEST_ALPHA)
        out.append(SetOutcome(float(r.mean()), truth, dec.lo, dec.hi))
    return out


def run_selftests(
    all_scored: Frame,
    pool: Frame,
    *,
    k: int = SELFTEST_K,
    b: int = 10_000,
    seed: int = 2026,
) -> SelfTestResult:
    """Run (a), (b), (c). ``pool`` = the decision pool; ``all_scored`` adds descriptive rows."""
    fails = identity_failures(all_scored)
    set_rng = np.random.default_rng([seed, 1])
    boot_rng = np.random.default_rng([seed, 2])
    lad, n_lad = st.codes(pool.col("ladder"))
    u = set_rng.random((k, n_lad))
    null = _run_sets(pool, 1.0, u, lad, boot_rng, b)
    plant = _run_sets(pool, SELFTEST_PLANT_FACTOR, u, lad, boot_rng, b)
    lo_ex = float(np.mean([s.lo > s.truth for s in null]))
    hi_ex = float(np.mean([s.hi < s.truth for s in null]))
    in_band = SELFTEST_NULL_BAND[0] <= lo_ex <= SELFTEST_NULL_BAND[1]
    mp = float(np.mean([s.point for s in plant]))
    mt = float(np.mean([s.truth for s in plant]))
    within = float(np.mean([abs(s.point - s.truth) <= SELFTEST_PLANT_TOL for s in plant]))
    p_lo_ex = float(np.mean([s.lo > s.truth for s in plant]))
    p_hi_ex = float(np.mean([s.hi < s.truth for s in plant]))
    plant_ok = abs(mp - mt) <= SELFTEST_PLANT_TOL and p_lo_ex <= SELFTEST_NULL_BAND[1]
    ok = fails == 0 and in_band and plant_ok
    return SelfTestResult(
        identity_failures=fails,
        identity_n=all_scored.n,
        k=k,
        null_lo_excl_rate=lo_ex,
        null_hi_excl_rate=hi_ex,
        null_in_band=in_band,
        plant_mean_point=mp,
        plant_mean_truth=mt,
        plant_abs_diff=abs(mp - mt),
        plant_frac_sets_within=within,
        plant_lo_excl_rate=p_lo_ex,
        plant_hi_excl_rate=p_hi_ex,
        plant_ok=plant_ok,
        seeds=f"default_rng([{seed}, 1]) outcomes; default_rng([{seed}, 2]) bootstrap",
        ok=ok,
    )
