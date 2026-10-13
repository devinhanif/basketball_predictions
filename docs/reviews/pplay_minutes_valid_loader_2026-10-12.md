# PPLAY_MINUTES_VALID loader review (adversary, pre-run), 2026-10-12

Verdict: APPROVED WITH FIXES
Reviewed-commit: ce6afc2321baf3ff1cd61045e14c8a6921617f90
Rule: PPLAY_MINUTES_VALID 91d82de7

This is the pre-run review the frozen rule requires ("Fixed before the freeze"). Under the harness's own gate this file does **not**
open `run`, because its verdict is not exactly `APPROVED`. A fix-check review of the fix commit is needed before any run.

Scope: the harness at ce6afc2 (`research/eval/pplay_minutes_valid.py`, `pplay_minutes_valid_core.py`, `pplay_minutes_valid_rule.py`,
`tests/research/pplay_minutes_valid/`), checked against the rule text above the marker (sha256 91d82de7..., freeze commit 8e15012).
I built the real union frames read-only and ran every frame-level guard and gate that fits no arm. I did **not** fit or score any arm
on real data, run G5, run `run`, or run `gate` (reason under "gate subcommand"). I read no season-2025 row. Every DuckDB connection
was `read_only=True`. Probe scripts are in the session scratchpad: `a1_build.py`, `a2_guards.py`, `a3_parity.py`, `b1_gate.py`
(the review-gate attacks run on a scratch `git clone`, never on the real repo).

Model classes: P0 is empirical-Bayes shrinkage with no fitted parameters; P0c is P0 plus a calibration layer; R19 and M2_19 are
gradient-boosted trees.

## Rule integrity

| check | command | result | pass |
|---|---|---|---|
| rule text unchanged from freeze to harness | `git diff --quiet 8e15012 HEAD -- docs/prereg/PPLAY_MINUTES_VALID.md` | exit 0 | PASS |
| hash of the text above the marker | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PPLAY_MINUTES_VALID.md \| shasum -a 256` | 91d82de7f47daee0... | PASS |
| freeze precedes harness | `git log` | 8e15012 21:48:41, harness ce6afc2 22:17:11 CDT; superseded b1be2ea (b3578a9c) never matches the doc on disk | PASS |
| no artifact before review | `ls data/ops/pplay_minutes_valid_cache reports/prereg_pplay_minutes_valid` | no cache dir; only `gate_draft.json` (17:00, pre-freeze, no fit) | PASS |
| suite | `.venv/bin/python -m pytest -q tests/research/pplay_minutes_valid -p no:cacheprovider --no-cov` | 54 passed, exit 0 | PASS |
| lint / types | `ruff check`, `ruff format --check`, `mypy` on the 3 modules (+ tests for ruff) | 0 / 0 / 0 | PASS |

## Review-gate attacks (modelled on the SEQ_LEARNING_CURVE F1 attacks)

`b1_gate.py` runs `require_review(root=<scratch clone>)` after crafting commits in the clone. 22 of 26 scenarios behave as intended.

| scenario | expected | got |
|---|---|---|
| no review; untracked review; empty committed review | refuse | refuse (x3) |
| `Verdict: BLOCKED`; `APPROVED WITH FIXES`; `**Verdict:** APPROVED`; lowercase key; a BLOCKED line placed before an APPROVED one | refuse | refuse (x5) |
| correct header | open | open |
| approved review dirty; approved review edited in a later commit (R1) | refuse | refuse (x2) |
| later BLOCKED review committed after the approved one; both added in the same commit | refuse | refuse (x2) |
| harness dirty; harness changed in a commit after the review; untracked file in the tests dir | refuse | refuse (x3) |
| Reviewed-commit before the harness (0011f18), before the freeze (b1be2ea), non-existent, 7-hex | refuse | refuse (x4) |
| Rule line names the superseded b3578a9c; rule text edited above the marker | refuse | refuse (x2) |
| results section edited (by design) | open | open |
| **S7: later BLOCKED review removed with `git rm` in a commit** | refuse | **OPEN** (the earlier APPROVED review becomes "latest") |
| **S7b: later BLOCKED review renamed out of the glob** | refuse | **OPEN** |
| S9c: committed change to an imported dependency (`research/eval/pplay_context_eval.py`) | refuse (strict reading) | OPEN (same as the earlier R3) |
| S12b: `Rule: NOT_PPLAY_MINUTES_VALID_X 91d82de7 BLOCKED` | refuse | OPEN (substring match) |

S7 and S7b are the sibling of the R1 edit hole the builder closed: an adversary's BLOCKED verdict can be withdrawn by deleting or
renaming its file. Both need a deliberate commit that shows up in history, but this is the one gate this review exists to protect, so
fix F5 is required.

## Holdout: no path to season 2025

* Every harness SQL query on games and box data carries `season IN (...) AND season <= 2024`. HW.load_world bounds the report rows at
  `as_of < 2025-07-01`. The R22 path (`PC.prepare`, `WE.load_sources`) is bounded. `predict_slate` reads only `game_date < as_of` for
  2024-25 dates (latest 2025-06-22).
* Measured: max season is 2024 in the P frame, the M frame, union games and era flags. Max report game_date is 2025-06-22, which is the
  2024-25 Finals.
* **Finding (F4):** `flagged_parity_gate` calls `nba.eval.context_residual_eval.load_inputs(db, None)`. That function bounds games and
  box at season 2024, but it reads `player_availability` with no season or date bound. Every 2025-26 (and 2026-27) report row in
  `nba.duckdb` is therefore loaded into memory. Those rows are only joined against games up to 2024, so nothing reaches a frame. It
  still breaks the rule's "every query `season <= 2024` in SQL" and "the 2025 path does not exist in this harness". I did not count
  those rows; counting would read them.

## Player 1626218 exclusion and the age assertion

* Read-only lookup: player 1626218 has 2 box rows in history (2019-20, games 0021900917 / 0021900954, both minutes NULL, team
  1610612739). He has no `players_static` row in either DB, and no rows in `nba.duckdb`.
* He never played, so `n_played_prior` = 0 < 5. He is in neither the Cell-P frame nor the played-rows M frame. I wrapped
  `exclude_null_age_player` to catch NULL-age rows before the drop and got 0 on both frames, so **the exclusion is a no-op on the
  real data**. The binding part is the assertion that no other NULL `age` exists, and it holds: NULL/NaN age = 0 of 185,680 P rows and
  0 of 156,237 M rows. That is correct behaviour. The rule's "2 rows excluded" is satisfied in substance, because those rows can
  never be training rows. Results should state "0 rows dropped (the 2 rows are not Cell-P)" so nobody reads it as a silent failure.
* Minor: run_gate's age check counts `is_null()` only. The core function counts NaN as well. Harmless at 0/0 (R4).

## Frame-level checks on the real data (read-only, no arm fitted)

| check | command | n | result | pass |
|---|---|---|---|---|
| frames build | `a1_build.py` | P 185,680; M 156,237 | 17.8 s | - |
| Cell-P rows / games, union recount | `a1_build.py` + count | 2023: 33,295 / 1,318; 2024: 33,158 / 1,320 (2019 24,467; 2020 29,612; 2021 32,282; 2022 32,866) | >= 25,000 / >= 1,000 | PASS |
| holdout gate | `H.holdout_gate` | 3 frames | max season 2024 | PASS |
| 2021-22 report drop, P(play) | `H.era_and_report_gate` | 32,282 rows | all 7 report columns NULL | PASS |
| 2021-22 report drop, minutes head | same gate | 27,442 rows | the gate passes, **but wrongly**: `vac_min` and `vac_pts` are non-NULL on 100% of 2021-22 M rows (83.8% non-zero). `vac_min` is an M2_19 input. `M2_REPORT_COLS` omits both | **FAIL (F1)** |
| era_report_dropped exact; era channels 0 on 2022-24 | same gate | all rows | exact; 0 | PASS |
| report config | `world["cfg"].report` | - | `tip_source='real'`, lead 60, `nba_official_report`; 0 of 446,714 report rows lack a real tip | PASS |
| OUT-set parity vs production (2022-24) | `H.flagged_parity_gate` (`a3_parity.py`) | 3,958 games | 3,952 / 3,952 sets identical | PASS (F4 applies) |
| parity: `predict_slate` vs P0, nba-only | `H.parity_gate` (`a3_parity.py`) | 10 dates, 1,083 shared rows (25-226 per date) | max abs dp 0.0, max abs dmu 0.0 | PASS |
| declared data-scope effect (union vs nba-only P0, 2023-24) | same | 66,453 rows | 40.9% of rows changed, mean abs dp 0.0127 | printed (see note) |
| P0p lineage | `p_raw` (game-context hurdle) vs `p0` | 185,680 | max abs diff 0.0 | the old map is applied to the served p |

Note on the data-scope effect: union P0 is the served function **with full history**, as the rule intends (live 2026-27 serving has
2022-2025 behind it). It is **not** what production actually served in 2023-24, when it had a shorter window. The result note must
say so. 41% of rows move, by about 1.3 pp each on average.

### G1 planted future (`a2_guards.py`, `H.planted_future_gate` on the real world)

Cutoff 2024-01-26 (median 2023 Cell-P date).

| arm inputs | future edit (box from the cutoff day on, results, reports and tips after it, national_tv after it) | control edit (box 15-30 days before) |
|---|---|---|
| P0 + R19 inputs, rows on or before the cutoff | n 136,037, same rows, 0 differing columns | row set changes (136,037 vs 136,047) |
| M2_19 inputs (membership compared strictly before the cutoff day) | n 114,857, identical | row set changes (114,857 vs 115,385) |
| P0c maps, blocks starting on or before the cutoff | identical | differ |

Result: **PASS**.

My own report-snapshot plants, aimed at what G1 does not edit (snapshots of the cutoff day's own games, and the 2021-22 lead):

| plant | rows | expected | got |
|---|---|---|---|
| every boxed player OUT at tip + 10 min, 2023 cutoff day | 217 | P rows and inputs identical | identical (n 210) PASS |
| every boxed player OUT at tip - 30 min (inside the 60-min lead), same day | 217 | identical | identical PASS |
| control: OUT at exactly tip - 60 | 217 | changes | all 210 rows leave the population PASS |
| OUT at tip - 120 on 2021-22 day 2022-02-04 (inside the 180-min lead) | 219 | identical | identical (n 216) PASS |
| control: OUT at exactly tip - 180, same day | 219 | changes | all 216 leave PASS |

The OUT filter respects real tips and both leads, and the 2021-22 rows cannot be made cleaner by the hourly archive.

### G2 missingness (harness `C.g2_missingness`, plus my own outcome groups)

* **P(play) inputs: ok.** Every NULL input has a listed cause. Worst gap per season, against the threshold max(2 pp, 3 x permuted
  gap): 2019 0.59 / 2.85, 2020 2.49 / 2.99, 2021 0 / 2, 2022 3.92 / 3.98, 2023 3.96 / 8.62, 2024 5.01 / 7.22 pp. The worst column is
  `on_evening_report` in every season (NULL 38-44% in 2022-24). 2022 passes by 0.06 pp. The 2023 and 2024 thresholds are loose
  because the minute-bucket placebo is noisy (the "<5" bucket is small). That is the frozen design, not a defect.
* **Minutes-head inputs: FAILS on the real data (F2).** `unexplained_null_inputs = ['opp_r20_pts_for', 'opp_r20_pts_against']`:
  29 rows on 2019-10-31..11-02 (team_game_no 5) where the opponent has no earlier game in the held data (2018-19 is not held). The gap
  test on those columns is 0.18 pp vs placebo 0.17 pp, so the NULLs are outcome-independent. Only the `NULL_CAUSES` entry is missing.
  As committed, `gate` and `run` stop with NO VERDICT at this check.
* **ret_* exemption (ruling 5).** `ret_mean_min`: NULL iff `ret_n >= 99 or ret_n <= 0`, exact both ways (111,124 = 111,124). `ret_ramp`:
  its NULL set equals `ret_mean_min`'s row for row (0 differ). Its coded cause expression is broader, though: 45,113 rows meet it with
  a non-NULL value. And the harness tests only NULL => cause, so the exemption is weaker than the claim "an exact function of ret_n" (F3).
  `ret_n` has no NULLs and is a G1-verified as-of input.
* **My outcome groups** (`a2_guards.py`), per training season, with a permuted-label placebo:
  * P(play) inputs, 215 tests, played/DNP, starter tonight, team won, minutes < 5, points above/below median: 0 flagged. Max: 1.91 pp
    (on_evening_report by points group, 2022). Max played-vs-DNP: 1.71 pp (2020).
  * M2 inputs, 75 tests, won / minutes < 5 / started: 0 flagged (max 0.14 pp).
* **By era within season** (the rule asks for "by row source and era"; the harness reports season only, R3): terc is NULL on all 2019
  rows (cause listed). tag60 NULL is 0% on 2019 non-era rows vs 1.19% on 2019 bubble rows. on_evening_report is 3.2% vs 5.2%. All are
  game-level report availability, knowable pre-tip.

## gate subcommand

I did not run `gate --sha 91d82de7...`. (a) `run_real` calls `RULE.require_review()` for `gate` too, so with no approved review it
refuses. (b) In code, `gate` is not arm-free: `run_gate -> repro_gate -> RR.walk(...)` fits LightGBM R22 monthly and predicts 2023-24.
It computes no loss, only max |p - stored p_R|, against a stored, already-spent result. I ran every other part of `run_gate` myself
(above). The R22 reproduction is the only gate left unverified. The maintainer should say whether a predict-only reproduction counts
as "scoring an arm". My reading: it does not, because no metric is computed and the stored array already exists. But it is not
"no arm".

## Checks that need scores (not run: they would score arms)

Shuffled labels (G5), lag-one-day, knockout, replication and seed swap are post-run checks. Integer support does not arise for
P(play). Props descriptive scores every arm on the same integer grid (`row_delta` on the stored A0 `qi`), so the support is fair.

## Other code findings

| id | severity | finding |
|---|---|---|
| F1 | major (run-time gate mis-implemented, rule 4) | `M2_REPORT_COLS` omits `vac_min` (an M2_19 input) and `vac_pts`. Both carry the 2021-22 report on every 2021-22 minutes row, and the gate "no 2021-22 row carries a report feature" checks the incomplete list, so it passes falsely. Affects the M2_19 secondary only, not the verdict, but the gate is a run-time gate |
| F2 | major (gate fails on real data) | `NULL_CAUSES` lacks `opp_r20_pts_for` / `opp_r20_pts_against` (29 rows, 2019 opener window). G2 (minutes head) fails, so `run` would stop with NO VERDICT |
| F3 | minor | `AS_OF_CAUSE` checks only NULL => cause, and `ret_ramp`'s cause is broader than its NULL set; the "exact function" premise of the exemption is not actually enforced |
| F4 | major (holdout hygiene) | `flagged_parity_gate` loads `player_availability` with no bound (2025-26+ report rows in memory) |
| F5 | major (hard-stop integrity) | a later BLOCKED review deleted or renamed out of the glob re-opens the gate on an earlier APPROVED review (S7/S7b); the Rule line is a substring match (S12b) |
| F6 | major (integrity of a resumed run) | checkpoints (`CACHE/arms/block_<start>.npz`, `m2_block_*`, `g5/perm_*.json`, `r22_blocks`) are keyed only by block start or seed. The stored label hash is never checked on reload, and reload uses `allow_pickle=True`. A run interrupted and resumed after a data drift (`--accept-data-version`) or a code fix would silently mix blocks. No cache exists today, so a clean first run is unaffected |
| R1 | minor | `require_review` and `data_stamp` run before the heavy.lock wait (ERROR_FEEDBACK's K = 50 null holds it now). A commit made during the wait is not seen, and `forward`, `minutes_v2` and `workload_eval` are imported lazily after it. Re-check both after the lock is taken |
| R2 | minor | `REVIEWED_PATHS` excludes the imported modules (S9c). Parity (exact) and the R22 reproduction catch drift in P0 and R. Stamp the git blob shas of `pplay_context_eval`, `recent_role_eval`, `minutes_v2_eval`, `history_window_eval`, `workload_eval`, `pplay_into_props_live`, `research/recent_role/prod_state`, `research/workload/asof`, `nba/props/{minutes,forward,context_residual}.py` and `nba/eval/context_residual_eval.py` into results.json |
| R3 | minor | G2's "by row source and era" table is by season only; add the season x era_any split |
| R4 | minor | run_gate's age check should count NaN as well as NULL |
| R5 | note | `cluster_boot` is local CI code. `nba/truth/bootstrap.py` does not exist yet, so there is no single CI module to defer to. The scheme (multinomial game weights, ratio of sums, percentile, 2,000 draws, seed 0) matches the rule |

Things I checked that hold: walk-forward strictness (train = `game_date < block start`, calendar months); the Platt map excludes
era-flagged rows and never sees the block; G5 permutes once per block with seed `[k, block]`, and the same permuted vector feeds R19
and the map. G5's 2023-24 blocks and training sets are the same as the main walk's, so the observed D1 and s_k cover identical rows.
Also: BH over exactly the 8 verdict tests; the PRODUCTION_BEATEN / HOLDS / MIXED / NO VERDICT logic (BEATEN and HOLDS are mutually
exclusive); G3 uses a within-date shuffle against P0c; G4 uses the strict-past same-season previous row; the ECE guard uses 10
equal-width bins; slice guards use n >= 300 and point > +0.01. The OUT filter uses real tips with lead 180 on 2021-22 (planted above).
`since_flag` / `flag_dir` are box-minute CUSUMs, not report features.

## Rulings on the builder's 15 resolved ambiguities

1. **Three-module split:** consistent.
2. **Review gate stricter than F1, no exports manifest:** consistent (no Colab, no exported artifacts). Close S7/S7b/S12b (F5).
3. **Pinned data versions (history strict 9e66461ad129; production a55164376845 with `--accept-data-version`):** consistent. The rule
   asks only for stamping; pinning is stricter. Today both match (the production manifest is uncommitted-modified but still
   a55164376845). Drift acceptance is safe only with F6, so that a resumed run cannot mix versions.
4. **G1 construction:** consistent with the rule. Ran on the real world: PASS. The cutoff day's own report snapshots are not edited
   (the rule says "dated after a cutoff"); my plants cover that gap and pass.
5. **G2 per season, NULL_CAUSES, age no-NULL, ret_mean_min / ret_ramp exempt:** the exemption fits "beyond the as-of cause" **only if**
   the NULL is an exact two-sided function of an as-of input. On the real data it is (NULL iff `ret_n` >= 99 or <= 0; `ret_n` has no
   NULL and passes G1). The code must enforce iff (F3). NULL_CAUSES must be completed (F2).
6. **G4 excludes rows without an earlier same-season row:** consistent. Without an earlier row there is no placebo. Keeping those
   rows at D = 0 would dilute the mean toward 0. Excluding them is the literal reading and slightly more lenient, so
   `n_without_previous` must be reported (it is).
7. **G5 s_k = point difference of mean log loss per verdict season, seed [k, block]:** consistent.
8. **2020 and 2022 descriptive; era-zero gate on 2022-24 only:** consistent. 2020-21 is era-flagged by definition, so "0 on every scored
   row" cannot hold for 2020. The binding requirement is on the verdict seasons.
9. **Report-derived column lists:** the P(play) list is complete for R19's inputs. The minutes-head list is **incomplete**
   (`vac_min`, `vac_pts`; F1). Derive the list from every column `MV.build_frame` computes from the OUT sets, and do not hand-list it.
10. **P0 from `production_minutes_table(use_game_context=False)`, R columns via `player_load_features`, P_PLAY_PLATT lineage only:**
    consistent. Parity is exact, and `p_raw` equals `p0` exactly.
11. **Parity on p_play_raw and proj_minutes over 10 seeded 2024 dates; rookie/new-team structural:** consistent. In
    `forward.predict_slate`, `official_roster=None` means `use_official=False`, so neither `new_team_k_multipliers` nor
    `rookie_minutes_mu` runs. Measured: 0.0 / 0.0 on 1,083 rows.
12. **R22 via `recent_role_eval.build_frame`/`walk`:** consistent. Its path is season-bounded. Not run by me (see "gate subcommand").
13. **Minutes secondary on Cell-P played rows, M2 trained on all played rows:** consistent with MINUTES_V2 lineage.
14. **Props secondary failure non-fatal:** consistent. It is descriptive and the error is recorded.
15. **MIN_TRAIN 1,500, LightGBM 4 threads, returner index across all games incl. playoffs:** consistent. The first two are written in
    the rule's "Unstated details". "The team's immediately preceding games" naturally includes playoffs.

**G5 "<= 2 h" row:** does not bind. It appears only as an "unmeasured" feasibility row in the minimum-data table. No guard, run-time
gate or kill criterion cites it, and per-permutation checkpoints make the wall clock irrelevant to validity. Exceeding 2 h is not NO
VERDICT. But the freeze went ahead with that row unmeasured, which is a non-negotiable-8 process miss. Record the measured G5
runtime in results.json and note the miss (drafted row below). Before trusting resumes across the 1.5-2.7 h, F6 matters.

## Playbook check

1. **Information or capacity?** R19 vs P0 is mostly information (reports, recent minutes) plus a learned mapping. P0c vs P0 is
   calibration only. The window is data. The harness keeps the decomposition arms (C19, Cp19) for "does R only re-express the base?".
2. **Enough data?** Trees: about 118k training rows before 2023. Platt: 2 parameters. Yes for both.
3. **Test valid?** On the frame side, yes once F1 and F2 are fixed: real tips, parity exact, G1 and planted reports pass, missingness
   clean by every outcome group I tried. One population caveat, shared by both arms: Cell-P membership is tonight's box (inactive
   players are absent). That is the declared design and paired-fair, but P0's absolute log loss is not the live population's.
4. **Lineage:** carried guards (past-only placebo, non-inert shuffle, planted future, missingness, ECE/slices; T473's single-draw
   guard replaced by the K = 50 null) are all implemented. The birth-date NULL guard is in place and holds at 0.
5. **Scored against?** P(play) log loss / Brier on Cell-P rows; minutes CRPS given play; props on the integer grid, descriptive only.
   Nothing touches a market.

Class checks (GBM): missingness beyond the as-of cause (run, clean except F2's listing and F3's enforcement); planted future (run,
PASS); re-expression of the base (decomposition arms present). Calibration layer: the 0.5-threshold eligibility table for P0 and P0c
is in `analyze`.

## Biggest risk

The minutes-head rule-4 leak (F1). It is not in the verdict, but it is a run-time gate that passes for the wrong reason, the same
pattern as a guard that checks only that a file exists. Its fingerprint would be an M2_19 - P0 gain concentrated in 2021-22-trained
blocks. For the verdict itself, the largest remaining risk is a resumed run mixing stale checkpoints (F6).

## Not verified

R22 reproduction (fits an arm); G3-G5, ECE/slices, the family table and the verdict on real data (need scores); G5 runtime;
`run` end to end on real data; the props descriptive on real stored grids (the components npz are untracked under
`reports/lower_tail/`); behaviour if `nba.duckdb` is written by ingest mid-run (the stamp is taken before the lock).

## Required before an APPROVED fix-check

F1, F2, F3, F4, F5 and F6 above. Each needs a negative test on the synthetic world: a 2021-22 `vac_min` value fails the era gate; an
unlisted NULL input fails G2; a non-exact as-of cause fails; a deleted later BLOCKED review refuses; a checkpoint from a different
manifest refuses. R1-R4 are recommended. None of these changes the rule (all are plumbing), and the rule text must stay byte-identical.

## Drafted rows (not appended; the main session decides)

* DECISIONS (draft): "2026-10-12 PPLAY_MINUTES_VALID loader review APPROVED WITH FIXES (F1-F6). Why: minutes-head 2021-22 report
  drop incomplete (vac_min), G2 fails on unlisted opp_r20 NULLs, unbounded availability read, review-deletion hole, unkeyed
  checkpoints. Reverses if: a fix-check review finds any of F1-F6 not closed."
* BEST_PRACTICES candidate (draft): "Derive report-derived column lists from the producing code, never hand-list them. Incident:
  PPLAY_MINUTES_VALID's M2_REPORT_COLS omitted vac_min, so the 2021-22 drop gate passed while the input carried the hourly report on
  100% of 2021-22 rows (loader review 2026-10-12)."
* Process note (draft, for the ledger or retro): the freeze of 91d82de7 left the G5 runtime row unmeasured; the row does not bind, and
  the measured runtime goes into results.json.
