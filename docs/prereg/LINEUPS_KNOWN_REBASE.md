# LINEUPS_KNOWN_REBASE: how much does the T-30 starting five add once the T-60 model knows recent minutes? (descriptive re-baseline of T085-T090, T096-T097)

Status: **FROZEN 2026-10-27 19:09 CDT (from `date`; Devin "redo all tests that didn't work", DECISIONS f5cc952; adversary pre-freeze AMEND applied, docs/reviews/redo_wave1_prefreeze_2026-10-27.md). Was: DRAFT (written 2026-10-27 18:41 CDT, from `date`; revised 2026-10-27 19:07 CDT (from `date`) after the adversary pre-freeze review
docs/reviews/redo_wave1_prefreeze_2026-10-27.md, edits L1-L12; L4 takes the cheaper default), modeler, branch `agent/modeler-lineups-rebase`
off f5cc952. Not frozen.**
Devin's request (DECISIONS 2026-10-27 18:32, wave 1, item 4); validity audit redo list rank 5 (docs/reviews/validity_audit_2026-10-12.md).
DESCRIPTIVE: this rule produces numbers and a fixed reading of them; it makes no pass/fail claim, promotes nothing and touches no holdout.
It is still committed before any arm is fitted (`prereg(LINEUPS_KNOWN_REBASE): <sha256>` of everything above `=== RESULTS BELOW ===`;
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINEUPS_KNOWN_REBASE.md | shasum -a 256`), so the reading cannot be chosen
after the numbers. Seasons 2023 and 2024 only; season 2025 is never loaded; `nba.duckdb` read-only; `season <= 2024` in SQL and asserted.

## The numbers it produces, and the decision each informs
| # | number (per stat or cell, 2023 and 2024, n and game-clustered 95% CI) | informs |
|---|---|---|
| N1 | **T30 - P0**: production heads with vs without the five `t30_*` columns, raw (non-mixture) readout, integer-support CRPS. This is the live arm `props_context_residual_t30` vs production, the construction FORWARD_PREREG_2026_27 section 2 scores | the expected size of the live T-30 shadow, superseding, in the result note only, the continuous, proxy-tip planning range in FORWARD_PREREG_2026_27 section 2 (that file, its direction check and its section 6 power table are not edited) with an integer-support, real-tip range [min(pess2, P2S), headline] |
| N2 | **T30F - FULL**: RECENT_ROLE_FULL heads with vs without the `t30_*` columns, FULL `pi`, lower-tail mixture, integer support | whether the starting five still adds once the T-60 model holds recent-role minutes (F10), i.e. whether a T-30 arm on top of FULL / AVG is worth building |
| N3 | **overlap** = 1 - (T30F - FULL) / (T30M - A0), T30M = production heads + `t30_*` through the A0 mixture (same heads walk as N1, mixture readout) | how much of the T-30 gain a T-60 recent-role model already captures (same mixture construction on both sides); reported only for stats whose T30M - A0 CI upper is < -0.002 (else 'denominator too small') |
| N4 | **P(play) RT - R**: RECENT_ROLE R (T458) with vs without the `t30_*` columns, log loss; all rows and bench-tonight rows | whether a T-30 P(play) refresh is worth a rule; R is the comparator the audit asked for (T089-T097's comparator lacked recent minutes, F10) |
Each number is reported four ways, because the box-score starter flag stands in for the announced five (F11, red team 2026-10-09):
**headline** (box-score flag), **excl-late** (rows outside team-games with a late-resolved usual-starter absence, a slice of the headline
arms; conditioned on post-tip outcomes by construction; a paired descriptive slice, not a bound and not a live estimate; no weight in the
reading rule), **pess2** (the red team's worst case, features rebuilt in train and test) and **P2S** (served-degraded: trained on headline
flags, scored on pess2 flags; the live construction, which FORWARD section 2.1 item 3 names: fit on box-score starters, served announced ones).
pess2 and P2S are worst cases for ONE channel: late-resolved absences of usual starters. Two channels are not modelled: non-usual
previous-game starters absent and DNP (up to 146 / 232 absences, 2023 / 2024, reviewer count, not split by T-60 OUT status), and
announcement changes after T-30 for players who played. The range is a planning range, not a bound.

## Honest prior (copied verbatim from docs/PREDICTIONS_LEDGER.md, row 2026-10-27 18:48, commit cb46562)
> N1 pts 2024 headline expected -0.038 (80% -0.046 to -0.030), pess2 -0.021 (80% -0.027 to -0.015); N2 pts headline -0.030 (80% -0.038 to
> -0.020); N3 pts overlap 0.20 (80% 0.05 to 0.40); P(N2 pess2 <= -0.005 with CI<0 both seasons) pts 0.85, reb 0.55, ast 0.10, fg3m 0.02; N4
> bench headline expected -0.010 (80% -0.016 to -0.004), P(N4 pess2 bench <= -0.005 with CI<0 both seasons) 0.25; P(shuffle validity check
> passes) 0.95

Restated after the review (new events created by L1 and L4; the 18:48 row is unchanged and still scored): N1 P2S pts 2024 expected -0.018
(80% -0.026 to -0.010); P(N2 pess2 AND P2S <= -0.005 with CI upper < 0 in both seasons) pts 0.75, reb 0.40, ast 0.05, fg3m 0.01; P(N4 pess2 AND
P2S bench <= -0.005 with CI upper < 0 in both seasons) 0.15; P(starter-shuffle flag raised, i.e. any cell with point <= -0.002 and CI upper < 0) 0.10.

## Playbook check (docs/MODEL_PLAYBOOK.md)
* Class: gradient-boosted trees (LightGBM context-residual heads, read raw and through the lower-tail mixture) for props; LightGBM binary for P(play).
* Information or capacity: information (tonight's announced five, public at T-30), measured through the box-score proxy (F11), so the headline
  is upward-biased; pess2 and P2S bracket one channel of that bias.
* Data enough for the class: yes. About 27.6k props and 33.1k P(play) rows per season; the T085 MDE (pts 0.0065) is far below the expected effects.
* Test validity: integer support (the live support), real tips (F1), FULL and R comparators (F10), the served-degraded construction (L1), frame
  identity pinned, `max_season=2024` on every tip read. The starter-flag shuffle is a harness check, not a proxy check (below).
* Lineage: T085-T090, T096-T097, red team 2026-10-09, validity audit row T085-T097; guards carried or dropped in the table below.
* Scored against: integer CRPS and log loss of the whole distribution, not a line. FORWARD section 2 scores the same N1 construction live.

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
  | starter-flag shuffle within team-game, features rebuilt (red team attack 3) | **carried** as a reported flag, not a stop (below) |
  | planted future on the t30 builder (`test_future_games_never_change_earlier_t30_features` + real-table plant) | **carried** |
  | missingness of `t30_*` by realised-minutes bucket (red team 3b) | **carried**, reported with a flag |
  | pess2 worst case (red team attack 4) | **carried** as an arm, plus its served-degraded form P2S (adversary 2026-10-27 L1) |
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
| T30_P2S, T30F_P2S | trained as T30 / T30F (headline `t30_*` on training rows); each block's TEST rows carry pess2 `t30_*` | as T30 / T30F | live-construction worst case (train on box flags, serve degraded) |
| T30SH | as T30, starter flags permuted within each (game, team) keeping five, features rebuilt in train and test | raw | harness flag (reported) |
P(play), on the RECENT_ROLE cell-P rows (rostered, `n_played_prior >= 5`, not OUT on the real-tip T-60 report), RECENT_ROLE's walk
(`recent_role_eval.walk`, LightGBM binary, month blocks), label `played = minutes > 0`:
| arm | features | role |
|---|---|---|
| R | RECENT_ROLE R (stored `data/ops/recent_role_cache/scores.npz` `p_R`; control refit must match to 1e-6) | comparator |
| RT | R + the 5 `t30_*` columns | N4 |
| RT_P2 | R + pess2 `t30_*` | worst case |
| RT_P2S | R + headline `t30_*` in training, pess2 `t30_*` on test rows | live-construction worst case |
| RT_SH | R + shuffled-flag `t30_*` | harness flag (reported) |
* The `t30_*` columns enter the stat heads only; `pi` is not refit with them (the live arm does not refit `pi`; whether the five helps the
  short-minutes classifier is a different question, not asked).
* **Late-resolved** (fixed now, from the gate): a usual starter (started >= 50% of the team's previous <= 10 games of the season, >= 3 games)
  absent from tonight's box-score five who was NOT OUT on the real-tip T-60 report and did NOT play: either dressed with 0 minutes, or with no
  box row AND a `player_availability` row for this game dated before tip (`as_of < tip_et`, real tips; still on the roster as-of; this drops
  traded or waived players). The team-game is late-resolved. Re-measured in the gate: 536 / 564 absences (2023 / 2024; 2022: 572), 1,672 over
  2022-24 against the red team's 1,519.
* **pess2** (red team attack 4, fixed now): every late-resolved usual starter is flagged an announced starter (a DNP starter row is added for
  one without a box row) and, for each, the box-score starter of that team-game with the fewest prior starts this season (ties: lowest
  player_id) is flagged bench-announced; `t30_*` rebuilt; applied to training and test rows (P2S: test rows only). Added DNP starter rows
  feed the `t30_*` team features only. The scored row set of every arm equals the A0 (props) or R (P(play)) row set, asserted by key hash.
* Metric: `to_integer_support` (`ceil(q - 0.5)`, floored at 0, running max) of the 199-point grid, CRPS on the integer grid for every props arm;
  also the continuous 199-grid CRPS of T30 - P0 as a bridge to T085 only. P(play): log loss. Per-row paired differences; 95% percentile bootstrap
  clustered by `game_id` (2000 draws, seed 0, `WE.delta_stats`); overlap share CI from the same paired draws. Secondary: date-clustered CI.
  No multiplicity correction: nothing here is a claim; every interval is a per-number 95% interval and is labelled so.
* Slices (reported): status changed (`t30_start_delta != 0`), starter tonight, bench tonight, realised minutes < 5, playoffs, cold start
  (`n_prior < 10`), late-resolved team-games. Coverage at tau 0.10/0.20/0.80/0.90 and bias per arm.

## Validity checks (checks 1 and 3 stop: if one fails, the numbers are NOT reported as a re-baseline; checks 2, 4, 5 are reported flags)
1. Reproduction: P0C equals stored components, FULL refit equals the cache, R refit equals stored `p_R` (1e-6). If a reproduction fails and the
   inputs differ from those stored with the cache (data drift: row keys and stored row inputs such as `row_y`, `row_m`, `row_minutes`,
   `row_n_prior`, and for R the cached `y`), every arm is refit on the current data and compared within the run; the drift is reported (a data
   note). A failure with identical stored inputs is a code defect and a stop.
   Frame identity: the gate stage writes the sha256 of the season <= 2024 frames (props: sorted keys plus every column an arm reads; P(play):
   the same for the R frame) to gate.json; `arms` refuses if a frame it builds differs. The props-frame and P(play)-key hashes recorded in the
   gate section below must be reproduced by the harness gate, or it stops before any arm (the data changed after the freeze). The manifest
   cited in the gate table is committed with the freeze commit.
2. Starter-flag shuffle (Devin's default taken, adversary L4): a single-seed starter-flag shuffle (T30SH - P0, RT_SH - R) is reported, not
   judged (its null sd is unmeasured; BEST_PRACTICES 2026-10-12). It is flagged beside the headline numbers if a cell shows point <= -0.002
   with CI upper < 0. It tests the harness, not the proxy: the shuffle destroys starter identity, which is where the proxy bias lives. Plants
   (check 3) and reproduction (check 1) remain the stops.
3. Plants: on the real tables, editing every game dated after a cutoff (flags, minutes, adding a fake future game) leaves the `t30_*` of all
   earlier rows bit-identical; flipping tonight's flags changes tonight's rows (non-inert control).
4. Bridge: continuous T30 - P0, pts 2024, is reported next to T085's -0.0412; a gap > 0.008 is explained in the result note (rows 27,583 vs
   27,701, real tips, data version) before N1 is used.
5. Missingness of each `t30_*` column by realised-minutes bucket: gap reported against max(2 pp, 3 x permuted-bucket gap); over the limit is a
   flag printed beside every number, not a stop (the NULLs have an as-of cause: a team's or player's first games of a season).

## Reading rule (fixed before any number exists; recommendations to Devin, not decisions)
* **N1 (live planning).** The planning range for the live T30 arm becomes [min(pess2, P2S), headline] of N1 in 2024 per stat (superseding
  FORWARD's range in the result note only). The harness also prints the probability that the live T30 pts and reb cells clear -0.005 with CI
  upper < 0 at their first efficacy look (L60, 5,000 paired player-games) at the low end of the range, using FORWARD section 6's method
  (date-clustered, logged-share inflation 1/sqrt(0.85)) so it is comparable. If N1's 2024 headline is below a frozen FORWARD direction-check
  threshold (pts -0.041, reb -0.018, ast -0.0089), the note flags that the frozen tripwire may fire on honest data (the frozen rule is not
  edited; Devin decides). If the low end for a stat is above -0.005, the note says the live cell is expected to miss its floor.
* **N2 / N3 (beyond recent role).** Per stat:
  - N2 both pess2 and P2S <= -0.005 with CI upper < 0 in BOTH seasons: "the starting five adds beyond recent role"; recommend a T30-on-FULL (or on AVG)
    shadow arm under a new rule id for that stat.
  - N2 headline 2024 point > -0.005: "the offline T-30 gain over FULL is below the floor"; recommend no T30-on-FULL arm for that stat; the
    existing T30 shadow keeps running for its timing measurement (FORWARD section 2.1).
  - Otherwise: "conditional; the live T30 shadow decides".
* **N4 (P(play)).** RT - R on bench-tonight rows, both pess2 and P2S <= -0.005 with CI upper < 0 in BOTH seasons: recommend drafting a T-30 P(play)
  refresh rule (new id, live only). Otherwise no T-30 P(play) work. The all-rows number is reported but carries no weight (a box-score starter
  always played, so it is mostly mechanical).
* Nothing here changes the frozen FORWARD_PREREG_2026_27 rule, production, or a promotion. Ledger rows are labelled DESCRIPTIVE.

## Minimum-data gate (NN8): measured 2026-10-27 18:39 CDT (output mtime 18:39:38 CDT); re-measured 2026-10-27 19:05 CDT (output mtime
19:05:59 CDT, `nba.duckdb` mtime 18:56:44 CDT) under the L3 late-resolved definition, with frame hashes; counts and coverage only
Data: `nba.duckdb` read-only (manifest `docs/data_manifests/latest.json` data_version **49a2fc3d3dff**, created 2026-10-27T12:09Z; committed
manifest a55164376845), seasons 2022-2024; scratch script `gates/lkr_gate.py` (`load_inputs`, `flagged_from_availability` with real tips,
`build_lineup_features`, `attach_real_tips(..., max_season=2024)`; 0 availability rows without a real tip); no fit, no score. Realised minutes
enter only the late-resolved definition and the minutes < 5 slice count. **All PASS.** Counts below are the 19:05 re-measure; only the
late-resolved rows changed (L3).
**Props frame sha256: `9359bcf4405cee2b236a7725c74d738ac8e59e8793795906a13c9c9083bc66d1`** (55,320 A0 rows; 81 columns: the union of
`stat_feature_names(stat, lineups_known=True)` over the four stats, the 5 recent-role columns, `pts, reb, ast, fg3m, minutes`).
**P(play) key + `t30_*` sha256: `9a38c78a85ffcc678e9e91faafcd3a07b7195072a0303f544ec932b33ef91108`** (66,220 R rows, the 5 `t30_*` columns; the
harness gate adds the full R-frame hash). Recipe: rows sorted by (game_id, player_id); key columns hashed as newline-joined text; other columns
in name order, each cast to Float64 (null -> NaN), little-endian bytes, prefixed by the name and a NUL.
| check | 2023 | 2024 | limit | pass |
|---|---|---|---|---|
| props rows (A0) / games | 27,619 / 1,318 | 27,701 / 1,320 | as stored components | yes |
| `t30_starter` attached | 27,619 (0 null) | 27,701 (0 null) | all | yes |
| other `t30_*` nulls: n_changed / n_usual_absent and vac_starter_min / start_delta | 298 / 889 / 344 | 311 / 902 / 369 | as-of cause only | yes (bucket audit in the harness) |
| starter tonight / bench tonight | 13,139 / 14,480 | 13,162 / 14,539 | >= 300 | yes |
| status changed (`start_delta != 0`) | 1,849 | 2,422 | >= 300 | yes |
| rows in late-resolved team-games / outside | 4,625 / 22,994 | 4,940 / 22,761 | >= 300 each | yes |
| late-resolved team-games | 461 | 487 | (2022: 483) | n/a |
| usual-starter absences: OUT at T-60 / benched but played / late-resolved (dressed DNP + no box row with a pre-tip availability row) / excluded (no box row, no pre-tip availability row) | 1,076 / 701 / 536 (227 + 309) / 86 | 1,297 / 811 / 564 (271 + 293) / 70 | (2022: 1,165 / 779 / 572 (182 + 390) / 93) | n/a |
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
  serve the bridge check only; N1-N4 need new heads walks (about 33 minutes of CPU, below).

## Outputs, harness and the one command (harness written after the freeze; adversary review before the run)
`research/eval/lineups_known_rebase.py` (stages `gate`, `arms`, `score`, `report`; refuses unless the sha matches the freeze commit; heads via
`recent_role_full_eval.collect_heads` with explicit feature lists, P(play) via `recent_role_eval.walk`; tests in
`tests/research/lineups_known_rebase/`: pess2 and P2S construction on a toy frame, scored row sets equal A0 / R by key hash, shuffle keeps five
per team-game, plants non-inert, readouts). Real tips via `flagged_from_availability(..., max_season=2024)` and `attach_real_tips(...,
max_season=2024)`; a test asserts no schedule file of season > 2024 is opened.
Outputs `reports/lineups_known_rebase/{gate,results}.json`, `reports/lineups_known_rebase.md`; checkpoints `data/ops/lineups_known_rebase_cache/`.
Command (the maintainer, after the freeze commit and review; the harness takes `data/ops/heavy.lock` with mkdir + pid, retry 120 s, removes only
its own; NOT while the replay oracles hold the lock from 21:05 CDT):
`.venv/bin/python -W ignore -m research.eval.lineups_known_rebase run --sha <64 hex>`
Runtime on the Mac CPU, single process (from 2026-10-12 cache timestamps: two-season heads walk about 25-35 s, a P(play) walk about 60-70 s):
gate and three `t30_*` builds (real, pess2, shuffled) about 4 min; 36 heads walks (9 per stat: P0C, FULL control, T30, T30F, T30_P2, T30F_P2,
T30_P2S, T30F_P2S, T30SH) about 18 min; 5 P(play) walks about 6 min; scoring about 5 min: **about 33 minutes**.

## Open choices (defaults taken)
1. P(play) comparator: default RECENT_ROLE R (T458, 2025-validated, stored arrays, the live `p_play_r` family). Alternative: PPLAY_MINUTES_VALID
   R19 (T482, stronger, adversary WOUNDED); +2 walks.
2. All five `t30_*` columns, not the starter flag alone. Default: all five (the live arm's set and T089's). Alternative: `t30_starter` only, or both.
3. Late-resolved definition: default the one above (dressed DNP, or no box row with a pre-tip availability row; L3). Alternative: the red
   team's narrower one (absences with a T-60 report row only).
5. Starter-flag shuffle: default a reported single-seed flag (Devin's default per L4). Alternative: K = 10 seeds per cell judged as a stop
   (mean <= -0.002 AND mean + 3.69 x sd/sqrt(10) < 0), about +28 minutes.
4. `t30_*` into the heads only, not `pi`. Default: heads only (live construction). Alternative: also `pi` (a separate question; new id).

## Amendment 1 (2026-10-27 19:51 CDT, from `date`; DECISIONS 2026-10-27 19:50, option (a)). Before any arm was fitted
* What changed: the two frame pins and the plant comparison only. The props pin becomes
  **`231e864858a8b067ecf9c462d104d5a20246cd29bfb5ff88271376e41c92b24a`** and the P(play) key + `t30_*` pin becomes
  **`372dacf34f275f6743d4a79d717de582d86dafcdfddfeda587c0552ac4c14ef1`**, both computed with `t30_vac_starter_min` rounded to 1e-9
  (`round(9)`) before hashing; the recipe is otherwise unchanged (same rows, columns, sort, Float64 cast, NaN, byte layout). The same
  rounding applies to that column in the full R-frame hash and in check 3's plant comparison ("bit-identical" is judged on the rounded
  column; the other four `t30_*` columns stay bit-identical as written). These supersede the two pins in the gate section above
  (9359bcf4 / 9a38c78a); every other line of the rule, including the gate counts (late-resolved 572 / 536 / 564), is unchanged.
* Why: `t30_vac_starter_min` is built by a polars `group_by` sum in `nba/props/lineup_features.py` whose summation order varies run to run
  (max |diff| 2.8e-14; six rebuilds gave six different raw hashes, including the gate's own recipe re-run). The raw pins could not be
  reproduced by any rebuild, so check 1 could never pass. With the column rounded to 1e-9 the hash is stable across rebuilds, and the
  harness frame equals the gate's construction on the other 80 columns (measured 2026-10-27 19:4x CDT, read-only, no fit). The fitted
  features themselves are not rounded; only the identity check is.
* Exposure: no arm of this rule has been fitted or scored; nothing but frame hashes, row counts and the late-resolved counts was seen.

=== RESULTS BELOW ===

(not run)
