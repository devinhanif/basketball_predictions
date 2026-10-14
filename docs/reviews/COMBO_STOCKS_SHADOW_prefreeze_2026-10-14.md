# COMBO_STOCKS_SHADOW pre-freeze review (adversary, 2026-10-14)

Object: DRAFT rule `docs/prereg/COMBO_STOCKS_SHADOW.md`, `configs/combo_stocks_shadow.yaml`, `nba/props/{stock_features,count_head,combo_copula}.py`, the
`nba/props/baselines.py` edit, and the rehearsal artifacts `reports/prereg_combo_stocks_shadow/` on branch `agent/modeler-combo-stocks` (commit f8f22d5).
No live result exists, so this review attacks the rule and the rehearsal, not a claim.
Data read: the cached rehearsal arrays in the session scratchpad (`stage_a*/`, `stage_b/grids_2024.npz`, all season <= 2024; the stage-B
`R_chosen.npz` and `select.json` are byte-identical to the committed copies), and `data/backups/nba_20261014.duckdb` opened `read_only=True`
with `season <= 2024` in every query (an assert on max season is in every script). Not opened: any season-2025 row, `data/history/`.
Model classes: a gradient-boosted tree count head (LightGBM Poisson mean plus binned negative-binomial dispersion), a Gaussian copula over
calibrated integer marginals, and empirical-distribution comparators.

## Verdict summary
The science holds up. Features are as-of (a planted-future attack, a live-placeholder attack and a real-data missingness audit all pass).
The NB head loses all of its gain under shuffled labels. The copula's correlation matrix comes from 2023 only. Re-derived on the cached rows,
the 2024 copula-vs-independence contrasts match the report to 4 dp, and every month has the same sign. The `baselines.py` edit cannot change
any production output.
Five items block the freeze. If they are not fixed, the frozen text would not determine its own verdict, or would not cover its own inputs, or
would be false about the holdout:
- The rule hash does not cover R.
- G1 depends on row order.
- The slice guard was never rehearsed, and RA fails it on 2024.
- The holdout sentence is false.
- The wiring spec has three gaps the steward cannot build around.
All five are text or spec fixes, and none needs a new number from 2024.

## Checks

| # | Check | Command (scratch = session scratchpad) | n | Number (CI) | Result |
|---|---|---|---|---|---|
| 1 | Shuffled labels, NB head (y permuted within date in training; fit < 2024-12-01, scored on the 2024-12 block) | `.venv/bin/python -W ignore scratch/cs_shuffle.py scratch/sched`, exit 0 | 4,007 rows / 191 games | blk true NB minus RECENT -0.0100 [-0.0127, -0.0076]; shuffled +0.0371 [+0.0292, +0.0449]. stl -0.0163 [-0.0200, -0.0126]; shuffled +0.0348 [+0.0265, +0.0426] (game-clustered 5th/95th) | PASS (gain vanishes) |
| 2a | Planted future: every box value of every game after date D set to 37; stock features at D | `.venv/bin/python -W ignore scratch/cs_placeholder.py`, exit 0 (branch fixture league) | 50 rows at D | 0 of 18 stock columns moved | PASS |
| 2b | Live placeholder path (history < D plus `_with_slate_placeholders`-style rows, blk/stl NULL or 0) vs the historical rows at D | same script | 50 | 0 columns differ (both NULL and 0 variants) | PASS |
| 2c | Lag every feature one extra day | not run | - | - | NOT RUN (structurally as-of recurrences; reports gated by production's real-tip code, unchanged) |
| 3 | Missingness of the 18 stock columns by min<5, starter, blk>=1, stl>=1 (played rows; DNP rows are not in the frame) | `.venv/bin/python -W ignore scratch/cs_missing.py scratch/sched`, exit 0 | 51,582 played rows, 2023-2024 regular, n_prior >= 5 | the only NULLs are vac_blk/vac_stl at 0.11%, NULL pattern identical to production's vac_pts (no report); largest gap 0.0009 (min<5 0.0002 vs 0.0011) | PASS |
| 4 | Knockout | not run as ablation; copula gain decomposed by the rule's own INDEP arm | 25,767 | the combo gain over RECENT is mostly the production marginals (pts lt 3.1825 vs 3.3275 alone); the dependence adds -0.0285 PRA | n/a (information is production's, disclosed in the playbook check) |
| 5a | Re-derive COPULA minus INDEP 2024 from per-row grids with the shared `one_sided` | `.venv/bin/python -W ignore scratch/cs_rederive.py`, exit 0 | 25,767 rows / 1,230 games / 163 dates | pra -0.0285 [-0.0316, -0.0256], pr -0.0150 [-0.0170, -0.0131], pa -0.0041 [-0.0050, -0.0031], ra -0.0067 [-0.0076, -0.0060]; date upper -0.0255 / -0.0132 / -0.0031 / -0.0059 | PASS (matches report_2024.json) |
| 5b | Replication: sign by month, drop best month | same script | 7 months | 7/7 months negative for all four; dropping best month: -0.0280 / -0.0146 / -0.0038 / -0.0064 | PASS |
| 5c | Replication across seasons | report vs select | 2023 13,041 / 2024 25,767 | blk -0.0069 -> -0.0094, stl -0.0133 -> -0.0151, pra copula-indep -0.0281 -> -0.0285 | PASS |
| 6 | Integer support for every arm | code read: NB quantiles are integers; copula sums integer draws; RECENT is an integer empirical grid; all int16 199-grids scored by one `crps_from_quantiles` | all | every arm on the same 199-atom support | PASS |
| 7 | 2024 OOF reproduction at ~20% | `.venv/bin/python -I scratch/cs_eqdate.py scratch/stage_a`, exit 0; `scratch/cs_ingest.py`, `scratch/cs_g147.py` exit 0 | 27,583 pts rows 2024 | match 100% through 2024-11-03, 56-100% per date in Nov from 11-04, 0% from 2024-12 on. `ingest_log`: five season-2024 box scores (re)ingested 2026-10-10, i.e. 0022400147 (2024-11-02), 0022401229/1230 (2024-12-14), 0022400621 (2025-01-23), 0022400633 (2025-01-25) | EXPLAINED (benign repair cascading through walk-forward training; not a leak) |
| 8 | G1 seed and row-order sensitivity (randomised PIT q10) | `.venv/bin/python -I scratch/cs_pitseed.py`, exit 0 | 25,767 | PRA seed 0 0.1205; over 200 seeds mean 0.1199, sd 0.0006, **44.5% of seeds > 0.12**; same seed after a row permutation 0.1186 (pass). At n = 3,000 (iid resample) P(q10 > 0.12): pra 0.51, pr 0.35, pa 0.34, ra 0.27, bs 0.001 | FAIL as written (B2) |
| 9 | Rule 6(e) slice guard and 6(f) later half, dry run on 2024 | `.venv/bin/python -W ignore scratch/cs_slices.py`, exit 0 | 25,767; slices 464-22,378 | **ra, realised minutes < 10: S minus RECENT +0.0378 (n 3,874) > floor 0.0188, so 6(e) FAILS**. pra min<10 -0.0418 (floor 0.0438), every other slice of every target negative; later half negative for all seven | FAIL (B3) |
| 10 | Config equals the select artifacts | inline python, exit 0 | - | floors and use_copula equal select.json; config R equals R_chosen.npz to max abs 4.7e-5 (rounded to 4 dp); min eigenvalue 0.618 | PASS with note (B1, B5c) |
| 11 | Production inertness of `baselines.py` | `git show main:nba/props/baselines.py`; `grep -rn _DEFAULT_STD nba research tests` | - | 4 production keys and values unchanged, 9 keys added; every use in `nba/` is a keyed lookup for a PROP_STATS stat (forward.py:323-330, context_residual.py:500/907, pplay_shadow/state.py:150); no iteration or membership test in `nba/`; the new modules are imported by nothing in `nba/` or `ops/` | PASS (oracle not needed for this diff; P3 still needed for the writer) |
| 12 | Tests and lint on f8f22d5 | `ruff check nba tests reports/prereg_combo_stocks_shadow` exit 0; `ruff format --check` exit 0; `mypy` on the 4 modules exit 0; `pytest -q -p no:cacheprovider --no-cov tests/props tests/test_layering.py tests/daily/{test_canonical_dump,test_forward_wiring,test_full_shadow,test_pplay_shadow}.py` exit 0 | 212 passed | - | PASS (full suite not run: >10 min on the shared machine; the run with coverage on exits 1 from the fail-under gate on a subset, 176 passed) |
| 13 | Holdout | stage SQL `season <= 2024`; stored prod OOF holds seasons 2023/2024 only | - | rehearsal clean; **live path and P3 use 2025 rows** | FAIL in text (B4) |

## Answers to the seven questions

1. **Forking paths.** Changing the copula-use criterion from a floor to significance on 2023 is acceptable. It is a select-season choice, it is
   disclosed, and the live rule 6(g) tests COPULA against INDEP again with a clustered bound. A wrong choice can only cost a PASS; it cannot
   manufacture one. The logs show **five select runs** (`stage_b_select.log` to `stage_b_select5.log`, 12:59 to 13:23). In run 4, PA flipped
   to INDEP under the 0.005 floor (-0.0032 [-0.0046, -0.0018]); in run 5 it returned to copula under the significance criterion. The rule
   should say "five select runs" in the disclosure.
   2024 was looked at before the freeze, by design: one report run at 13:26, after `R_chosen.npz` (13:23) and the 13:24 ledger prior. The
   select phase also read 2024 q19 values, label-free, for the equivalence check. All numeric rule content is provably fixed before 2024:
   floors, R, use_copula and the head choice equal `select.json` sha256 575c6bb6. The rule's prose (slices, G1 tolerances, looks) was
   committed after 2024 was read, so its timing cannot be proven either way. That is acceptable only because the verdict venue is the
   unseen live log. The text should say so in one sentence. The G1 tolerance was not loosened after the PRA miss, which is the right call.
2. **`baselines.py`.** Inert (check 11). The replay oracle does not need to run for this diff. P3 must still run with the writer on (B5b).
   Optional: move the nine new keys into `stock_features` so that production's dict stays untouched. They feed only the losing CR head, the
   descriptive DIRECT arm and the `sd10_` default.
   Side effects outside production: the export metadata from `research/props/context_features_v2.py:982` would change on a re-export, and
   `research/stack/adapters.py:102` now accepts blk/stl.
3. **2024 reproduction at 20%.** It does not invalidate the rehearsal. The cause is the five 2024 box scores ingested on 2026-10-10 (check 7).
   Every arm and RECENT reads the same rebuilt data, so the comparisons are fair. Replace "cause not diffed" with the cause.
4. **PRA calibration.** Rule 6(d) applies G1 to every target, so PRA cannot PASS unless G1 holds live. That follows from the rule but is not
   stated as a sentence; add one. The larger problem is that the outcome depends on row order and seed (B2).
5. **Leakage.** Clean on every check run (1, 2a, 2b, 3). Tips are real: `tip_source="real"`, and no proxy fallback appears in the stage
   logs. R comes from 2023 rows only (`sel = reg & season == 2023`). The choice of copula or INDEP used a time split inside 2023: fit on the
   first half, judge on the second.
6. **n_min 3,000.** Projected live supply is about 9,700 rows, which is an inference, not a measurement. MDE at the one-sided 95% / 80% test
   (2023 sd and game design effect):

   | target | MDE at 3,000 rows | MDE at 9,700 rows | floor |
   |---|---|---|---|
   | blk | 0.0049 | 0.0027 | 0.005 |
   | pra | 0.081 | 0.045 | 0.0438 |

   The gate passes. All seven targets share one set of rows, so they reach n together. The binding constraint is about 80 date clusters at
   week 12, not the row count.
7. **Wiring spec.** Not yet buildable as written (B5).

## Blocking findings and exact fixes

**B1. The frozen hash does not cover R.** Line 3 hashes only the text above `=== RESULTS BELOW ===`. The R matrix appears only below the
marker (line 102) and in `configs/combo_stocks_shadow.yaml`. Line 15 says "R fixed on 2023 (below)", which points below the marker.
Fix: add this line above the marker: "Frozen inputs: `configs/combo_stocks_shadow.yaml` sha256
189815371fca289ccad02679c01f0b109bfca57750d0cbf4e4b6de648c842c1b (R to 4 dp is canonical); `select.json` sha256 575c6bb6...;
`R_chosen.npz` sha256 33fb90bd...; base-normal sha <value>; any change to these files is a new id."

**B2. G1 is not determined by the rule.** Rule 6(d) fixes seed 0 but not the row order. PRA's q10 fails on 44.5% of seeds and passes after
a row permutation (check 8). The reader that the rehearsal used (`nba.eval.avg_shadow_live.calibration`) also disagrees with the text in two
ways. It requires 0.95 coverage (`REQUIRED_GUARD_TAUS`, lower_tail_eval.py:342), while the rule says 0.95 is only reported. Its bias
tolerance is an absolute 0.5 (`MAX_BIAS`, avg_shadow_live.py:68), while the rule says 0.05 x mean(y). The rehearsal outcomes do not change
under either reading: every S arm has 0.95 coverage within 0.02 and relative bias inside tolerance.
Fix in 6(d):
- "rows sorted by (game_date, game_id, player_id) before `randomized_pit(q, y, 0)`, one call per target over that target's scored rows";
- name the reader function;
- state the required taus exactly as the text says, (0.10, 0.20, 0.80, 0.90), and the relative bias tolerance;
- state that `avg_shadow_live.calibration` is not used.

A seed-free G1 (expected coverage, `mean(clip((t - f_lo)/(f_hi - f_lo), 0, 1))`) is the better estimator. Adopting it now would be a change
made after the 2024 PRA miss was read, so it would need that disclosure. That is Devin's call; the safe default is the fixed order.

**B3. The slice guard and later-half condition were never rehearsed, and RA fails 6(e) on 2024.** In the realised-minutes-under-10 slice,
RA's S minus RECENT is +0.0378 (n 3,874), above its floor of 0.0188 (check 9). The rehearsal table shows RA as passing on every computed
condition. Its prior (P(PASS) 0.50) was stated without this information.
Fix:
- add the 6(e) and 6(f) dry run for all seven targets to the rehearsal record;
- restate RA's prior in `PREDICTIONS_LEDGER.md` before the freeze;
- decide one of: (i) keep 6(e) as written, so RA is expected to FAIL; or (ii) drop the three realised-minutes slices for every target. Option
  (ii) is defensible, because those slices condition on a post-tip outcome while the forecast is conditional only on playing. But it would be
  decided after this 2024 reading, so it must be disclosed as such.

The default is (i): no change.

**B4. The holdout sentence is false (same pattern as the AVG_SHADOW_LIVE B7 finding, 2026-10-13).** Line 5 says season 2025 is "never
loaded, never touched by this rule". But live, the NB heads refit nightly on played rows before the slate date, which includes all of
2025-26. The stock features and RECENT windows at opening night read mostly 2025-26 games. P3 replays the oracle's 20 dates of season 2025
with the writer on (DAILY_PIPELINE.md:543).
Fix:
- line 5 becomes: "2025 rows are used as training and history inputs live and by P3 (systems use only); no 2025 forecast of this rule is
  scored, read or reported; the P3 replay copy is deleted unread";
- draft a HOLDOUT_ACCESS_LOG row of type "training and systems use only", committed before P3 runs.

**B5. Wiring-spec gaps (section "What the steward builds").**
- (a) Step 2a: `_history_frames` (nba/props/forward.py:582) does not select blk or stl, so `add_stock_features` on `build_full_frame`'s
  frame raises a KeyError. Add: "a separate read-only query of `player_game_stats.blk, stl` for `game_date < run_date`, joined onto the
  history box frame inside `combo_shadow/run.py`; `_history_frames` is not edited".
- (b) P3 must run with `with_combo_shadow` on, and must show heartbeat `ok` with `rows_written > 0` in the replay. Otherwise P3 only proves
  that the code is inert when off, which was the AVG B6 finding.
- (c) P1 has no reference. `grids_2024.npz` and the stage-A arrays live only in the session scratchpad, and the rehearsal used R at full
  precision, while live uses the 4-dp config R. Add: "P1 reference = 200 rows (inputs and expected grids built with the config R) committed
  under `reports/prereg_combo_stocks_shadow/p1_reference.npz` before 10-16".

## Non-blocking findings
- N1. Disclose the five select runs (Q1).
- N2. Replace "cause not diffed" with the ingest_log cause (check 7).
- N3. `tests/props/test_stock_features.py` `test_stock_features_are_asof_planted_future`: the docstring says "or any later game", but the test
  edits only the last date. Add the later-games case from check 2a and the placeholder case from check 2b as tests.
- N4. G1 near its bound: at the live n, PR, PA and RA each fail q10 by sampling alone with probability 0.27-0.35 (check 8; that is iid, and
  clustered will be higher). The combo priors in PREDICTIONS_LEDGER look optimistic; the restated PRA 0.35 is about right.
- N5. Rule 7: when a target is INCONCLUSIVE with at least 3,000 rows, the second look is an unadjusted second success look. Say so, or
  require z = 1.96 for that look's bound as well.
- N6. Storage: up to five `pmf199` JSON arrays per row, seven targets, write-on-change. Put a measured MB-per-slate figure in P4; drop the
  optional arms if it is large.
- N7. Servability depends on the production lt row (the `--log-lower-tail-variant` shadow flag, ops/nba_daily.sh:128). Rows without one
  leave the denominator silently. Print at every look "played rostered players without on-time pts(lt)/reb/ast rows", and flag it above 5%.
- N8. The NB head is a gradient-boosted tree, so the playbook's shuffle guard applies to it. The draft's "no shuffle guard" reasoning covers
  only the copula. Record check 1 in the rehearsal record.
- N9. Playbook gap: there is no count-model section. Proposed text for the main session is below.

## Playbook check
Model classes: a gradient-boosted tree count head and a Gaussian copula ("Gradient-boosted trees", "Set and joint models", "Quantile and
distribution layers"). There is no count-model section.

1. **Information or capacity.** Mostly construction and shape: the right support for zero-heavy counts, and dependence for the sums. The
   inputs are production's pre-tip features, with nothing new at T-30. The large combo gains over RECENT are production's marginals. The
   rule's own new claims are the small parts: blk -0.009, stl -0.015, and the dependence term (PRA -0.029).
2. **Enough data.** Trees: one season for 2023, more for 2024, and the stock gain grew. Copula: about 25,800 rows, SE about 0.006 per
   correlation entry. Enough.
3. **Valid test.** Real tips, integer support for every arm, as-of features (checks 1-3). Two defects: G1 is not deterministic (B2), and the
   slice guard was not rehearsed (B3). An invalid G1 reading would be an invalid test, not a null.
4. **Lineage.** The guard inherited from the parlay copula (it must beat independence with a clustered bound) is carried forward in 6(g). The
   production recipe's low-count failure (P(Y=0) predicted 3% against 19.6% observed) was found on 2023 and replaced.
5. **Scored against.** CRPS of the whole pmf against the player's own recent empirical distribution, plus calibration. No market and no money.

Proposed MODEL_PLAYBOOK section (for the main session):
> **Count models (NB / Poisson heads, zero-heavy stats: blk, stl, fg3m).** Driven by: the mean model and the dispersion; a continuous scale
> family rounded to integers misplaces P(Y=0). Data appetite: moderate; dispersion needs a few thousand rows per mean bin. Typical failure:
> P(0) reliability (production recipe 3% predicted vs 19.6% observed on 2023 blk), tails at low means, randomised-PIT noise near a
> coverage bound. Fair test: integer CRPS on the same grid for every arm, the empirical recent pmf as the comparator, shuffle guard on the
> mean head. Checks: P(0) reliability by decile; randomised PIT with a fixed row order; slices by role and minutes.

## Corrected family and power
The live family is F = 7 targets, BH, q = 0.05, as drafted. That is correct for the live look. The select-season search is not part of it:
CR vs NB head, three correlation schemes, two estimators, lt vs plain pts, the criterion change, and five runs. That search is disclosed and
selection-only.
Rehearsal dry run of the full rule on 2024 (descriptive, game-clustered; date-clustered bounds from report_2024.json):
- blk, stl, pr, pa and bs meet every rehearsed condition;
- **pra fails (d)** at q10 0.1205;
- **ra fails (e)** in min<10.

So the honest 2024 reading is 5 of 7, not 6 of 7.

## Biggest risk
The verdict turns on the PIT draw, not on the forecast. PRA's G1 on 2024 is a coin flip across seeds. Live, at about 9,700 rows, sampling
noise alone gives PR, PA and RA a 12-22% chance of failing q10 (iid normal approximation from check 8 means). An unfixed row order would make that a reader-implementation choice
made after the data exist.

## Not verified
- The full pytest suite (stopped after more than 5 minutes on the shared machine).
- The lag-one-day check.
- Date-clustered bounds for every 2024 slice.
- Live supply of eligible rows (no 2026 regular-season rows yet).
- Whether the 4-dp R changes any grid compared with full-precision R.
- The NB head's determinism with `n_jobs = 4`.
- The `combo_copula` estimator internals beyond reading `estimate_latent_corr`'s inputs.

## Drafted rows (not appended)
- DECISIONS: `2026-10-14 | COMBO_STOCKS_SHADOW pre-freeze review BLOCKED (B1-B5, text and spec fixes; no 2024 re-run needed); adversary re-reads the diff only | what would reverse it: none, these are definitional`.
- HOLDOUT_ACCESS_LOG (B4, before P3): `2026-10-1x | COMBO_STOCKS_SHADOW live refits, RECENT windows and P3 oracle replay (20 dates of season 2025, writer on) | training and systems use only | all | NOT a confirmatory touch. 2025 labels are training and history rows; no 2025 forecast of this rule is scored, read or reported; replay copy deleted unread.`
- PREDICTIONS_LEDGER (modeler, before freeze): RA P(PASS) restated given the 2024 min<10 slice result (+0.0378 vs floor 0.0188).

Verdict: BLOCKED

## Re-review (revision 5005a3b, 2026-10-14)

Object: `agent/modeler-combo-stocks` at 5005a3b (one commit on top of f8f22d5). Line numbers below refer to `docs/prereg/COMBO_STOCKS_SHADOW.md`
at 5005a3b. This verdict supersedes the BLOCKED line above.

### Blocking items

| Item | Status | Where | Evidence |
|---|---|---|---|
| B1 hash covers R | FIXED | line 4 | `shasum -a 256` reproduces all four named hashes: config 8932ccec, select.json 84619e3e, R_chosen.npz e535258c, p1_reference.npz 1356c78a. The config carries R (4 dp, canonical), base sha 08ddb2a2 (recomputed from `base_normals()`: equal), floors and the G1 spec. |
| B2 G1 deterministic, reader matches text | FIXED | line 20 (6d); config `g1:`; `nba/props/combo_g1.py` | The row order is sorted inside `g1()`; required taus are 0.10/0.20/0.80/0.90; bias is relative; `avg_shadow_live.calibration` is excluded by name. Re-derived on the six-stat 2024 grids (`scratch/cs_g1v2.py`, exit 0, n 25,767): pra q10 0.1211 fail, pa 0.1204 fail, pr 0.1184, ra 0.1171, bs 0.0986 pass, all matching line 128. A random permutation of the input order gives an identical q10 for every target. |
| B3 slice guard rehearsed, RA stated | FIXED | line 20 ("RA cannot PASS..."), lines 120-132, `guards_2024.json`, ledger row 14:55 | The realised-minutes slices are kept (Devin's default). RA min<10 reads +0.0384 vs 0.0188 (my five-stat figure was +0.0378). The 6(e)/6(f) dry run covers all seven targets. |
| B4 holdout text | FIXED | line 6, drafted row at line 75 | The rule now states the 2025 training, history and P3 uses. The log row is drafted for writing before P3. It is not yet in HOLDOUT_ACCESS_LOG, which is correct at this stage. |
| B5a blk/stl read | FIXED | line 69 | A separate read-only query; `_history_frames` is not edited. |
| B5b P3 with the shadow on | FIXED | line 71 | Requires `with_combo_shadow` ON, heartbeat `ok` and `rows_written > 0`. |
| B5c P1 reference | FIXED | line 71; `tests/props/test_combo_p1.py` | 200 rows built with the config R, committed, reproduced bit for bit in two chunkings (test passes). |

### The one six-stat joint
- **Positive definite:** symmetric; eigenvalues 0.247, 0.704, 0.844, 0.945, 1.049, 2.212. The 4-dp config R equals `R_chosen.npz` to 4.8e-5.
- **Select season only:** `select.json` has season 2023, n 25,815, fit/eval halves 12,774 / 13,041. The final R comes from `sel = reg & season == 2023`.
- **Seed fixed:** base seed 20261014; base sha 08ddb2a2 recomputed and equal.
- **Timing:** the select re-run (14:41:35) precedes the six-stat report (14:44:16), per the scratch logs. The re-run kept every earlier choice: NB heads, rpit, pooled. So nothing was re-selected on a 2024 number.
- **Disclosure:** the design change itself came after the first 2024 read; line 79 says so. It moved PA's G1 from pass to fail, which is the opposite of a favourable fork.

### Honesty about which targets can PASS
Yes. Line 132 says 4 of 7 (blk, stl, pr, bs). Line 20 says RA cannot PASS on the 2024 reading. Line 65 names G1 as the binding risk for PRA
and PA, with a Monte Carlo check showing the miss is systematic rather than draw noise.
The priors (pra 0.30, pa 0.25, ra 0.10) are consistent with that. At about 9,700 live rows the sampling sd of q10 is about 0.0033, so a true
0.1214 still has about a one-in-three chance of reading at or below 0.12.

### Heavy-lock runs
Read-only, verified:
- Every DuckDB connect in `stage_a_oof.py`, `stage_b.py` and `stage_c_guards.py` passes `read_only=True`; `mc_noise.py` opens no database.
- `make_config_and_p1.py` writes only the config and `reports/prereg_combo_stocks_shadow/`.
- The source copy `data/backups/nba_20261014.duckdb` still has mtime 07:41.

### Verification
On 5005a3b:
- `ruff check` exit 0; `ruff format --check` exit 0;
- `mypy` on the five props modules exit 0;
- `pytest -q -p no:cacheprovider --no-cov tests/props tests/test_layering.py tests/daily/test_canonical_dump.py tests/daily/test_full_shadow.py`: 204 passed, exit 0.

### New findings
- **R1 (COSMETIC).** Line 4 names `nba/props/combo_g1.py` as the implementation but gives no hash for it. The text now fully specifies G1, so
  the text governs. Fix: add "`nba/props/combo_g1.py` sha256 792635d02311f2fb758c3d182b138fee2cba7564237307e7e67c0426ef18b9c4" to line 4.
- **R2 (COSMETIC).** The consequences sentence in line 20 names RA but not PRA and PA. Fix: append "PRA and PA fail G1 at q10 on the 2024
  rehearsal (0.1211, 0.1204) and can PASS only if live calibration differs."
- **R3 (COSMETIC, process).** `stage_a_oof.py` and `stage_b.py` now carry a `COMBO_NO_LOCK` bypass of the cooperative heavy lock. It is
  disclosed (line 81 vii) and harmless to the data. Fix: the main session decides whether the bypass stays in committed scripts; the
  default is to remove it before merge.
- **R4 (COSMETIC, scope).** The modeler branch edits `docs/MODEL_PLAYBOOK.md`, which my review assigned to the main session. The content is
  accurate. The main session should review it as its own change at merge.
- No other contradictions found. Checked:
  - line 2 vs 6(g) for bs (reported, not required);
  - fg3m now in the servability condition (line 18) and in `src`;
  - n_min figures (line 31) vs select.json;
  - the second-look z in line 21 (stricter, consistent).

Drafted DECISIONS row (not appended): `2026-10-14 | COMBO_STOCKS_SHADOW pre-freeze re-review APPROVED (B1-B5 fixed at 5005a3b; R1-R4 cosmetic) | what would reverse it: a post-freeze change to any hashed input`.

Verdict: APPROVED
