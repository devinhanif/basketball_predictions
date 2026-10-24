# MOVER_DEPTH_LIVE: a depth-chart minutes prior for players on a new team, scored forward on the 2026-27 live log (pre-registration)

Status: **DRAFT (modeler, 2026-10-23 evening). NOT FROZEN. RETROSPECTIVE GATE: STOPPED (power). LIVE GATE: partly measured, see below.** No fit,
no outcome read, season 2025 never loaded. Not a rule until the main session commits `prereg(MOVER_DEPTH_LIVE): <sha256>` on `main`, and not
before the live gate in "Minimum data checks" item L3 is measured by the maintainer. Everything above `=== RESULTS BELOW ===` freezes.
Reading-log note: `docs/research/team_strength_and_movers_2026-10-23.md` (Part B). Work label: Devin's request (2026-10-23 ~20:00 CDT,
"players on a new team", with his pace/quality scratch). Claude's own view, stated once: this is a measurement we cannot power inside a
season; the cheap, useful half is to log the inputs and watch the new-team slice, and the expected verdict is INCONCLUSIVE.

## Hypothesis
H1: for players whose team of record at tip differs from their last 2025-26 regular-season team ("movers"), in their first 10 team games of
2026-27, replacing production's carried-over expected minutes with a depth-chart prior (empirical-Bayes shrinkage of own minutes toward the
stayers' minutes-by-rank curve on the NEW roster, with the team's as-of pace and quality) lowers the paired integer-support CRPS of pts
against production (`props_context_residual`, T-60), per-minute rates untouched.
H0: no change. Counter-hypotheses up front: (a) the live movers' errors are mostly stale-roster rows and fringe/two-way players, not a
role-prior problem (the 2026-10-22 scorecard contrast, 1.657 [1.430, 1.920] n 300 vs 1.532 [1.425, 1.644] n 1,012, is not a detected
difference); (b) pace and quality move rotation depth by only 0.1-0.2 players (about 0.3-0.5 minutes for a given bench player).

## Lineage (playbook question 4)
T500 PRESEASON_ROLE_PRIOR (FAIL_ONE_SEASON x3, minutes-only arm carries the 2024 pts gain -0.0318; 5 of 115 players carry 98.6%;
player clustering required), T504 ROLE_PRIOR_SALARY (gate: power; MDE about 0.024 pts), T509 TEAM_USAGE_CAP (gate), T512 COLDSTART_SHAREBALL_T15
(gate: needs 7,700-32,000 rows), T446-T449 TEAM_BUDGET (HARM: no budget raking here), T264-T279 and T424-T439 (pace as a per-stat feature:
null; pace here enters only as a minutes/rotation input), T181 and F9/F9b (draft-slot role priors: small). This rule differs from T500 and T504
in that it is forward-only (blind by construction), isolates minutes, and adds the new-roster rank. Guards carried: player-level clustering,
leave-top-5-players-out, minutes-only isolation, within-date label shuffle, planted future.

## Data exposure (honest)
Retrospective 2023-24 mover outcomes were seen in T500/T504 (minutes-only arm and salary rank). The 2026-27 mover rows are unseen except the
first 15 games of the scorecard (2026-10-20..22; aggregate slice only, definition different). Those first 15 games are EXCLUDED from scoring
(found before the rule; the rule starts counting at the freeze commit time, by tip time, not by game date).

## Fixed design (constants final)
**Population (fixed before any result).** Rows (stat, game_id, player_id) for players who appear in the 2026-27 official roster cache at
tip - 60 min for team T, whose last 2025-26 regular-season team (from the roster/box history tables, read through the live pipeline, not by
this rule's author) is not T, with team-game number k = 1..10 for T, both arms scored (not DNP), tip after the freeze commit time. Exclusions
(declared): two-way/exhibit-10 players with < 100 prior NBA minutes (no own-minutes anchor); rookies (prior-season minutes undefined; the draft-slot
route is a different, closed question: F9b).
**Arms (all pre-tip, tip-gated, replayable from as-of tables; each differs from M0 only in the expected-minutes input):**
* M0 production `props_context_residual` at T-60, as logged.
* M1 depth prior: mu_i = w_i * mbar_i + (1 - w_i) * m(rho_i), w_i = n_i/(n_i + 5) where n_i = games played for the new team, mbar_i = production's
  recency minutes average; m(rho) = mean minutes of stayers at roster rank rho (rank of prior-season minutes per game among T's roster at tip - 60,
  ties broken by salary rank from the Basketball-Reference pull, then by name id) estimated on the stayers of 2022-2024 (nba.duckdb,
  `season <= 2024`, regular season) and frozen at the commit (fitted once, stored as a 15-row table; no refit during the season).
* M2 = M1 plus the rotation-depth term: m(rho) is replaced by m(rho; z_pace, z_qual) = m(rho) + beta_pace * z_pace * 1[rho >= 8] + beta_qual * z_qual * 1[rho >= 8],
  z the team's as-of prior-10-game pace (box possessions) and prior win% standardised on the league as-of, beta estimated once on 2022-24 stayers
  (same table) and frozen. (Devin's 2026-10-23 scratch gives only the sign and size order: +0.167 players per SD pace, -0.072 per +10 pts win%; the betas
  are re-estimated, not taken from the scratch.)
* Mc control (information-matched): M1 with rho computed from the player's OLD-team roster rank (no new-team information): isolates "any shrinkage
  toward a rank curve" from "the new roster".
* Shuffle S: M1 with the rank vector permuted within date across movers (seed 20261024): must lose its gain.
Rate heads and the distribution family are production's; the arms re-enter production's integer-grid distribution through the minutes feature only
(the T500 A1 hook), scored on the integer-support CRPS.
**Primary:** pts, M1 - M0 paired integer-support CRPS on the declared n. **Family:** {M1 - M0, M2 - M1, M1 - Mc} for pts (primary M1 - M0 only
decides; the others explain); secondary reb, ast, fg3m M1 - M0 (BH over 3) and minutes MAE and P(played >= 8 min) (descriptive).

## Declared n and inference
Counting stops at the first checkpoint where **>= 1,000 scored mover rows AND >= 100 distinct movers AND >= 14 distinct dates**; no earlier look
except a futility look at 400 rows (stop only if M1 - M0 > +0.02 with player-clustered CI lower bound > 0, i.e. clear harm). Expected arrival: when
every team has played its 10th game (about mid-November 2026); the live "new team" slice counted 300 rows in 15 games under a broader definition, so the
declared n may take the whole first month; if the season ends before it, INCONCLUSIVE. Inference: paired differences; clustered by player (binding, ~100
clusters), by game and by ISO week; the widest CI decides; B = 10,000 resamples, seed 2026 (the FORWARD_PREREG convention). Floors: pts 0.005 (the
family floor), reb 0.003, ast 0.002, fg3m 0.001 (T504's bars; loosening a floor to pass a gate is not allowed, so they are unchanged).
**Power, stated now.** At ~1,000 rows the binding half-width is about 0.017 pts (T504 scaled: 0.0167 at 1,202 rows; an estimate, not measured on this
design). MDE at 80% power about 0.024-0.030. The floor 0.005 cannot be certified by this n unless the true effect is several times larger; a result
within +-0.017 is INCONCLUSIVE and says nothing about the existence of an effect.

## Minimum data checks (fail = stop, no scoring)
Retrospective (done, rule 8, label-free; counts only, from `data/history/nba_history.duckdb` and `nba.duckdb`, `season <= 2024`):
R1. Offseason movers and first-10-game rows by season (prior-season minutes >= 500; my definition, narrower than T500/T504's by about 1.5x):
2014 93 / 673; 2015 98 / 749; 2016 106 / 779; 2017 95 / 715; 2018 87 / 551; 2019 121 / 990; 2020 92 / 741; 2021 106 / 862; 2023 93 / 703; 2024 75 / 551
(2022 not counted, union query not run). Pooled 7,314 rows (about 11,000 by T500's definition) -> best pooled pts half-width about 0.0055, at
the floor, with no production forecast before 2022 and three minutes eras (played-only rows until 2018): **RETROSPECTIVE PROPS TEST CANNOT PASS ITS GATE** (consistent
with T504 and T512). Not repairable inside the rule's life: A0 would have to be rebuilt for nine seasons and 2023-24 mover outcomes were already seen.
R2. Minutes-level retrospective test: better powered (T515: minutes half-width 0.021 min at ~7,500 rows) but a minutes gain is not a props gain
(T167: -0.0018 pts for a rule vs -0.571 for oracle minutes) and the minutes noise arm was not run (no harness): UNMEASURED; not part of this rule.
Live (maintainer to measure before the freeze):
L1. The stayers' curve m(rho) and betas fit on 2022-24 stayers and stored as a table with its sha256; stayers n per rank cell >= 200 (not yet
measured; the cells are 15 ranks x 3 seasons of ~30 teams).
L2. Known-at: the official roster cache at tip - 60 min exists for >= 99% of team-games since 2026-10-20 (the 2026-10-20 log shows a failed fetch and
stale-team rows on opening night); inputs (prior minutes, salary rank from `data/raw/bref_salaries_2026-10-21/`, pace and win% as-of) each carry a
known-at <= tip - 60. Planted future: shifting any input by one game changes M1 for the shifted player only.
L3. Mover count under the declared definition on the live log (needs 2025-26 rosters, a holdout season I do not read): the maintainer reports
movers per team-game and projected n by 2026-11-15; if the projection is < 1,000 rows by 2026-12-31, the rule stops as UNDERPOWERED before freeze.
L4. M0 reproduction: the arm pipeline with the minutes feature set to production's own value reproduces M0 to 1e-9 on 20 logged dates.
Data audit for rule 8 (what else could repair power): 2013-2021 box scores in the history DB (counted above), preseason logs 2019-2025 (30/30 teams;
a minutes anchor, not a role observation), NBA transactions (not captured; ToS check queued in DECISIONS 2026-10-22), salary pull 2022-23..2026-27 (payroll
is end-of-season: no signing dates, T504). Each repair is a different rule, not a patch to this one.

## Pass rule (all must hold at the declared n)
1. M1 - M0 pts paired integer CRPS point <= -0.005 and the widest CI upper < 0.
2. M1 - Mc <= -0.002 with widest CI upper < 0 (the new roster beats shrinking toward the old-roster rank).
3. S (shuffled ranks) minus M0 >= -0.002 (the gain disappears when the information is scrambled).
4. Leave-top-5-players-out: M1 - M0 still <= -0.002.
5. No harm: reb, ast, fg3m M1 - M0 CI lower bound <= +floor each (no stat significantly worse); stayers not scored (they are not touched).
A pass is a SHADOW candidate (a minutes override for movers' first 10 games) for 2027-28 review; no production change, no promotion (Devin's), no 2025
touch (the holdout is spent for this data class by T500 anyway).

## Verdicts (named in advance)
* PASS: above. * FAIL_HARM: M1 - M0 CI lower > +0.005. * INCONCLUSIVE: anything else, including a point estimate below -0.005 with the CI crossing 0
(the expected outcome). * LEAK_SUSPECT: S minus M0 < -0.004 or a planted-future difference. * UNDERPOWERED_STOP: L3 fails.
One pre-registered follow-up at most: if INCONCLUSIVE with a negative point estimate, the same arm pooled with the first 1,000 rows of 2027-28 at a
declared 2,000-row n (MDE about 0.017), then closed. No retrospective variants, no pace/quality grid, no re-tuning of kappa = 5 or the 8-minute cut.

## What each choice protects against
* Forward-only, tip-gated, first 15 games excluded: the T500/T504 seasons are not blind; the live log is.
* Player-clustered CI and leave-top-5-out: 5 of 115 players carried 98.6% of T500's pts gain.
* Mc and S controls: shrinkage toward any rank curve could win by variance reduction, and a gain from scrambled ranks would be a leak or a trend.
* Minutes-only isolation: rates, usage and a team budget are closed ground (T446-T449, T509, T512); mixing them would hide which factor moved.
* Stated MDE and INCONCLUSIVE as the expected verdict: F9 and F12 could not pass their own gates; a null from an unpowered test is not a null.
* Frozen m(rho) and betas fitted on 2022-24 stayers (high n): no season of mover outcomes ever enters the fit.
* Floors unchanged: raising or lowering them after seeing the MDE would be choosing the answer.

## Playbook check
Class: shrinkage/count layer on a GBM production model (minutes input swapped). 1 Information or capacity: information (rank on the new roster, salary rank);
pace/quality are second-order. 2 Data enough: the curve yes (stayers); the mover evaluation no (T504), hence live and INCONCLUSIVE expected. 3 Test valid: tip-gated
roster, planted future, M0 reproduction, integer support, player clustering; the scorecard's "vs last pre-season game" slice is NOT the population. 4 Lineage: above.
5 Scored against: production's integer CRPS (not the line; the line is out of reach at this n, T477).

## Priors (also in docs/PREDICTIONS_LEDGER.md, written before any row)
P(freezable: L1-L4 pass and the maintainer wants it) 0.55; P(declared n reached by 2026-12-31 | frozen) 0.80; at the declared n: P(PASS) 0.08, P(FAIL_HARM) 0.05,
P(INCONCLUSIVE) 0.82, P(LEAK_SUSPECT) 0.03, P(UNDERPOWERED_STOP) 0.02 (sum with n-not-reached 0.20 folded into INCONCLUSIVE); expected M1 - M0 pts -0.010
(80% -0.040 to +0.015).

=== RESULTS BELOW ===
(none)
