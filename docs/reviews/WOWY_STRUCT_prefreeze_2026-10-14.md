# WOWY_STRUCT pre-freeze review, adversary, 2026-10-14

Reviewed: `docs/prereg/WOWY_STRUCT.md` on `origin/agent/modeler-wowy` at b5a5569 (draft, not frozen), with
`research/eval/wowy_struct_gate.py`, `research/eval/wowy_struct_null.py`, `nba/props/struct_alloc.py` and
`reports/prereg_wowy_redistribution/gate.json` (keys `struct_warm`, `struct_null`) from that branch. Context:
`docs/reviews/struct_leak_audit_2026-10-14.md` on `origin/agent/adversary-struct-leak` at 6d3a3bf (WOUNDED, no leak).
Line numbers below are lines of the draft at b5a5569.

Nothing here reads season 2025. The one attack run (G5 slice counts) built the warm frame with the branch's own
`warm_frame` (it raises on `season > 2024`; I asserted `games.season.max() <= 2024` as well). It used read-only DuckDB and
features only: no outcome of 2023 or 2024 was related to any arm.

**Verdict: APPROVED WITH EDITS.** The rule as written cannot be frozen because its G1 guard says three different
things in three places. That and five more edits (E1-E6) must land in the text, and the freeze hash must be taken on the
edited text. No edit needs a new measurement, and none uses a 2023 or 2024 outcome.

Verdict: APPROVED WITH EDITS

Top finding: **G1 is a flat `S+R minus P+R <= -0.003` in rule line 6 (line 11) and in the pass rule (line 53), but the
freeze-blocker text (line 89) sets per-cell thresholds of 0.0049 / 0.0063 / 0.003 written without a sign. A harness
author can implement either one.** For both pts cells, G1 (not the -0.005 primary floor) is the binding test, and the
power table does not size it.

## Model class and the claim
Class: gradient-boosted trees (production `context_residual` LightGBM stat heads, refit with extra columns), read through
the stored lower-tail mixture and scored by integer-support CRPS. Claim to be tested: S+R minus ROLE <= -0.005 with CI
upper < 0, BH q = 0.05 over m = 3 per season, in 2023 (select) and 2024 (report), with guards G1-G5.

## 1. Rule line 6 / G1: sign, scale, boundary

| item | finding |
|---|---|
| Sign | Correct where it is written with a sign: CRPS is lower-better, so `S+R minus P+R <= -t` means S+R beats the permuted arm by at least t. Line 89 gives t as positive magnitudes (0.0049 / 0.0063 / 0.003) and never writes them into lines 11 or 53, so the frozen text would carry -0.003 for every cell. |
| Provenance | Clean. `wowy_struct_null.py` line 55 filters `season in (2020, 2021, 2022)`; `struct_null.seasons_read = [2020, 2021, 2022]`. The CRPS scale it converts with (`cell_crps_level_gaussian_rule` 3.41-3.55 / 4.02-4.27 / 1.01-1.03) is 0.564 x RMSE of the 2020-22 ROLE-GBM residual, not a 2023/24 CRPS. **No G1 number came from the select or report season.** |
| Re-derived | From `gate.json` `struct_null`: pts leader out 2 x 0.5 x 0.13894% x 3.5461 = **0.004927** (D1 is the largest design); pts top 25% D1 2 x 0.5 x 0.14661% x 4.2669 = **0.006256**; ast leader out 2 sd = 0.001592, so max(0.003, 0.0016) = **0.003**. Arithmetic matches the draft. |
| Scale | The thresholds depend entirely on an unverified Gaussian conversion (relative CRPS gain ~ half the relative squared-error gain; CRPS ~ 0.564 x RMSE) from a point-residual GBM to a mixture-head CRPS. For pts (CRPS 3.4-4.3, near-Gaussian), that is plausible to within a factor of about 1.5. For ast (CRPS 1.0, zero-heavy counts), the conversion is weaker, but it does not bind there (see boundary). |
| Boundary | (i) **ast leader out sits on the floor of the max(): the threshold 0.003 is the floor, not a measurement** (the measured 2 sd is 0.0016). (ii) **pts leader out, 0.004927, is within 0.0001 of the primary floor 0.005**; the draft's "0.0049" rounds it toward the lenient side by 0.00003. (iii) pts top 25%, 0.0063, is stricter than the primary floor. |
| Binding | P+R is not a zero arm. Its null mean gain over ROLE is +0.15% to +0.35% relative squared error for pts, which is about 0.001-0.003 CRPS after conversion. If P+R gets its null mean in 2023/24, G1 implies S+R minus ROLE <= about -0.008 to -0.011 (pts leader out) and about -0.009 (pts top 25%), against expected gains at the plain kappa of -0.015 / -0.018 and -0.011 / -0.013. **G1, not the primary floor, is the pass line for both pts cells, and no power is stated for it.** For ast leader out the null mean is -0.29% to +0.30%, so G1 implies about -0.0045, which is looser than the primary. |
| Re-measurement | Line 89 says the thresholds "should be re-measured on the CRPS arms of the first real run". That run reads 2023, so any re-measurement must be descriptive only. The text says "stay as written", but "should be re-measured" next to it invites a post-result change (non-negotiable 4). |
| Seed | The P+R permutation seed is not stated anywhere (line 42 "seed 0" is the LightGBM seed). G1 is calibrated as 2 sd of single permutation draws, so the rule must fix the draw: K = 1 with a stated seed. |

What the rule must say before freezing (E1): see the edits list.

## 2. Non-negotiable 8: minimum-data gates against the real 2023 / 2024 rows

| gate | command / source | 2023 | 2024 | pass |
|---|---|---|---|---|
| Cell rows / games (sizing, `struct_warm.sizing`) | gate.json | pts leader 4,301 / 388; pts top 25% 3,560 / 811; ast leader 4,926 / 440 | 5,667 / 493; 4,472 / 903; 4,409 / 387 | PASS |
| Same cells re-derived (features only, `n_prior >= 5`) | `slices2.py` below, exit 0 | 4,316 / 388; 3,565 / 811; 4,942 / 440 | 5,678 / 493; 4,469 / 902; 4,414 / 387 | PASS (within 0.4% of the gate; the gate's row set is the stored-component keys) |
| (a) power >= 0.5 at the plain kappa, version B | gate.json `sizing` `plain_2020_21(reference).power_b` | 0.78 / 0.55 / 1.00 | 0.93 / 0.72 / 1.00 | PASS. **pts top 25% is on the boundary: 0.55 in 2023, and 0.44 under version A** |
| (a) as the committed artifact computes it | gate.json `struct_warm.cell_gate` | uses `beyond_role_2020_21(primary)` power; **all 8 cells pass** | | **CONTRADICTS the draft** (E3) |
| G5 slices n >= 300, within each cell | `slices2.py` | see the table below | | **Partly inert** (E4) |

G5 slice rows inside each cell (`n_prior >= 5`; bench and starter use as-of `last_starter`, because the draft does not
say which starter flag it means; "star out" is undefined in the draft, so I used a proxy, V_pts >= 20):

| cell, season | n_out_rot = 1 | n_out_rot >= 2 | playoffs | first 15 team games | starter / bench | star proxy |
|---|---|---|---|---|---|---|
| pts leader 2023 | 367 | 3,949 | **97** | 617 | 2,024 / 2,292 | 4,287 |
| pts leader 2024 | 731 | 4,947 | **86** | 939 | 2,597 / 3,081 | 5,638 |
| pts top25 2023 | **66** | 3,499 | **77** | 494 | 2,804 / 761 | 3,553 |
| pts top25 2024 | **75** | 4,394 | **21** | 640 | 3,189 / 1,280 | 4,458 |
| ast leader 2023 | 599 | 4,343 | **115** | 872 | 2,343 / 2,599 | 4,461 |
| ast leader 2024 | 326 | 4,088 | **27** | 854 | 1,984 / 2,430 | 4,202 |

The playoffs slice never reaches 300 in any cell or season. `n_out_rot = 1` never does for pts top 25% and sits at
326-367 for two cell-seasons. The star-out and `n_out_rot >= 2` slices are 90-99% of their cells, so they duplicate
the cell. The draft never says what happens below n = 300.

Command (exit 0, 7.6 s, cached frame):
`cd /Users/devin/Downloads/nba-prediction && NBA_ROOT=/Users/devin/Downloads/nba-prediction PYTHONPATH=<scratch>/tree nice -n 10 .venv/bin/python -W ignore <scratch>/attack/slices2.py`
(`<scratch>/tree` = `git archive origin/agent/modeler-wowy nba research configs`; the script stays in the adversary
scratchpad.)

Data-capture audit (line 57): it lists every season held (history 2013-2021 box, 2019-21 reports, `nba.duckdb`
2022-24) and names the repairable gap (F1). No gate is declared failed on data, so the comprehensive-capture clause does
not trigger.

## 3. Holdout
No input needed season 2025. `prereg_gates_common.MAX_SEASON = 2024`. `warm_frame` and the stored-component loader raise
on `season > 2024`. The box query is `WHERE g.season <= 2024`. The planted-future cut-off 2025-01-15 is a date inside
season 2024 (2024-25). `HOLDOUT_ACCESS_LOG.md` on main has no WOWY or STRUCT row, which is correct: the draft schedules
no touch. **Select/report-season disclosure (minor):** version A sizing (`wowy_struct_gate.py`, the
`d_ = PR.crps_int(q1, y) - PR.crps_int(q0, y)` block) reads 2023/24 outcomes (`row_y`) to get the second moments of the
stored FULL-minus-A0 delta. That is a different contrast and does not feed gate (a), but line 60 quotes its powers
without saying they read 2023/24 outcomes (E6).

## 4. Playbook check (gradient-boosted trees)
1. **Information or capacity:** information (report OUT set x as-of pool). Capacity is controlled by P+R and ROLE. PASS.
2. **Enough data:** the 2023 refit trains on about one season (window from 2022-10). Per the playbook, a 2023-only
   fail is weak evidence; the rule requires both seasons, so a 2023 fail closes the cell on weak evidence. The draft
   states this. Accepted as Devin's trade-off, noted.
3. **Test valid:** warm comparator (B1 fixed), real tips, integer support for every arm, NULL rule unit-tested, planted
   future 7.1e-15. G1 is sized from a permutation null (PASS) but in unverified units (section 1). **Gap: the playbook's
   fair test for trees names a past-only placebo.** The audit's placebo OUT set is not an arm or a guard in the rule, and
   the draft does not say why P+R replaces it (E5). Shuffled-label guard dropped as invalid for a mixture contrast:
   accepted.
4. **Lineage:** T047, `vac_*`, TEAM_BUDGET, F11, WOWY_REDISTRIBUTION, MINUTES_V2, RECENT_ROLE_FULL (T467-T473 passed
   2023-24 and failed 2025) are all named. PASS.
5. **Scored against:** CRPS vs ROLE and A0_warm, explicitly not the market line (T477). PASS.

Class checks: missingness beyond the as-of cause (B4, unit test `test_attach_struct_null_rule_and_role_share`); planted
future (pass); does the arm only re-express the base? (V is `vac_*`; the ROLE arm handles the share). Branch tests:
`tests/props/test_struct_alloc.py` and `tests/props/test_wowy.py` gave 19 passed, **exit 1** (run from a scratch copy
of the branch, unpiped). The non-zero exit is most likely the repo coverage threshold on a partial run; I did not
confirm that.

## 5. Multiplicity, CI unit, and whether "keep ast leader-out" was pre-specified
- **BH family declared:** yes, m = 3 per season (line 10, line 32), with a sensitivity at m = 8. **Not specified: the
  p-value** fed to BH (one- or two-sided; bootstrap share of draws >= 0, or normal approximation) (E2). The family
  actually examined on the way here is larger: 8 cells x 3 designs x {STRUCT, beyond-role, role, permuted}, plus PROP,
  the WOWY columns and versions. The audit counted m >= 112 for the earlier gate. All of these are dev-season (2020-22)
  looks that select cells; none is a 2023/24 contrast. So m = 3 is the right family for the confirmatory test, provided
  the forking-path list is complete (see the next bullet).
- **CI unit:** `game_id`, 2000 draws, seed 0, date clustering reported. Leader-out cells average about 11 rows per game,
  so game clustering is necessary. **Not pinned: "the house bootstrap once `nba/truth/bootstrap.py` exists" (line 9).**
  The file does not exist on main (`nba/truth/` has `__init__.py`, `metrics.py`, `walkforward.py`). Switching CI code
  between freeze and run would be a rule change (E2). Also unstated: which clustering governs if the date-clustered CI
  crosses 0.
- **ast leader-out, pre-specified or chosen after?** Chosen after, on dev seasons. Criteria (a)-(c) (line 61) first
  appear in commit 40465fe (18:59), together with the 2020-22 numbers they judge (gate `measured_at` 18:53). No earlier
  commit carries them. The criterion that keeps the cell, "beyond-role positive in all three designs, CI excludes 0 in
  D1", passes ast leader out on D2a +0.31 [-0.34, 0.94] and drops the three other ast cells on D2a negatives. Criterion
  (c) passes all 8 cells and so selects nothing. Criterion (a) was switched by hand from the artifact's beyond-role kappa
  (all 8 pass) to the plain kappa (5 drop). That switch is the conservative direction. **This is acceptable because
  every input was 2020-22 or outcome-free 2023/24 forecasts, and the later permutation null supports the cell
  independently (ast D2a z 6.0 vs the player-season null).** But it is a forking path and must be listed as one (E3).
  Dropping or adding a cell now, after the permutation null, would be another fork: keep m = 3 as drafted.
- **Column ambiguity (major):** rule line 3 (line 8) and the arms table (lines 47-49) say `struct_d_s` for every
  stat. Line 38 defines `struct_d_pts` as the RATE version and `struct_u_pts` as USAGE. For pts the claim and the
  top-25% cell are USAGE (line 6, line 10, `chosen["pts"] = struct_u_pts` in gate.json). A harness built from the arms
  table would fit RATE for pts, which the audit measured at about half the warm gain (E1).

## The single most likely failure mode if a pass is wrong
pts top 25% passes 2023 or 2024 on G1 by an unverified unit conversion. The draft's G1 threshold can be off by about
1.5x in either direction because it comes from a squared-error null, not a CRPS null. The cell is already the weakest
(power 0.55 / 0.44, permutation D2a z 1.6). A pass there should be treated as a candidate whose guard was mis-sized
until the CRPS-arm null is reported descriptively beside it.

## Required edits before the freeze hash (exact text; the modeler applies, not the adversary)
- **E1 (blocker, lines 8, 11, 47-49, 53, 89).** (i) Line 11, replace "(G1) P+R: S+R minus P+R <= -0.003" with "(G1)
  S+R minus P+R <= -t_c, per cell and per season, with t_c frozen from the 2020-22 permutation null (`struct_null`):
  pts leader out 0.0050, pts top 25% fixed 0.0063, ast leader out 0.0030 (floor-bound; measured 2 sd 0.0016); values
  rounded toward stricter". (ii) Line 53, same text in place of "G1 (S+R minus P+R <= -0.003)". (iii) Line 89, replace
  "it should be re-measured on the CRPS arms of the first real run" with "the CRPS-arm spread of P+R is not measured
  in the confirmatory run; if reported later, it is descriptive and never changes t_c". (iv) Lines 8 and 47-49, name
  the columns: "`struct_u_pts` for pts, `struct_d_ast` for ast" in place of `struct_d_s`. (v) Line 42: "P+R permutation:
  one draw, numpy default_rng(0) per stat and season, train and test permuted separately".
- **E2 (major, lines 9, 10, 53).** Pin the CI and p-value: "game-clustered percentile bootstrap, 2000 draws, seed 0,
  with the code at `<path>::<function>` as of the freeze commit (the house bootstrap is used only if it exists at the
  freeze commit); one-sided p = (1 + #draws with delta >= 0) / (1 + 2000); game clustering governs, date clustering
  reported only".
- **E3 (major, lines 30, 60-62).** Add a forking-path item 7: "cell-gate criteria (a)-(c) written after the 2020-22 GBM
  numbers (commit 40465fe); criterion (a) switched from the artifact's beyond-role kappa (`struct_warm.cell_gate`: all 8
  pass) to the plain kappa (3 pass); criterion (c) selects nothing". Either fix `wowy_struct_gate.py` so that
  `cell_gate` computes (a) at the plain kappa and (b), or rename the key to `cell_gate_beyond_role_superseded`. Freeze
  blocker 3 ("reproduce today's gate numbers") must reproduce the line-63 table, not a key that contradicts it.
- **E4 (major, line 11 G5).** Define the slices and the small-n rule: "bench / starter = as-of `last_starter`
  (`<column>`); star out = `<definition>`; first 15 team games = `team_game_no <= 15`; playoffs = game_id prefix 004; a
  slice with n < 300 in a cell-season is reported and not gated". With the counts above, playoffs is never gated and
  `n_out_rot = 1` is not gated for pts top 25%. Say so in the text.
- **E5 (minor, lines 11 / 21).** Either add the placebo-OUT-set arm as a reported diagnostic, or add one sentence on why
  P+R and ROLE replace the playbook's past-only placebo for this class.
- **E6 (minor, line 60).** After "version A", add "(reads 2023/24 outcomes of the stored FULL-minus-A0 delta, second
  moments only; not used by gate (a))". Also add a G1 power note: "G1 binds before the primary floor for both pts cells
  (implied S+R minus ROLE about -0.008 to -0.011); its power is not sized".
- **Also unstated (line 97):** G2 and G3 say which arm is compared ("S+R vs A0_warm"?); the A0 row set for 2024 given the
  79.9% stored-vs-rebuild difference.

## What was not verified
- Not re-run: the gate's GBM numbers, the K = 30 null, or the cold/warm reproduction. I re-derived the G1 arithmetic and
  the cell sizes from the artifact and the warm frame only.
- The Gaussian squared-error-to-CRPS conversion. It cannot be checked without a CRPS arm on some season. A 2022 CRPS run
  of ROLE and P+R (dev season, no holdout) would check it before the freeze, if Devin wants to pay a few minutes for it.
- That the non-zero pytest exit is the coverage threshold.
- The harness (`wowy_struct_eval.py`), which does not exist yet; E1 (iv) is what keeps it honest.

## Power note and corrected family
Family for the confirmatory test: m = 3 (pts leader out, pts top 25% fixed, ast leader out) per season, BH q = 0.05.
Dev-season looks on the way here: at least 112 contrasts (audit) plus the 8-cell x 3-design gate and the 9 null
designs, all on 2020-22. Power for the primary at the plain kappa (version B): 0.78 / 0.93, 0.55 / 0.72, 1.00 / 1.00.
Joint two-season power for pts top 25% is about 0.40 before G1. A null in that cell would read "underpowered, cannot
conclude", not "no effect".

## Drafted rows (for the main session; not appended)
DECISIONS: none. TEST_LEDGER (descriptive):
`| T4xx | 2026-10-14 | WOWY_STRUCT pre-freeze review (docs/reviews/WOWY_STRUCT_prefreeze_2026-10-14.md), draft b5a5569, no 2023-25 outcome read | APPROVED WITH EDITS. G1 stated three ways (flat -0.003 in rule line 6 and the pass rule; per-cell 0.0049 / 0.0063 / 0.003 in freeze blocker 1). Thresholds come from the 2020-22 null (clean), but via an unverified squared-error-to-CRPS conversion. ast leader-out is floor-bound; pts leader-out is 0.0001 below the primary floor. G1 binds before the primary for both pts cells. Cells 2023/24 re-derived from the warm frame: 4,316 / 5,678; 3,565 / 4,469; 4,942 / 4,414 rows. Playoffs slice < 300 in every cell-season. Cell-gate criteria written after the 2020-22 numbers; the artifact's cell_gate passes all 8 cells. Arms table names struct_d_pts (RATE) where the claim is USAGE. Edits E1-E6. |`
