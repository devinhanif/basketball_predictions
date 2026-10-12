# TEAM_BUDGET: a game is a closed system; forecast the team's minutes budget, then allocate by role (pre-registration)

Status: FROZEN Sun Oct 11 22:37:56 CDT 2026 by the main session on Devin's words ("coaches go team to team, resting back to back given the teams status ... if not freeze it"), after the coach-habit amendment. The main
session commits `prereg(TEAM_BUDGET): <sha256>` BEFORE any arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the
rule. Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TEAM_BUDGET.md | shasum -a 256` (first 8 hex into the results section
and every ledger row). Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = frozen holdout, never loaded).
No arm exists. The only model computation run is the A0 reproduction (stored components, no fit).
Amendment (Devin, relayed 2026-10-11 after the first draft, in his words "coaches go team to team, resting back to back given the
team's status ... if not [in the ensemble], freeze it"): (1) coach habits are CAREER-pooled across teams, not per coach-season;
(2) back-to-back rest is conditional on as-of team status; (3) the reliability screen is re-run with those definitions; (4)
confirmations recorded: gamma = 1 primary, previous-game coach identity, stage-1 totals deferred to TEAM_BUDGET_2, `in_roster` not a
feature. Habits that failed the first screen are dropped only if they still fail the redefined one (see Coach habits).

## What Devin said
> "a game is a closed system; model the role, not the player." (2026-10-11, approved reframing)
Budget, then allocate: team minutes are fixed (240 + 25 per OT); each player's minutes are a share of that budget (including P(DNP))
set by a ROLE (starter/bench, rotation rank, closing share, recent trend, change of team or coach, the coach's habits); a player's
stat is share x budget x per-minute rate; teammates' forecasts are drawn jointly so they are consistent.

## Honest prior (read first)
* Information, not architecture, has won here. This idea is architecture over information the model mostly already has: the
  official report (vacated minutes, `n_out_rot`), recency minutes, role and rest are all in production (A0). Nearest neighbours,
  all closed: USAGE_PACE and team pace (FOUR_FACTORS T264-T279; ADVANCED_RATES T424-T439, 28b9ea6), lineup context (F11 T244-T251,
  1a93e56), minutes model v2 (T162-T173, 1005158: minutes CRPS -0.83 min yet pts props -0.0053 in 2023 and -0.0018 in 2024, floor
  missed in 2024), MINUTES_HAZARD (T300-T313: props null).
* The possession sim lost for props. T001 "sim beats season-average on reb" was a DNP-zero artifact and is retracted; the fair
  rematch (55d0c3b, T062-T064, 600 games of 2024, played rows, clustered) had the recency average winning: pts +0.268, reb +0.123,
  ast +0.014 CRPS for the sim; T061 pts +0.190 [+0.175, +0.206] on 138,409 player-games. Why this is a smaller, different claim: the
  sim REPLACED the forecast and generated stats from rates, so every rate error compounded. Here A0 (the calibrated production
  distribution, with the lower-tail mixture) stays; the budget acts as ONE multiplicative factor per player, mean one by
  construction, clipped to [0.8, 1.25], with no new rate model. The claim is only "the team-level shortfall or surplus of the
  minutes forecast, and who absorbs it, predicts the stat error". It can lose the same way if the factor is noise; the oracle
  below says how much is at stake if the minutes were KNOWN, not forecast: pts -0.573 (T166 oracle, MINUTES_V2), i.e. the value
  is in realised minutes, which are not knowable at T-60. Expected outcome: a null (my estimate: 10-15% that any cell clears the
  floor). The rule exists so a null is clean and closed.
* What is genuinely new, found while measuring the gates: production's unconditional expected minutes do NOT sum to the budget.
  Summed over the as-of rotation roster, `p_play x mu` has mean 264.2 / 258.6 (2023 / 2024) and sd 46 against a 241.5 budget; the
  raking factor has sd 0.184 / 0.205 (gate G5). That is a large, pre-tip, feature-only team-level inconsistency, which is the only
  reason the idea is testable at the floor. Whether it is information or the noise of an imperfect roster is the experiment.

## Hypothesis
H1 (T1, constraint only): scaling each player's A0 distribution by the factor that rakes production's roster minutes to the team
budget lowers integer-support CRPS by at least 0.005 for at least one of pts, reb, ast, fg3m. H2 (T2, role model): a share model
over the roster (role features + coach habits) raked to the same budget does so beyond T1. Null: no change. Protects against:
crediting a bias correction as "role" (T1 is the minimal constraint and the bar T2 must beat), and crediting coaches for what role
alone explains (knockout T2-noCoach).

## Data exposure (honest)
Seen: only gate statistics (stint/box reconciliation, minutes identities, coach sample sizes, habit reliability across 2022 vs
2023 seasons from the box only, forecast-minutes sums, A0 reproduction). No stat outcome of any budget arm; no coach habit was
related to any stat error. Season 2025 is not loaded (`season <= 2024` asserted in SQL and code); `nba.duckdb` is opened
`read_only=True`. Selection 2023, report 2024; 2022 is warm-up only. Rows scored: the A0 rows (played, `n_prior >= 5`; 27,619 /
27,701 per stat) so the comparison is the same rows as production; this means the unconditional budget uses ALL roster rows
(including DNP) to form its factor but is scored on players who played, exactly like A0.

## Fixed design (constants final)

### Stage 1, team budget
Budget `B_g = 240 + 25 x ot_rate_asof` (`research/budget/constraint.py::team_budget`), `ot_rate_asof` = share of earlier team-games
that went to overtime (strictly earlier dates; 0.06 before any exist; observed 5.4-5.5%, so B = 241.4). The budget for the rostered
players is `B_roster = B_g x cover_asof`, `cover_asof` = as-of mean share of a team's realised minutes held by the as-of roster
(observed 98.7-98.8%). Team possessions, FGA, FTA and points totals are NOT forecast or scored in this rule: stat totals are the
minutes budget times a team rate, the rate half has been tested as pace/four-factors/usage and was null (T264-T279, T424), and
coupling stats to team totals is the single pre-registered follow-up TEAM_BUDGET_2, conditional on a PASS here.

### As-of roster (never the box score)
`research/budget/asof.py::asof_roster`: players with at least one appearance (minutes > 0) for the team in its previous 10
games, minus the real-tip-gated OUT set (`tip_source="real"`, as F11), with `apps`, `mean_min`, `start_rate` from that window.
Tonight's box, minutes, starters and stints are never read (planted-same-game test). A player who plays but is not rostered
(1.2% of minutes) is outside the budget and keeps his A0 distribution unscaled (r = 1) in every arm.

### Production expected minutes
`e_i = p_i x mu_i` from the production minutes path (`predict_minutes`, shrunk trailing-150 `p_play`, `mu`) at the player's
latest row dated STRICTLY before the game (tonight's row used when present: 90% of roster rows; else the latest earlier row, else
`p = 0.5`, `mu = mean_min`). Gate G5 measures the sum of `e_i` per team-game.

### Arms (identical rows, seeds, A0 grids; integer support `ceil(q - 0.5)` for EVERY arm and stat)
* A0 = production + lower-tail mixture (`reports/lower_tail/components_*.npz`, `arm_grid("mixture")`): pts integer CRPS 3.140236 (2023)
  / 3.176840 (2024); reb 1.288179 / 1.311401; ast 0.901954 / 0.898517; fg3m 0.566212 / 0.590155. Reproduced to 0.0 (G4).
* T1 (constraint only): `e~ = rake_to_budget(e, B_roster, cap 48)` per team-game; `r_i = clip((e~_i / e_i) / kbar_asof, 0.8, 1.25)`,
  `kbar_asof` = mean of `e~/e` over A0 rows of strictly earlier dates (preserves A0's average scale; only the tilt acts).
* T2 (full budget + role): a LightGBM regressor (production params from `configs/context_residual.yaml`, CPU, seed 0) on the
  share of rostered-player minutes `m_i / sum_roster m` (DNP = 0, so P(DNP) is inside the target), walk-forward month blocks, 2022
  warm-up, trained strictly before the block. Prediction raked to share-sum 1, times `B_roster`, capped at 48, so the sum is exact.
  `r_i = clip((e2_i / e_i) / k2bar_asof, 0.8, 1.25)` with `k2bar_asof` defined as for T1.
  Role columns: `e_i`, `p_i`, `mu_i`, rank of `e_i` in the roster, `e_i / sum e`, `start_rate`, `apps`, `mean_min`, closing share
  (share of the team's previous 10 games with final |margin| <= 10 in which the player was on court in the last stint of the
  fourth period; `stints`), recent trend (share of roster minutes, last 3 vs last 10 appearances), games with the team (cap 10),
  `n_roster`, `S = sum e`, rest days, back-to-back, as-of |Elo margin|, `n_out_rot`. Coach columns (Section below): `h_depth`,
  `h_conc`, `h_blowout`, `h_star_rest_all`, `h_star_rest_matched`, plus tonight's as-of status (win%, games back of the 6th and 10th
  seed, late flag). No `in_roster` flag, no tonight's lineup.
* T2-noCoach (knockout): T2 without the coach columns.
* P (placebo): T2 with the role and coach columns permuted jointly across rows within team-season (marginals kept, link broken).
* Descriptive only, never in the family: O1 oracle budget (realised OT known), O2 oracle shares (realised minutes shares). They are
  leaks by construction and bound what any version could give; a non-oracle arm that beats O2 is a presumed leak.
* Sensitivity, declared now: elasticity exponent gamma = 0.5 (`r^0.5`) for T1 and T2, descriptive. Primary uses gamma = 1.

### Why raking
Raking (scale all expected minutes by one factor, freeze players at the 48-minute cap, re-spread) is the KL projection of the
production forecasts onto the single linear constraint, has no parameters, is exact to 1e-9 (tested; 1.1e-13 on 2,000 real
team-games), and keeps the player order. A softmax or Dirichlet head would add a model and a concentration to tune; the
Dirichlet only enters for the joint draws (below), where the sum constraint must hold structurally.

### Stage 3 and joint draws
A player's arm distribution is `A0 quantile x r_i` on the 199-quantile grid, then the integer grid: stat = (share x budget) x rate,
with the rate the one A0 already implies (no new rate model; the sim's failure). Joint team draws: minutes ~ `B_roster x
Dirichlet(kappa x e~/B_roster)`, kappa estimated from prior blocks by the method of moments on share residuals; the sum equals the
budget exactly in every draw. Joint draws do not change the marginal CRPS; they serve the consistency check only.

### Coach habits (as-of; `research/budget/asof.py`, `research/budget/status.py`)
Source `team_coach_games`. Its `known_at` is EACH GAME'S OWN tip (equal to `tipoff_utc` on every row), so tonight's coach is never
stamped knowable at T-60. The coach whose habits a game receives is the coach of the team's PREVIOUS game (stamped before tonight's
tip for 100% of 2023-24 team-games; equals the actual coach in 99.6%); a new coach's first game gets the predecessor's habits.
Habits follow the COACH: each is estimated over his whole as-of career in our data (all his earlier games on any team and season,
strictly earlier dates), shrunk to the league as-of value (pseudo-counts 15 games for depth, concentration and blowout pull; 10 star
pairs for star rest), not per coach-season. Definitions, all from post-game boxes of earlier games:
* `h_depth`: players with >= 10 minutes. `h_conc`: Herfindahl of minute shares. `h_blowout`: top-5 minutes in |margin| >= 20 games minus
  <= 10 games (career-pooled over all statuses: the median coach has 14.5 / 10 / 7 blowout games per status in 2022-23, below the
  declared 15 needed to condition it on status, so it is NOT status-conditioned).
* As-of team status (`status.py::team_status`, standings from games dated STRICTLY earlier within the season, same-day games blind):
  win%, games back of the 6th and the 10th seed of the team's conference, `late` = 60 or more games played. Class: contending = at or
  above the 6th seed; middle = below it but inside the 10th seed; out = beyond the 10th seed; middle when fewer than 20 games played.
* Star rest: the team's top-2 players by minutes per appearance over its previous 10 games, on the second night of a back-to-back, who
  played the night before. `rest` = 1 if absent with a rest or management reason in `player_availability`, 0 if he played; absent for
  any other reason (injury) is dropped. `h_star_rest_all` pools statuses; `h_star_rest_matched` is the coach's rate in tonight's status
  when it is contending or middle and the pooled rate when it is out (that cell fails the screen). Plain back-to-back top-5 minutes
  difference (the earlier definition) is `h_b2b`.
Reliability screen, stated before it was computed (`team_budget_gates.py reliability`; box and availability data only, no stat outcome,
season 2024 untouched): coaches with >= 60 games in 2022-23 (33 coaches, 5,095 team-games); each habit estimated career-pooled on
the ODD and the EVEN games of the coach's 2022-23 career (alternate games, by date), Pearson r across coaches, Spearman-Brown corrected
`2r/(1+r)`; a habit is kept iff the corrected value >= 0.30; conditional means need >= 3 games of each kind (>= 5 star pairs) per
half. Result:
| habit | coaches | split-half r | corrected | kept |
|---|---|---|---|---|
| depth | 33 | 0.930 | 0.963 | yes |
| concentration | 33 | 0.927 | 0.962 | yes |
| blowout pull (career) | 33 | 0.323 | 0.488 | yes |
| back-to-back top-5 minutes (career) | 33 | 0.058 | 0.110 | NO |
| star rest, contending | 19 | 0.623 | 0.767 | yes (17 rest events, 393 pairs) |
| star rest, middle | 25 | 0.335 | 0.501 | yes (16 events, 573 pairs) |
| star rest, out | 11 | 0.038 | 0.073 | NO (8 events, 270 pairs) |
| star rest, all statuses | 33 | 0.415 | 0.587 | yes (41 events, 1,236 pairs) |
Entering T2: `h_depth`, `h_conc`, `h_blowout`, `h_star_rest_all`, `h_star_rest_matched`. Dropped: `h_b2b` and star rest in the out status
(they still fail after the redefinition). Cautions recorded, not used to change the rule: alternate-game splits share the same
roster and season, so depth and concentration reliabilities (0.96) measure team stability as much as the coach (the previous
cross-season r was 0.70-0.71); star-rest estimates rest on only 41 events, so a few coaches carry them and the feature is mostly
shrunk values near 0.03. The knockout T2-noCoach removes all five columns.

## Metrics, support, inference
Integer-support CRPS (199-quantile form) per stat per arm; paired dCRPS = CRPS(arm) - CRPS(A0); 95% percentile bootstrap clustered
by `game_id` (2000 draws, seed 0, the two teams of a game share a cluster); two-sided clustered-bootstrap p; Benjamini-Hochberg
across the 8 primary cells {T1, T2} x {pts, reb, ast, fg3m} at q = 0.05, separately for 2023 and 2024. Per-cell MDE (1.96 x
clustered SE) printed beside each result (prior studies on these rows: 0.0006-0.0029, below the floor). Secondary, declared and
unable to rescue a failure: bias, 80% PIT coverage, lower-tail PIT, threshold log loss; team consistency (below); decision score.
* Team consistency (secondary): per team-game `|sum e - B_roster|` for production (mean 39.3 / 37.9 minutes at G5) vs `|sum e~ - B_roster|`
  (exactly 0) for T1/T2, and the Dirichlet joint draws' within-team pairwise correlation of minutes vs the realised one.
* Decision score (secondary; `docs/DECISION_SCORING.md`): P(over) at the DraftKings T-60 line from each arm's integer grid, 53,247
  (2023) and 53,752 (2024) legs, all on A0 rows (by stat 2023: pts 14,354, reb 15,052, ast 9,650, fg3m 14,191), thresholds 0 / 2 /
  5 / 10%, ROI per unit with the game-clustered CI, "insufficient sample" below 300 bets. A0's own scored ROI is the comparator.
  It is silent on limits and availability; a descriptive number, not a claim.
* Slices (descriptive plus the guard): starter / bench (as-of `start_rate >= 0.5`), star-out (an as-of top-2 minutes player of the
  team is OUT on the report), overtime games (known only afterwards: a descriptive slice, not a feature), cold start (5 <= n_prior
  < 10), back-to-back, first 15 team games, coach in his first 20 games (`coach_n_prior < 20`), team status (contending / middle / out, late season), terciles of the raking factor.

## Pass rule (per cell; 2023 selects, 2024 confirms; all must hold)
1. 2023: point dCRPS <= -0.005 with clustered CI upper < 0. 2024: point <= -0.005, CI upper < 0, BH p <= 0.05 over 8 cells.
2. Guards: `|cov80_arm - 0.80| <= |cov80_A0 - 0.80| + 0.03`; `|bias_arm| <= |bias_A0| + 0.05` and <= 0.5; no slice with n >= 300 worse
   by more than +0.01; cold-start and coach-new slices have CI lower bound <= 0 (a gain only where there is no history is a flag).
3. Attack gates (a cell is a PASS only if all hold): arm beats the placebo P by a point <= -0.003 (T1's placebo permutes `r` across
   rows within team-season); shuffled-label (T2's share model refit on permuted training shares within block) gives dCRPS >= -MDE;
   the planted tests (Checks 3-4) pass with non-inert controls; the missingness audit (G6) passed; the arm does not beat its oracle.
4. Role claim: T2 passes AS ROLE only if T2 - T1 <= -0.003 (paired, same rows); otherwise any gain is credited to the constraint.
   Coach claim: only if T2 - T2noCoach <= -0.002 and the T2 gain does not drop by >= 80% when any single coach column is removed alone.
5. Honest ceiling: only the gain beyond A0 counts; A0 already contains vacated minutes, `n_out_rot`, recency minutes, role and rest.
A PASS means a red-team review and a drafted one-touch holdout row. Nothing is promoted or wired in; that is Devin's.

## Minimum-data checks run after the freeze (fail = stop, no model result)
1. G1-G6 below re-run on the frozen data (same numbers).
2. Planted same-game: edit the target game's box, minutes, starters, stints and score; every roster, `e`, coach-habit and role
   column for that game is byte-identical. Second plant: move a rostered player to OUT AFTER the real tip: no change. A control
   (the NEXT game) must move. CI tests exist for the roster and the coach habits (`tests/research/budget/test_asof.py`).
3. Planted-future: any game dated >= the target date is ignored by every feature. 4. The rake sum equals `B_roster` to 1e-9 and flag-off
   (no scaling) reproduces A0 to 1e-6.

## What each choice protects against
* A0 = production + mixture, same rows: a weak baseline (the sim's backtest wins vanished against a calibrated recency baseline).
* T1 before T2: the constraint credited as "role" when it is a bias correction. Mean-one normalisation `kbar` and the [0.8, 1.25] clip:
  a global rescale, or one bad roster, moving every forecast.
* Roster from the previous 10 games and the previous game's coach: the box-score membership leak; the coach stamp equal to the tip.
* Integer grid for every arm: the T114-T118 artifact.
* Reliability screen on 2022 vs 2023 box data: coach "habits" that are noise; chosen before any stat error was seen.
* Placebo, shuffled labels, knockout: extra columns and capacity; the single-group-carries-everything fingerprint
  (`opp_adjusted_ridge`). Missingness audit by minutes bucket: the NULL-pattern leak.
* Select 2023, report 2024, 2025 untouched; floor -0.005 and MDE: a p-value on a gain nobody could use.

## Kill criteria
* Checks 2-4 fail or G6 fails after the freeze: stop; the fix is a new id, not an edit.
* No cell selects on 2023: closed. No variant (other windows, habits, elasticity, rate models). Gamma = 0.5 is reported, not chosen.
* A non-oracle arm beats O2: presumed leak, audited before any number is reported.
* One pre-registered follow-up at most (TEAM_BUDGET_2: couple stat totals to a team-points forecast; or A0 refit with the budget
  columns as plain features, the fair rival to the constraint), only after a PASS. Season 2025: no touch is requested by this rule.

## Outputs and harness
`research/budget/` (constraint, as-of builders, 15 unit tests), `research/eval/team_budget_gates.py` (gates, runnable now), and
after the freeze `research/eval/team_budget_eval.py` with stages `gate`, `arms`, `attack`, `report`, refusing unless the sha256
matches. Exact command after the freeze (the maintainer; the modeler never takes `heavy.lock`):
`mkdir data/ops/heavy.lock` (retry 120 s; `rmdir` in `finally`), then
`.venv/bin/python -m research.eval.team_budget_eval run`. Estimated runtime on the Mac CPU, single process: T2 share model about 4
fits x 18 month blocks x ~3 s about 4 min; scoring 4 stats x 5 arms x 2 seasons with 2000-draw clustered bootstrap about 5 min;
decision score about 1 min; about 10-15 min in total, with per-stage checkpoints in `data/ops/team_budget_cache/`.
Outputs `reports/prereg_team_budget/results.json` and `reports/prereg_team_budget.md`: `[stat, season, arm, n_rows, n_games, crps_int,
dcrps, ci_lo, ci_hi, p, p_bh, mde, bias, cov80]`, slice rows, audit rows, check rows, decision-score rows, seeds, sha256.

## Minimum-data gates (non-negotiable 8): measured 2026-10-11 on `nba.duckdb` (read-only) and stored components; seasons 2022-2024
No arm was fitted. Reproduce with `.venv/bin/python -m research.eval.team_budget_gates all` (about 20 s; light, no lock); JSON in
`reports/prereg_team_budget/gates.json`.
* G1 stints coverage: 7,916 / 7,916 team-games (2022 2,640; 2023 2,636; 2024 2,640) have stints (100%); the stint clock sums to
  2880 + 300 x OT seconds in 100% (OT from the highest period; 424 OT team-games in 2022-24, 4.7-6.4% by season). PASS.
* G2 minutes sum: box player minutes sum to 240 + 25 x OT within 0.5 min in 7,916 / 7,916 team-games (mean gap +0.0025, max 0.067);
  10.5-10.7 players play per team-game. Stint-derived player minutes vs box: 84,170 player-games, mean absolute gap 0.0026 min, 22
  (0.026%) over 1 minute (before the stints rebuild this was 48.1% within 1 minute, F12 T185). PASS.
* G3 coach data: a coach row for 7,916 / 7,916 team-games; `coach_id` NULL in 117 (name present; key = id else name). Coach-team-seasons:
  33 / 34 / 34 (2022 / 23 / 24), median 83 games; 1-3 under 20 games. As-of coach history (the coach's earlier games in the data):
  `coach_n_prior < 10` for 2.2% (2023) and 2.7% (2024) of team-games, `< 20` for 4.4% and 5.5%, `>= 40` for 91% and 90% (2022 is the
  warm-up, 12% under 10). Conditional samples per coach-season: median 14-15 back-to-back games, 15.5-17 blowout (>= 20) games, 43-44
  close (<= 10) games. Previous-game coach usable at T-60 for 98.9% (2022) and 100% (2023, 2024); equals the actual coach in 99.6%.
  Own-game coach stamped before T-60: 0% (the `known_at` finding). Reliability: superseded by the career-pooled, status-conditioned
  screen in the Coach habits section (first-draft per-season screen: depth 0.70, concentration 0.71, back-to-back 0.20, blowout 0.14).
  PASS: five habit columns kept, `h_b2b` and out-status star rest dropped.
* G4 A0 reproduction (stored components, no fit, 10 s): 16 of 16 cells (4 stats x 2 seasons x {raw, mixture}) match
  `reports/lower_tail/candidates.json` to 0.0 with the same n (27,619 / 27,701): pts 3.152830 / 3.187553 raw and 3.140236 / 3.176840 mixture;
  reb 1.292817 / 1.314247 and 1.288179 / 1.311401; ast 0.905903 / 0.900215 and 0.901954 / 0.898517; fg3m 0.569049 / 0.592213 and
  0.566212 / 0.590155. Note Devin's quoted pts 3.140236 / 3.176840 is the mixture; the re-baselined production A0 is 3.152830 / 3.187553
  (ADVANCED_RATES G5). PASS.
* G5 roster and budget (feature-only; no outcome): as-of roster 14.1 / 13.9 players per team-game (2023 / 2024); the roster holds 98.8% /
  98.7% of realised team minutes (5th percentile 92%); 10.2% / 9.3% of roster rows have no row in tonight's box and use the latest
  earlier forecast. Production `sum p x mu` over the roster: mean 264.2 / 258.6, sd 46.3 / 46.0 (box-membership sum for contrast: 251.3,
  sd 32.3, which is the leaked roster). Rake factor against B = 241.4-241.5: mean 0.944 / 0.967, sd 0.184 / 0.205, 5th-95th
  percentile 0.70-1.29 / 0.72-1.34; |factor - 1| > 5% in 80% / 79% of team-games. Reach limit (sd >= 0.02, otherwise T1 cannot
  move a forecast by the floor): PASS by 9x. Rake exact to 1.1e-13. PASS. Caution recorded: the large spread partly reflects roster
  rows for players who will be inactive (known at T-30, not T-60), so the factor may be noise.
* G6 missingness audit (A0 rows 2023-24, 55,320, by realised-minutes bucket <5 / 5-10 / 10-20 / >20 min): NULL rate of every coach
  habit column and of `coach_n_prior = 0` is 0.0% in all buckets (gap 0.0 pp; shrinkage guarantees no NaN, limit 2 pp). The "not in the
  as-of roster" rate is 6.0 / 3.7 / 2.2 / 0.8% (gap 5.2 pp against a permuted-label placebo gap 0.3 pp): a real pre-game fact (players
  with no recent appearances play less) but exactly the leak shape, so `in_roster` is NOT a feature, those players keep r = 1, and the
  adversary should attack it first. PASS for NULLs; the in-roster gap is flagged, not waved through.
* G7 decision legs: DraftKings T-60 legs on A0 rows: 53,247 (2023) and 53,752 (2024), 100% of the stored legs. PASS.
* G8 fan-out: the maintainer's run needs the roster-by-game frame (about 112k rows) and four LightGBM fits per block; peak memory
  well under 1 GB; no GPU.

=== RESULTS BELOW ===

(not run)
