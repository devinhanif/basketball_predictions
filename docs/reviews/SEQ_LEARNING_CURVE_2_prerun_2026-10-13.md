# SEQ_LEARNING_CURVE_2: pre-run (pre-freeze) review, 2026-10-13

Scope: the DRAFT rule `docs/prereg/SEQ_LEARNING_CURVE_2.md` (uncommitted state at review time, last commit 514db21), the exporter `research/features/seq_learning_curve_2_export.py`, the scorer `research/eval/seq_learning_curve_2_eval.py`, the rule and review gate `research/eval/seq_learning_curve_2_rule.py`, the Colab job `research/colab/jobs/seq_learning_curve_2/`, and the tests `tests/research/seq_learning_curve_2/`. Nothing was fitted. Season 2025 was not loaded. Every DuckDB connection was `read_only=True`. Nothing under `nba/daily` or ops was touched.

This is not the loader review the rule requires (`seq_learning_curve_2_loader_<date>.md`, which comes after the freeze). The file name is outside `REVIEW_GLOB`, so `require_review` cannot see it.

## Verdict summary
The design is sound and the code mostly matches it. Three gaps in the rule text and the code block the freeze. All three are cheap to fix:
- the rule records the wrong data_version in six places;
- nothing refuses to run on a different data_version;
- the U(4) reproduction failure has two outcomes written into the text.

D8 (the Colab rate) is still Devin's.

## Checks

| # | check | command / evidence | result | pass |
|---|---|---|---|---|
| 1a | Decision words, floors, CI, multiplicity, slices fixed in text and code | rule lines 29-47 vs eval lines 76-81, 118-160, 229-234 | DATA/STEPS/MIXED and RISING_10/FLAT/HURTS/AMBIGUOUS match the text line for line. Floors are -0.003, STEPS point -0.0015, flat band 0.0015. Interval is 97.5% (Bonferroni, m = 2); BH applies over the 6 secondaries. Draws: 2,000 two-way (game_id seed 0 and per-arm seed multisets). Ties are broken in listed order (disclosed). | pass |
| 1b | Failed U(8) or failed FULL/A0 grids = VOID in the TEXT | rule line 45: "Reproduction and construction gates (voids the run, harness fixed, rule untouched)" covers U(8) and the grids. eval `gate_verdict` line 156 returns `VOID (gate)` | the text says it | pass |
| 1c | Failed U(4) = INCONCLUSIVE (construction) in the TEXT | Line 45 puts U(4) under "voids the run". Line 46 says INCONCLUSIVE "if ... after D4 changed position". Line 120 (kill criteria) says reproduction failure means "harness fixed ... or rule withdrawn". The eval disclosure (line 579) calls its INCONCLUSIVE "the reading that keeps the fallback sentence meaningful". | The text gives two outcomes, and the condition is not stated as met. **BLOCKING B3** | fail |
| 2a | M(N) holds warm steps equal across windows | job `walk` lines 584-594: M uses `steps_of(pool_count(meta, 4, start))` and `fit_steps`, drawing batches from the window-N pool by permutation sweeps. Seeds are `1000+sd` for U and M alike. `test_smoke_matched_steps...` asserts warm_steps M_8 == U_4 == M_10 at every refit and that M_8's pool is larger than U_4's. `test_fit_steps_equals_fit_bit_for_bit...` passes. | equal by construction and by test | pass |
| 2b | UP(4) is what the rule says (window-4 data, U(8)'s steps) | line 588 `pool_count(meta, 8, start)`; the test asserts UP_4 == U_8 steps and that n_fit(UP_4) == n_fit(U_4) | matches rule line 22 | pass |
| 3a | Planted future, exporter | `test_planted_future_later_games_change_no_earlier_row` [S1 2013, S3 2023]: earlier rows are bit-identical including norm_c, and later rows do change (so the test can fail) | pass. S2 is not parametrized (N4). | pass |
| 3b | As-of reports, real tips | S1: `avail` emptied and `tips=None` (has_report = 0). S2/S3 use `RT.tip_table` real schedules, inherited from T486 (SURVIVES on this point) | inherited, no change | pass |
| 3c | No 2025 row | SQL `max_season=hi <= 2023` plus `assert_cap` (exporter lines 70-74, 165, 223, 227); job `load_capped` refuses > 2023; scorer `oof_index` refuses any season other than 2023; `test_nothing_after_2023_can_be_loaded` | none possible | pass |
| 3d | Position from the sidecar, >1% refusal | `fill_position` (DB, then cache, then sidecar); `position_guard` per label <= 2021 raises above 1%; tests pass. Live history DB read-only: players_static has 1,316 non-empty positions (restored by 514db21) and the sidecar has 13 rows, all mapped to the NBA vocabulary (Guard/Forward/Center) | pass. The 13 hand fills are single positions where NBA uses hyphenated ones (N7). | pass |
| 3e | Era encoding risk (T483: W_ALL encodes era, AUC 0.984 beyond tokens) | 2013-15 carry the same S1 flag and all-zero rule flags as 2015-16. Test rows are gated equal to 2022-23's era vector (`era_runtime_gate`). | Not a leak: era is known before tip. It does bear on how E is read: FLAT or HURTS can be era dilution, not "data stops helping". The rule's playbook check does not cite T483 (N2). | pass (disclose) |
| 4 | Review gate: exact whole-line `Rule:`; a deleted or renamed latest review = BLOCKED | `parse_header` regex `^Rule:[ \t]*(.*?)[ \t]*$` with an exact equality check; lines 236-241 refuse when the latest blob is no longer present; lines 207-214 refuse a review edited after it was added. `test_rule.py` covers 8 cases: git rm and git mv, six wrong Rule forms, header rewrite, two Verdict lines, parent-glob isolation, a pre-freeze review, changed code. | `.venv/bin/python -m pytest -q tests/research/seq_learning_curve_2 -p no:cacheprovider --no-cov`: **62 passed, exit 0**. The first run with coverage on: 62 passed, exit 3 (pytest-cov "Can't combine statement coverage data with branch data", environmental) | pass |
| 5a | Gates measured on the data_version the run reads, and the rule records it | `gate_check_2.json` is stamped `c90139d87ded` (13:16) and shows D4 0 unknown played rows per season, 2013-2021. `docs/data_manifests/history_latest.json` = c90139d87ded. Rule line 3 says "gates re-measured ... on 4104818c6695". D2 (line 55), D3 (56), D6 (59) and D7 (15, 60) all say 4104818c6695. Line 50 says "re-measured on 4104818c6695, not carried". Line 57 still says "D4 is therefore OPEN until the archivist restores". | The text names the superseded version and contradicts itself. **BLOCKING B1** | fail |
| 5b | The run refuses a different data_version | `grep -n -i data_version` over the exporter, eval, rule and job files: no matches (grep exit 1) | No refusal and no stamp. The Exports-manifest pin binds the files, not the DB they came from. Positions were clobbered once already today, at 12:58. **BLOCKING B2** | fail |
| 6 | Cost vs `configs/cost.yaml` | units_remaining 50; every per-hour rate is null. Rule: about 130 T4 min (110-190), ceiling 240 T4 min. job.yaml does not pin the accelerator. | On a T4 (about 1.8 u/h, unverified) the ceiling is about 7 units. On an A100 (about 12-13 u/h) the ceiling is about 50 units, i.e. everything. D8 stays UNKNOWN (Devin). N1 | open (Devin) |

## Blocking (fix before the freeze commit)
- **B1. The rule records the wrong data_version.**
  - Where: `docs/prereg/SEQ_LEARNING_CURVE_2.md` line 3 (status), line 15 ("measured at the gate (D7) on data_version 4104818c6695"), line 50 (last sentence), lines 55/56/59 ("PASS on data_version 4104818c6695"), and line 57 (the "Re-check 13:00 ... D4 is therefore OPEN" sentence).
  - Fix: name `c90139d87ded` as the measured and required history data_version everywhere. State that D2/D3/D6/D7 are the `gate_check_2.json` values stamped c90139d87ded; re-check that the printed numbers equal that file. Move the 12:58 clobber and 13:00 FAIL into the audit list as history, and leave the D4 cell reading PASS on c90139d87ded. Also add the nba.duckdb data_version (S3 source) at export time.
- **B2. No data_version refusal.**
  - Where: `research/features/seq_learning_curve_2_export.py` `build()` (lines 272-308).
  - Fix: before loading, compute `nba.datamanifest.manifest.build_manifest(SEG_DB['S1'])["data_version"]` (read-only, recomputed, not the cached JSON). Raise unless it equals a module constant `HISTORY_DATA_VERSION = "c90139d87ded"` that is quoted in the rule. Write both history and nba.duckdb data_versions into `export_info.json`.
  - Further fixes: `seq_learning_curve_2_eval.check_run` must refuse when `export_info.json`'s history data_version differs. Add a test that a mismatched version raises.
- **B3. The U(4) reproduction failure has two outcomes in the text.**
  - Where: rule lines 45, 46 and 120.
  - Fix: line 45, replace "U(4) AVG - FULL within 0.003 ..." under "voids the run" with "U(8) within 0.004 ... and FULL/A0 grids to 1e-6 void the run (VOID (gate))". Make line 46 unconditional: "A U(4) AVG - FULL outside 0.003 of -0.0201 is INCONCLUSIVE (construction): D4 changed position for S2 (322 rows) and S1, so this condition holds; next step Devin's; it overrides line 45 and the kill criteria." Line 120: add "except U(4), see Fallback".
  - Then drop the "reading" disclosure (eval line 579) or reword it as the rule's text.

## Non-blocking
- **N1** (rule line 92, job.yaml): pin the runtime to T4. Add a units ceiling, for example "halt at 10 units or 240 T4 min, whichever first". As written, the 240-minute ceiling on an A100 spends all 50 units.
- **N2** (rule line 102, playbook check 3): cite T483. W_ALL encodes era beyond tokens, so E = C_M(10) - C_M(8) measures "more seasons including a distinct era". A FLAT or HURTS result does not separate dilution from no-information.
- **N3** (rule line 47 vs eval lines 480-491): team drop-one has point estimates only, but the rule says "n and 97.5% clustered CI each". Either say "team drop-one: point range" in the rule, or add CIs.
- **N4** (`tests/research/seq_learning_curve_2/test_export.py:228`): add `(2019, "S2")` to the planted-future parametrization, since S2 uses the history tip schedule.
- **N5**: the CI code is `P1.SharedBoot` plus local two-way draws, not `truth/bootstrap.py`. This is inherited. The local code is checked against a brute-force resample (`test_two_way_draws_equal_a_brute_force_resample`).
- **N6** (rule line 45, "harness fixed, rule untouched"): the reproduction gate computes U(4) and U(8) AVG - FULL, so after a VOID the analyst has seen A. State that a post-VOID rerun discloses this and may not change the warm-walk or blend code.
- **N7**: the 13 sidecar ids get a single position ("Forward") where NBA's own vocabulary would often say "Forward-Center". This affects 550 low-minute played rows. Disclose in the D4 cell.

## Playbook check (neural sequence model, E2 transformer, W_ALL lineage)
1. **Information or capacity?** Data volume and optimisation only; no new pre-tip information. The question is legitimate because T486 left data and steps unseparated.
2. **Enough data for this class?** Yes for the question. About 280k rows at N = 10, against a model whose cold validation loss still fell from 4 to 8 seasons.
3. **Test valid?**
   - Contrasts are paired on cold fits, rows are identical across arms (sha asserted), scoring is on the integer grid, and intervals include seed and game.
   - The step-match holds by construction and by test.
   - Floors sit at the stated MDE (about 0.0031-0.0034, n about 27.6k rows / 1,318 games), so AMBIGUOUS is a likely, valid outcome.
   - Missing: a data_version guard (B2).
4. **Lineage and inherited guards:** real tips, no 19:00 proxy, past-only normalizer, the era run-time gate and the position audit are carried. The new guard is the review lineage, which tests cover.
5. **Scored against:** FULL inside AVG; no market comparison.

Class checks:
- Window length is the question itself.
- The era vector on test rows is gated.
- Era encoding (T483) is not disclosed (N2).
- Seed variance is handled (6 seeds, two-way).

## Most likely failure mode if a later "DATA" claim is wrong
The exports are rebuilt from a history DB that has drifted from the one the gates measured. The 12:58 position clobber happened today. Nothing in the run would notice except the 1% position guard (B2).

## Not verified
- The exporter, scorer and job have never run on real data.
- I did not check that D2/D3/D7 numbers in the rule text equal `gate_check_2.json` field by field. I checked only the stamp and D4.
- I did not test the 0.75 matched-cost factor or runtime.
- The Colab rate (D8) is unread.

## Drafted rows (not appended)
- Ledger: none (pre-run review, no result).
- DECISIONS: none.

Verdict: BLOCKED
