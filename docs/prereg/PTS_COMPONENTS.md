# PTS_COMPONENTS: points as 2s, 3s and free throws, each its own categorical count (pre-registration)

Status: DRAFT 2026-10-10 ~18:50 CT, NOT FROZEN; awaiting Devin's "confirmed". Committed by the maintainer's hand BEFORE any fit.
Everything above the line `=== RESULTS BELOW ===` is frozen at that time. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PTS_COMPONENTS.md | shasum -a 256` (first 8 hex chars into the results
section and every ledger row). Season labels: 2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26 (frozen holdout, never loaded).

## What Devin said
> "3 is different from 2; the type of make should be categorical; a lot of the numbers should be categorical." (2026-10-10)

## Hypothesis
H1: forecasting points as the sum of three separately modelled make counts, 2-point makes, 3-point makes and free throws made,
each with its own attempt rate and make rate, sharing one minutes/usage draw, gives a better integer-support points
distribution (CRPS, and especially the upper tail) than production's single-number points model. Null: no change.
Descriptive support (seen, 2026-10-10, OOF rows): corr(points miss, 3 x threes miss) = 0.66 / 0.69 (2023 / 2024); 41-43% of the
points-miss variance moves with the threes miss; points are 49-51% twos, 34-36% threes, 15% free throws; on nights >= +10 over
forecast (n ~1,540 per season) +2.1 threes account for +6.3 of the +14 points.
Protects against: the repo's record that architecture alone loses (the components reuse production's features and harness;
only the output structure changes), and against a decomposition that quietly drops the correlation between components
(the shared draw is the design, the independent-sum is a declared ablation arm).

## Data exposure (honest)
Seen: the descriptive numbers above; production OOF aggregates for 2023/2024. Not seen: any components model result. Season
2025 never loaded; `season <= 2024` asserted; `nba.duckdb` read_only. Selection 2023, report 2024; walk-forward month blocks,
2022 warm-up, rows = played, `n_prior >= 5`, seed 0, CPU.

## Fixed design (constants final)
Flag `ContextResidualConfig.pts_components` (default `off`, byte-identical). Targets from `player_game_stats`: `fg2m = fgm -
fg3m`, `fg3m`, `ftm`; attempts `fg2a = fga - fg3a`, `fg3a`, `fta`. Features: production's `stat_feature_names("pts")` plus the
same recency set (m10, m5, m40, m100, sd10, rate, opp_allow, vac) computed for fg2a, fg3a, fta, and prior-40 make rates for
2s, 3s and FTs (empirical-Bayes shrunk to the league rate with pseudo-count 50 attempts). All as-of; nothing from tonight.
Component heads (same gradient-boosted residual learner as production, same hyper-parameters, seed 0):
* attempts: NegBin heads for fg2a, fg3a, fta (mean and dispersion as production's count stats);
* makes given attempts: Beta-Binomial with the shrunk make rate and a per-stat concentration fit on the training window.
Joint draw: one shared "activity" factor z ~ N(0,1) per row enters each attempt head's mean as exp(beta_k z) with beta_k fit
by maximum likelihood on the training window (captures minutes/usage co-movement); makes are conditionally independent given
attempts. Points = 2 fg2m + 3 fg3m + ftm, evaluated by exact enumeration on the integer grid (attempt grids capped at 40/25/30).

## Arms (identical rows/seeds/calibration windows)
* A0 production pts (reproduces integer CRPS 3.1528 / 3.1884 for 2023 / 2024).
* A1 candidate = components with the shared factor.
* A2 ablation = components with beta_k = 0 (independent sum): tests whether the correlation matters.
* A3 = A0 plus the new attempt/make-rate features as plain columns (tests whether the gain is the features, not the structure).
* A4 placebo = A1 with the component targets permuted within player (keeps marginals, breaks the game link).
Secondary (descriptive): the fg3m head alone vs production fg3m (same support), so the threes model gets a free look.

## Metrics, support, inference
Integer support for every arm. Primary: paired dCRPS (arm - A0) for pts per season, all rows, game-clustered bootstrap
(2000, seed 0). Secondary: upper-tail threshold log loss (pts >= 20, 25, 30), A1.1 80% PIT coverage, upper PIT (q0.80, q0.90),
bias. Multiplicity: one primary test per season (pts only); the fg3m secondary is reported, not tested. Per-row MDE beside
each result. Slices: scoring tier (<10, 10-17, 17-24, 24+ prior ppg), three-point share of points in terciles, starter/bench,
cold start `n_prior < 20`. Mechanism: dCRPS by three-point-share tercile, expected largest for high-share shooters.

## Minimum data checks (fail = stop, no model result)
1. `fgm, fga, fg3m, fg3a, ftm, fta` non-null for >= 99.9% of played rows 2022-2024, and `pts == 2 fg2m + 3 fg3m + ftm` for
   >= 99.9% (rows that fail are dropped from ALL arms and counted).
2. Missingness audit of every new column by realised-minutes bucket; max pp difference <= 2.
3. Planted same-game and planted-future tests on the new columns (CI).
4. A0 reproduces production to 1e-6; flag `off` byte-identical.
5. Enumeration check: the components grid sums to 1 within 1e-9 for every row.

## Pass rule (2023 selects, 2024 confirms; all must hold)
1. A1 dCRPS point <= -0.010 (the pts floor used by LOWER_TAIL), CI upper < 0.
2. Guards: |bias| <= 0.5; 80% PIT coverage in [0.75, 0.85]; upper PIT q0.90 within +/- 0.02 of 0.90; no slice with n >= 300
   worse by more than +0.01 CRPS.
3. Attack gates: A1 beats A4 by <= -0.005; A1 beats A3 by <= -0.003 (else the finding is "attempt features help", recorded as
   that and A3 becomes the candidate under the same guards); A1 vs A2 reported (if A2 >= A1 the shared factor did nothing).
Secondary metrics cannot rescue a failure. No promotion; a confirmed candidate gets a shadow-log recommendation and a drafted
holdout row. Season 2025 untouched.

## Kill criteria
Fails on 2023: close. Do not add component heads (and-1s, technical FTs), change the joint structure to a copula, or tune the
shrinkage afterwards; at most ONE pre-registered follow-up, then closed.

## Outputs (exact)
`reports/prereg_pts_components/results.json`, `reports/prereg_pts_components.md`: `[season, arm, n_rows, n_games, crps_int,
dcrps, ci_lo, ci_hi, p, mde, bias, cov80, pit_q80, pit_q90, tll20, tll25, tll30]`; `[season, slice, arm, n, dcrps, ci_lo,
ci_hi]`; `[col, bucket, null_rate]`; `[test, ok]` for checks 1-5; seeds, SHA, sha256. Code under `research/` only
(`research/eval/pts_components_eval.py`, `research/props/pts_components.py`).

=== RESULTS BELOW ===

(not run)
