# Ledger audit, merged (2026-10-12 01:16 CDT)

Sources: ledger_audit_2026-10-12_part1.md (T001-T149, de689e9), part2 (T150-T299, 402fa50), part3 (T300-T458, 464b141). Read-only; no fit
became a claim; season 2025 not loaded. Prompted by Devin: "look at all failed tests and see if we missed anything similar".

## The string

One root cause sits upstream of most of tonight's failures: production's minutes path. The deployed P(play) hurdle uses trailing-150-game
columns only (nba/props/minutes.py, DEFAULT_LOOKBACK_GAMES = 150; verified), no report tag, no last-game minutes; the props lower-tail
short-minutes classifier pi is fit on COMMON_FEATURES with no recent-role minutes either. Results that hit that hole:

| Result | How it hits the hole |
|---|---|
| RECENT_ROLE_HOLDOUT T458 (PASS) | measured against WORKLOAD's C, not the deployed hurdle; point gap to the deployed hurdle about -0.076 / -0.091 log loss (no paired CI) |
| MINUTES_V2 secondary (T162-T173) | p_v2 beat the hurdle by -0.0486 / -0.0577 in Oct 9; filed as secondary, never acted on |
| TEAM_BUDGET T446-T449 (harm) | budget built from the same hurdle |
| ENSEMBLE E1 T440, E2 T442 | router gate on the same path; blend fails only on near-DNP rows (+0.055) and playoffs |
| WORKLOAD pts T452 | LOAD reached the mean/scale heads, never pi |
| LOWER_TAIL T136-T159 | pi without recent minutes; oracle pi worth -0.163 / -0.156 pts, current mixture ~8% of it |

Exploratory mechanism (part3): after a game under 5 minutes, P(<5 min | plays) is 37.6% (n 1,451) vs 7.6% otherwise.

## Candidates, in the order they will be handled

1. PI_RECENT_ROLE — refit pi with the five RECENT_ROLE columns (found independently by parts 1, 2 and 3). Drafting now. Venue 2023/24, then one
   logged 2025 touch (pi untouched) on Devin's word.
2. P(play) shadow sized against the deployed hurdle, all rostered not-OUT players (steward, in flight); PPLAY_INTO_PROPS_LIVE frozen after
   the shadow table format is confirmed, before 2026-10-20.
3. ROUTER_G2 — router gate g' and the near-DNP route built from the T-60 report + recent-role columns, with a hard postseason-to-A0 route
   (post-hoc by origin, declared). XGBoost and SEQ are virgin on 2025. Replaces the weak-g construction inside NEAR_MISS_BUNDLE, which will not
   be frozen as drafted (see DECISIONS).
4. Before opening night, cheap forward rules: declare the T-30 lineup gain's expected slices (<17 ppg, cold start, fringe rotation);
   shadow-log the lower-tail mixture for reb/ast/fg3m with threshold log loss primary.
5. Smaller: E3 reb blend blocked by an era guard that cannot pass for an id-aware network (current seasons already 0.84-0.86); a new rule
   with player ids ablated (Colab), prior ~25%, value small. Cold-start rows gain ~-0.013 from any added information (four tests). F12 re-measure
   after the stint rebuild (descriptive). fg3m P(>=1) over-predicted by ~+0.03 (small value).

## Corrections to tonight's readings

- ADVANCED_RATES T439: the null stands, but "the placebo beat the real columns in 14 of 15 cells" is not evidence against the features. Its
  placebo permuted values within player-season, carrying future, player-level information: USAGE_PACE/pts placebo beat A0 by -0.0218 / -0.0151.
- Harness defect census: ADVANCED_RATES (inert shuffle, future-leaking placebo), TEAM_BUDGET (both; harm verdict unaffected), COACH_PHASE
  (inert shuffle; never ran), FOUR_FACTORS and F11 (future-leaking placebo; nulls unaffected). Fixed: RECENT_ROLE, PPLAY_INTO_PROPS_LIVE,
  NEAR_MISS_BUNDLE draft; ENSEMBLE's shuffle could fail.

## Dismissed (one line each in the parts)

Early sim/routing wins (DNP-baseline artefacts), re-running pre-clean-up lineup tests (defects blur, cannot create nulls), P(over) bias shift
(ROI -0.8% [-1.6%, +0.1%]), following Pinnacle vs DK (+0.8% [-1.1%, +2.6%]), P(play)-weighted injury-Elo (capped by INJURY_ELO_T30).
