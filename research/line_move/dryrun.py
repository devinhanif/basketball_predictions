"""Synthetic dryrun (T510 L-S2; T510b B4). Synthetic quotes and forecasts only; never a real mid.

World: each player-stat-game has several rungs. Each rung's efficient price follows a driftless
random
walk; the quote mid is that price plus a reverting AR(1) noise term; the quote is rounded to integer
cents
with a spread drawn from the OBSERVED spread distribution (all 389,331 two-sided quotes on the
2026-10-21 gate night), and with the OBSERVED stale-quote persistence (61.7% of consecutive
snapshots of a
ticker are identical on 10-21): a stale snapshot repeats the previous one. These are quote-structure
measurements (spread and repetition); no forecast was read and no movement toward any model was
formed.
The forecasts p_cond (M and B) and p_play are drawn independently of the quote noise.

Selection is T510's own code (``selection.select_psg``); T510b imports it. The analysis is the
frozen
``analysis.evaluate``. A planted move adds +0.02 toward M (T510) or M' (T510b) between S1 and close.

Gate criteria (written before any run):
  (i) null, primary scenario, 1,000 draws: T510 part-1 false-pass rate <= 5% and
      |mean part-2| <= 0.002;
      T510b false-pass rate (PASS) and part-1-alone rate both <= 2.5% at the 97.5% bounds;
  (ii) planted +0.02 passes in at least 95% of 100 draws (T510 toward M; T510b toward M').
Stress scenarios are reported and decide nothing.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy.stats import poisson

from research.line_move import analysis, spec
from research.line_move.selection import PSG, Forecast, Rung, Selected, SelectionLog, select_psg

# Observed on 2026-10-21 (quote structure only). Counts of two-sided quotes by spread in cents,
# 1..25.
SPREAD_COUNTS = [
    50119, 24985, 19780, 18554, 16794, 15688, 18540, 20892, 17393, 16679, 17308, 13460, 11495,
    9924, 7906, 7691, 8114, 7817, 6097, 5910, 6024, 4312, 3483, 3375, 2681,
]  # fmt: skip
SPREAD_TAIL_MASS = 389331 - sum(SPREAD_COUNTS)  # spreads 26..40, spread uniformly
OBSERVED_STALE_P = 0.617

SNAP_STEP_S = 120
SNAP_START_MIN = 300
N_PSG_PER_DATE = 150
N_DATES = 14
RUNGS = 5
KMAX = 80
NDRAWS_FORECAST = 200


@dataclass(frozen=True)
class Scenario:
    name: str
    sigma_e: float  # std of the reverting quote noise (probability units)
    phi: float  # AR(1) coefficient per snapshot
    sigma_w: float  # std of the efficient-price step per snapshot
    stale_p: float
    m_bias: float = 0.0  # log-space bias of M's mean (the model leans low when negative)
    drift: float = 0.0  # uniform drift of every quote mid from S1 to close (no information)


PRIMARY = Scenario("primary_observed_spread_and_staleness", 0.010, 0.80, 0.0015, OBSERVED_STALE_P)
STRESS = [
    Scenario("stress_persistent_noise", 0.030, 0.95, 0.0015, OBSERVED_STALE_P),
    Scenario("lean_drift_-1.5c", 0.010, 0.80, 0.0015, OBSERVED_STALE_P, m_bias=-0.35, drift=-0.015),
    Scenario("stress_very_stale", 0.020, 0.90, 0.0015, 0.90),
]


def _spread_pmf() -> tuple[np.ndarray, np.ndarray]:
    sp = np.arange(1, 26)
    tail = np.arange(26, 41)
    w = np.concatenate(
        [np.array(SPREAD_COUNTS, float), np.full(len(tail), SPREAD_TAIL_MASS / len(tail))]
    )
    return np.concatenate([sp, tail]), w / w.sum()


def _counts(draws: np.ndarray) -> np.ndarray:
    """c[k-1] = # of draws with stat >= k, for k = 1..KMAX (integer support, monotone)."""
    hist = np.bincount(draws, minlength=KMAX + 1)[: KMAX + 1]
    return np.cumsum(hist[::-1])[::-1][1 : KMAX + 1].astype(int)


@dataclass
class World:
    psgs: list[PSG]
    rung_mu_true: np.ndarray


def make_world(
    rng: np.random.Generator,
    sc: Scenario,
    rule: str | None = None,
    plant: float = 0.0,
    n_dates: int = N_DATES,
    n_per_date: int = N_PSG_PER_DATE,
) -> list[PSG]:
    """One synthetic window; ``plant`` > 0 adds a move toward the rule's p_M, S1 to close."""
    p_total = n_dates * n_per_date
    ts_grid = np.arange(-SNAP_START_MIN * 60, 0, SNAP_STEP_S)  # relative to tip; last = tip - 120 s
    j = len(ts_grid)
    mu = np.exp(rng.normal(np.log(8.0), 0.6, size=p_total)).clip(1.5, 30.0)
    base_k = np.rint(mu).astype(int)
    offs = np.array([-6, -3, 0, 3, 6])
    ks = np.maximum(1, base_k[:, None] + offs[None, :])  # (P, R)
    t0 = poisson.sf(ks - 1, mu[:, None])  # (P, R) efficient price at the start
    walk = np.cumsum(rng.normal(0.0, sc.sigma_w, size=(p_total, RUNGS, j)), axis=2)
    eff = np.clip(t0[:, :, None] + walk, 0.02, 0.98)
    e = np.zeros((p_total, RUNGS, j))
    z = rng.normal(0.0, 1.0, size=(p_total, RUNGS, j))
    e[:, :, 0] = sc.sigma_e * z[:, :, 0]
    sd_in = np.sqrt(1.0 - sc.phi**2) * sc.sigma_e
    for i in range(1, j):
        e[:, :, i] = sc.phi * e[:, :, i - 1] + sd_in * z[:, :, i]
    sp_vals, sp_p = _spread_pmf()
    spread = rng.choice(sp_vals, size=(p_total, RUNGS, j), p=sp_p)
    stale = rng.random((p_total, RUNGS, j)) < sc.stale_p
    stale[:, :, 0] = False
    src = np.where(stale, 0, np.arange(j)[None, None, :])
    src = np.maximum.accumulate(src, axis=2)  # a stale snapshot repeats the previous one

    # forecasts, independent of the quote noise
    play = np.where(
        rng.random(p_total) < 0.85, rng.uniform(0.97, 1.0, p_total), rng.uniform(0.2, 0.9, p_total)
    )
    mu_m0 = mu * np.exp(rng.normal(0.0, 0.25, p_total))
    mu_m = mu_m0 * np.exp(sc.m_bias)  # the lean is in M only; B keeps the unbiased mean
    mu_b = mu_m0 * np.exp(rng.normal(0.0, 0.20, p_total))
    cm = np.empty((p_total, KMAX), int)
    cb = np.empty((p_total, KMAX), int)
    for p in range(p_total):
        cm[p] = _counts(rng.poisson(mu_m[p], NDRAWS_FORECAST).clip(0, KMAX))
        cb[p] = _counts(rng.poisson(mu_b[p], NDRAWS_FORECAST).clip(0, KMAX))

    def render(ramp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mid = np.clip(eff + e + ramp, 0.01, 0.99)
        bid = np.clip(np.rint(100 * mid - spread / 2.0), 1, 98)
        ask = np.clip(bid + spread, 2, 99)
        bidr = np.take_along_axis(bid, src, axis=2)
        askr = np.take_along_axis(ask, src, axis=2)
        return bidr / 100.0, askr / 100.0

    j0 = int(np.searchsorted(ts_grid, -spec.S0_LEAD_MIN * 60, side="left"))
    j1 = int(np.searchsorted(ts_grid, ts_grid[j0] + spec.S1_GAP_MIN * 60, side="left"))
    jc = j - 1
    ramp_t = np.zeros(j)
    ramp_t[j1 : jc + 1] = np.linspace(0.0, 1.0, jc - j1 + 1)
    ramp_t[jc + 1 :] = 1.0
    drift_ramp = (sc.drift * ramp_t)[None, None, :]
    bid, ask = render(drift_ramp)
    if plant and rule is not None:
        mid0 = (np.rint(100 * bid[:, :, j0]) + np.rint(100 * ask[:, :, j0])) / 200.0
        pc = np.take_along_axis(cm, np.minimum(ks, KMAX) - 1, axis=1) / NDRAWS_FORECAST
        pc = np.where(ks > KMAX, 0.0, pc)
        pv = (
            pc * play[:, None]
            if rule == "T510"
            else pc * play[:, None] + (1 - play[:, None]) * mid0
        )
        sgn = np.sign(pv - mid0)
        bid, ask = render(drift_ramp + plant * sgn[:, :, None] * ramp_t[None, None, :])

    t_abs = ts_grid  # per-psg offset added below
    psgs: list[PSG] = []
    base_day = spec.FIRST_DATE
    for p in range(p_total):
        day = base_day + timedelta(days=p // n_per_date)
        tip_s = int(np.datetime64(f"{day.isoformat()}T23:00:00", "s").astype("int64"))
        t_arr = (tip_s + t_abs).astype(np.int64)
        rungs = [
            Rung(f"SYN-{p}-{r}", int(ks[p, r]), t_arr, bid[p, r], ask[p, r]) for r in range(RUNGS)
        ]
        # ET date of a 23:00Z tip equals the same calendar date (EDT/EST both put 18:00-19:00 on the
        # day)
        psgs.append(
            PSG(
                p,
                "pts",
                day,
                f"G{p // 6}",
                tip_s,
                tip_s - 600 * 60,
                Forecast(tuple(int(x) for x in cm[p]), NDRAWS_FORECAST, float(play[p])),
                Forecast(tuple(int(x) for x in cb[p]), NDRAWS_FORECAST, float(play[p])),
                rungs,
                t_arr,
            )
        )
    return psgs


def select_world(psgs: list[PSG]) -> list[Selected]:
    log = SelectionLog()
    out = []
    for p in psgs:
        s = select_psg(p, log)
        if s is not None:
            out.append(s)
    return out


@dataclass
class DrawOutcome:
    n_q1: int
    p1_mean: float
    p2_mean: float
    verdict: str
    part1_alone: bool
    at95: bool


def run_rule(sel: list[Selected], rule: str) -> DrawOutcome:
    t = analysis.build_table(sel, rule)
    r = analysis.evaluate(t)
    lv = r.level
    return DrawOutcome(
        int(t.q1.sum()),
        r.part1.mean,
        r.part2.mean,
        r.verdict,
        bool(r.part1.passed.get(lv, False)),
        r.passes_at_95,
    )


@dataclass
class ScenarioResult:
    scenario: str
    draws: int
    min_q1: int
    t510_part1_false_pass: float
    t510_pass: float
    t510_mean_part1: float
    t510_mean_part2: float
    t510b_part1_false_pass: float
    t510b_pass: float
    t510b_mean_part1: float
    t510b_mean_part2: float


def run_null(sc: Scenario, draws: int, seed: int) -> ScenarioResult:
    rng = np.random.default_rng(seed)
    a: list[DrawOutcome] = []
    b: list[DrawOutcome] = []
    for _ in range(draws):
        sel = select_world(make_world(rng, sc))
        a.append(run_rule(sel, "T510"))
        b.append(run_rule(sel, "T510b"))
    return ScenarioResult(
        sc.name,
        draws,
        min(min(x.n_q1 for x in a), min(x.n_q1 for x in b)),
        float(np.mean([x.part1_alone for x in a])),
        float(np.mean([x.verdict == "PASS" for x in a])),
        float(np.mean([x.p1_mean for x in a])),
        float(np.mean([x.p2_mean for x in a])),
        float(np.mean([x.part1_alone for x in b])),
        float(np.mean([x.verdict == "PASS" for x in b])),
        float(np.mean([x.p1_mean for x in b])),
        float(np.mean([x.p2_mean for x in b])),
    )


def run_planted(sc: Scenario, rule: str, draws: int, seed: int) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    out = [
        run_rule(select_world(make_world(rng, sc, rule, spec.DRYRUN_PLANT)), rule)
        for _ in range(draws)
    ]
    return {
        "pass_rate": float(np.mean([o.verdict == "PASS" for o in out])),
        "mean_p1": float(np.mean([o.p1_mean for o in out])),
        "mean_p2": float(np.mean([o.p2_mean for o in out])),
        "min_q1": float(min(o.n_q1 for o in out)),
    }


def run_dryrun(
    draws: int = spec.DRYRUN_DRAWS, planted_draws: int = 100, stress: bool = True
) -> dict[str, object]:
    spec.verify_texts()
    prim = run_null(PRIMARY, draws, 1)
    p510 = run_planted(PRIMARY, "T510", planted_draws, 2)
    p510b = run_planted(PRIMARY, "T510b", planted_draws, 3)
    crit = {
        "T510_i_part1_false_pass_le_5pct": prim.t510_part1_false_pass
        <= spec.DRYRUN_FALSE_PASS_T510,
        "T510_i_part2_mean_abs_le_0.002": abs(prim.t510_mean_part2) <= 0.002,
        "T510_windows_hold_300_part1": prim.min_q1 >= spec.MIN_QUALIFYING
        and p510["min_q1"] >= spec.MIN_QUALIFYING,
        "T510_ii_planted_passes": p510["pass_rate"] >= 0.95,
        "T510b_i_false_pass_le_2.5pct": prim.t510b_pass <= spec.DRYRUN_FALSE_PASS_T510B,
        "T510b_i_part1_alone_le_2.5pct": prim.t510b_part1_false_pass
        <= spec.DRYRUN_FALSE_PASS_T510B,
        "T510b_windows_hold_300_part1": prim.min_q1 >= spec.MIN_QUALIFYING
        and p510b["min_q1"] >= spec.MIN_QUALIFYING,
        "T510b_ii_planted_passes": p510b["pass_rate"] >= 0.95,
    }
    rec: dict[str, object] = {
        "code_sha256": spec.code_sha256(),
        "t510_sha256": spec.T510_SHA256,
        "t510b_sha256": spec.T510B_SHA256,
        "primary": asdict(prim),
        "planted_T510": p510,
        "planted_T510b": p510b,
        "criteria": crit,
        "T510_dryrun_pass": all(v for k, v in crit.items() if k.startswith("T510_")),
        "T510b_dryrun_pass": all(v for k, v in crit.items() if k.startswith("T510b_")),
    }
    if stress:
        rec["stress"] = [
            asdict(run_null(s, max(100, draws // 5), 10 + i)) for i, s in enumerate(STRESS)
        ]
    return rec


def write_record(rec: dict[str, object], path: Path) -> None:
    path.write_text(json.dumps(rec, indent=2))


def require_pass(path: Path, rule: str) -> None:
    """The real-mid readers call this first: a passing dryrun record for the current code."""
    spec.verify_texts()
    if not path.exists():
        raise DryrunMissing(f"no dryrun record at {path}; run `dryrun` first")
    rec = json.loads(path.read_text())
    if rec.get("code_sha256") != spec.code_sha256():
        raise DryrunMissing("dryrun record does not match the current selection/analysis code")
    key = "T510_dryrun_pass" if rule == "T510" else "T510b_dryrun_pass"
    if rule == "T510b" and not rec.get("T510_dryrun_pass"):
        raise DryrunMissing("T510 is unscoreable (its dryrun failed); T510b is not scored")
    if not rec.get(key):
        raise DryrunMissing(f"{rule} dryrun failed: the rule is reported unscoreable, not run")


class DryrunMissing(RuntimeError):
    pass


__all__ = ["PRIMARY", "STRESS", "require_pass", "run_dryrun"]
