"""Plain-text and JSON reports. Prints counts; returns appear only after the self-tests pass."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from research.market_forward.engine import LookResult, PreflightResult, SensRow
from research.market_forward.score import KellyRow, SliceRow
from research.market_forward.spec import LOOK_READ_DATE, SLICE_MIN_GAMES


def _c(x: float) -> str:
    return f"{100 * x:+.2f}c"


def _slice_line(r: SliceRow) -> str:
    if r.insufficient:
        return f"  {r.name:<28} n={r.n} games={r.n_games}  insufficient sample"
    return (
        f"  {r.name:<28} n={r.n:<6} games={r.n_games:<4} dates={r.n_dates:<3} dnp={r.n_dnp:<3} "
        f"mid={r.mean_mid:.4f} y={r.mean_y:.4f} spread={100 * r.mean_spread:.2f}c "
        f"fee={100 * r.mean_fee:.2f}c r_no={_c(r.mean_r_no)}"
    )


def _sens_line(s: SensRow) -> str:
    if s.n == 0:
        return f"  {s.name:<42} n=0"
    return (
        f"  {s.name:<42} n={s.n:<6} games={s.n_games:<4} mean={_c(s.mean)} "
        f"decision CI [{_c(s.lo)}, {_c(s.hi)}]"
    )


def format_look(res: LookResult) -> str:
    sp, u, g, t = res.spec, res.universe, res.guards, res.selftest
    L: list[str] = []
    a = L.append
    a(f"{sp.rule_id} look {res.look} (read {LOOK_READ_DATE[res.look]})")
    a(f"frozen section sha256 verified against {sp.frozen_sha256[:8]}")
    a(
        f"seed default_rng({res.seed}) B={res.b}; one-sided alpha={res.alpha} "
        f"(decision CI = {100 * (1 - 2 * res.alpha):.0f}% two-sided percentile; WIDER of game/date)"
    )
    a(res.fee_desc)
    if u.settled_by is not None:
        a(f"settled_by (naive UTC; read date 23:59:59 ET): {u.settled_by.isoformat()}")
        a(f"db copy sha256: {u.db_sha256}")
        rr = "none" if u.raw_range is None else f"{u.raw_range[0]} .. {u.raw_range[1]}"
        a(f"raw page dirs read: {rr}")
    a("")
    a("SELF-TESTS (guard 8.6)")
    a(f"  (a) identity failures {t.identity_failures} of {t.identity_n} contracts")
    a(
        f"  (b) calibrated null, K={t.k}: lower bound excludes truth in "
        f"{100 * t.null_lo_excl_rate:.1f}% of sets "
        f"(band 2-9%: {'ok' if t.null_in_band else 'FAIL'}); "
        f"upper side {100 * t.null_hi_excl_rate:.1f}% (info)"
    )
    a(
        f"  (c) planted x0.80: |mean point - mean true| = {100 * t.plant_abs_diff:.3f}c "
        f"(tol 0.5c); sets within 0.5c {100 * t.plant_frac_sets_within:.0f}%; lower bound excludes "
        f"truth in {100 * t.plant_lo_excl_rate:.1f}% (<= 9%): {'ok' if t.plant_ok else 'FAIL'}; "
        f"upper side {100 * t.plant_hi_excl_rate:.1f}% (info)"
    )
    a(f"  seeds: {t.seeds}")
    a("")
    a("COUNTS")
    a(f"  games: {dict(u.game_counts)}")
    a(f"  contracts: {dict(u.counts)}")
    cup = sum(
        n for d, n in u.no_schedule_by_date.items() if date(2026, 12, 4) <= d <= date(2026, 12, 11)
    )
    a(
        f"  no_schedule_match by ET date: {dict(sorted(u.no_schedule_by_date.items()))}; "
        f"Cup week (2026-12-04..11) subtotal {cup}"
    )
    a(
        f"  decision pool: n={res.n_pool} games={res.n_games} dates={res.n_dates} "
        f"n_dnp={res.n_dnp}; "
        f"prop/combo with player_id NULL: {len(u.null_player_props)}"
    )
    if u.null_player_props:
        a(f"  null-player_id tickers (first 20): {u.null_player_props[:20]}")
    if u.scalar_missing:
        a(f"  FLAG scalar markets missing settlement_value_dollars: {u.scalar_missing[:20]}")
    cov = "n/a" if g.coverage is None else f"{100 * g.coverage:.1f}%"
    a(f"  entry coverage (guard 8.1): {cov}; dates below 70%: {g.coverage_gap_dates}")
    mf = "n/a" if g.scalar_missing_frac is None else f"{100 * g.scalar_missing_frac:.2f}%"
    a(f"  scalar missing (guard 8.3): {mf} of {u.scalar_total} scalar markets")
    a(f"  fraction of entries with age <= 30 min (S2): {100 * res.age30_frac:.1f}%")
    if res.projected is not None:
        a(f"  projected date both counts are reached: {res.projected}")
    a("")
    a(f"VERDICT: {res.verdict.name}  ({res.verdict.detail})")
    if res.counts_only or res.pooled is None:
        a("(counts only; no return is printed at this look)")
        return "\n".join(L)
    p = res.pooled
    a("")
    a("POOLED (equal weight per contract)")
    a(f"  n={p.n} games={p.n_games} dates={p.n_dates} n_dnp={p.n_dnp}")
    a(f"  R = {_c(p.R)}  (floor +1.00c); ROI per dollar paid = {100 * p.roi:+.2f}%")
    a(f"  game-clustered CI  [{_c(p.ci_game.lo)}, {_c(p.ci_game.hi)}]")
    a(f"  date-clustered CI  [{_c(p.ci_date.lo)}, {_c(p.ci_date.hi)}]")
    a(
        f"  DECISION CI ({p.decision.clustering}) [{_c(p.decision.lo)}, {_c(p.decision.hi)}] "
        f"at one-sided {res.alpha}"
    )
    a(
        f"  calibration y = a + b*mid: a={p.intercept:+.4f} b={p.slope:.3f} "
        f"(decision CI for b [{p.slope_decision.lo:.3f}, {p.slope_decision.hi:.3f}]); "
        f"mean(y)-mean(mid) = {p.gap:+.4f}  (descriptive)"
    )
    a(
        f"  mean mid {p.mean_mid:.4f}  mean y {p.mean_y:.4f}  "
        f"mean spread {100 * p.mean_spread:.2f}c  "
        f"mean fee {100 * p.mean_fee:.2f}c  YES-side mirror {_c(p.mean_r_yes)} (not decided)"
    )
    a("")
    a("GUARDS")
    a(
        f"  leave-one-{res.spec.loo_axis}-out R (point): "
        + ", ".join(f"{k}={_c(v)}" for k, v in sorted(g.loo.items()))
    )
    a(f"  all leave-one-out R > 0: {g.loo_ok}")
    if res.concentration:
        for game, share, r_wo in res.concentration:
            a(f"  game {game} is {100 * share:.1f}% of contracts (> 3%); R without it {_c(r_wo)}")
    else:
        a("  no game above 3% of eligible contracts")
    a("")
    a(f"BUCKETS (slices under {SLICE_MIN_GAMES} games print 'insufficient sample')")
    for r in res.buckets:
        a(_slice_line(r))
    a("KINDS")
    for r in res.kinds:
        a(_slice_line(r))
    a("STATS")
    for r in res.stats_axis:
        a(_slice_line(r))
    if res.spread_desc is not None:
        a("KXNBASPREAD (descriptive only; overlap with SPREAD_TAIL_NO Part B not decidable here)")
        a(_slice_line(res.spread_desc))
        a(f"  eligible KXNBASPREAD contracts (upper bound of the overlap): {res.spread_overlap_n}")
    if res.r_band is not None:
        b = res.r_band
        a(
            f"R_band (long-shot band U1-U3, OVERLAPS LONGSHOT props): n={b.n} games={b.n_games} "
            f"R={_c(b.R)} decision CI [{_c(b.decision.lo)}, {_c(b.decision.hi)}]"
        )
    if res.cross_first is not None:
        a("CROSS CELL (printed first in the spec; NOT a second decision)")
        a(_slice_line(res.cross_first))
    for name, rows in res.strata:
        a(f"STRATA {name}")
        for r in rows:
            a(_slice_line(r))
    for note in res.strata_notes:
        a(f"  {note}")
    if res.strata_counts is not None:
        sc = res.strata_counts
        a(
            f"  strata unknowns: z unknown {sc.z_unknown}, no_pinnacle {sc.pinnacle_none}, "
            f"cross cell n {sc.cross_n}"
        )
    a("")
    a("SENSITIVITIES (descriptive, no decision weight)")
    for s in res.sens:
        a(_sens_line(s))
    if res.kelly:
        a("")
        a(
            "PAPER KELLY (PAPER ONLY. Not a stake. Not advice.) "
            "percent of a notional bankroll, per bucket"
        )
        for k in res.kelly:
            if isinstance(k, KellyRow):
                a(
                    f"  {k.bucket}: cost {k.cost:.4f}, q_lo {k.q_lo:.4f}, "
                    f"full {100 * k.full:.2f}%, half {100 * k.half:.2f}%"
                )
            else:
                a(f"  {k}: not printed (fewer than {SLICE_MIN_GAMES} games)")
    return "\n".join(L)


def format_preflight(
    rule_id: str, pf: PreflightResult, fee_desc: str, extra: dict[str, Any]
) -> str:
    L = [
        f"{rule_id} PREFLIGHT (pre-window data 2026-10-20..24; NO returns computed or printed)",
        fee_desc,
        f"counts: {extra}",
        f"contracts scored {pf.n_contracts}; decision pool {pf.n_pool} over {pf.n_games} games, "
        f"{pf.n_dates} dates; n_dnp {pf.n_dnp}",
        f"identity failures: {pf.identity_failures} (pass={pf.identity_failures == 0})",
        f"self-test null band: {'pass' if pf.selftest_null_in_band else 'FAIL'}; "
        f"planted edge: {'pass' if pf.selftest_plant_ok else 'FAIL'}; all: "
        f"{'pass' if pf.selftest_ok else 'FAIL'}",
        f"coverage guard (>= 70%): {'pass' if pf.coverage_ok else 'FAIL'}; "
        f"settlement guard (<= 2% missing): {'pass' if pf.settlement_ok else 'FAIL'}",
    ]
    return "\n".join(L)


def look_json(res: LookResult) -> str:
    p = res.pooled
    d: dict[str, Any] = {
        "rule": res.spec.rule_id,
        "frozen_sha256": res.spec.frozen_sha256,
        "look": res.look,
        "verdict": res.verdict.name,
        "detail": res.verdict.detail,
        "seed": res.seed,
        "B": res.b,
        "alpha": res.alpha,
        "fee": res.fee_desc,
        "n_pool": res.n_pool,
        "n_games": res.n_games,
        "n_dates": res.n_dates,
        "n_dnp": res.n_dnp,
        "selftest_ok": res.selftest.ok,
        "counts": dict(res.universe.counts),
        "game_counts": dict(res.universe.game_counts),
    }
    if p is not None and not res.counts_only:
        d.update(
            {
                "R": p.R,
                "decision_lo": p.decision.lo,
                "decision_hi": p.decision.hi,
                "decision_clustering": p.decision.clustering,
                "game_ci": [p.ci_game.lo, p.ci_game.hi],
                "date_ci": [p.ci_date.lo, p.ci_date.hi],
            }
        )
    return json.dumps(d, indent=2, default=str)
