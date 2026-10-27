# LINEUPS_KNOWN_REBASE: how much does the T-30 starting five add once the T-60 model knows recent minutes? (descriptive re-baseline of T085-T090, T096-T097)

Status: **DRAFT (written 2026-10-27 18:41 CDT, from `date`), modeler, branch `agent/modeler-lineups-rebase` off f5cc952. Not frozen.**
Devin's request (DECISIONS 2026-10-27 18:32, wave 1, item 4); validity audit redo list rank 5 (docs/reviews/validity_audit_2026-10-12.md).
DESCRIPTIVE: this rule produces numbers and a fixed reading of them; it makes no pass/fail claim, promotes nothing and touches no holdout.
It is still committed before any arm is fitted (`prereg(LINEUPS_KNOWN_REBASE): <sha256>` of everything above `=== RESULTS BELOW ===`;
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINEUPS_KNOWN_REBASE.md | shasum -a 256`), so the reading cannot be chosen
after the numbers. Seasons 2023 and 2024 only; season 2025 is never loaded; `nba.duckdb` read-only; `season <= 2024` in SQL and asserted.

## The numbers it produces, and the decision each informs
| # | number (per stat or cell, 2023 and 2024, n and game-clustered 95% CI) | informs |
|---|---|---|
| N1 | **T30 - P0**: production heads with vs without the five `t30_*` columns, raw (non-mixture) readout, integer-support CRPS. This is the live arm `props_context_residual_t30` vs production, the construction FORWARD_PREREG_2026_27 section 2 scores | the expected size of the live T-30 shadow, replacing the continuous, proxy-tip planning range "pts -0.023..-0.041" with an integer-support, real-tip range [pess2, headline] |
| N2 | **T30F - FULL**: RECENT_ROLE_FULL heads with vs without the `t30_*` columns, FULL `pi`, lower-tail mixture, integer support | whether the starting five still adds once the T-60 model holds recent-role minutes (F10), i.e. whether a T-30 arm on top of FULL / AVG is worth building |
| N3 | **overlap** = 1 - (T30F - FULL) / (T30M - A0), T30M = production heads + `t30_*` through the A0 mixture (same heads walk as N1, mixture readout) | how much of the T-30 gain a T-60 recent-role model already captures (same mixture construction on both sides) |
| N4 | **P(play) RT - R**: RECENT_ROLE R (T458) with vs without the `t30_*` columns, log loss; all rows and bench-tonight rows | whether a T-30 P(play) refresh is worth a rule; R is the comparator the audit asked for (T089-T097's comparator lacked recent minutes, F10) |
Each number is reported three ways, because the box-score starter flag stands in for the announced five (F11, red team 2026-10-09):
**headline** (box-score flag), **excl-late** (rows outside team-games with a late-resolved usual-starter absence, a slice of the headline
arms), and **pess2** (the red team's worst case, features rebuilt in train and test). The truth lies between pess2 and headline.

## Lineage and what is wrong with the old numbers
* T085-T088 (LINEUPS_KNOWN, 2026-10-08): pts -0.0412 [-0.0475, -0.0346], reb -0.0182, ast -0.0089, fg3m -0.0041 (2024, n 27,583), B - A where
  A = production T-60 heads. Continuous 199-quantile CRPS for both arms (same support, so not the T114 artifact, but not the live support);
  proxy 19:00 tips (before the 2026-10-09 real-tip fix, F1, shared by both arms); production heads without the lower-tail mixture.
* T089-T090 / T096-T097: P(play) T-30 logistic vs a recalibrated production P(play); the fair-baseline redo (T096-T097) still had no recent
  minutes (F10): bench-tonight -0.0176 [-0.021, -0.014].
* Red team (docs/reviews/redteam_lineups_known_2026-10-09.md): WOUNDED. No leak beyond the flag; the flag is optimistic because the box
  score has resolved every game-time decision; pess2 pts -0.0230 [-0.0278, -0.0183], reb -0.0087, ast -0.0050, fg3m -0.0024 (2024).
* Validity audit: UNCERTAIN in size (F11 proxy, F10 comparator, F1 shared); direction valid.
* Guards from ancestors, carried or dropped:
  | guard | status |
  |---|---|
  | starter-flag shuffle within team-game, features rebuilt (red team attack 3) | **carried** as a validity check (below) |
  | planted future on the t30 builder (`test_future_games_never_change_earlier_t30_features` + real-table plant) | **carried** |
  | missingness of `t30_*` by realised-minutes bucket (red team 3b) | **carried**, reported with a flag |
  | pess2 worst case (red team attack 4) | **carried** as an arm |
  | synthetic 5% random scratch sensitivity (LINEUPS_KNOWN) | **dropped**: the red team showed it does not model the structured channel; pess2 replaces it |
  | coverage / bias guards of T085 | **carried** as reported columns (nothing passes or fails here) |
  | lag arm (previous-game five) | **dropped**: FULL now carries the T-60-knowable recent role; N2 is the sharper version of that question |

## Arms (heads walks on the production month blocks, 2022 = warm-up, production LightGBM, seed 0, no tuning; four stats)
| arm | stat heads | readout | role |
|---|---|---|---|
| P0 / A0 | stored components (`reports/lower_tail/components_*.npz`) | raw (`kind="off"`) / mixture with stored `pi` | comparators (P0C control refit must equal them to 1e-6) |
| FULL | production + 5 recent-role columns (cache `data/ops/recent_role_full_cache/heads_<stat>_R.npz`; control refit must match) | mixture, FULL `pi` (`pi_R.npz`) | comparator for N2 |
| T30 / T30M | `stat_feature_names(stat, lineups_known=True)` = production + `t30_starter, t30_n_changed, t30_n_usual_absent, t30_vac_starter_min, t30_start_delta` | raw (N1) / mixture with stored A0 `pi` (N3) | the live construction |
| T30F | production + 5 recent-role + 5 `t30_*` | mixture, FULL `pi` | N2 |
| T30_P2, T30F_P2 | as T30 and T30F, `t30_*` rebuilt from pess2 starter flags | as above | worst case |
| T30SH | as T30, starter flags permuted within each (game, team) keeping five, features rebuilt in train and test | raw | validity check |
P(play), on the RECENT_ROLE cell-P rows (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report), RECENT_ROLE's walk
(`recent_role_eval.walk`, LightGBM binary, month blocks), label `played = minutes > 0`:
| arm | features | role |
|---|---|---|
| R | RECENT_ROLE R (stored `data/ops/recent_role_cache/scores.npz` `p_R`; control refit must match to 1e-6) | comparator |
| RT | R + the 5 `t30_*` columns | N4 |
| RT_P2 | R + pess2 `t30_*` | worst case |
| RT_SH | R + shuffled-flag `t30_*` | validity check |
* The `t30_*` columns enter the stat heads only; `pi` is not refit with them (the live arm does not refit `pi`; whether the five helps the
  short-minutes classifier is a different question, not asked).
* **Late-resolved** (fixed now, from the gate): a usual starter (started >= 50% of the team's previous <= 10 games of the season, >= 3 games)
  absent from tonight's box-score five who was NOT OUT on the real-tip T-60 report and did NOT play (no box row, or 0 minutes). The team-game
  is late-resolved. This is broader than the red team's 1,519 (it also counts absences with no report row at all).
* **pess2** (red team attack 4, fixed now): every late-resolved usual starter is flagged an announced starter (a DNP starter row is added for
  one without a box row) and, for each, the box-score starter of that team-game with the fewest prior starts this season (ties: lowest
  player_id) is flagged bench-announced; `t30_*` rebuilt; applied to training and test rows.
* Metric: `to_integer_support` (`ceil(q - 0.5)`, floored at 0, running max) of the 199-point grid, CRPS on the integer grid for every props arm;
  also the continuous 199-grid CRPS of T30 - P0 as a bridge to T085 only. P(play): log loss. Per-row paired differences; 95% percentile bootstrap
  clustered by `game_id` (2000 draws, seed 0, `WE.delta_stats`); overlap share CI from the same paired draws. Secondary: date-clustered CI.
  No multiplicity correction: nothing here is a claim; every interval is a per-number 95% interval and is labelled so.
* Slices (reported): status changed (`t30_start_delta != 0`), starter tonight, bench tonight, realised minutes < 5, playoffs, cold start
  (`n_prior < 10`), late-resolved team-games. Coverage at tau 0.10/0.20/0.80/0.90 and bias per arm.

## Validity checks (if one fails, the numbers are NOT reported as a re-baseline; stop and report the failure)
1. Reproduction: P0C equals stored components, FULL refit equals the cache, R refit equals stored `p_R` (1e-6). If the data changed since the
   caches (not a code defect), every arm is refit on the current data and compared within the run; the drift is reported (a data note).
2. Starter-flag shuffle: T30SH - P0 and RT_SH - R must not show a gain: fails if the point is <= -0.002 (props, any stat and season) or <= -0.002
   log loss (P(play)) with CI upper < 0. A worse (positive) value is not a failure (red team: fg3m 2023 +0.0007).
3. Plants: on the real tables, editing every game dated after a cutoff (flags, minutes, adding a fake future game) leaves the `t30_*` of all
   earlier rows bit-identical; flipping tonight's flags changes tonight's rows (non-inert control).
4. Bridge: continuous T30 - P0, pts 2024, is reported next to T085's -0.0412; a gap > 0.008 is explained in the result note (rows 27,583 vs
   27,701, real tips, data version) before N1 is used.
5. Missingness of each `t30_*` column by realised-minutes bucket: gap reported against max(2 pp, 3 x permuted-bucket gap); over the limit is a
   flag printed beside every number, not a stop (the NULLs have an as-of cause: a team's or player's first games of a season).

## Reading rule (fixed before any number exists; recommendations to Devin, not decisions)
* **N1 (live planning).** The planning range for the live T30 arm becomes [pess2, headline] of N1 in 2024 per stat. The harness also prints
  the probability that the live T30 pts and reb cells clear -0.005 with CI upper < 0 at their first efficacy look (L60, 5,000 paired
  player-games), by a normal approximation with the offline game-clustered SE scaled by sqrt(27,701 / 5,000) at the pess2 effect. If N1's
  2024 headline for pts is below -0.041 (the frozen FORWARD direction check), the note flags that the frozen tripwire may fire on honest data
  (the frozen rule is not edited; Devin decides). If N1 pess2 for a stat is above -0.005, the note says the live cell is expected to miss its floor.
* **N2 / N3 (beyond recent role).** Per stat:
  - N2 pess2 <= -0.005 with CI upper < 0 in BOTH seasons: "the starting five adds beyond recent role"; recommend a T30-on-FULL (or on AVG)
    shadow arm under a new rule id for that stat.
  - N2 headline 2024 point > -0.005: "the offline T-30 gain over FULL is below the floor"; recommend no T30-on-FULL arm for that stat; the
    existing T30 shadow keeps running for its timing measurement (FORWARD section 2.1).
  - Otherwise: "conditional; the live T30 shadow decides".
* **N4 (P(play)).** RT - R on bench-tonight rows, pess2, <= -0.005 with CI upper < 0 in BOTH seasons: recommend drafting a T-30 P(play)
  refresh rule (new id, live only). Otherwise no T-30 P(play) work. The all-rows number is reported but carries no weight (a box-score starter
  always played, so it is mostly mechanical).
* Nothing here changes the frozen FORWARD_PREREG_2026_27 rule, production, or a promotion. Ledger rows are labelled DESCRIPTIVE.

## Minimum-data gate (NN8): measured 2026-10-27 18:39 CDT (output mtime 18:39:38 CDT), counts and coverage only
Data: `nba.duckdb` read-only (manifest `docs/data_manifests/latest.json` data_version **49a2fc3d3dff**, created 2026-10-27T12:09Z; committed
manifest a55164376845), seasons 2022-2024; scratch script `gates/lkr_gate.py` (`load_inputs`, `flagged_from_availability` with real tips,
`build_lineup_features`); no fit, no score. Realised minutes enter only the late-resolved definition and the minutes < 5 slice count. **All PASS.**
| check | 2023 | 2024 | limit | pass |
|---|---|---|---|---|
| props rows (A0) / games | 27,619 / 1,318 | 27,701 / 1,320 | as stored components | yes |
| `t30_starter` attached | 27,619 (0 null) | 27,701 (0 null) | all | yes |
| other `t30_*` nulls: n_changed / n_usual_absent and vac_starter_min / start_delta | 298 / 889 / 344 | 311 / 902 / 369 | as-of cause only | yes (bucket audit in the harness) |
| starter tonight / bench tonight | 13,139 / 14,480 | 13,162 / 14,539 | >= 300 | yes |
| status changed (`start_delta != 0`) | 1,849 | 2,422 | >= 300 | yes |
| rows in late-resolved team-games / outside | 5,377 / 22,242 | 5,473 / 22,228 | >= 300 each | yes |
| late-resolved team-games | 531 | 539 | (2022: 549) | n/a |
| usual-starter absences: OUT at T-60 / benched but played / late-resolved | 1,076 / 701 / 622 | 1,297 / 811 / 634 | (2022: 1,165 / 779 / 665) | n/a |
| minutes < 5 / playoffs / cold start | 2,383 / 1,684 / 574 | 2,326 / 1,804 / 464 | >= 300 | yes |
| P(play) rows (R) / games | 33,093 / 1,318 | 33,127 / 1,320 | as RECENT_ROLE | yes |
| P(play) rows with a box row (so `t30_*` defined) | 33,093 (100%) | 33,127 (100%) | all | yes |
| P(play) starter tonight / bench tonight | 13,138 / 19,955 | 13,162 / 19,965 | >= 300 | yes |
| power (MDE from T085 CI half-widths, pts / reb / ast / fg3m) | ~0.0065 / 0.0028 / 0.0018 / 0.0011 | same | descriptive | n/a |
* The P(play) universe holds only players with a box row; rostered players who were inactive with no box row are not in it (T484 wound 1),
  so the "listed inactive" part of a T-30 announcement is not measured here, as in T090.
* Data-capture audit: the announced five is not stored for any past season (the live collector started 2026-10-09); 2019-21 history has the
  same box-score proxy and would add training rows only. No source we could add gives historical announcements; the live T30 shadow is the fix.
* Deviation from the redo-list wording ("on stored 2023/24 arrays"): the stored T085 rows (`reports/lineups_known/rows_*.parquet`) hold per-row
  continuous CRPS, mean, q10 and q90 only, not the 199-point grid, so they cannot be put on the integer support or through the mixture. They
  serve the bridge check only; N1-N4 need new heads walks (about 25-30 minutes of CPU, below).

## Outputs, harness and the one command (harness written after the freeze; adversary review before the run)
`research/eval/lineups_known_rebase.py` (stages `gate`, `arms`, `score`, `report`; refuses unless the sha matches the freeze commit; heads via
`recent_role_full_eval.collect_heads` with explicit feature lists, P(play) via `recent_role_eval.walk`; tests in
`tests/research/lineups_known_rebase/`: pess2 construction on a toy frame, shuffle keeps five per team-game, plants non-inert, readouts).
Outputs `reports/lineups_known_rebase/{gate,results}.json`, `reports/lineups_known_rebase.md`; checkpoints `data/ops/lineups_known_rebase_cache/`.
Command (the maintainer, after the freeze commit and review; the harness takes `data/ops/heavy.lock` with mkdir + pid, retry 120 s, removes only
its own; NOT while the replay oracles hold the lock from 21:05 CDT):
`.venv/bin/python -W ignore -m research.eval.lineups_known_rebase run --sha <64 hex>`
Runtime on the Mac CPU, single process (from 2026-10-12 cache timestamps: two-season heads walk about 25-35 s, a P(play) walk about 60-70 s):
gate and three `t30_*` builds (real, pess2, shuffled) about 4 min; 28 heads walks (7 per stat: P0C, FULL control, T30, T30F, T30_P2, T30F_P2,
T30SH) about 14 min; 4 P(play) walks about 5 min; scoring about 4 min: **about 27 minutes**.

## Open choices (defaults taken)
1. P(play) comparator: default RECENT_ROLE R (T458, 2025-validated, stored arrays, the live `p_play_r` family). Alternative: PPLAY_MINUTES_VALID
   R19 (T482, stronger, adversary WOUNDED); +2 walks.
2. All five `t30_*` columns, not the starter flag alone. Default: all five (the live arm's set and T089's). Alternative: `t30_starter` only, or both.
3. Late-resolved definition: default the broad one above (includes absences with no report row). Alternative: the red team's narrower one
   (absences with a T-60 report row only).
4. `t30_*` into the heads only, not `pi`. Default: heads only (live construction). Alternative: also `pi` (a separate question; new id).

=== RESULTS BELOW ===

(not run)
