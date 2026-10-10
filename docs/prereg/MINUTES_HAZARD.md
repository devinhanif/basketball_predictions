# MINUTES_HAZARD: minutes as an on-court hazard over game time (pre-registration)

Status: DRAFT 2026-10-10 ~21:55 CT, NOT FROZEN; awaiting Devin's "confirmed". Committed by the maintainer's hand BEFORE any fit.
Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MINUTES_HAZARD.md | shasum -a 256` (first 8 hex chars into the results
section and every ledger row). Season labels: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (frozen holdout, never loaded).

## Where this comes from
Devin asked what the mathematics of a team sport buys us beyond ML (2026-10-10). Claude's pick: minutes are the largest props
error, and a player's minutes are the integral over game time of the probability he is on the floor, which depends on the
coach's rotation habit, his foul count and the score margin. Those are mechanisms, not features. The question is whether
writing them down beats the best pre-game minutes model we have.

## Honest prior (what has already been seen)
MINUTES_V2 (docs/MINUTES_V2.md, T162-T173): a LightGBM distributional head over production's features (M2) beats production's
minutes path by 0.83-0.85 minutes of CRPS and the recency baseline by 0.58-0.66, with the gain coming almost entirely from the
feature set through a distributional head. Fed into the props model, that large minutes gain bought pts −0.0053 (2023) and
−0.0018 (2024, not a pass), while oracle minutes would buy −0.57: most minutes error is not knowable before tip. Coach
allocation profiles (reports/coach_allocation_2026-10-10.md): rotation habits are moderately stable traits (year-to-year r
0.5-0.7 for first-sub time, starters' share, close-game usage), absence handling is not. Expectation stated before the run:
the hazard model may improve the SHAPE of the minutes distribution (blowout tail, foul-out tail) over M2; the props gain is
expected to be small or null. A null here closes "better pre-game minutes" as a direction for the props model.

## Hypothesis
H1 (minutes shape): a hazard model of on-court time beats M2 on minutes CRPS and on tail calibration (PIT at q0.10 and q0.90),
on identical rows. H2 (props): the hazard minutes distribution, substituted for M2's in the MINUTES_V2 downstream path, lowers
props CRPS beyond production. Null for each: no change.

## Data exposure (honest)
Seen: everything in docs/MINUTES_V2.md results; the coach profiles; descriptive short-night tables (FAN_KNOWLEDGE 2026-10-10).
Not seen: any hazard-model number. Season 2025 never loaded; `season <= 2024` asserted; nba.duckdb read_only. Selection 2023,
report 2024; month-block walk-forward, 2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU.

## Fixed design (constants final)
Game time is discretised into 48 one-minute slots (overtime slots appended, 5 per period). For player p in game g, the
on-court indicator in slot t is `Y_pt` (from `stints`). Minutes = sum_t Y_pt (plus overtime). The model gives `P(Y_pt = 1 | x_p, state_t)`
and simulates games to get a minutes distribution.
State in slot t (knowable pre-tip as a distribution, never read from tonight): (a) slot index and period; (b) the player's
rotation habit: his as-of on-court probability profile by slot, `h_p(t)` = mean of Y_pt over his last 10 games with the same
team, shrunk toward his coach-season's role-average profile (starters / first bench / deep bench by prior minutes) with
pseudo-count 5 games; (c) foul state: fouls-so-far simulated from his as-of per-minute foul rate (prior 40 games, shrunk,
pseudo-count 300 minutes) as a Poisson process while on court; the foul-trouble effect on `P(on)` is one fixed multiplier per
(fouls so far, period) cell fit on the training window; (d) score margin: simulated from the game model's pre-tip margin
distribution (rung0 injury-Elo win probability mapped to a Normal margin with the training-window sd), evolving as a Brownian
bridge over slots; the blowout effect on `P(on)` is one fixed multiplier per (|margin| bucket {<10, 10-19, 20+}, period, role)
fit on the training window.
Model: `logit P(Y_pt = 1) = logit h_p(t) + f_foul(fouls_t, period) + f_margin(|margin_t|, period, role) + b_team-OUT`, where
`b_team-OUT` is production's `vac_min` scaled by a coefficient fit on the training window (the teammate-OUT redistribution
production already uses). Fit = one L-BFGS logistic fit per block on the training rows (slot-level, no trees). Simulation: 400
paths per player-game, seed 0; the minutes distribution is the empirical 199-quantile grid of path sums, clipped to [0, 60].
Minutes given plays only (P(play) is MINUTES_V2's secondary and untouched).

## Arms (identical rows/seeds; minutes given plays)
* M2 (comparator): MINUTES_V2's frozen candidate, refit by the same harness.
* H1 candidate: the hazard model above.
* H0 habit-only: `h_p(t)` alone (no foul, no margin): is the structure doing anything beyond the rotation profile?
* H2 oracle-margin (descriptive, leak by construction): H1 with tonight's ACTUAL margin path; the ceiling for the blowout term.
* Props: the MINUTES_V2 downstream path with H1 minutes in place of M2 minutes (A1) vs production A0 and vs M2-fed (A_M2).

## Metrics, support, inference
Minutes: continuous CRPS, mean bias, 80% coverage, PIT at q0.10 and q0.90, game-clustered bootstrap (2000, seed 0). Props:
integer-support CRPS per stat, same bootstrap, BH over the 4 stats per season. Slices: starter/bench, first 15 team games,
cold start, teammate-OUT, Elo favourite >= 0.65 / underdog <= 0.35 (where the margin term should matter), returning from 3+
games. Mechanism: minutes CRPS gain by Elo bucket (expected largest for heavy favourites and underdogs).

## Minimum data checks (fail = stop, no model result). Measured 2026-10-10 on nba.duckdb read-only, 2022-2024
1. `stints` cover >= 95% of games with a result. Measured: 1320/1320, 1318/1318, 1315/1315 (pass).
2. Fouls (pf) non-null for >= 99% of played rows. Measured: 100% (pass). Per-slot foul times come from play-by-play; if
   unavailable for a game the per-minute rate is used (declared fallback; share reported).
3. `possessions.score_diff` and clocks non-null for >= 99%. Measured: 100% of 785,039 (pass).
4. Starter-games 2022-24 >= 30,000 for the role profiles. Measured: 39,530 (pass).
5. Missingness audit of every input by realised-minutes bucket; max pp gap <= 2. Planted same-game (tonight's stints, fouls,
   margin edited) and planted-future tests: H1 minutes byte-identical. M2 reproduces its stored results to 1e-6.

## Pass rule (2023 selects, 2024 confirms; all must hold)
H1 minutes: CRPS(H1) − CRPS(M2) <= −0.10 minutes with CI upper < 0; PIT q0.10 and q0.90 each within ±0.02 of nominal; |bias| <= 0.5
min; no slice (n >= 300) worse than +0.10 min; H1 beats H0 by <= −0.05 (else the mechanisms did nothing and the result is
"rotation profile"). H2 props: per stat, dCRPS(A1 − A0) <= −0.005 with CI upper < 0 and BH p < 0.05, AND A1 better than A_M2
by <= −0.002 (else the gain is MINUTES_V2's, already recorded).
Secondary metrics cannot rescue a failure. No promotion; a confirmed result gets a shadow-log recommendation and a drafted
holdout row. Season 2025 untouched.

## Kill criteria
H1 fails on 2023: close; do not tune slot width, pseudo-counts, path count, margin buckets or add mechanisms (injury hazard,
ejections, rest nights). H1 passes but H2 fails: record "better minutes shape, no props gain" and close the pre-game-minutes
direction for props. At most ONE pre-registered follow-up, then closed.

## Outputs (exact)
`reports/prereg_minutes_hazard/results.json`, `reports/prereg_minutes_hazard.md`: `[season, arm, n_rows, n_games, crps_min,
dcrps, ci_lo, ci_hi, bias, cov80, pit_q10, pit_q90]`; `[stat, season, arm, n_rows, n_games, crps_int, dcrps, ci_lo, ci_hi, p,
p_bh]`; `[season, slice, arm, n, dcrps, ci_lo, ci_hi]`; `[col, bucket, null_rate]`; `[test, ok]` for checks 1-5; seeds, SHA,
sha256. Code under `research/` only (`research/minutes/hazard.py`, `research/eval/minutes_hazard_eval.py`).

=== RESULTS BELOW ===

(not run)
