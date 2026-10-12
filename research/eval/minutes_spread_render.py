# ruff: noqa: E501
# mypy: disable-error-code="arg-type,no-untyped-call,unused-ignore,union-attr,index,assignment,operator,no-any-return,misc,call-overload,attr-defined,type-var"
"""Render reports/minutes_spread_eda_2026-10-12.md from the raw EDA json (descriptive; see minutes_spread_eda.py)."""

from __future__ import annotations

import json
import sys
from math import erf, sqrt
from pathlib import Path
from typing import Any


def phi(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def power(mu: float, sd: float, floor: float) -> float:
    """P(point <= floor and 95% upper bound < 0) for an estimate ~ N(mu, sd)."""
    cut = min(floor, -1.96 * sd)
    return phi((cut - mu) / sd)


def ci(v: list[float], nd: int = 3) -> str:
    return f"{v[0]:.{nd}f} [{v[1]:.{nd}f},{v[2]:.{nd}f}]"


def d4(v: list[float]) -> str:
    return f"{v[0]:+.4f} [{v[1]:+.4f},{v[2]:+.4f}]"


def level_table(a: dict[str, Any], with_k: bool) -> list[str]:
    hdr = "| dimension | level | n (games) | P(PIT<.10) | P(PIT<.20) | P(PIT<.80) | P(PIT<.90) | central 80% [CI] | verdict | 2023 / 2024 central | lower miss excl. short | upper miss excl. short | miss share from short nights | RMSE / (W80/2.563) |"
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    if with_k:
        hdr += " k_S 23/24 | k_L 23/24 | dCRPS 2024 S [CI] | dCRPS 2024 L [CI] |"
        sep += "---|---|---|---|"
    rows = [hdr, sep]
    for dn, dd in a["dims"].items():
        for lv in dd["levels"]:
            r = f"| {dn} | {lv['level']} | {lv['n']} ({lv['games']}) | {lv['cov10'][0]:.3f} | {lv['cov20'][0]:.3f} | {lv['cov80'][0]:.3f} | {lv['cov90'][0]:.3f} | {ci(lv['central80'])} | {lv['verdict']} | {lv['central80_2023']:.3f} / {lv['central80_2024']:.3f} | {lv.get('lower_miss_excl_short', float('nan')):.3f} | {lv.get('upper_miss_excl_short', float('nan')):.3f} | {lv.get('miss_share_from_short', float('nan')):.2f} | {lv['spread_ratio']:.2f} |"
            if with_k:
                r += f" {lv['k23_S']:.2f} / {lv['k24_S']:.2f} | {lv['k23_L']:.2f} / {lv['k24_L']:.2f} | {d4(lv['d2024_S'])} | {d4(lv['d2024_L'])} |"
            rows.append(r)
    return rows


def main(raw: Path, out_md: Path, out_json: Path) -> None:
    r = json.loads(raw.read_text())
    L: list[str] = []
    A = L.append
    A(
        "# Minutes-spread EDA (Q7.3): where is the forecast spread wrong, and would widening it help?"
    )
    A("")
    A(
        "DESCRIPTIVE. Seasons 2023 and 2024 only (2025 never loaded). No claim, no frozen rule, no ledger row. Read-only: stored out-of-fold forecasts (`reports/lower_tail/components_*.npz`, `reports/minutes_v2/minutes_scores.npz`, `data/ops/recent_role_full_cache/` for FULL) plus one as-of frame dump (exp_margin, is_home, team_game_no, wl_min_last) built read-only. Harness: `research/eval/minutes_spread_eda.py` + `research/eval/minutes_spread_render.py`. Any real fix needs a frozen rule (draft at the end)."
    )
    A("")
    A("## What was measured")
    A(
        "* Arms. Props: **A0** = production context-residual + stored lower-tail mixture (integer-grid CRPS reproduced: pts 3.140237 / 3.176840 vs the 3.140236 / 3.176840 on file); **FULL** = RECENT_ROLE_FULL candidate (heads + pi with the five recent-role columns; reproduced 3.131886 / 3.168525). Minutes: **P** = production minutes path (truncated normal), reconstructed exactly from its stored q10/q90 (max |CRPS - stored| = "
        + f"{r['minutes_P_recon_max_abs_crps_diff']:.1e}"
        + "); **M2** = the minutes-v2 mixture (coverage only: its full distribution is not stored, so no inflation arm)."
    )
    A(
        "* Calibration: randomised PIT (integer-grid for props, continuous for minutes), coverage P(PIT < tau) at 0.10 / 0.20 / 0.80 / 0.90, central-80% coverage P(0.1 <= PIT < 0.9) with a 95% game-clustered percentile CI (1000 multinomial game resamples, one common weight matrix, seed 0). Verdict: central-80 CI wholly below 0.80 = too narrow, wholly above = too wide. P(PIT<.10) is the LOWER-tail miss rate (nominal 0.10), 1 - P(PIT<.90) the UPPER-tail miss rate. Spread ratio = RMSE about the median / (q90-q10 / 2.563); above 1 means too narrow."
    )
    A(
        "* Oracle-free inflation (declared before any number): symmetric S `q' = med + k(q - med)` and lower-only L `q' = med - k(med - q)` below the median (upper tail untouched, the short-night side). k on a 0.05 grid in [0.70, 1.60]; per slice, k fitted on 2023 (argmin mean CRPS), shrunk `k_s = 1 + n/(n+500)(k - 1)`, snapped to the grid and applied to 2024. Props are re-integerised after scaling and scored on the integer grid; minutes are continuous. Fit uses no realised minutes, no box-score role: slices are as-of (prior-10 minutes, Elo expected margin from `asof_elo_margin`, team game number, `starter10`, back-to-back flag, venue, `wl_min_last`)."
    )
    A(
        "* Slice cut points were fixed in the script before reading results: prior-10 minutes <15 / 15-24 / 24-30 / 30-34 / 34+; Elo expected-margin terciles cut on 2023 rows; first 15 team games vs rest; `wl_min_last` <12 / 12-24 / 24-32 / 32+ / missing. 7 dimensions x ~3.5 levels, 4 stats, 3 minute arms: this is a wide look; nothing here is multiplicity-corrected."
    )
    A(
        "* 'Excl. short' columns drop rows whose REALISED minutes < 0.5 x prior-10 (the house short-night flag). That conditions on an outcome, so it is a diagnostic of where the miss mass sits, not a forecast or a fit input."
    )
    A("")
    A("## Answer in plain words")
    A(
        "See the conclusion block returned with this report; the tables below are the evidence. Short version:"
    )
    A(
        "1. The Q7 hypothesis (widen for lower-minute players and underdogs) is mostly NOT what the data show. Underdog/favourite and early-season slices are calibrated like everyone else for props; the early-season effect flips sign between seasons."
    )
    A(
        "2. What is mis-calibrated in props is the LOWER tail of high-minute players and starters (P(PIT<.10) = 0.13-0.14 vs 0.10), not low-minute players. Low-minute rows have a too-thin UPPER tail instead (0.12-0.155). The miss mass for starters is mostly on rows that were NOT short nights (rate-driven), so it is not a minutes-spread problem."
    )
    A(
        "3. Widening symmetrically does nothing (pts overall +-0.0002); lower-only widening on starters / high-minute bands helps pts by about 0.0025 CRPS in 2024, far below the -0.005 floor, and reb/ast/fg3m by <= 0.0008."
    )
    A(
        "4. The production minutes path P is badly too narrow below the median for bench/low-minute players (31-39% of outcomes under the forecast 10th percentile), the very thing M2 (pi mixture) fixes (9%). Props do not use P, so this does not carry into props."
    )
    A(
        "5. FULL does not absorb the effect: coverage barely moves and the lower-only multiplier gains the same amount on top of FULL."
    )
    A("")
    for stat in ("pts", "reb", "ast", "fg3m"):
        a0, fu = r[stat]["A0"], r[stat]["FULL"]
        A(f"## {stat}: A0 (stored production + mixture), n = {a0['n']} rows")
        A("")
        o = a0["overall"]
        A(
            f"Overall coverage: P(PIT<.10) {ci(o['cov10'])}, P(PIT<.20) {ci(o['cov20'])}, P(PIT<.80) {ci(o['cov80'])}, P(PIT<.90) {ci(o['cov90'])}, central-80 {ci(o['central80'])}. Global multiplier fitted on 2023: S k = {a0['global_k_2023']['S']:.2f}, L k = {a0['global_k_2023']['L']:.2f}; 2024 dCRPS {d4([a0['global_d2024_L']['delta'], a0['global_d2024_L']['lo'], a0['global_d2024_L']['hi']])} (L), {d4([a0['global_d2024_S']['delta'], a0['global_d2024_S']['lo'], a0['global_d2024_S']['hi']])} (S)."
        )
        A("")
        L.extend(level_table(a0, True))
        A("")
        A(
            f"Whole-2024 effect of applying each dimension's per-level shrunk multipliers (n_2024 = {int((r[stat]['A0']['n']) / 2)} rows approx., game-clustered CI; fitted 2023, applied 2024; and the reverse, fitted 2024 applied 2023):"
        )
        A("")
        A("| dimension | S 2024 [CI] | L 2024 [CI] | L reverse (fit 2024 -> 2023) |")
        A("|---|---|---|---|")
        for dn, dd in a0["dims"].items():
            s_, l_, rv = dd["overall_d2024_S"], dd["overall_d2024_L"], dd["overall_rev2023_L"]
            A(
                f"| {dn} | {d4([s_['delta'], s_['lo'], s_['hi']])} | {d4([l_['delta'], l_['lo'], l_['hi']])} | {rv['delta']:+.4f} |"
            )
        A("")
        A(f"### {stat}: overlap with RECENT_ROLE_FULL (FULL arm)")
        A("")
        A(
            f"FULL overall central-80 {ci(fu['overall']['central80'])} (A0 {ci(o['central80'])}); lower-tail P(PIT<.10) {fu['overall']['cov10'][0]:.3f} vs A0 {o['cov10'][0]:.3f}."
        )
        A("")
        A(
            "| dimension | level | A0 lower miss | FULL lower miss | A0 central80 | FULL central80 | k_L fitted on FULL (23/24) | FULL+L vs FULL, 2024 [CI] |"
        )
        A("|---|---|---|---|---|---|---|---|")
        for dn, dd in fu["dims"].items():
            for lv, lv0 in zip(dd["levels"], a0["dims"][dn]["levels"], strict=True):
                A(
                    f"| {dn} | {lv['level']} | {lv0['cov10'][0]:.3f} | {lv['cov10'][0]:.3f} | {lv0['central80'][0]:.3f} | {lv['central80'][0]:.3f} | {lv['k23_L']:.2f} / {lv['k24_L']:.2f} | {d4(lv['d2024_L'])} |"
                )
        A("")
        A("Whole-2024 stack (FULL with its own per-level L multipliers, game-clustered): ")
        A("")
        A("| dimension | FULL - A0 | (FULL+L) - FULL | (FULL+L) - A0 |")
        A("|---|---|---|---|")
        for k, v in r["combined"][stat].items():
            if k.endswith("|L"):
                A(
                    f"| {k[:-2]} | {d4([v['FULL_minus_A0']['delta'], v['FULL_minus_A0']['lo'], v['FULL_minus_A0']['hi']])} | {d4([v['FULLk_minus_FULL']['delta'], v['FULLk_minus_FULL']['lo'], v['FULLk_minus_FULL']['hi']])} | {d4([v['FULLk_minus_A0']['delta'], v['FULLk_minus_A0']['lo'], v['FULLk_minus_A0']['hi']])} |"
                )
        A("")
        A(
            f"### {stat}: as-of walk-forward version of the lower-only multiplier (k for each group fitted on EARLIER month blocks only, shrunk n/(n+500))"
        )
        A("")
        A("| grouping | arm | 2023 dCRPS [CI] | 2024 dCRPS [CI] | mean k in 2024 by group |")
        A("|---|---|---|---|---|")
        for k, v in r["walk"][stat].items():
            dn, arm = k.split("|")
            A(
                f"| {dn} | {arm} | {d4([v['2023']['delta'], v['2023']['lo'], v['2023']['hi']])} | {d4([v['2024']['delta'], v['2024']['lo'], v['2024']['hi']])} | {', '.join(f'{g}: {x:.2f}' for g, x in v['mean_k_2024_by_level'].items())} |"
            )
        A("")
    A(
        "## minutes: production minutes path P (truncated normal), n = "
        + str(r["minutes"]["P"]["n"])
    )
    A("")
    p = r["minutes"]["P"]
    A(
        f"Overall: P(PIT<.10) {ci(p['overall']['cov10'])}, central-80 {ci(p['overall']['central80'])}. Global k: S {p['global_k_2023']['S']:.2f}, L {p['global_k_2023']['L']:.2f}; 2024 dCRPS (minutes) S {d4([p['global_d2024_S']['delta'], p['global_d2024_S']['lo'], p['global_d2024_S']['hi']])}, L {d4([p['global_d2024_L']['delta'], p['global_d2024_L']['lo'], p['global_d2024_L']['hi']])}."
    )
    A("")
    L.extend(level_table(p, True))
    A("")
    A("## minutes: M2 (v2 pi mixture), coverage only; and R (recency ratio)")
    A("")
    L.extend(level_table(r["minutes"]["M2"], False))
    A("")
    A(
        "R arm (recency-ratio quantiles) central-80 overall: "
        + ci(r["minutes"]["R"]["overall"]["central80"])
        + "; lower-tail "
        + f"{r['minutes']['R']['overall']['cov10'][0]:.3f}."
    )
    A("")
    # power block
    pw: dict[str, Any] = {}
    A("## Proposed smallest frozen rule (NOT run, NOT frozen) and its power check")
    sel = r["walk"]["pts"]["role(as-of starter10)|FULL"]
    ful = r["combined"]["pts"]["role(as-of starter10)|L"]["FULLk_minus_FULL"]
    A(
        "Candidate id (suggested): `LOWER_TAIL_SCALE`. Hypothesis: the lower tail of the points forecast for as-of starters is too short in the same direction in every season; a one-parameter lower-only scale (k about 1.10, as-of, shrunk, refit on all earlier month blocks) applied to the stored FULL mixture grid lowers integer CRPS. No model refit; post-hoc on the stored quantile grid; pts only."
    )
    A("")
    A(
        "| scenario | true effect | SD of estimate | P(point <= floor and CI upper < 0), one season | both seasons |"
    )
    A("|---|---|---|---|---|")
    scen = []
    for floor in (-0.005, -0.002):
        for lab, mu23, mu24, sd23, sd24 in (
            (
                "as observed (walk-forward, FULL arm)",
                sel["2023"]["delta"],
                sel["2024"]["delta"],
                sel["2023"]["sd"],
                sel["2024"]["sd"],
            ),
            (
                "half the observed effect",
                sel["2023"]["delta"] / 2,
                sel["2024"]["delta"] / 2,
                sel["2023"]["sd"],
                sel["2024"]["sd"],
            ),
        ):
            p23, p24 = power(mu23, sd23, floor), power(mu24, sd24, floor)
            scen.append(
                {
                    "floor": floor,
                    "scenario": lab,
                    "mu23": mu23,
                    "mu24": mu24,
                    "sd23": sd23,
                    "sd24": sd24,
                    "power23": p23,
                    "power24": p24,
                    "both": p23 * p24,
                }
            )
            A(
                f"| floor {floor:+.3f}, {lab} | 2023 {mu23:+.4f} / 2024 {mu24:+.4f} | {sd23:.4f} / {sd24:.4f} | {p23:.2f} / {p24:.2f} | {p23 * p24:.2f} |"
            )
    pw["scenarios"] = scen
    pw["mde_80pct_power_single_season"] = 2.8 * sel["2024"]["sd"]
    A("")
    A(
        f"MDE (80% power, two-sided 5%) for one season at SD {sel['2024']['sd']:.4f} is {2.8 * sel['2024']['sd']:.4f}. Incremental on FULL in 2024 (fixed 2023 fit): {d4([ful['delta'], ful['lo'], ful['hi']])}."
    )
    A("")
    A("Rule sketch for Devin to confirm or kill (everything below would be frozen before any run):")
    A(
        "* Primary contrast: integer CRPS of (stored FULL mixture grid with as-of lower-only scale on starters) minus (stored FULL), pts only, played rows; game-clustered 95% percentile CI, 2000 draws."
    )
    A(
        "* The house floor of -0.005 is UNREACHABLE (observed -0.0026, ceiling roughly -0.003 even if the effect doubles); a rule that cannot pass its own gate is not frozen (non-negotiable 8). Even a -0.002 incremental floor fails the minimum-data gate: both-season power is about 0.01 as observed (2023 is the weak season, the walk-forward cold start leaves k near 1 for the first months and the FULL-arm 2023 CI includes 0). So this is NOT ready to freeze; my recommendation is to record it as a descriptive finding and not pursue, unless Devin wants a single pooled-2023-24 test with a stated -0.002 floor (power about 0.6 on the pooled SD, untested)."
    )
    A("* Family: m = 1 (pts). reb/ast/fg3m are reported descriptively only (observed <= 0.0008).")
    A(
        "* Guards: the symmetric arm and a bench-only lower-only arm must show no gain (specificity); lower-tail coverage P(PIT<.10) for starters must move toward 0.10 and the upper tail must not move by > 0.005, slices bench/min<5/playoffs/cold start no worse than +0.01, A0 and FULL reproduced to 1e-6 first."
    )
    A(
        "* No 2025 touch unless FULL itself reaches Stage 2 and passes; this is a post-processing of FULL, so the clean place to test it is the live log."
    )
    A("")
    A("## What I could not verify")
    A(
        "* M2's full distribution is not stored, so the inflation arm is absent for M2; only its coverage is shown."
    )
    A(
        "* Elo expected margin comes from the frame (`exp_margin` of the player's team, as-of); I did not independently recompute it. Slice labels are as-of by construction but I did not re-run planted-future tests on this dump (this is an EDA, not a feature)."
    )
    A(
        "* The multiplier scales about the median of an integer-snapped, skewed distribution; for fg3m/ast the 0.05 k grid is coarse relative to the integer support, so small-stat deltas are lumpy."
    )
    A(
        "* Many slices x stats x 2 forms were inspected: a few 'too narrow' verdicts at the 5% level are expected by chance; only patterns that replicate across seasons and the reverse fit are treated as signal."
    )
    out_md.write_text("\n".join(L) + "\n")
    r["power"] = pw
    out_json.write_text(json.dumps(r, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
