# SEQ_LEARNING_CURVE pre-run loader review (adversary), 2026-10-12

Verdict: APPROVED WITH FIXES

Reviewed-commit: b695b5dddd8eec86163a8908c050918f2310528e
Rule: SEQ_LEARNING_CURVE ad0408a7 (freeze commit b02894c, 2026-10-12 19:40:44 -0500)
Exports-manifest: 9bf217e60af7a62d5b3e932fcee09f4044b764e4474277e9a81468cf2de46064

Required by the frozen rule's "Before the run" section and BEST_PRACTICES "A required pre-run review is a hard stop" (T479).
Scope: `research/features/seq_learning_curve_export.py`, `research/colab/jobs/seq_learning_curve/` (job.yaml, build_notebook.py,
tail, train, ipynb), `research/eval/seq_learning_curve_eval.py`, `research/eval/seq_learning_curve_rule.py`,
`tests/research/seq_learning_curve/`, and the built exports in `data/colab/seq_learning_curve/` (gitignored). I trained nothing and
scored no window on real data. The only scoring I did was re-scoring run #1's stored 2023 OOF arrays (already scored, T478 lineage)
through the new scorer's code, to test the harness. Nothing from 2024-25 or 2025-26 was scored. All DuckDB connections were read-only.

**The verdict's condition.** The run must not be staged until F1-F5 below are applied and an adversary fix-check file
(`docs/reviews/seq_learning_curve_loader_<date>_fixcheck.md`, `Verdict: APPROVED`, naming the fixed commit) is committed.
**Warning: the current hard stop ignores the verdict (F1).** Committing this file alone already satisfies
`require_review` and `stage`. Do not stage until F1 lands.

## Top finding

The hard stop checks that a review file exists, not what it says. `require_review`
(`research/eval/seq_learning_curve_rule.py`, lines 115-151) checks that a file exists, is non-empty, is tracked, is unmodified, and was
added after the freeze. It never reads the file's verdict, and it does not tie the review to the code or exports that were reviewed.
So a review saying BLOCKED, or one written for older code, opens the Colab run and the scorer. That is the T479 failure mode again
(an unreviewed post-freeze notebook change, 716da5e). Everything else I attacked held. No leak, no 2024/2025 exposure, and the
reproduction path reproduces run #1 exactly.

## Checks (exact commands; numbers with n and CI)

Scripts live in the session scratchpad (`a1_windows.py`, `a3_exports.py`, `a4_s1drop.py`, `a5_rescore.py`, `a6_keys.py`). Each was run
with `.venv/bin/python -I` (or `-W ignore` for scoring) and exit codes were shown unpiped.

| # | check | command / evidence | n | result | pass |
|---|---|---|---|---|---|
| 0 | rule integrity | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/SEQ_LEARNING_CURVE.md \| shasum -a 256` = ad0408a7...; `git diff b02894c b695b5d -- docs/prereg/SEQ_LEARNING_CURVE.md` empty; freeze 19:40:44 precedes the export build (20:36) and the code commit (20:50:26) | | sha matches the notebook's baked `RULE_SHA`; rule unchanged | PASS |
| 1 | window row sets (N = 1..8) | `a1_windows.py`: `XP.window_counts` on the built metas vs run #1 `info_W22_E2.json` / `info_WALL_E2.json` | fit N1..8: 22,853 / 50,508 / 75,247 / 97,774 / 125,196 / 152,487 / 179,895 / 206,291; val 3,286 every N; test 28,197 every N | N = 1 equals W22 (fit 22,853, val 3,286, first pool 26,139). N = 4 equals WALL (fit 97,774, val 3,286, first pool 101,060 = fit + val). Pool-eligible counts match run #1's last-block pools (53,985 = 26,139 + 27,846). NORM and RAW counts are identical | PASS |
| 2 | 2023 cap / no 2024-25 outcome | code: `assert_cap` on SQL bound, export meta and every segment; `load_capped` and `run_arm` asserts on Colab; export_info `rows_by_season` holds only 2015-2023; the scorer's `restrict_world` drops the cache's 2024 rows before any computation | 244,077 export rows; max season 2023 (both variants) | No 2024 row reaches training. The A0/FULL loader (`S.stored_world_base`) does only row-local work before the drop (integer grids, a meta join). The blend context and the learned blend run after the drop. See check 9: run #1's 2023 figures reproduce bit-for-bit after the drop | PASS |
| 3 | planted future, normalizer (real S3 inputs) | `a3_exports.py`: `XP.norm_factors` on real 2022-23 box rows; every row dated >= cut is perturbed (x3 + 50) | 2,638 games, cuts 2023-01-15 and 2024-01-15 | c(d) for every game up to and including the cut date is bit-identical (same-day excluded). 99.5% / 98.5% of later games changed (the probe is live) | PASS |
| 4 | normalizer re-derived independently | `a3_exports.py`: SQL team-game totals, strictly-before cumulative sums, frozen Lprev, compared to the stored `norm_c_*` | 110,948 / 77,195 / 55,934 rows (S1/S2/S3) | max abs diff 0.0 for all four stats in every segment; 0 unmatched rows. Season constants reproduce for 2019-2022 exactly | PASS |
| 5 | inversion before integer scoring | `a3_exports.py` round trip; `a5_rescore.py`: run #1 WALL q multiplied by the real 2023-24 c, then scored through the NORM path | 27,619 rows | y_norm / c - y_raw max 3.1e-6 (float32); raw y integer. NORM path vs RAW path mean CRPS diff +5.9e-8 (max one-row 0.0019, a float32 crossing of an integer boundary). No-divide control: -0.00016. 2023-24 pts c lies in [0.990, 1.036], so this guard is weak on this test season | PASS (weak control disclosed) |
| 6 | era/rule flags; test vector = 2022-23 | `a3_exports.py` distinct era vectors per season; export_info `era_runtime_gate`; the scorer recomputes it from the attested S3 npz | 28,197 test rows | 2023 = 2022 = [0,0,0,1,1,1,1,1,0,0], one vector. S1 steps are cumulative (2015: no rule flags; 2016: +away_foul; 2017: +timeouts; 2018: +reset14). The S1 flag is 1 only on S1 rows; report-dropped is 1 only on 2021 rows | PASS |
| 7 | S1 handling | export_info; `a3_exports.py`; `a4_s1drop.py` | 110,948 S1 rows | Played rows only (620 dropped). has_report and every st_*/rep/return channel is 0 on 100% of rows; pos_g/f/c are 0 on 100%; tips None; n_tm_out/vac_tm come only from gap games. Disclosure: 18-46 dropped rows a season carry stats with 0 derived minutes (pts 30-58 a season). The frozen Lprev constants include them and the running L(d) excludes them, a 0.02% level gap. Immaterial | PASS |
| 8 | missingness by outcome-correlated group | `a3_exports.py`: zero-rate of every one of 41 ctx channels and 8 step channels by realised minutes < 5, realised starter, win, pts above median, per segment | S1 110,948; S2 77,195; S3 55,934 | 0 NaN. No channel is NULL or zero iff an outcome. Every gap over 0.10 is a pre-tip quantity with an obvious cause (as-of `starter10` vs bench/starter; `home` vs win, i.e. home edge; past-token `present/absent/usg/zone` vs bench status). The same gaps appear with similar size in S1, S2 and S3, so S1 adds no new asymmetry. Position missingness is gone from S1 (all zero) | PASS |
| 9 | token as-of | `a3_exports.py`: valid tokens with days_ago = 0; `build_export` asserts token index < target index | 2.10M / 1.45M / 1.03M valid tokens | 0 same-day tokens in any segment | PASS |
| 10 | reproduction gate harness (re-derive) | `a5_rescore.py`: run #1 `oof_W22_E2` and `oof_WALL_E2` filtered to season 2023, scored as RAW_1 / RAW_4 through `score_arm_stat` and `SharedBoot` (seed 0, 2,000 game draws) | 27,619 rows / 1,318 games | RAW_1 AVG-FULL -0.008323 [-0.010797, -0.005793]; RAW_4 -0.020313 [-0.023396, -0.017340] (run #1 gate: -0.020312965, identical); FULL-A0 -0.0083501 = T467 to 1e-9; `repro_gate` ok; world rows ⊆ export rows (0 missing) | PASS |
| 11 | integer support | code: every arm goes through `S.build_arms` → `seqc_grid` (`to_integer_support`) → `mix_half`; A0 and FULL are stored integer grids | all arms | One support for every arm and comparator | PASS |
| 12 | notebook = builder output; model code = run #1 | `a2_nb.py`: `build()` equals the committed train.py and ipynb; `diff` of lines 1-284 vs `seq_full_mix_rt_sel_train.py` is empty | | identical; baked sha ad0408a7 | PASS |
| 13 | compute equality across windows | code: `COLD`/`WARM`/seeds are constants; `hidden` comes from the `NBA_HIDDEN` env; `check_run` does not compare per-arm configs | | The same policy for every N as written. But a restart with a different env can mix configs undetected (F3) | FAIL (F3) |
| 14 | hard stop not bypassable on Colab | code: notebook `check_gates` needs the frozen doc, an attestation, and the review and export shas; the attestation is unsigned JSON; the Mac scorer re-runs `require_review` with git | | A hand-written attestation could start a Colab run, but such a run cannot be scored on the Mac. The real hole is verdict blindness and the missing code binding (F1) | FAIL (F1) |
| 15 | nothing read on a void run | code: `run()` scores every arm and stat into `data/ops/seq_learning_curve_cache` before the gates decide | | A VOID/INVALID/unfinished run leaves per-row CRPS for every NORM arm on disk, so the curve could be computed from them. The rule says "nothing is read", then the harness is fixed and rerun **under this id** (F2) | FAIL (F2) |
| 16 | tests, lint, types | `pytest -q tests/research/seq_learning_curve -p no:cacheprovider --no-cov`; `ruff check`; `ruff format --check`; `mypy` (3 modules) | 30 tests | 30 passed exit 0 (with the repo's coverage addopts, the same 30 pass and pytest-cov exits 3 on a stale branch/statement data mix, unrelated to this code); ruff 0; format 0; mypy 0 | PASS |

Not applicable before a fit: shuffled labels (no gain exists yet), the knockout, replication, and the one-day lag. The lag needs a training
run. The run's own guards cover it only partly (identity with run #1's real-tip exports, which T479 attacked).

## Rulings on the builder's interpretations (fixed now; cannot change after a run)

I was given seven of the twelve (1, 2, 3, 4, 7, 8, 11). Items 5, 6, 9, 10 and 12 were not provided to me. Each of them is fixed as
implemented at b695b5d, unless the main session shows me one that conflicts with the rule text before the fix-check.

1. **Epoch cap pooled over every completed cold fit (NORM, RAW, NREP; 54 fits), NORM-only also reported:** consistent with the text
   ("more than one third of cold fits"). **Defect in what counts as reaching the cap:** the code counts `len(hist) >= 25`, which
   includes fits whose patience fired on the 25th epoch. Ruling: a fit reached the cap iff it ran 25 epochs **and** `best_epoch >= 25 -
   patience = 21`, meaning the cap ended it, not patience. The scorer computes this from the recorded `best_epoch` (F4). No Colab change
   is needed; the job's `cap_diagnostic.json` is descriptive only. Disclosure that does not change the verdict: per-arm counts are
   printed, and if all three fits of NORM_4, NORM_6 or NORM_8 hit the cap, the report says so beside the verdict.
2. **(i) and (ii) pass, (iii) fails → "INCONCLUSIVE (replicate guard)":** consistent. NOT RISING is defined as "(i) or (ii) fails", so
   it cannot be used, and RISING needs (iii). Consequences, fixed now: no SEQ_LEARNING_CURVE_2 draft is triggered; W_ALL stays the
   window; the question is not closed; the next step is Devin's; no replicate rerun under this id.
3. **Precedence VOID > INCONCLUSIVE(unfinished) > INVALID(budget) > INCONCLUSIVE(construction) > HURTS > RISING > NOT RISING:**
   consistent. Budget comes before construction because under-trained fits could fake a construction gap. Construction comes before
   HURTS because a construction-harmed NORM curve cannot be read as a window effect (A_raw is still reported, as the rule says).
   HURTS and RISING cannot both hold. **One defect:** if RAW_1 or RAW_4 is missing, the code says VOID (repro not computable). Ruling:
   a missing arm is INCONCLUSIVE (unfinished), per the kill criterion "not completed within the 240 T4-minute ceiling: INCONCLUSIVE"
   (F5).
4. **The curve is read only when every gate passes (and every arm finished, and the budget passed):** consistent with "nothing is read"
   and "no partial curve is called". Extending this to INVALID(budget) is right: that verdict sends the question to a new id, which must
   be drafted without seeing this curve. The implementation leaks, though (F2). Under INCONCLUSIVE(construction) the curve is assembled
   and reported descriptively, which the rule allows (A_raw is reported, no follow-up).
7. **Warm seed 1000 + seed:** consistent. For seeds 0-2 it is bit-identical to run #1's `1000 + k` (k = net index = seed). For the
   replicate seeds 3-5 it gives independent warm shuffles, which "a second independent 3-seed set" requires. `1000 + k` would have
   reused the main ensemble's warm shuffles.
8. **Elo and tokens restart per segment:** consistent. Tokens are rule text. Elo is not stated, but the RAW(4) anchor needs S2/S3 equal to
   run #1's per-era exports, and export_info shows 100% equality on every array including ctx (`elo_margin`). S1 Elo cold-starts at
   2015-16 opening night (disclosed; S1 only).
11. **Seed-noise formula:** consistent as a descriptive figure, with a labeling fix (F7). `sqrt(mean(d^2)/2)` over the three
    main-minus-replicate differences (N = 4, 6, 8) estimates the seed sd of one 3-seed ensemble's C(N), assuming the two sets have the
    same mean. It rests on 3 values, so its 95% range is about 0.6x-3.7x. The ddof=1 sd of the same three values measures how the
    difference varies across N; it is not the seed sd and must not be labeled so.

Further interpretations I found in the code, ruled consistent: the scored set is the 27,619 A0 rows of 2023-24 (A0 ∩ OOF, the same for
every arm, the same as run #1; the walk predicts all 28,197). Blocks are calendar months (9: Oct-Jun). Play-in and playoffs (game_id
prefix not "002") take A0 and form the postseason slice. Early-stopping validation is the same 3,286 rows for every N. The player
vocabulary is built from fit rows with >= 20 rows, so larger windows embed more players; that is part of the window effect, as in run #1.
The arm order (RAW_1, RAW_4, NORM_1..8, RAW_8, NREP) does not affect any verdict.

## Required fixes (none applied by me)

| id | severity | fix | why |
|---|---|---|---|
| F1 | **blocker (process)** | `require_review` / `stage`: (a) parse the `Verdict:` line and accept only `APPROVED` (this file's `APPROVED WITH FIXES` must not open the gate; the fix-check does); (b) parse `Reviewed-commit:` and refuse if `git diff --quiet <sha> HEAD --` the reviewed paths fails; (c) parse `Exports-manifest:` and refuse unless it equals the manifest of the staged exports. The scorer's `check_run` also compares `cfg["review_commit"]` with the review `require_review` returns. Negative tests for each: BLOCKED review, WITH FIXES review, code changed after review, rebuilt exports | The gate is verdict-blind and code-blind (top finding; T479) |
| F2 | major | `run()`: decide unfinished, budget and runtime first, from infos and exports. Then score only RAW_1 and RAW_4 on pts and evaluate the repro gate. Score NORM, NREP, RAW_8 and the other stats only when all gates pass. Test: a VOID run writes no NORM cache file | "Nothing is read"; a voided run is rerun under this id, so its curve must never exist |
| F3 | major | `check_run`: every info has `hidden == 64`, `cold_cfg == {25, 4, 1e-3, 1024}`, `warm_cfg == {3, 3e-4, 1024}`, seeds (0,1,2) for NORM/RAW and (3,4,5) for NREP, `rule_sha256` = frozen, `n_test == 28197`, `smoke == False`, and info `exports` = attested; any mismatch is VOID (runtime) | `NBA_HIDDEN` and restarts can mix configs silently; `config.json` is rewritten each session |
| F4 | minor | `budget_gate`: reached cap = `epochs_run == 25 and best_epoch >= 21` (ruling 1); print per-arm counts and the NORM-only share | ruling 1 |
| F5 | minor | `run()`: RAW_1 or RAW_4 missing → INCONCLUSIVE (unfinished), not VOID | ruling 3 |
| F6 | minor | read `norm_c` from the attested S3 npz (verified equal to the meta today) or check the meta parquet's sha against export_info | the meta that feeds the inversion is not sha-checked |
| F7 | minor | relabel the seed-noise figures (ruling 11) | mislabel |
| F8 | minor | `np.load(..., allow_pickle=False)` for OOF files | Colab artifacts need no pickles |
| F9 | minor, disclose | the ceiling sums `runtime_s` of completed arms only, so time spent in an interrupted arm is not counted; write a per-session wall-clock record, or disclose that the ceiling can be overrun by one partial arm | cost ceiling |

Not a fix, recorded: `SharedBoot` is a second CI implementation outside `nba/truth/` (`truth/bootstrap.py` does not exist yet). A test
pins it equal to `E.clustered`.

## Playbook check (neural sequence model, SEQ / ENSEMBLE E2, W_ALL)

1. **Information or capacity?** Data volume only. No new pre-tip information; this is the playbook's demanded learning curve.
2. **Enough data for this class?** That is the question. The windows reach 206,291 fit rows at N = 8 (vs 97,774 for W_ALL).
3. **Test valid?** Mostly yes: real tips for S2/S3; integer support for every arm; same rows, blend and comparators; paired
   game-clustered CI; reproduction anchors re-derived exactly (check 10). Limits: (a) the c-inversion is barely testable on 2023-24
   (c within 1% to 4% of 1). (b) There is no shuffled-input arm or permutation null. That is acceptable because every decision contrast
   is AVG(N) vs AVG(N') inside the same 50/50 mixture, so the mixture's spread advantage cancels. But a window gain through spread or
   calibration is still possible, and only the coverage table can show it. (c) The bootstrap omits training-seed noise, about
   0.001-0.002 in RT's rerun. That is the same size as B's floor, so the replicate guard is the only protection, and it rests on one
   replicate set.
4. **Lineage and guards:** E2 → E3 → W_ALL. Proxy-tip leak: carried (real tips; S1 has no tokens). Position survivorship NULL:
   removed in S1 (check 8). E3 era probe: replaced by the run-time era gate (check 6). T442 slices and coverage: carried as descriptive.
   2021-22 hourly reports: dropped, with their own channel.
5. **Scored against:** pts integer CRPS of the AVG blend vs FULL; nothing at a market line.
Class checks: window long enough? the question. Era channels on test rows equal 2022-23? yes (check 6). Lineage guards carried? yes.

## Power note and family

n = 27,619 player-games / 1,318 games (pts, 2023-24). Run #1's paired window contrast had a CI half-width of about 0.0024, so SE is about
0.0012 and the 80%-power MDE about 0.0034 for a 4-season step, roughly the rule's A floor of -0.003. The prior's expected A (-0.0025)
and B (-0.0006) are below the MDE: "NOT RISING" with a wide CI is the likely result and is a valid closure under this rule. Seed noise
adds to this. The decision family is m = 1 (an intersection), and the secondary family S is m = 6 under BH, as coded. No result exists,
so no multiplicity correction applies yet.

## Single biggest risk if this approval is wrong

The run starts, or is scored, on code or exports other than those reviewed here, because the gate accepts any committed review file
(F1). After that, the next risk is a voided run's NORM curve being seen through the cache and then "fixed" under the same id (F2).

## Not verified

- The builder's interpretations 5, 6, 9, 10, 12 (not provided to me).
- That the exports on disk were built by the code exactly as committed. They were built at 20:36 and committed at 20:50; I did not
  rebuild them (10-20 heavy minutes). The independent re-derivations (checks 4, 6, 8) and run #1 identity hold on the files as they are.
- The one-day lag check and any behaviour under real training (needs a fit).
- Colab runtime, GPU determinism, `NBA_PARQUET` and `NBA_ARTIFACT_ROOT` wiring on the VM.
- That `configs/mov_elo_tuned.yaml` was tuned without 2023-24 outcomes. It is the same config as run #1 and production; disclosed only.

## Drafted rows (not appended)

DECISIONS (draft): 2026-10-12, SEQ_LEARNING_CURVE loader review, APPROVED WITH FIXES (docs/reviews/seq_learning_curve_loader_2026-10-12.md).
What: interpretation rulings 1, 2, 3, 4, 7, 8 and 11 are fixed as stated in the review (cap = the cap ended the fit, best_epoch >= 21;
replicate-guard failure = INCONCLUSIVE with no follow-up; a missing reproduction arm = INCONCLUSIVE (unfinished); curve withheld under
VOID/unfinished/INVALID). Fixes F1-F5 are required before staging, plus an adversary fix-check with `Verdict: APPROVED`.
Why: the hard stop was verdict-blind and code-blind (T479 class). Reverse if: never for the rulings (they are frozen once a run
exists); the fix list can only grow at the fix-check.
