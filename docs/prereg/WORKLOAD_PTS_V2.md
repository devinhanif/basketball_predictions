# WORKLOAD_PTS_V2: do the workload, schedule and travel columns add to points beyond recent-role minutes? (re-test of T452, pre-registration)

Status: **FROZEN 2026-10-27 19:09 CDT (from `date`; Devin "redo all tests that didn't work", DECISIONS f5cc952; adversary pre-freeze AMEND applied, docs/reviews/redo_wave1_prefreeze_2026-10-27.md). Was: DRAFT (written 2026-10-27 18:41 CDT, from `date`; revised 2026-10-27 19:07 CDT (from `date`) after the adversary pre-freeze review
docs/reviews/redo_wave1_prefreeze_2026-10-27.md, edits W1-W7), modeler, branch `agent/modeler-workload-pts-v2` off f5cc952. Not frozen.**
Devin's request (DECISIONS 2026-10-27 18:32, wave 1, item 1): "redo all tests that didn't work before"; validity audit redo list rank 4
(docs/reviews/validity_audit_2026-10-12.md, section 3). The main session reads this with Devin and commits
`prereg(WORKLOAD_PTS_V2): <sha256>` BEFORE any arm is fitted. Everything above the line `=== RESULTS BELOW ===` is the rule.
Freeze evidence: commit hash plus `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/WORKLOAD_PTS_V2.md | shasum -a 256`.
Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = the frozen holdout, never loaded by this rule's Stage 1).
No arm of this rule has been fitted or scored. The gate below is counts and coverage only.

## The rule in five lines
1. Candidate **WF** = RECENT_ROLE_FULL (FULL: production heads + the 5 recent-role columns, + FULL's refit `pi`) with the OTHER 22 WORKLOAD
   columns added to the stat heads only; comparator **FULL** itself. Stat: pts. Metric: integer-support CRPS on played rows, WF minus FULL.
2. Pass, per season: WF - FULL <= **-0.005**, game-clustered 95% CI upper < 0; 2023 selects, 2024 reports (p <= 0.05; family m = 1).
3. Guards: past-only placebo of the 22 columns (WF - PL <= -0.003 each season); a both-arms label shuffle judged against its own
   permutation null (K = 50 seeds per season); coverage, slices, plants, missingness, reproduction.
4. Descriptive, cannot pass or fail the rule: T452 re-judged with the fixed placebo (B, the 27 columns on A0, minus a past-only placebo of
   the 27), the overlap of T452's gain with recent role, per-family knockouts.
5. Expected outcome, stated now: a null (P(pass) about 0.02). A null on WF - FULL is a valid null for the 22-column increment (a new ledger
   row). T452's INVALID label is re-judged only by the descriptive B - PL27 reading (section Descriptive), which is this rule's answer to
   validity-audit redo item 4.

## What this re-tests and why (lineage)
* **T452** (WORKLOAD pts, rule 261fabce): B = A0 + 27 workload columns in the heads: -0.0071 [-0.0109, -0.0034] (2023) / -0.0082 (2024),
  n 27,619 / 27,701. It failed only "beats the placebo by -0.003" (2023 B - PL +0.0006), and that placebo permuted each column across the
  WHOLE player-season or team-season, handing early rows later values (harness defect D2, workload_review_2026-10-12; validity audit F8).
  Its shuffled-label arm was inert (D1: could only create a false pass). Validity audit: **INVALID**, may not be cited against the idea.
* **T451** (cell P, same rule): passed; the adversary found it carried by `min_last`. That finding produced RECENT_ROLE (T458, P(play), 2025
  PASS) and **RECENT_ROLE_FULL** (T467-T469: pts FULL - A0 -0.0084 / -0.0083, adversary SURVIVES; T473: 2025 -0.0116, not a pass only
  because a shuffle guard fired that fires on ~19% of honest shuffles, F9 / shuffle_guard_diagnostic_2026-10-12). FULL's 5 columns
  (`wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5`) are 5 of WORKLOAD's 27.
* So the open question is not "does WORKLOAD carry pts information" (T452's LOAD knockout already said: LOAD carries all of it, and recent
  minutes are LOAD), but "**does anything in WORKLOAD beyond the five recent-role columns add to pts**". The comparator therefore holds the
  five columns (BEST_PRACTICES "Baselines must know what the model knows", T508; validity audit F10).
* Guards that disqualified or wounded an ancestor, carried forward or dropped:
  | ancestor guard | status here |
  |---|---|
  | WORKLOAD placebo (whole-season permutation, D2) | **replaced** by the past-only placebo (`research/recent_role/placebo.py`, RECENT_ROLE / PI_RECENT_ROLE / RECENT_ROLE_FULL) |
  | WORKLOAD shuffled label (inert, D1) | **replaced** by the shared-permutation shuffle in BOTH arms (RECENT_ROLE_FULL construction, unit-tested to fail on a planted signal) |
  | RECENT_ROLE_FULL fixed shuffle threshold (-0.001 with a bootstrap CI; F9, fires ~19%) | **replaced** by a K = 50 permutation null per season (BEST_PRACTICES "Shuffle guards must be sized from a permutation null") |
  | WORKLOAD knockout "one family carries >= 80%" (`opp_adjusted_ridge` fingerprint) | **carried**, as a naming flag needing adversary clearance |
  | WORKLOAD G6 missingness by realised-minutes bucket (failed once on schedule NULLs) | **carried** (gap <= max(2 pp, 3 x permuted-bucket gap)) |
  | WORKLOAD / RECENT_ROLE_FULL planted-future, same-game, schedule and heads-walk plants | **carried** |
  | RECENT_ROLE_FULL coverage and slice guards (SEQ failed the near-DNP slice, T442) | **carried** |
  | RECENT_ROLE_FULL control refit equals stored components | **carried** (reproduction gate) |
  | WORKLOAD D3 oracle-minutes pair (A3 vs A3 + W) | **dropped**: this rule claims no channel; T452's D3 numbers stay on record |
  | WORKLOAD cells P, M, reb, ast, fg3m | **dropped**: T451 passed and was followed up; T453 failed validly (not on the redo list) |
  | BEST_PRACTICES "a null gets a positive control and an arm-saw-it check" (2026-10-21) | **added** (below) |
  | BEST_PRACTICES per-player clustering (2026-10-20) | not applicable: the arm's effect is not constant within a player; date-clustered CI reported as a secondary |

## Hypothesis
H1: the 22 WORKLOAD columns that are not recent-role minutes (LOAD extras `g_3d, g_7d, min_l5_vs_base`; LOAD_TRACK `dist_3d, dist_7d, dpm_l3,
speed_l3, dpm_decline, speed_decline, track_n_l3`; SCHEDULE `three_in_four, five_in_seven, games_last10d, road_streak, road_trip4plus,
early_tip`; TRAVEL `miles_from_prev, miles_7d, tz_cross_hr, eastbound, alt_km, alt_delta_km`) lower the integer-support CRPS of points
beyond FULL. Null: no change. Every column is as-of (games dated strictly before the row's date; tonight's arena and published tip only),
built by `research/workload/asof.py` unchanged (13 unit tests), real tips only (`tip_source="real"`).

## Honest prior (read first)
* T452's own knockouts, already on record (reports/prereg_workload/results.json), say SCHEDULE and TRAVEL carry nothing: LOAD knocked out
  leaves +0.0017 / -0.0003; SCHEDULE alone +0.0006 / +0.0009; TRAVEL alone +0.0006 / -0.0007; tracking adds nothing (B minus no-tracking
  -0.0015 / +0.0018). TRACKING_SCREEN (T119-T123, T174-T175) and ADVANCED_RATES (T424-T439) were null.
* By subtraction of rounded ledger numbers already seen (not a paired CI, different `pi`): B (27 columns, A0 `pi`) minus HEADS (5 columns,
  A0 `pi`, T467) is about -0.0009 (2023) / -0.0006 (2024). The primary contrast is close to this quantity. **This rule is not blind**; it is
  a valid re-measurement of a quantity whose rough size is known.
* My priors: P(WF - FULL clears -0.005 with CI upper < 0 in both seasons) 0.03; P(full pass incl. guards) 0.02; expected WF - FULL in 2024
  -0.0006, 80% interval [-0.0025, +0.0012]. Descriptive T452 re-judgment: P(B - PL27 <= -0.003 in both seasons) 0.85 (the past-only placebo
  of the five columns carried almost no gain in T467: FULL - PL_FULL -0.0100 / -0.0086 against FULL - A0 -0.0084 / -0.0083).
* Why run it: it costs about 12 minutes of Mac CPU (unless the primary passes); its descriptive B - PL27 reading re-judges T452 with a valid
  placebo, and its primary says whether T452's -0.007 / -0.008 held anything beyond recent-role minutes. Recommendation: run it, record, close; do not build on it.

## Playbook check (docs/MODEL_PLAYBOOK.md)
* Class: gradient-boosted trees (LightGBM context-residual centre and scale heads) read through the lower-tail mixture (quantile layer).
* Information or capacity: claimed as information (as-of schedule, travel and tracking load, all knowable before tip). Every prior result says
  it adds nothing beyond recent minutes (T452 knockouts, TRACKING_SCREEN, ADVANCED_RATES): expected null. The GBM class check "does the arm only
  re-express the base?" is exactly the primary contrast (duplicated signal with the five recent-role columns).
* Data enough for the class: yes. 2022 warm-up plus month blocks, about 27.6k scored rows per season; MDE of an increment-over-heads contrast
  about 0.002-0.0026, well under the 0.005 floor. No learning curve needed for trees.
* Test validity: integer support for every arm, game-clustered CI, real tips, FULL as the comparator (fixes F10), past-only placebo (fixes D2),
  shared-permutation shuffle in both arms (fixes D1) judged on its own K = 50 null mean (fixes F9), frame identity pinned (below).
* Lineage: T452's B; guards carried or dropped in the table above. T467 showed the past-only placebo is roughly neutral (PL_FULL - A0 about
  +0.0016 / +0.0003, by subtraction), so the placebo guard is expected to be non-binding.
* Scored against: integer CRPS of the whole distribution; no line, no market, no bet claim.

## Data exposure (honest)
Seen before this draft: every T452/T453 arm (B, PL, knockouts, no-tracking, AL_*, A3, SHUF), T467-T469 and T473 (FULL, HEADS, PI, PL_FULL),
the shuffle-guard diagnostic, and the gate counts below. Not seen: any arm containing FULL's heads plus any of the 22 columns, any past-only
placebo of the 22 or of the 27, any shuffle of WF. Season 2025 not loaded. `nba.duckdb` opened read-only; `season <= 2024` in SQL and asserted.

## Arms (pts; heads walks on the production month blocks, 2022 = warm-up, production LightGBM, seed 0, no tuning)
| arm | stat heads | pi | role |
|---|---|---|---|
| A0 | stored components | stored | reproduction anchor |
| P0C | production features, refit | stored | control: must equal stored components to 1e-6 |
| **FULL** | production + 5 (refit, must equal `data/ops/recent_role_full_cache/heads_pts_R.npz` to 1e-6) | FULL `pi` (`pi_R.npz`, control refit must match) | **comparator** |
| **WF** | production + 5 + 22 | FULL `pi` | **the claim** |
| PL | production + 5 + past-only placebo of the 22 | FULL `pi` | placebo guard |
| KO_LOADX | production + 5 + SCHEDULE + TRAVEL | FULL `pi` | knockout (naming) |
| KO_SCHEDULE | production + 5 + LOADX + TRAVEL | FULL `pi` | knockout (naming) |
| KO_TRAVEL | production + 5 + LOADX + SCHEDULE | FULL `pi` | knockout (naming) |
| B | production + 27 (T452's B, refit; reported against the cached `pts__B.npz`) | stored A0 `pi` | descriptive: T452 re-judgment |
| PL27 | production + past-only placebo of the 27 | stored A0 `pi` | descriptive: T452 re-judgment |
| SHUF_FC(s), SHUF_WF(s), s = 0..49 | production + 5, and + 5 + 22, trained on the SAME permuted residuals | FULL `pi` | shuffle guard and its null (conditional, below) |

* The 22 columns enter the heads only (as T452's B); `pi` is FULL's in every FULL-based arm, so WF - FULL is a pure heads effect. Whether
  the 22 belong in `pi` is a different question and is not asked.
* Past-only placebo: one uniformly drawn STRICTLY EARLIER row of the same player-season (seed 0) supplies all 22 (or all 27) columns, so
  the columns keep their covariance; NaN for a player-season's first row (441 / 452 A0 rows, gate). Team columns follow the player's earlier
  team-game; that keeps real past values and breaks only the link to tonight.
* Shuffle: per block ONE permutation of the training residuals, `default_rng([s, block])`, identical for SHUF_FC(s) and SHUF_WF(s) (label
  hashes asserted equal), scored against the real outcomes; two-season walks as in Stage 1 of RECENT_ROLE_FULL. Unit tests: identical
  hashes, real labels do not fit, a planted-signal frame passes with the permutation and fails with `permute=False`.
* Metric: `to_integer_support` (`ceil(q - 0.5)`, floored at 0, running max) of the 199-point lower-tail mixture grid, CRPS on the integer grid
  for EVERY arm (T114-T118). Per-row paired differences; 95% percentile bootstrap clustered by `game_id` (2000 draws, seed 0, `WE.delta_stats`);
  two-sided clustered-bootstrap p; MDE = 1.96 x clustered SE printed beside every row. Secondary: the same CI clustered by game date.

## Pass rule (all must hold; 2023 selects, 2024 reports)
1. 2023: WF - FULL <= -0.005 and CI upper < 0.
2. 2024: WF - FULL <= -0.005, CI upper < 0, p <= 0.05. Family m = 1 (pts only, declared; BH is the identity, stated not hidden).
3. Placebo: WF - PL <= -0.003 in each season. (Non-binding in practice: T467's past-only placebo was about neutral against A0, +0.0016 /
   +0.0003, so rule 2 implies rule 3. It is kept for the record.)
4. Shuffle guard (run only if 1 and 2 hold; otherwise reported "not run, primary failed"): for each season, d(s) = SHUF_WF(s) - SHUF_FC(s)
   for s = 0..49; the guard FIRES if mean(d) + 2 x sd(d)/sqrt(50) < -0.001 in either season (a label-free gain larger than 0.001 at about
   97.7% one-sided confidence). False-fire rate if the null mean is -0.0003 (the diagnostic's): at most 2.1% per season and 4.1% for the pair,
   for any null sd; lower if the null mean is 0. Deviation from BEST_PRACTICES 2026-10-12, stated: the null is measured in this run on both
   seasons, not before the freeze; the guard tests the null's own mean, so it needs no prior sizing. The seed-0 value, its z against the null
   and the null sd are printed next to the bootstrap CI. A fire fails the rule.
5. Forecast calibration: randomised-PIT coverage at tau 0.10 / 0.20 / 0.80 / 0.90 (fixed row order, seed 0):
   |cov_WF - tau| - |cov_FULL - tau| <= 0.01 at every tau and season.
6. Slices (WF - FULL; n < 300 is reported and cannot fail): realised minutes < 5, bench (as-of `starter10 < 0.5`), playoffs (`004`), cold start
   (`n_prior < 10`), second night of a back-to-back, 3-in-4, road trip 4+. None worse than +0.01.
7. Gates, plants, missingness and reproduction (below) pass.
Naming: if one family (LOADX, SCHEDULE, TRAVEL) carries >= 80% of the gain (dropping it leaves <= 20%), the claim is named for that family and is
NOT a pass until the adversary clears it. A pass is a CANDIDATE: red-team review, then a drafted holdout row; nothing is wired or promoted.

## Positive control, arm-saw-it, smallest excluded effect (BEST_PRACTICES 2026-10-21)
* Positive control: FULL - A0 in this run must reproduce T467 (pts -0.0084 / -0.0083) within 1e-4 when the reproduction gates hold; after a
  data-drift refit (below) it must still be <= -0.005 with CI upper < 0 in both seasons. Otherwise stop: the harness cannot move.
* Arm saw it: the 22 columns' share of WF's LightGBM gain importance (centre and scale heads, summed over blocks) is reported; a share of 0
  means the arm never read them and the run is a pipeline fault, not a null. Join coverage of the 22 on A0 rows is checked in the gate.
* A null is written as "no effect larger than X", X = the 2024 CI lower bound of WF - FULL.

## Descriptive (cannot rescue or fail the rule)
Intervals here are per-number 95%, not multiplicity-adjusted.
* **T452 re-judged**: B - A0 (refit, against the cached T452 value) and B - PL27 per season, with game-clustered CI (B refit must equal
  `data/ops/workload_cache/pts__B.npz` to 1e-6, or the drift is reported beside the reading). Reading fixed now: if
  B - PL27 <= -0.003 in both seasons, T452's failed condition would have held under a valid placebo; the knockouts already name LOAD, so T452
  becomes "LOAD (recent-role minutes) carries a pts gain", i.e. FULL. Recorded as a T452 annotation, not a new pass.
* **Overlap with recent role**: share = 1 - (WF - FULL) / (B - A0), point and paired-bootstrap interval; per-row correlation of the two gains.
* Knockouts per family; LOADX - KO_LOADX style partials; WF - FULL date-clustered; bias and coverage tables.
* Decision score at DraftKings T-60 legs (`nba.markets.decision_scoring`, 2023-24), FULL vs WF. Descriptive only.

## Minimum-data gate (NN8): measured 2026-10-27 18:38 CDT (output mtime 18:38:08 CDT); re-measured 2026-10-27 19:04 CDT (output mtime
19:04:23 CDT, `nba.duckdb` mtime 18:56:44 CDT) with the frame hash: every count below identical; counts and coverage only
Data: `nba.duckdb` read-only (manifest `docs/data_manifests/latest.json` data_version **49a2fc3d3dff**, created 2026-10-27T12:09Z; the
committed manifest reads a55164376845), seasons 2022-2024. Script: scratch `gates/wpv2_gate.py` (loaders `four_factors_eval.load_study`,
`workload_eval.load_sources` / `attach_workload`, `placebo.permute_past_only`); no fit, no outcome compared across arms. **All PASS.**
**Frame sha256 (19:04 re-measure): `47b569fba20e58bcacfcf38148fc270d0b5e4cd6f235f7a8485c329bcd7a1614`** over the 55,320 A0 rows and 71 columns
(`stat_feature_names("pts")`, the 27 `wl_*` columns, `pts`, `minutes`). Recipe: rows sorted by (game_id, player_id); key columns hashed as
newline-joined text; other columns in name order, each cast to Float64 (null -> NaN), little-endian bytes, prefixed by the name and a NUL.
| check | 2023 | 2024 | limit | pass |
|---|---|---|---|---|
| A0 rows / games (stored components) | 27,619 / 1,318 | 27,701 / 1,320 | as T452 | yes |
| A0 rows found in the current feature frame | 27,619 | 27,701 | all | yes (55,320 / 55,320) |
| FULL cache rows identical to A0 rows (order) | yes | yes | identical | yes |
| 22 columns null/NaN on A0 rows | 298 rows (8 schedule/travel cols: team's first game of a season); 0 for the other 14 | 311 rows (same 8); 0 | NULL only by an as-of cause | yes (missingness audit by bucket runs in the harness gate) |
| past-only placebo NaN rows | 441 (1.6%) | 452 (1.6%) | NaN iff the row has no earlier played row in its player-season (asserted); equals production `n_season == 0` on A0 rows (reported) | yes: asserted on the whole played frame; 893 NaN rows = 893 `n_season == 0` rows, overlap 893 |
| slice n: minutes < 5 / bench / playoffs / cold start | 2,383 / 15,352 / 1,684 / 574 | 2,326 / 15,589 / 1,804 / 464 | >= 300 to count | yes |
| slice n: b2b second / 3-in-4 / road trip 4+ | 4,422 / 6,417 / 2,020 | 4,694 / 6,884 / 1,837 | >= 300 | yes |
| power: expected MDE of an increment-over-heads contrast | ~0.0026 (T452 B - NT half-width) | ~0.0020 | < floor 0.005 | yes |
* Column note: `wl_track_n_l3` has standard deviation 0.0 on A0 rows (constant; LightGBM cannot split on it). It stays in the list (the rule
  tests WORKLOAD's columns as frozen); effectively 21 informative columns. Pooled sd of the others is non-trivial (e.g. `miles_7d` 1,109,
  `min_l5_vs_base` 5.06, `speed_decline` 0.151).
* Data-capture audit (BEST_PRACTICES 2026-10-12): 2019-21 history is not used. It would add training rows only (power is set by the report
  season, 27.7k rows); tracking distance and speed for 2019-21 sit in data/history with unverified coverage. Not needed: the gate passes.

## Gates that run in the harness after the freeze (before any WF / PL / KO / B / PL27 arm)
* Reproduction: P0C equals the stored components (centre, scale, calibration) to 1e-6; FULL refit equals `heads_pts_R.npz`; FULL `pi` refit
  equals `pi_R.npz`. If a reproduction fails and the inputs differ from those stored with the cache (data drift: the harness compares row keys
  and the stored row inputs `row_y`, `row_m`, `row_minutes`, `row_min10`, `row_n_prior` of the components and cache), every arm, A0 and FULL
  included, is refit on the current data and compared within the run; the drift (max abs diff, rows) is reported. This is a data note, not a
  kill. A reproduction failure with identical stored inputs is a code defect and a kill.
* Frame identity: the gate stage writes the sha256 of the season <= 2024 feature frame (sorted keys plus every column an arm reads, recipe
  above) to gate.json; `arms` refuses if the frame it builds differs. The value recorded in this draft (47b569fb) is what the harness gate must
  reproduce; if it does not, stop and report before any arm (the data changed after the freeze). The manifest cited in the gate table
  (`docs/data_manifests/latest.json`, data_version 49a2fc3d3dff, uncommitted at drafting) is committed with the freeze commit.
* Missingness of the 22 by realised-minutes bucket (< 5, 5-10, 10-20, > 20): max gap <= max(2 pp, 3 x permuted-bucket gap).
* Plants on the real tables (WORKLOAD `planted_tests`: same-game, future, schedule; RECENT_ROLE_FULL `planted_heads` on WF's feature list):
  earlier rows bit-identical, controls change.
* Holdout log names no `WORKLOAD_PTS_V2` row; no 2025 loader exists.

## Kill criteria
* A gate, plant or the positive control fails: stop, fit nothing further, report; the fix is a new id.
* Any WF, PL, KO, B-refit, PL27 or shuffle arm scored before the freeze commit: the rule is void.
* Not a pass: closed. No other columns, windows, `pi` variants or learners; at most ONE follow-up (new id), and only after a pass.
* Shuffle guard fires: no pass; investigate under a new id, no rerun.

## Stage 2 (season 2025): declared, not built
Only if Stage 1 passes AND Devin confirms with a committed `HOLDOUT_ACCESS_LOG.md` row naming `WORKLOAD_PTS_V2`, the sha8, `PRE-REGISTERED`
and `DEVIN CONFIRMED`. WF has never been evaluated on 2025; FULL has (T473, T478), so the touch would re-read FULL's 2025 pts as a comparator.
Same rule, one run. Probability it is ever needed: about 0.02.

## What each choice protects against
* FULL as comparator: crediting the 22 columns for recent-role minutes the comparator lacked (F10, T508).
* Past-only placebo: capacity and player-level scale, without handing early rows later values (D2). Shared source row: keeps covariance.
* Shuffle in both arms with a K = 50 null: a pipeline-manufactured gain (D1), sized so it does not fire on honest noise (F9).
* Integer grid for every arm: the support artifact (T114-T118). Game-clustered CI: row-level anti-conservatism.
* Floor -0.005 and the stated prior: a clear but useless gain; naming the likely null in advance keeps it from being rescued.
* Knockout naming: one group carrying everything (`opp_adjusted_ridge`). Coverage and slice guards: a gain bought by mis-calibration or by
  hurting near-DNP rows (T442).
* Positive control and arm-saw-it: a null from a harness that could not move (T501).

## Outputs, harness and the one command (harness written after the freeze, reviewed by the adversary before the run)
`research/eval/workload_pts_v2_eval.py` (stages `gate`, `arms`, `score`, `shuffle`, `report`; refuses unless the sha matches the freeze commit;
heads walks via `recent_role_full_eval.collect_heads` with an explicit feature list; tests in `tests/research/workload_pts_v2/`). Outputs
`reports/prereg_workload_pts_v2/{gate,results}.json`, `reports/prereg_workload_pts_v2.md`; per-arm checkpoints in
`data/ops/workload_pts_v2_cache/` (git-ignored; a rerun resumes; one progress line per block).
Command (the maintainer, after the freeze commit and the harness review; the harness takes `data/ops/heavy.lock` itself with mkdir + pid,
retry every 120 s, removes only its own; do NOT start while the replay oracles hold the lock from 21:05 CDT):
`.venv/bin/python -W ignore -m research.eval.workload_pts_v2_eval run --sha <64 hex>`
Runtime on the Mac CPU, single process (estimates from the 2026-10-12 cache timestamps: a two-season heads walk about 30-35 s, a `pi` walk about
15 s): gate about 4 min; 9 heads walks + 1 `pi` walk about 6 min; scoring with 2000-draw bootstraps about 2 min: **about 12 minutes**. If rules 1-2
pass, the shuffle adds 100 heads walks, **about 55-60 minutes** more. Peak memory: one frame of about 84k rows x ~130 columns plus one block model.

## Open choices (defaults taken)
1. Columns into the heads only, not `pi`. Default: heads only (T452's construction; one question). Alternative: also `pi` (a second arm, m = 2).
2. Shuffle only if the primary passes. Default: conditional (saves about an hour on the expected null; a shuffle cannot rescue a null).
   Alternative: always run it (cost +55-60 min).
3. Family m = 1. Default: pts only, as the brief. Alternative: reopen reb/ast/fg3m (T453 failed validly; not recommended).
4. `wl_track_n_l3` is constant; default keep (frozen column list). Alternative: drop it (no effect on any fit).

=== RESULTS BELOW ===

(not run)
