# MINUTES_DECISION: minutes and starter status as a coach's decision, predicted from the situation (pre-registration)

Status: **DRAFT 2026-11-02 (shell clock 20:2x CDT). Branch `agent/modeler-role-shift`. NOT FROZEN; no arm fitted; no scored outcome read;
season 2025 never loaded.** Program: `docs/research/role_shift_program_2026-11-02.md` (part 1 of 5). Drafting approved by Devin (DECISIONS
2026-11-02 20:09); freezing is a separate decision after the adversary's pre-freeze read. Everything above `=== RESULTS BELOW ===` is the
rule once frozen. Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/MINUTES_DECISION.md | shasum -a 256`.
Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25; 2022 is warm-up; 2025 is the spent holdout and is never loaded).

Model class: **gradient-boosted trees** (LightGBM two-component minutes mixture, the MINUTES_V2 M2 recipe, frozen hyper-parameters) with
declared feature groups; props through the existing context-residual path (MINUTES_V2 route: six minutes columns appended).

**Advice or momentum.** Devin's request. Claude's prior is low (section "Priors") because the closest measurements say situational columns
move this mixture by about 0.02 minutes (MINUTES_V2: M2 minus M1 -0.021 / -0.003 min) and coach habits moved props by +0.0002 (T447).

## Hypotheses

* **H1 (situation, primary).** A minutes-given-plays forecast that sees the situation (both teams' outs, opponent size and 3-point profile, the
  coach's own rotation template and minutes-vs-margin curve, the player's history under this coach, rest, role stability) has lower minutes
  CRPS than the simplest learned baseline that already knows recency and recent role (arm F), by at least 0.10 minutes, in 2023 and 2024.
* **H2 (the coach's part).** Of that gain, the coach and player-under-coach groups carry at least 0.05 minutes beyond every other group (C minus B),
  and shuffled coach identities do not reproduce it.
* **H3 (role stability, Devin's addition B).** The gain of C over F is larger for players whose recent role is unstable than for stable ones.
* **H4 (props, conditional).** Fed to the props model, C's minutes improve integer-support props CRPS over FULL.
* Null: none of these. A null is a result: it says the coach's decision is not predictable beyond what recent minutes already say at T-60.

## What each design choice protects against (read with the arms)

* F, not production's minutes path P, is the comparator (Devin's addition A; the T508 incident): P is 0.8 min worse than M1 (MINUTES_V2), so a
  gain over P would be the baseline's weakness. F knows recency and recent role, the two things that already won (T458, T467).
* B (everything but the coach) before C, and two coach placebos: crediting the coach for what outs, rest or blowout risk explain.
* Same model, hyper-parameters, rows, folds and seeds in every arm; no tuning: a bigger model passing for information (60-trial searches added nothing).
* Coach of the PREVIOUS team game (season opener: the reference's first coach): `team_coach_games.known_at` equals each game's own tip.
* Binding CI = the widest of game, player-season and ISO-week clustering: the arm's effect is shared within a player-season (BEST_PRACTICES 2026-10-20).
* Conditional missingness audit with a positive control: the NULL-pattern leak, without the T450 mistake of failing a legitimately as-of NULL.
* Integer grid for every props arm; continuous for minutes in every arm: the T114-T118 support artifact.
* Familiarity WITH for movers declared untestable now (gate G8): a claim the data cannot carry cannot be made.

## Rows, venue, data

* **Minutes rows**: the MINUTES_V2 minutes rows (played, `n_prior >= 5`; ids in `reports/minutes_v2/minutes_scores.npz`): 27,619 (2023, 1,318
  games) and 27,583 (2024, 1,315 games); none of the 5 games back-filled by d872dc7 (0022400147, 0022400621, 0022400633, 0022401229,
  0022401230) is among them, and they are excluded from every state build. Regular season, play-in and playoffs (slice `playoffs` reported).
* **Props rows**: the stored A0 rows per stat (27,619 / 27,701), same exclusion.
* **Secondary rows**: P(start) and P(play): rostered, not OUT on the T-60 report, `n_prior >= 5` (the T455 / T458 row set, 33,093 / 33,127).
* **Venue**: pre-tip at T-60: the official report gated on the real tip (`tip_source = "real"`), schedule, standings, earlier boxes, stints and
  possessions of EARLIER games. No T-30 lineups (history has none). Coach data: 2022-10-18 onward (what we hold; history 2013-21 has no coach
  rows, gate G10). `nba.duckdb` read-only; `season <= 2024` in every SQL. Data version at drafting: manifest `378e4fbed639`
  (docs/data_manifests/latest.json, 2026-11-02 12:09 UTC); `minutes_scores.npz` sha256 prefix 9ad6a6ed. The gate is re-run at the freeze and
  at the run on the data the run uses.

## Feature groups (constants final; all as-of: strictly earlier games, or the real-tip-gated T-60 report, or schedule facts)

* **BASE** = production `COMMON_FEATURES` as in MINUTES_V2 M1 (recency `min5/min10/min40`, starter history, rest, b2b, `exp_margin` /
  `abs_margin`, `n_out_rot`, `vac_min`, report flags, ...).
* **RR** (5) = `wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5` (`research/workload/asof.py`, unchanged; RECENT_ROLE).
* **V2** (22) = the MINUTES_V2 group exactly (foul risk, age, own b2b gap, return ramp, own blowout history, rotation tightness, teammates
  OUT detail, game importance, season phase).
* **STAB** (3, Devin's addition B) = `rs_sd10` (sd of the player's minutes over his last 10 played games, >= 5 required), `rs_use_sd10` (sd of
  (FGA + 0.44 FTA + TOV) / minutes over his last 10 played games with minutes >= 5), `rs_p8` (ROLE_CHANGEPOINT's BOCPD P(run length < 8) at that
  draft's declared gate constants SIGMA 5.0, TAU 6.0, HAZARD 1/30, NO grid search; function vendored verbatim from
  `research/eval/role_changepoint_rule.py` at 78d0250).
* **OPP** (6) = `opp_n_out_rot`, `opp_out_max_min40` (the opponent's OUT list on its T-60 report, the same definitions as the own-team columns);
  `opp_height_w` (as-of `min10`-weighted mean `players_static.height_in` of the opponent's not-OUT players with `n_prior >= 5`; unknown height
  = the league as-of mean, flagged in the audit); `opp_big_share` (share of the opponent's minutes over its previous 10 games in stints with >= 2
  players of height >= 82 in); `opp_3pa_rate` (3PA / FGA, previous 20 games); `opp_3p_pct` (previous 20 games).
* **COACH** (21). Coach = the as-of coach above. Habits from his EARLIER team-games since 2022-10-18 on any team, EWMA with half-life 40 of his
  games, shrunk to the league as-of mean with K = 10 pseudo-games (`h = (sum w x + K L) / (sum w + K)`, the COACH_PHASE form). Eleven habit
  columns: `ch_n` (log1p of his earlier games), `ch_depth10` (players with >= 10 min), `ch_top8_share`, `ch_first_sub_s` (seconds to the team's
  first substitution, from stints; team-games whose stint durations miss the game length by more than 5 s are skipped), `ch_starter_stint_s`
  (mean seconds to each opener's first exit), `ch_close5_share` (in games with abs(margin) <= 5 at 6:00 of Q4, share of Q4 last-6-minute
  player-seconds taken by the five players with the largest prior-10 minutes), `ch_blow_slope` (starters' mean minutes in games with final
  abs(margin) >= 20 minus < 10), `ch_q4_cliff` (starters' Q4 minutes when abs(margin at the end of Q3) >= 15 minus < 10; the T508 SHAPE form),
  `ch_b2b_top5` (top-5 minutes on second nights minus other nights), `ch_b2b_rest_rate` (share of as-of rotation players, `min10 >= 20`, absent
  from the box on second nights minus other nights), `ch_out_depth` (`ch_depth10` in games with >= 1 rotation player absent minus none).
  Five tonight-state products: `ch_blow_slope x p_blow20`, `ch_q4_cliff x p_blow15` (`p_blowK` = P(abs(margin) >= K) under Normal(`exp_margin`,
  12.5)), `ch_b2b_top5 x b2b`, `ch_b2b_rest_rate x b2b`, `ch_out_depth x 1[n_out_rot >= 1]`; and the same five times `starter10`.
* **FAM** (6) = `pc_with_n` (log1p of the player's earlier played games whose actual coach is tonight's as-of coach, any team), `pc_with_min`
  (EWMA half-life 20 of his minutes in those games; NaN when none), `pc_with_start` (starter share in those games; NaN when none),
  `pc_role_gap` = `pc_with_min - min10`, `pc_against_n` (log1p of his earlier played games against a team this coach coached), `pc_against_min`
  (his mean minutes in those; NaN when none). NaN means "no such earlier game": known at tip, audited below.

## Arms (minutes given plays; identical rows, month-block walk-forward with 2022 warm-up, train strictly before each block, seed 0)

| arm | features | role |
|---|---|---|
| R | `min10 x` proportional-conformal quantiles (MINUTES_V2 R) | A0's own recency centre: the simplest; reported |
| **F** | BASE + RR | **simplest learned baseline ("FULL minutes"); H1 comparator** |
| B | BASE + RR + V2 + STAB + OPP | everything except the coach; H2 comparator |
| **C** | B + COACH + FAM | **the decision model** |
| KO_g | C minus one group g in {COACH, FAM, OPP, STAB, V2} | knockouts: what each group carries beyond the rest (descriptive) |
| PL_ID (3 seeds) | C with coach identities shuffled: within each season the coaches are permuted across team-seasons (a team-season's coach is replaced by another coach of that season for all its games), then COACH and FAM are rebuilt for the assigned coach with the same as-of timing | placebo with real labels (Devin: "shuffled coach ids") |
| PL_DONOR (3 seeds) | C with the COACH and FAM columns permuted jointly across rows within game date | noise-column control under real labels (H2) |
| PL_DONOR_ALL (3 seeds) | C with every column outside F (V2, STAB, OPP, COACH, FAM) permuted jointly within game date | noise-column control for H1 |
| P, M2 | production minutes path; MINUTES_V2 M2 refit on current data | reported (M2 = BASE + V2) |
| O | C + realised final margin + realised starter flag | ORACLE, descriptive; a non-oracle arm that beats it is a presumed leak |

## Props (H4; run only if H1 clears the 2023 select step)

Six columns per minutes arm out of fold (`mv_mean, mv_q10, mv_q50, mv_q90, mv_sd, mv_pi`) appended to the props features by
`stat_feature_names(stat, extra=...)`; heads, calibration, lower-tail mixture and integer grid (199 points, `ceil(q - 0.5)`) otherwise
production's. Arms: **A0** (production), **FULL** (A0 + RR, T467 recipe), **PF** = FULL + F's columns, **PC** = FULL + C's columns.
Contrasts: PC minus FULL (primary), PC minus PF (knockout: what the decision model carries beyond the simplest minutes model), PC minus A0
and FULL minus A0 (reported, the simplest-baseline line Devin asked for).

## Metrics, support, inference

* Minutes: continuous CRPS (2 x mean pinball over the 199-point grid), minutes; paired arm minus comparator per row.
* Props: integer-support CRPS per stat; composite CM = mean over pts/reb/ast/fg3m of (delta / floor), floors 0.005 / 0.003 / 0.002 / 0.001
  (T507 construction, one joint bootstrap on shared clusters).
* P(start), P(play): log loss and Brier, paired.
* CI: 95% percentile bootstrap, 2000 draws, seed 0 (`nba/props/metrics.py::_clustered_boot_means`), clustered by game, player-season and ISO
  week; **the widest binds**. Every cell also prints the game-clustered CI (Devin's addition A) and n.
* p: two-sided bootstrap p under the binding clustering. BH q = 0.05 within each family and season.

## Families, floors, pass rules (2023 selects; 2024 is evaluated only for cells that cleared 2023)

* **Family M (minutes, m = 3 per season; cells named after the hypotheses):** cell H1 = C minus F, all minutes rows, floor **-0.10 min** (the MINUTES_V2 / COACH_PHASE / WORKLOAD
  minutes floor; about 3% of F's CRPS). cell H2 = C minus B, all rows, floor **-0.05 min**. cell H3 = (C minus F on unstable rows) minus (C minus F on
  stable rows), directional, no floor (its detectable size is printed, about 0.03 min). Unstable = `rs_sd10 >= 6.339` min, the 67th
  percentile of 2022 rows (as-of, label-free; fixed now).
* **Family P (props, m = 5 per season):** CM of PC minus FULL, floor **-1.0 floor unit**; pts PC minus FULL, floor **-0.005**; reb -0.003,
  ast -0.002, fg3m -0.001.
* **Family S (secondary, m = 2, cannot carry a verdict):** P(start) C-start minus F-start (LightGBM binary, same groups), floor -0.005 log
  loss; P(play) C-play minus RECENT_ROLE R (T458's model), floor -0.005.
* A cell **clears a season** when point <= floor, binding-CI upper < 0 and BH p <= 0.05 (H3: CI upper < 0 and BH p <= 0.05).
* **Guards (each must hold in both seasons for a word that names the cell):** (g1) abs(mean(y - mean)) <= 0.5 min; (g2) central 80% coverage in
  [0.75, 0.85]; (g3) P(PIT <= 0.10) in [0.08, 0.12]; (g4) no slice with n >= 300 where C is worse than F by more than +0.05 min with binding-CI
  lower > 0 (slices below); (g5) top-5 player-season share of the H1 gain <= 0.5; (g6) the near-DNP slice (realised minutes < 5) is printed and
  may not be worse than F by more than +0.10 min (T471 / T479 lesson); props: abs(bias) <= 0.5, 80% coverage in [0.75, 0.85], no slice
  (n >= 300) worse than FULL by more than +0.01.
* **Attack gates (a run that fails one is VOID, no model result; the fix is a new id):** (a1) PL_ID mean gain over B and PL_DONOR mean gain over
  B each keep strictly less than half of C's gain over B (for H2), and PL_DONOR_ALL keeps strictly less than half of C's gain over F
  (for H1); (a2) planted future:
  every game dated on or after a cut (2023-12-01 and 2024-12-01) altered (coaches renamed, margins +40, box minutes x 50, stints shuffled):
  every added column of every earlier row identical, a control (later rows) changes >= 50%; (a3) planted same-game: the target game's box,
  stints, possessions, score and coach edited: every added column of that row identical; (a4) planted coach change: a synthetic mid-season
  change on one team: COACH columns switch exactly one game after (previous-game rule), FAM switches with them; (a5) missingness audit
  (below) passes and its positive control fires; (a6) A0 props reproduce the stored A0 to 1e-6 per row; F's training frame hash is identical
  across two builds after rounding every float to 1e-9 (the LINEUPS_KNOWN_REBASE jitter incident); (a7) no non-oracle arm beats O.
* **Missingness audit (a5), conditional, post-freeze (it reads realised minutes buckets):** for each NULL-able indicator (coach history zero
  `ch_n == 0`; FAM NaN; `opp_height_w` imputed; STAB NaN), within as-of strata (min10 bucket <10 / 10-20 / 20-28 / >=28 x starter10 >= 0.5 x
  mover flag) with n >= 300, the NULL rate by realised-minutes bucket (<5, 5-10, 10-20, 20-30, >=30) may differ from the stratum rate by at
  most 2 pp (row-weighted mean of the absolute gaps). Positive control: a planted column NaN exactly when realised minutes < 5 on a random 10%
  must exceed 2 pp, or the audit is inert and the run is VOID. Label-free rates by as-of strata are in gate.json (`null_by_asof_strata`): coach
  history zero 0.0-0.23% of rows; familiarity zero 0.5-2.7% (highest for bench players with min10 < 10, as expected for new arrivals).

## Verdict words

* **PASS_SITUATION**: H1 clears both seasons with all guards and attack gates. Consequence: a red-team review; FULL + C minutes may enter a
  shadow log proposal (Devin's decision). Nothing is promoted.
* **PASS_COACH**: PASS_SITUATION and H2 clears both seasons (and a1). Only then may anyone say "the coach's decision is predictable".
* **PASS_PROPS** (adds to either): CM of PC minus FULL clears both seasons; pts named only if pts clears on its own.
* **STABILITY_CONFIRMED** (descriptive word, only alongside PASS_SITUATION): H3 clears both seasons.
* **FAIL_ONE_SEASON**, **FAIL_NO_GAIN**, **FAIL_GUARD**, **PLACEBO_TOO** (a1 fails), **VOID** (a2-a7), **HARM** (H1 point > +0.10 with binding
  CI lower > 0).
* The familiarity-for-movers claim is declared INCAPABLE of a word here (gate G8): FAM is a feature group and a knockout, never a headline.

## Slices (fixed; printed with n, point and binding CI; guard g4 uses those with n >= 300)

starters / bench (`starter10 >= 0.5`); movers' first 10 games with a new team (T500 definition) / stayers; in-season arrivals; teammate-OUT
(`n_out_rot >= 1`); realised blowout (final abs(margin) >= 20; outcome-defined, labelled so) and pre-tip blowout risk (abs(`exp_margin`) >= 10);
new player-coach relationship (`pc_with_n < 20` raw count); rookie or short-history coach (his earlier games < 20); unstable / stable
(addition B); back-to-back second night; playoffs (game id 004); near-DNP (realised minutes < 5).

## Descriptives D1-D4 (pre-declared, run in the same harness after the arms; no floors, no verdict, each feeds a later part)

* **D1, changepoint vs recent-role (part 2).** Rows with `rs_p8 >= 0.5` and >= 10 earlier games this season, 2023 and 2024: (a) share with
  abs(`wl_mean_min_l3 - min10`) < 2 (recent-role columns see no change); (b) F's mean bias and CRPS on flagged vs unflagged rows; (c) C minus F
  on flagged vs unflagged. Trigger for a NEW ROLE_CHANGEPOINT id: abs(F bias on flagged) >= 1.0 min with CI excluding 0 in both seasons AND
  C's gain on flagged >= 2x its gain on unflagged. Otherwise part 2 stays parked.
* **D2, situational substitutions (part 4).** Event: a bench player (as-of `starter10 < 0.5`, else his box-starter share of the prior 10 games)
  with as-of 3PA per minute >= 0.20 (prior 40 played games, >= 10 games) enters mid-period (a stint start that is not a period opener, periods
  1-4) while his team has >= 6 3PA so far and an in-game 3P% at least 5 pp below its as-of season 3P% (>= 5 earlier games). Control: his
  check-ins with the team at or above its rate. Outcome: his 3PA per on-court minute over the next 8 game minutes or until he exits, divided
  by his as-of 3PA per minute; also the team's 3PA share while he is on. Report the cold-minus-control difference, player-clustered CI, per
  season 2022-24. Trigger for a part-4 rule (an in-game feature for T508): difference >= 0.10 with CI excluding 0 in 2023 and 2024.
* **D3, familiarity vs convergence (part 3).** Movers' (offseason and in-season) games with the new team, 2023-24: games until the mean of
  his last 3 played minutes is within 3 min of his mean over games 11-30 with that team; compare `pc_against_n >= 2` vs < 2 at his first game
  with the team (player-clustered CI); `pc_with_n >= 1` is printed with its n (3 and 2 movers) and not tested; plus the new-coach cohort
  (stayers whose coach changed: 6 team-seasons at the opener and 3 / 4 mid-season). Trigger for part 3 (after the history coach pull):
  difference >= 2 games, CI excluding 0 in both seasons.
* **D4, pts/reb/ast relationship by role state (Devin's addition C).** Same-player correlations of randomized-PIT z residuals from FULL's
  integer grids for (pts, reb), (pts, ast), (reb, ast), 2022-24 (2022 from the warm-up OOF where it exists, else 2023-24 only, printed), by:
  as-of scorer vs playmaker (player's share of team pts minus share of team ast over the prior 20 games, terciles fixed on 2022); as-of minutes
  trend (`wl_mean_min_l3 - min10` > +3 / < -3 / else); realised minutes vs `min10` up / down (outcome-conditioned, labelled); stable vs unstable.
  Fisher-z difference from the pooled correlation with a player-season-clustered CI, compared with `nba/parlay/copula.py::estimate_pair_corr`
  on the same rows. Trigger for a JOINT_ROLE_COPULA rule (joint log loss on same-game parlay legs vs the static copula): a pre-tip-knowable
  state with abs(dRho) >= 0.05, CI excluding 0 in both seasons.

## Minimum-data gate (non-negotiable 8), measured 2026-11-02 20:19 CDT, label-free

Command (read-only, 10 s, light; no lock): from the worktree, `NBA_DATA_ROOT=/Users/devin/Downloads/nba-prediction
/Users/devin/Downloads/nba-prediction/.venv/bin/python -W ignore -m research.eval.minutes_decision_gate --seeds 10 --out
reports/prereg_minutes_decision/gate.json`. Output `reports/prereg_minutes_decision/gate.json` (sha256 prefix 2ccc44d7; a second run reproduced it exactly except `wall_seconds`). Dry-run counts: 3,953 games (2022-24, back-fill excluded), 7,906 coach team-game rows, 84,052
played box rows, 55,202 scored minutes rows. Reads no realised outcome of a scored row (see the module docstring).

| check | 2023 | 2024 | bar | pass |
|---|---|---|---|---|
| G1 team-games with an as-of coach | 2,636 / 2,636 | 2,630 / 2,630 | all | yes |
| G2 as-of coach equals the actual coach | 0.9985 | 0.9985 | >= 0.99 | yes |
| G3 scored rows whose coach has < 20 earlier games (window from 2022-10) | 0.046 | 0.055 | <= 0.10 | yes |
| G3b coach earlier games, median [p5, p25, p75] | 121 [21, 96, 149] | 196 [17, 112, 231] | none | 2023 sees one prior season only |
| G4 rows whose coach has >= 10 earlier games in the state: blowout (final abs margin >= 20) / 10-19 / close < 10 / second night | 0.858 / 0.925 / 0.954 / 0.887 | 0.866 / 0.922 / 0.947 / 0.859 | each >= 0.75 | yes |
| G5 minutes power, all rows: half-width of a label-free sd-0.05 player-season noise contrast (MINUTES_V2 M1 grid; 10 seeds; binding = player-season) | 0.0169 (sd 0.0017) | 0.0173 (sd 0.0017) | <= 0.05 (half the H1 floor) and <= 0.025 (half the H2 floor) | yes |
| G6 slice rows (all >= 300) and half-widths, min: starters 12,267 / 0.034; bench 15,352 / 0.011; teammate-out 23,246 / 0.018; movers first 10 1,213 / 0.038; new relationship 6,503 / 0.020; unstable 9,009 / 0.016; stable 18,610 / 0.021; realised blowout 6,155 / 0.022; b2b 4,422 / 0.025 | (2023 values) | starters 12,057 / 0.033; bench 15,526 / 0.011; teammate-out 23,115 / 0.017; movers 1,034 / 0.041; new relationship 6,462 / 0.021; unstable 8,947 / 0.017; stable 18,636 / 0.022; blowout 6,517 / 0.021; b2b 4,694 / 0.025 | n >= 300; half-width <= 0.05 for g4 to be able to fire | yes |
| G6b H3 detectable size (approx. sqrt(hw_unstable^2 + hw_stable^2)) | about 0.026 | about 0.028 | printed | n/a |
| G7 props power on the props rows | taken from T507's gate on the same A0 rows (regular season 25,815 / 25,649): composite CM half-width 0.400 / 0.390 floor units (bar 1.0); pts alone 1.10 / 1.08 floor units | same | CM <= 1.0 | yes for CM; **pts alone underpowered** (a true -0.005 is detected about half the time) |
| G8 movers' pre-move familiarity (first game with the new team): any earlier game WITH the new coach / >= 20 / >= 2 games AGAINST him | 3 / 1 / 52 of 133 | 2 / 2 / 71 of 114 | >= 100 movers with WITH >= 1 for a WITH claim | **FAIL for WITH** (claim declared incapable); AGAINST descriptive only (D3). Power on the movers' slice: minutes half-width 0.038 / 0.041 min (G6, adequate for a 0.10 slice guard); props on movers: T504's pts half-width 0.0167 / 0.0170 vs 0.005 (unpowered, as T504 found) |
| G9 planted future (cut 2023-12-01): coach and familiarity counts of the 5,584 earlier 2023 rows identical; control rows changed | identical; 0.987 | (one edit covers both) | identical; control >= 0.5 | yes |
| G10 history coach data 2013-21 (data audit) | `team_coach_games` 0 rows, `team_coaches` 0 rows; boxes 27,526-33,848 rows a season and stints 1,143-1,323 games a season are held | | listed | the long-history version is unavailable without the pull (open choice 2) |
| G11 D2 events (cold / control; players; games; top-5 player share) | 2,105 / 8,058; 93; 501; 0.205 | 2,359 / 10,021; 104; 533; 0.135 | >= 500 cold events and >= 50 players | yes |
| G12 coach changes (team-seasons with a new coach at the opener / a mid-season change) | 6 / 3 | 6 / 4 | printed | D3 new-coach cohort is small |
| G13 season 2025 loaded | no | no | never | yes |

Also measured: `coach_id` is NULL on 89 (2022) and 28 (2023) team-games (Dwane Casey 82, Steve Nash 7, Kevin Ollie 28), so the coach key is the
diacritic-free name; scored rows with no team match 0. Still to measure at the freeze (needs the harness, not labels): A0 and F identity (a6),
the OPP height coverage (`players_static.height_in` on opponent rotations), and the stint-validity share used by `ch_first_sub_s`.

## Priors (written before any arm; draft row for docs/PREDICTIONS_LEDGER.md, appended by the main session)

| Date | Rule / question | Prior stated before the result | Where written | Outcome | Hit? |
|---|---|---|---|---|---|
| 2026-11-02 20:3x | OPEN: MINUTES_DECISION priors (modeler, DRAFT on agent/modeler-role-shift; seen: label-free gate counts and noise-arm half-widths only; no arm output) | P(PASS_SITUATION) 0.07; P(PASS_COACH) 0.03; P(PASS_PROPS) 0.03 (pts named 0.02); P(H1 point < 0 with binding-CI upper < 0 in both seasons, floor ignored) 0.55; expected H1 (C minus F, 2024) -0.03 min (80% interval -0.08 to +0.00); expected H2 (C minus B) -0.01 min (80% -0.03 to +0.01); P(STABILITY_CONFIRMED given PASS_SITUATION) 0.45; P(P(start) cell clears both seasons) 0.20; P(PLACEBO_TOO or VOID) 0.15; P(D1 trigger) 0.10; P(D2 trigger) 0.25; P(D3 trigger) 0.05; P(D4 trigger) 0.30 | docs/prereg/MINUTES_DECISION.md | pending | |

Why low: the V2 group (own outs, rotation tightness, own blowout history, importance) moved this mixture by -0.021 / -0.003 min; FULL already
holds the recent-role information that won; coach habits added +0.0002 pts (T447) and the coach blowout habit was not reliable (split-half r
-0.07, T450); with coach data from 2022 a 2023 row sees one earlier season of its coach. The new information that could still matter is the
opponent's outs and size, and the coach's response to his own team's outs (`ch_out_depth`), which nothing here has measured. Transmission to
props: MINUTES_V2 turned -0.58 min into -0.0053 / -0.0018 pts, so a 0.10-minute gain predicts about -0.001 pts; PASS_PROPS needs far more.

## What would reverse the program's direction

* FAIL_NO_GAIN with H1's CI inside +-0.05 min in both seasons: "the coach's decision at T-60 is not predictable beyond recent minutes" is
  recorded; parts 2-3 close unless their own descriptive triggers fire; the minutes lever stays T-30 information (T522) and the late report.
* PASS_COACH: the history coach pull (open choice 2) becomes the next data job, and part 5 uses C's expected minutes.

## Kill criteria

* H1 fails the 2023 select step: H2-H4 are not evaluated on 2024; props are not fitted (cost); the descriptives D1-D4 still run (declared).
* Any attack gate fails: VOID; a new id, never an edit. One pre-registered follow-up at most (the history-window version after the coach
  pull, or the T-30 version once forward lineups exist), then closed. No variant chasing (other half-lives, K, windows, groups, GBM settings).
* Season 2025: no touch requested by this rule.

## Compute and harness (after the freeze)

Harness to build after the freeze: `research/features/coach_decision.py` (COACH, FAM, OPP, STAB builders with planted tests) and
`research/eval/minutes_decision_eval.py` (stages `gate`, `features`, `minutes`, `props`, `secondary`, `attack`, `descriptives`, `report`;
refuses unless the frozen sha256 matches; per-stage checkpoints in `data/ops/minutes_decision_cache/`; prints progress per block). Estimate,
one process on the Mac CPU, peak memory about 3 GB: features about 5 min (stints and possessions 2022-24, about 220k and 786k rows); minutes
arms R, F, B, C, 5 knockouts, 9 placebo fits, O = 18 mixture fits x 2 seasons x about 18 blocks at about 1 min per arm-season: about 36 min;
props (4 stats x 3 refits) about 8 min; secondary 2 min; descriptives 3 min; bootstraps about 3 min: **about 55 minutes**. The maintainer runs it
inside `mkdir data/ops/heavy.lock` (retry every 120 s; `rmdir` in `finally`); the modeler never runs the full fit.

## Open choices (defaults in bold; Devin decides before the freeze)

1. Minutes floor for H1: **-0.10 min** (consistent with MINUTES_V2) or -0.05 (detectable, practically worth about -0.0005 pts).
2. History coach pull 2013-21 (270 Basketball-Reference pages, about 15 min, Devin runs it; terms read pending): **not before this freeze**
   (run on 2022+ coach data as gated); the alternative re-gates on the longer window first.
3. Comparator for H1: **F (BASE + RR)**, or B-minus-OPP (also gives the opponent group credit to the baseline).
4. Unstable cut: **rs_sd10 >= 6.339 (2022 p67)**, or `rs_p8 >= 0.5` (changepoint confidence) as the slice definition.
5. Props primary: **composite CM** (powered) with pts as a named cell, or pts alone (underpowered, G7).
6. Run D1-D4 even if H1 fails the select step: **yes** (they are the gate evidence for parts 2-4).

## Playbook check

* Class: gradient-boosted trees (LightGBM mixture) on feature groups; props via the context-residual GBM; secondary binary GBMs.
* Information or capacity: OPP, COACH and FAM are new pre-tip information (the opponent's report, the coach's earlier decisions, the player's
  earlier games under him); STAB and V2 re-express the minutes stream (capacity) and are in B, so H1's credit to them is judged by knockouts.
* Enough data: trees on 2-3 seasons are fine; per-coach habits are thin (34 coaches, one earlier season at 2023, shrunk K = 10); the history pull
  is the repair, listed with its cost.
* Test validity: real-tip report gating; played rows; continuous minutes support and integer props support for every arm; binding CI over three
  clusterings; real-label placebos (PL_ID, PL_DONOR); planted future, same-game and coach-change plants; conditional missingness audit with a
  positive control; A0 / F identity; near-DNP slice; power measured on the rows (G5-G7). Comparator F knows recency and recent role (T508 lesson).
* Lineage: COACH_PHASE (stopped at its gate: data drift and an unconditional missingness gap), TEAM_BUDGET (coach habits null), MINUTES_V2 (the
  model recipe and route), RECENT_ROLE_FULL (FULL), ROLE_CHANGEPOINT (rs_p8 at declared constants). Guards carried: previous-game coach rule,
  5-game back-fill exclusion, frame-hash rounding, binding clustering.
* Scored against: minutes CRPS (primary), props integer CRPS (conditional); nothing here is scored at a market line.

=== RESULTS BELOW ===

(not run)
