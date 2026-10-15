# PARLAY_CONTEXT_PROB: does a context-conditioned copula give a more accurate same-game parlay probability than the base copula? (pre-registration DRAFT)

Status: **DRAFT. GATE FAILED. NOT FROZEN. Do not freeze as written.** No contrast has been computed, season 2025 was never read (every query carries `season <= 2024`, asserted in code), every store was opened read-only. Everything above the line `=== RESULTS BELOW ===` would be the rule if Devin chose to freeze it; the main session hashes it with `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PARLAY_CONTEXT_PROB.md | shasum -a 256`. Nothing is below the line.

- **Advice or momentum:** Devin's override (DECISIONS 2026-10-14 20:31: "people are gonna play parlays but this is the model's guess too"). Claude had recommended closing the context-copula work as a sizing null; this draft flags that recommendation again at the end (section "What Devin decides").
- **Prior stated before any gate number:** docs/PREDICTIONS_LEDGER.md row 2026-10-14 "PARLAY_CONTEXT_PROB" (commit 4dbc4c5): P(gate passes) 0.25, P(frozen) 0.20, P(primary pass if run) 0.05, expected effect -0.0002 per combination.
- **Model class:** copulas for parlays (MODEL_PLAYBOOK "Set and joint models"). Nothing is fitted in this draft; the stored production OOF marginals are composed with a Gaussian copula (nba/parlay/joint.py algebra).
- **Not an EV rule.** The EV question is closed (b6e1fca: best typical context swing +6.1% against costs of 5% to 13%). This rule asks only whether the probability the app shows is more accurate. No price, fee, stake or verdict enters.

## Question
For same-game leg pairs built from real posted lines, does replacing the pooled (relation x stat pair) correlation of the base copula with a context-specific correlation (teammate out, leader out, teammates competing, blowout, favourite) lower the joint log loss of "all legs hit", and is the displayed joint probability better calibrated?

## Arms (both are forecasts made before tip, composed from the same stored marginals)
- **BASE:** the production copula, `nba/parlay/joint.py` (one pooled correlation per relation x stat pair; Gaussian). Marginals are the stored production OOF quantile grids (`reports/context_residual/oof_context_residual.parquet`), identical in both arms, so the paired difference isolates dependence.
- **CTX:** the same copula; for a pair that falls in a declared context cell the latent correlation is the cell's value, shrunk toward the pooled value (shrinkage constant k chosen on 2023 from {50, 200, 800}); a pair outside every declared cell, or with a null label (`-1`, including every game with no report), uses the pooled value. **Cell values are estimated on the discovery seasons 2020-22 only** (the inventory, reports/parlay/context_copula_sizing.json; 114 looks, placebo about 5 cells against 36 real) and re-estimated for the report season on 2020-23 with the selected k. The cell family is declared by rule, not by hand: every inventory cell whose normal-score correlation-difference CI excludes 0 on 2020-22 (K = 36).
- **CTX-PLACEBO:** CTX with the context labels permuted across team-games within season (seeded). Its gain must vanish.

## Unit and support
- **Real market legs, box-score settlement.** A leg is a posted DraftKings t60 over main line (pts, reb, ast, fg3m; half-point so no push) for a player who played, with P(over) = P(Y >= point + 0.5) from the stored OOF grid on the integer support. Settlement is the box score (the same statistics sportsbooks and Kalshi settle on). DNP: legs exist only for players who played (a sportsbook voids a DNP; Kalshi settles at the last fair price), so absolute probabilities are conditional on playing, as in `nba/parlay/joint_eval.py`.
- **Pairs, not priced parlays.** No store holds a real same-game-parlay price (sportsbook SGP prices are not in the odds store; Kalshi has no historical multi-leg markets and its settled props are 2025-26, the holdout). So the pair is synthetic and the lines are real. The unit is a leg pair of one of the 23 inventory classes (same player x4, teammate same stat x4, teammate cross stat x3, opponent x4, own team margin x4, game total x4), over/over (an under leg is the complement event and carries no new outcome information). 3- and 4-leg sets use the same pairwise entries; they are a secondary slice, not a separate estimand.
- **Support:** the event "all legs hit" is binary; log loss and Brier on that event, both arms clipped identically to [1e-4, 1 - 1e-4]. The integer-grid support enters only through the shared marginals, so the two arms are compared on the same support.
- **Treated unit:** a pair that falls in at least one declared cell where CTX differs from BASE. The primary metric lives on treated units, because an untreated pair has identical probabilities in both arms and only dilutes the mean.

## Metric, floor, CI
- **Primary:** mean over treated report-season (2024) pairs of d = logloss(CTX) - logloss(BASE) (negative = CTX better).
- **Practical floor:** d <= -0.0003 nats per treated pair AND the median absolute change in the displayed joint probability among treated pairs >= 0.01. Reason: a one-percentage-point move in a 25% joint probability is a KL of about 2.7e-4 nats; if the shown number is quoted to the whole percent, a smaller improvement does not change what a person reads. This is the same order as the base copula's own whole gain over independence (-0.00053, CI [-0.00106, -0.00003], 15,798 synthetic parlays), so the floor is not arbitrary and not out of reach by construction.
- **CI:** paired game-clustered bootstrap (resample 2024 games, B = 5,000, `default_rng(2026)`), 95%, pass needs upper bound < 0. The game is the cluster: every pair of one game shares its outcomes. Cross-game residual dependence is about 0 (docs/PARLAY_ENGINE.md independence check), so a date-clustered interval is reported as a sensitivity only.
- **Secondary (descriptive):** Brier; reliability of the joint probability by predicted bin (CTX vs BASE); calibration intercept/slope of "all legs hit" on the logit of the joint probability.
- **Multiplicity:** one primary contrast (pooled CTX vs BASE on all treated pairs). The K = 36 cell-level contrasts are one BH family (q < 0.05), secondary, reported with n games and the same game-clustered CI; no cell word is issued without the pooled primary passing.

## Seasons
Discovery 2020-22 (the sizing inventory; seen, used only to declare the cell family and cell values), **select 2023** (k, nothing else), **report 2024** (one touch, walk-forward: everything fitted on 2020-23). 2025 is never loaded. This is the only split with real posted lines (2023-24 is the whole of the real-leg store before the holdout) and real OOF marginals; a different split would give up one of them.

## Slices (descriptive; no arm is kept on a slice, except K4)
By class kind, by context, by 2-leg versus 3-4-leg sets, by report season half, by leg-probability band (0.15-0.35, 0.35-0.65, 0.65-0.85), same-player versus cross-player.

## Kill criteria (any one ends the rule as a null; nothing is retuned afterwards)
- **K1 power gate (applies now):** the rule is not frozen unless the minimum-data gate shows power >= 0.80 for the primary at the 2020-22 effect size halved. It does not (below).
- **K2 placebo:** CTX-PLACEBO gain >= 50% of the real CTX gain, or the planted-future test (labels built from the game's own outcome) is not strongly better than real CTX (the harness is broken).
- **K3 persistence:** the 2023 select-season d and the 2024 report d have opposite signs.
- **K4 harm:** a class kind where CTX is worse than BASE by more than +0.0002 with the interval upper bound < +0.0005 is excluded from CTX (pooled primary re-run once on the remaining kinds, reported as a labelled secondary).
- **K5 calibration:** CTX reliability worse than BASE in the 0.20-0.40 and 0.40-1.00 predicted bins (the bins the app's headline numbers fall in).
- **K6 missingness:** treated share by season and a null-label audit (the `-1` label must not correlate with the outcome); the has_report NULL pattern is the project's one known leak shape.

## What this protects against
Reading 114 correlation looks as findings (36 versus a placebo's 5, but each cell is one look and winner's-curse prone); calling a context-specific marginal bias dependence (cells are centred inside the stratum and marginals are shared); an app that shows a second, fancier probability that is not more accurate than the first; a null mistaken for an effect because one season cannot detect 0.0002 (the power gate); a "context" feature that is really a missing-report pattern.

=== RESULTS BELOW ===

(empty: nothing has been run beyond the gate. The gate is documentation, not a result of the rule.)

---

# Minimum-data gate (non-negotiable 8), run 2026-10-14 on the real data

Code: `nba/parlay/context_prob_gate.py` (tests: `tests/parlay/test_context_prob_gate.py`). Outputs: `reports/parlay/context_prob_gate.json` and `.md`. No outcome enters any number below; no contrast was computed. The inventory shifts are from 2020-22; counts and marginals are from 2023-24.

## How legs and pairs are built, and what exists
| item | 2023 | 2024 |
|---|---|---|
| DraftKings t60 over main lines listed (pts, reb, ast, fg3m) | 54,000 | 54,711 |
| scored (player played, OOF marginal present) | 53,267 | 53,758 |
| games with a scored leg | 1,316 of 1,318 | 1,300 of 1,320 |
| pairs over the 23 inventory classes | 595,179 | 626,994 |
| book / snapshot rule | DraftKings only, `t60`; every line half-point; no multi-line legs. FanDuel is the stated sensitivity, not run | |

Pairs are exhaustive within a class, so they are heavily dependent (the 107k legs are the real information; the 1.2M pairs are not independent draws). The unit of dependence is the game.

**Real market legs versus box outcomes.** The lines are real; the outcomes are box scores; the pairing is synthetic because no real SGP price or settled multi-leg market exists in any store before the holdout. The existing joint evaluation (`joint_eval.py`, 15,798 synthetic parlays over 2,633 games) uses typical-ladder lines near the median; real DraftKings lines are an equivalent draw with the lines the app would see. Real lines are NOT at the model's median: mean model P(over) at those lines is 0.43 to 0.46 while the overs hit 0.47 to 0.49 (seen while building the legs, n = 107,025 legs; a marginal calibration fact, outcome-bearing, disclosed here because it was computed on the report season; it is not the contrast and does not enter any number below).

## Cells (114 class x context cells; the same cells as the 2020-22 inventory)
Counts per cell, report season 2024 (select 2023 is within 1% of these):
- every one of the 114 cells meets the inventory floor (>= 300 pairs and >= 40 games on each side) in both seasons and pooled: **114 of 114**;
- smallest cell: teammate fg3m-fg3m, leader out x competing: 1,045 pairs in 342 games (reference 2,936 pairs / 105 games on the smaller side); smallest games count: 342;
- the cells with the largest expected gain are same-player and teammate-cross pairs with 2,700 to 18,000 pairs in 600 to 1,300 games.
Counting is not the binding constraint. The binding constraint is size of effect against noise.

## Power (analytic, forecasts only)
Truth = the 2020-22 correlation shift of each cell (latent corr_z scale, the copula's own scale) applied to the stored 2024 marginals through the Gaussian orthant probability; expected paired log-loss difference = -KL(P_ctx || P_base), variance = P(1-P)(log(P_ctx/P_base) - log((1-P_ctx)/(1-P_base)))^2 per pair. Upper bound treats pairs as independent; lower bound treats all pairs of a game as one observation; the truth is between and clustering can only lower the upper number. One-sided alpha 0.05.

| season set | cells | best cell power, upper bound (point effect) | best cell, half effect | best cell, lower bound (point) | cells with upper-bound power >= 0.80 |
|---|---|---|---|---|---|
| report 2024 | 114 | 0.238 | 0.120 | 0.067 | **0 of 114** (0 of the 36 whose 2020-22 CI excluded 0) |
| select 2023 | 114 | 0.237 | 0.119 | 0.068 | 0 |
| pooled 2023-24 | 114 | 0.371 | 0.162 | 0.076 | 0 |

- Best cell (teammate cross-stat, A's points + B's assists, both top-3): expected gain per treated pair -1.05e-04 nats, median absolute change in the shown joint probability 0.0059 (0.6 points). Across all 114 cells the largest median absolute change is **0.0071; no cell reaches the 0.01 floor and none reaches -0.0003 nats at its own 2020-22 point estimate** (the best is -1.6e-04). With a Bonferroni alpha over 114 the best upper-bound power is 0.008.
- Effect multiple needed for 80% power in the best cell, upper bound: **2.7x** the 2020-22 estimate (report season), 1.9x pooled over the two seasons. The cells were picked from 114 looks, so a haircut is expected, not a multiple.
- Treating the 36 inventory-significant cells as independent evidence and summing their z (the most generous omnibus; overlap ignored, units independent) gives upper-bound power 0.51 for 2024 alone and 0.76 pooled at the full effect, 0.21 and 0.32 at half. The lower (clustered) bounds are 0.10 or below.
- Minimum detectable effect (80% power) in the best cell: 8e-05 nats (independent) to 5.4e-04 (game-clustered) per treated pair, against an expected gain of 1.05e-04.

**Gate verdict: FAIL (K1).** The effect is real in the correlations and invisible in the probability: the best realistic gain is about 1e-4 nats and a 0.6-point change in the displayed number, below the floor on its own, and below detection in one report season by a factor of 2 to 3 even under assumptions that favour the test. This is the playbook's stated failure for joint models ("power; one season detects about 0.002") confirmed on the real units.

## Data-capture audit (non-negotiable 8: before a gate is declared failed on data)
| source | seasons | state | repairable? cost |
|---|---|---|---|
| nba.duckdb box scores and games | 2022-2025 (+2026 live) | complete (1,318-1,320 games per season) | n/a; 2025 is the holdout, never read |
| nba_history.duckdb box scores | 2013-2021 (2019 has 1,143 games, 2020 1,171) | complete | n/a |
| official pre-tip injury report (OUT sets: vac, leader-out) | 2019-2024 (1,141 / 1,171 / 1,323 / 1,318 / 1,317 / 1,317 games) | present; none before 2019 | none before 2019 exists in a public archive we hold; leader-out and vacated-minutes contexts cannot be built for 2013-18 |
| production OOF marginals at real lines | 2023, 2024 only (110k rows per season) | stored | 2019-22 would need the month-block walk-forward over four stats (the sizing job's walk-forward, median line only): a maintainer run, minutes to an hour on the Mac; gives marginals, not lines |
| real posted player-prop lines | 2023, 2024 (DraftKings t60: 54k legs per season; 17 books) | stored in data/odds/odds_history.duckdb | 2022-23 regular-season props are not sold by the vendor (docs/research/historical_odds_2026-10-09.md; quote-only elsewhere); 2025-26 exists but is the holdout; extra books (FanDuel and others) add legs but are the same games, so no new clusters |
| real same-game-parlay prices | none | no store has one; Kalshi has no historical multi-leg markets; settled Kalshi props are 2025-26 | not repairable before the holdout; the paper/shadow log starts opening night |
| context-labelled pairs from box outcomes at median lines | 2020-22 stored (data/scratch/context_copula, discovery sample) | the inventory | the same machinery could extend to 2019 and, for the report-free contexts (compete, blowout, favourite) only, 2013-18 |

Repair that would move power: the report-free contexts evaluated on 2013-19 box-built median-line legs (7 seasons, about 9,000 games). By scaling the 2024 z by sqrt(7): best-cell upper-bound power about 0.79 at the full 2020-22 effect and about 0.34 at half, lower bound far below (scaling estimate, not a computation on those seasons). Costs: a walk-forward of the production props model over 2013-19 (maintainer run), era drift (three-point volume, pace), no real lines, and the leader-out and vacated-minutes cells (the largest same-player effects) cannot be evaluated there. It does not rescue the realistic case, so it is listed, not recommended.

## Playbook check ("Set and joint models", MODEL_PLAYBOOK)
- **Information or capacity?** Information in principle (pre-tip report contexts), but the information is already in the marginals: a context shifts a leg's own probability much more than the correlation between legs. The base copula's whole gain over independence is -0.00053; a context refinement of the correlation is a fraction of that.
- **Was the data enough for this class?** Pairs are plentiful (627k in 2024), clusters are not (1,300 games). Joint structure needs many games, not many pairs. Not enough games for an effect of about 1e-4.
- **Was the test valid?** The gate is valid for what it claims (counts and forecast-only power). Caveats stated: corr_z on count stats is attenuated by randomised-PIT noise (the inventory says differences are lower bounds), so true shifts may be larger than the estimates used (an upper-bound effect-multiple of 2.7 is not impossible); cells overlap; the 2020-22 shifts were selected from 114 looks (winner's curse), the opposite bias.
- **Lineage:** the b6e1fca inventory and the 2023-24 joint evaluation (independence vs Gaussian vs t), both carried forward; the t copula's gain over Gaussian was null.
- **What does it score against?** The joint probability of "all legs hit" at real lines, not a CRPS and not a price; calibration and log loss are the right pair here.
- **Typical failure (power):** confirmed. **Checks:** is the effect large enough to ever be detected? No. Playbook alternative: pool seasons or monitor rather than test once; pooling 2023-24 does not reach 0.8 either (0.37 best cell).

## What Devin decides
1. **Freeze or not.** Recommendation: do not freeze. The gate cannot pass (0 of 114 cells reach power 0.80 even in the independent-units upper bound at the full effect). Claude's earlier recommendation to close the work stands; this is the non-EV version of the same null: context moves the displayed joint probability by about half a point at most.
2. **If a probability answer is still wanted, three cheaper routes, none needing a new rule:** (a) ship the base copula and say in the app that same-game correlation is estimated from pooled history (what it already does); (b) shadow-log CTX next to BASE for 2026-27 pairs and read the cumulative difference at season end as descriptive only (no word; one season has power about 0.24 at best); (c) spend the effort on the larger lever: the marginals at real lines under-forecast overs by about three points (0.43-0.46 modelled against 0.47-0.49 observed, n = 107k legs), which moves a 2-leg joint probability at p of about 0.45 per leg by roughly three points (0.480 squared against 0.450 squared), about five times the largest context effect. That is a calibration question for LINE_CALIBRATION (ledger row 2026-10-12), not for this rule.
3. If Devin wants the test anyway: pooled seasons and the declared 36-cell omnibus is the only version with a power above 0.5 (upper bound), and a failure would be recorded as INCONCLUSIVE, not as evidence of no effect.

Disclosure: the marginal-calibration figures above were computed while building the legs, on both 2023 and 2024, before this text was written; they are descriptive and are not part of any rule.
