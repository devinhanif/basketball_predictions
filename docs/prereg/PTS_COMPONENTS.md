# PTS_COMPONENTS: points as 2s, 3s and free throws, each its own categorical count (pre-registration)

Status: FROZEN 2026-10-10 16:58 CT on Devin's confirmation (message: "do everything", 2026-10-10 ~19:30 CT; docs/DECISIONS.md). Drafted 2026-10-10 ~18:50 CT, NOT FROZEN. Committed by the maintainer's hand BEFORE any fit.
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

## Results (run 2026-10-10, research/eval/pts_components_eval.py, git SHA at run 3a729d73; reports/prereg_pts_components.md, reports/prereg_pts_components/results.json)

Frozen sha256 7e6fd8f0 verified before the run (matches). Seasons <= 2024 only (max loaded 2024), nba.duckdb read-only, CPU, seeds {model 0, bootstrap 0, pit 0, placebo 0}, 2000 game-clustered resamples.

**Verdict: FAIL on 2023 and 2024. The pre-registered kill applies: close (no component heads added, no copula, no shrinkage tuning; one pre-registered follow-up at most).** Adequately powered negative: A1 is WORSE than production, not merely tied.

Checks: C1 pass (six box columns non-null 100%, identity 100%, 0 rows dropped), C2 pass (max NULL-rate gap 0.07 pp), C3 pass (planted same-game and planted-future inert on earlier rows, not inert on later rows), C4 pass (A0 reproduces LOWER_TAIL integer CRPS exactly, delta 0.0, n 27619 / 27583; nba/props/context_residual.py and lower_tail.py unchanged vs HEAD), C5 pass (grid sums to 1 within 3.7e-14 at the gate, within 2.2e-11 for every fitted grid).

Primary, pts, integer support, arm minus A0 (all rows):

| season | arm | n_rows / n_games | crps_int | dCRPS | 95% CI | p | MDE | bias | cov80 | pit_q80 | pit_q90 | tll20 | tll25 | tll30 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2023 | A0 | 27619 / 1318 | 3.1528 | | | | | -0.149 | 0.772 | 0.804 | 0.904 | 0.2738 | 0.1791 | 0.1104 |
| 2023 | A1 | 27619 / 1318 | 3.1857 | +0.0329 | [+0.0261, +0.0397] | <0.001 | 0.0068 | -0.424 | 0.777 | 0.822 | 0.912 | 0.2752 | 0.1805 | 0.1124 |
| 2023 | A2 | | 3.1798 | +0.0270 | [+0.0201, +0.0339] | <0.001 | 0.0069 | -0.433 | 0.740 | 0.811 | 0.897 | 0.2741 | 0.1787 | 0.1100 |
| 2023 | A3 | | 3.1517 | -0.0011 | [-0.0048, +0.0027] | 0.593 | 0.0038 | -0.153 | 0.774 | 0.805 | 0.905 | 0.2732 | 0.1785 | 0.1099 |
| 2023 | A4 | | 3.3691 | +0.2163 | [+0.2023, +0.2307] | <0.001 | 0.0142 | -0.199 | 0.789 | 0.820 | 0.919 | 0.2888 | 0.1877 | 0.1153 |
| 2024 | A0 | 27583 / 1315 | 3.1884 | | | | | -0.113 | 0.764 | 0.800 | 0.898 | 0.2803 | 0.1797 | 0.1053 |
| 2024 | A1 | 27583 / 1315 | 3.2184 | +0.0300 | [+0.0239, +0.0361] | <0.001 | 0.0061 | -0.191 | 0.771 | 0.803 | 0.903 | 0.2832 | 0.1818 | 0.1069 |
| 2024 | A2 | | 3.2162 | +0.0278 | [+0.0217, +0.0342] | <0.001 | 0.0062 | -0.196 | 0.735 | 0.791 | 0.886 | 0.2831 | 0.1812 | 0.1056 |
| 2024 | A3 | | 3.1896 | +0.0012 | [-0.0016, +0.0040] | 0.432 | 0.0028 | -0.109 | 0.764 | 0.800 | 0.899 | 0.2799 | 0.1799 | 0.1056 |
| 2024 | A4 | | 3.4179 | +0.2295 | [+0.2141, +0.2452] | <0.001 | 0.0155 | +0.003 | 0.784 | 0.802 | 0.908 | 0.2963 | 0.1893 | 0.1114 |

(one primary test per season, family size 1, p_bh = p.)

Pass rule, per season (both required):
* Rule 1 (A1 dCRPS <= -0.010 and CI upper < 0): FAIL both seasons (+0.0329 and +0.0300; CI wholly above 0).
* Guards: bias, cov80, upper PIT q0.90 pass in both seasons; the slice guard FAILS in both seasons (9 of 10 declared slices with n >= 300 are worse than +0.01: e.g. tier 24+ prior ppg +0.165 [+0.116, +0.211] in 2023 and +0.162 [+0.113, +0.207] in 2024; cold start +0.060 / +0.048; starters +0.036 / +0.042).
* Attack gates: A1 vs A4 passes (-0.1834 [-0.1973, -0.1694] in 2023, -0.1995 [-0.2154, -0.1844] in 2024), but that only says the placebo is far worse; A1 vs A3 FAILS (A1 - A3 = +0.0340 [+0.0270, +0.0405] and +0.0288 [+0.0231, +0.0348]; the doc's rule is <= -0.003). A1 vs A2 reported: A1 - A2 = +0.0059 [+0.0032, +0.0085] (2023), +0.0021 [+0.0000, +0.0041] (2024); crps(A2) <= crps(A1) both seasons, so the shared factor did nothing useful (it slightly hurts).
* "Attempt features help, not structure" clause: A3 is the candidate under the same rule-1 and guards. A3 dCRPS -0.0011 [-0.0048, +0.0027] (2023) and +0.0012 [-0.0016, +0.0040] (2024): a tie, far from the -0.010 floor (MDE 0.0038 / 0.0028). A3 FAILS rule 1.

Upper tail (secondary, cannot rescue): threshold log loss at pts >= 20 / 25 / 30 is worse for A1 than A0 at every threshold and both seasons (2023: 0.2752 vs 0.2738, 0.1805 vs 0.1791, 0.1124 vs 0.1104; 2024: 0.2832 vs 0.2803, 0.1818 vs 0.1797, 0.1069 vs 0.1053). The hypothesis was that the upper tail would improve; it did not. A1 also over-forecasts more than A0 (bias -0.424 vs -0.149 in 2023, -0.191 vs -0.113 in 2024).

Mechanism table (A1 - A0 by three-point-share tercile, T1 low to T3 high): 2023 +0.0480 / +0.0321 / +0.0186; 2024 +0.0366 / +0.0403 / +0.0130. The doc expected the largest gain for high-share shooters; the loss is smallest for them but there is no gain in any tercile. Fitted activity loadings (A1) were stable across blocks: beta 0.27-0.38 (2s), 0.19-0.32 (3s), 0.41-0.55 (FTs); NegBin sizes r hit large values for 2s (393-1999, near Poisson) and for 3s (39-1825), and were 1.8-2.2 for FTs; Beta-Binomial kappa 290-5000 for 2s and 3s (often at the 5000 bound, i.e. binomial), 37-122 for FTs. In A4 the loadings came out about the same (beta about 0.43 / 0.28 / 0.54) because the permutation is joint within player and keeps the within-game co-movement of the six targets.

Secondary (fg3m marginal of the A1 model vs production fg3m, same rows and integer support, descriptive, not tested): 2023 dCRPS -0.0027 [-0.0042, -0.0012] (0.5690 -> 0.5663); 2024 -0.0004 [-0.0021, +0.0012] (0.5923 -> 0.5918). A threes-only head ties production and is nowhere near a practical floor.

Honest reading: the decomposition did not help. Structure (A1, A2) costs +0.027 to +0.033 CRPS versus the one-number production model; adding the same information as plain columns (A3) ties it. The shared-factor correlation did not matter on this score. The descriptive support in the hypothesis (corr(points miss, 3 x threes miss) about 0.67) is real but production's residual quantiles already absorb it; modelling components separately with constant NegBin dispersion and fixed Beta-Binomial rates lost shape for high scorers (tier 24+ is the worst slice). Not tried, by rule: heavier component heads, copula, shrinkage tuning.

Disclosure: one pre-scoring decision was taken after the one-block smoke (Oct 2023, n=1062; A0 3.1513, literal-uncentered A1 3.3199 with bias -1.87, centred A1 3.2337). The factor is applied mean-one, exp(beta z - beta^2/2), because the literal exp(beta z) inflates the attempt means by exp(beta^2/2) (about +0.75 points), contradicting the head mean mu_k that the doc names. The choice rests on that arithmetic, not on comparing CRPS across variants; the uncentered form was never run on a full season. A reader who distrusts this should treat the A1 numbers above as the centred reading only.

Unstated details, chosen before scoring (full list in UNSTATED in research/eval/pts_components_eval.py and reports/prereg_pts_components.md):
1. No `ContextResidualConfig.pts_components` flag (nba/ untouched; layering test forbids nba -> research); "off byte-identical" is checked as nba/props/context_residual.py and lower_tail.py unchanged vs HEAD plus A0 reproduction to 1e-6.
2. Attempt mean = m10 + production-learner residual (floored 0.05); all heads use the full feature list (production pts + 27 component columns).
3. NegBin sizes r_k, loadings beta_k and Beta-Binomial concentrations kappa_k are fit on production's calibration window (newest 15% of the training window), not on the head-fit rows.
4. beta_k with r_k by joint marginal likelihood, 15-node Gauss-Hermite integration, L-BFGS-B 60 iterations, start 0.15; factor mean-one (see disclosure).
5. A2 reuses the A1 heads; r_k its own 1-D MLE with beta = 0.
6. Make probability = shrunk prior-40 rate itself (no learner on makes); kappa_k by 1-D MLE.
7. Prior-40 window = previous 40 played games (tonight excluded); league rate = rows on strictly earlier dates; pseudo-count 50 attempts.
8. Attempt mass above caps 40 / 25 / 30 folded onto the cap cell (grid sums to 1 exactly).
9. sd10 for attempts = 1.0 when degenerate.
10. Points quantile grid = smallest k with CDF(k) >= tau on the 199-tau production grid; every arm scored by the same CRPS, randomized PIT and mean-of-grid bias code.
11. A3 appends all 27 component columns; A4 permutes the six targets jointly within player over 2022-2024 (seed 0), evaluation label real.
12. Slices: tier by m10_pts, three-point share = 3 m10_fg3m / max(m10_pts, 0.1) in per-season terciles, starter = starter10 >= 0.5, cold start n_prior < 20; slice CI only for n >= 300.
13. Pass rule required in each season; "A1 beats A4/A3 by" = crps(A1) - crps(other) point <= -0.005 / -0.003 on all rows; "shared factor did nothing" = crps(A2) <= crps(A1).
14. Check 5 at the gate uses fixed synthetic parameters on all scored rows; the arms stage asserts the same bound for every fitted grid (max 2.2e-11).

Files: research/props/pts_components.py, research/eval/pts_components_eval.py, tests/research/props/test_pts_components.py, reports/prereg_pts_components.md, reports/prereg_pts_components/{gate.json,results.json,ledger_rows.md}. Season 2025 untouched.


Freeze: 2026-10-10 16:58 CT, commit 918e03c, sha256 of everything above the line: 7e6fd8f019985032b712b71fb275f4bf6841862a09e8850dfd1c11913cbfafc8 (first 8: 7e6fd8f0). Verify with the awk command in the header.
