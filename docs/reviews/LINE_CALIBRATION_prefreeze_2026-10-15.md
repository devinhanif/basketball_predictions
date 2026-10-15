# LINE_CALIBRATION pre-freeze review (adversary), 2026-10-15

Candidate: branch `origin/agent/modeler-line-calibration-freeze`, commit 4b55924, `docs/prereg/LINE_CALIBRATION.md`
(draft rev 2 plus the 10-14 disclosure). Harness: `research/eval/line_calibration_eval.py`, `research/eval/line_calibration_rule.py`
(both unchanged since 92069c5, 2026-10-12 16:51 CDT). Gate artifact: `reports/prereg_line_calibration/gate_draft.json`.
Model class: quantile and distribution layers, recalibration at the line (docs/MODEL_PLAYBOOK.md).
Seasons touched by this review: 2023 and 2024 only. No real 2024 label entered any CAL or C0 fit run by me. All
outcome-grouped audits used 2023 (a fit season). Season 2025 was never loaded. Every DuckDB file was left alone (the harness
opens none).

Verdict: BLOCKED

Both blockers are text or one-line fixes that can be done tonight. After they are applied and the file is re-hashed, a
diff-only re-check (no re-run) is enough to approve. Nothing found makes Stage 1 invalid or biased toward a pass. The problem
is what a pass is allowed to mean.

## Top finding

The draft's exposure argument rests on one sentence: "Stage 2 (season 2025) is the clean test ... Nothing from 2025 has been
seen" (LINE_CALIBRATION.md:226-227). It is false at the comparator level:

- T478 (SEQ_FULL_MIX_RT, the 2025 holdout touch) and its line reading T480 (`reports/t478_line_reading_2026-10-12.md`, commit
  c94cca4, 2026-10-12 19:58 CDT, three hours after rev 2) scored production A0's P(over) at **2025** Pinnacle and DraftKings
  T-60 lines.
- On pts at Pinnacle, A0 log loss was 0.7085 (n 14,620 lines, 1,291 games; over rate 0.488), above a coin flip. The decile slope
  of A0 was 0.16 at Pinnacle and 0.11 at DraftKings. The AVG-arm slopes were reb 0.48, ast 0.42 and fg3m 0.72.
- Those 2025 slopes nearly match the scratch's 2024 DraftKings slopes (0.16 / 0.45 / 0.41 / 0.69).

So on 2025, P1 (beat raw) and a SHRINKAGE_ONLY confirmation are close to foregone, and the per-stat shape that drives P2 is
known. The rule's own answer to the exposure question, "a Stage 1 pass only earns the Stage 2 touch", is not enough when
Stage 2 is itself partly seen.

## Answers to the eight questions

| # | question | result | pass |
|---|---|---|---|
| 1 | sha256 | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CALIBRATION.md \| shasum -a 256` gives `ba1ff39bf57410db657c5f82b3fc5da1096f4a2ed7c25ad682fc9ac316c47641` (exit 0); `rule.text_sha` uses the same bytes | PASS |
| 2 | harness implements the eight lines | terms, weights, z, P1-P7, verdicts and CI all match (details below). One contradiction between the served-object choice and the product wording (B2) | PASS with B2 |
| 3 | no 2025 path in select | `select` loads only FIT_SEASONS (2023, 2024) through `read_pairs` / `read_grid`, which raise on season > 2024 without `holdout_ok`. `load_real` asserts max season <= 2024. The pairs directory holds only pairs_2023/2024. `report` alone calls `require_ready` | PASS |
| 4 | planted future and missingness on fit rows | 162/162 feature-level plants leave every output unchanged (158 bitwise; 4 differ by 1.1e-16 with bitwise-equal coefficients). Labels: gate 54/54 identical, 54/54 positive control. NULL patterns: none (2023 table below) | PASS |
| 5 | P3 null: powered and as written | computed as written; powered (CAL-SH CI half-width about 0.0005 against about -0.0045 under the scratch-like truth); size held 4/4 under no information. But P3 is a slightly easier copy of P2 (E2) | PASS (note) |
| 6 | multiplicity P1-P7 | an intersection-union conjunction needs no correction for CALIBRATED. P5's four uncorrected harm tests make a false FAIL about 10% likely under a true zero (conservative). Per-stat BH m = 4 is secondary only | PASS |
| 7 | playbook check | below; the class check (reliability at the line, slices fixed in advance) is met; test validity is met for Stage 1 and overstated for Stage 2 (B1) | PASS with B1 |
| 8 | non-negotiable 8 | gate re-run on the real data, 15/15, identical values; four input sha256 match the declared lines | PASS |

## Q2 detail: harness against the eight lines (file:line in research/eval/line_calibration_eval.py)

- **Terms** `[1, x, z, bench, bench*x]`, x = logit(clip(p, 0.5/199)), z clipped to [-5, 5] with the spread floor 0.5,
  bench = starter10 < 0.5: `design()` 302-309. It takes no price argument.
- **z from the continuous grid**: `block_zq` 162-182 brackets zq_j from the stored counts; `attach_grid` 191-237 builds
  `q = max.acc(max(c + s zq, 0))` and interpolates positions 49.25 / 99 / 148.75 (`TAU_POS` 64). The gate re-run shows
  0 inconsistent blocks, widest needed bracket 0.04935, and 0 of 179,673 legs where the continuous P(over) differs from the
  served p_model.
- **Weights** `0.5 ** (age/365)`, age from the block start, with age > 0 asserted: `age_weights` 312-315. Applied to CAL, C0 and
  SH, never to scoring: `walk_forward` 369-381, `score_stage` 433.
- **Fit window** = same book and stat, `date < block start`, every loaded season: 356. The last fit row is at least 1 day
  before the block start in every Pinnacle 2024 block (attack output).
- **IRLS**, ridge 1.0 on the non-intercept terms, start 0, tolerance 1e-10: 318-333. Cross-checked against scipy BFGS on three
  real fit windows (synthetic labels): max coefficient difference 5e-6 to 1.1e-5, which is BFGS's own tolerance.
- **P1-P7**: `evaluate` 548-567 matches doc lines 40-46 exactly. That covers the floor -0.003 with CI upper < 0, CAL-C0 upper < 0,
  CAL - mean(SH) <= -2 sd with upper < 0, |mean(CAL - o)| <= 0.015, the per-stat CI lower > 0 check, starter and bench point
  estimates <= 0, and the DraftKings point < 0.
- **Verdicts**: 568-579 match doc lines 47-51. If C0 is chosen and C0 fails its own checks, the verdict is FAIL even when CAL
  passes P1 and P4-P7 (579). That is consistent with doc line 128 but not stated in line 5 of the eight lines (see E6).
- **CI**: `Boot` (research/eval/model_clv_eval.py:241-278) draws a multinomial game resample with `default_rng(0)`, B = 2,000,
  and takes the 2.5/97.5 percentiles. One matrix is used per (stage, book) (430). The date-clustered CI is built the same way
  (430, 458). The one-sided p is `(1 + #{boot(A0 - CAL) <= 0}) / 2001` (455-457, `_pack` 267). This matches doc lines 424-427.
- **P3 null sd**: `select` checks that the gate's re-measured sd equals the frozen `NULL_SD_PINNACLE_2024` to 1e-12 (959-960).
  The stage then recomputes the same seeds on the same labels, so the value used in `evaluate` (553) is the declared one.

## Check table (the six adversary checks, applied pre-result)

Commands run from a scratch directory whose `data/ops/model_clv_pairs` and `reports/odds_history` are symlinks to the main
checkout (read-only use), with `PYTHONPATH=<this worktree>`. Attack script:
`<scratchpad>/attack_lc.py {planted|missing|irls|power}` (not in the repo).

| check | command | n | result | pass |
|---|---|---|---|---|
| sha | `awk ... \| shasum -a 256` | rule text | ba1ff39b... = declared | PASS |
| gate re-run (NN8) | `python -W ignore -m research.eval.line_calibration_eval gate --allow-draft --doc <worktree doc>` | 179,673 legs; Pinnacle 2024 37,528 / 1,260 games | 15/15 PASS, exit 0; half-width 0.0010582; null sd 0.00018281177430901576; input sha256 identical | PASS |
| 1 shuffled labels | gate check 6 plus the synthetic NOINFO truth (constant 0.525), K = 50, 4 label seeds | 37,528 Pinnacle 2024 legs | P2 fails 4/4, P3 fails 4/4; CAL-C0 +0.00016 [0.0000, +0.00032], -0.00004 [-0.00027, +0.00019], +0.00028 [+0.00009, +0.00047], +0.00015 [0.0000, +0.00028] | PASS |
| 2 planted future, labels | gate check 4 | 2 books x 9 blocks x 3 arms | 54/54 identical; 54/54 positive control moved | PASS |
| 2 planted future, features | `attack_lc.py planted`: 500 copies per block with p = 0.99, line 0.5, median 40, label 1, dated on the block start, mid-block, +40 days | 162 probes | 158 bitwise identical; 4 (DK 2025-06, cal/shuffle, in-block plants) differ by at most 1.1e-16 with bitwise-equal coefficients and fit counts (matmul rounding from the larger scored matrix) | PASS |
| 2 lag one day | not applicable as written: no input has a time tail; every leg's lead is 64 min (q05 to q95, all four season-books) | 179,673 | the historical "T-60 is really T-180" skew (NEXT_SESSION) does not apply to these frames | PASS |
| 3 missingness | `attack_lc.py missing`, 2023 only, by over/under, minutes < 5, starter/bench | 88,393 legs (224 with minutes < 5) | starter10 NULL 0 in every group; z clipped 0; p at eps 2e-5 / 0; starter10 == 0 1.3% vs 1.5% (over/under). Spread floor 0.14% (over) vs 0.41% (under) and med = 0 0.002% vs 0.015% are model-side (low-volume bench players go under), knowable pre-tip | PASS |
| 4 knockout | not runnable pre-freeze without fitting CAL on real labels | n/a | becomes edit E4 (declared knockout secondaries) | n/a |
| 5 replication | 4 label seeds per scenario, same rows | 37,528 | verdicts are stable within scenario (below) | PASS |
| 6 integer support | all lines x.5; continuous and integer P(over) identical on 179,673 legs | 179,673 | no support artifact possible at x.5 | PASS |
| harness tests | `pytest -q tests/research/line_calibration --no-cov -p no:cacheprovider` | 19 tests | 19 passed, exit 0 (with coverage on, the same 19 pass and exit is 1 only from the project-wide 70% floor on a subset run) | PASS |
| lint | `ruff check` / `ruff format --check` / `mypy` on both harness files | 2 files | exit 0 / 0 / 0 | PASS |

### P2/P3 size and power (synthetic truths on the real 2024 row and game structure; Pinnacle K = 50, DK K = 0)

| truth | seeds | CAL-A0 | CAL-C0 | CAL-SH | null sd | verdicts |
|---|---|---|---|---|---|---|
| NOINFO: p = 0.525 | 0-3 | -0.037 to -0.042 | -0.00004 to +0.00028 (CI upper >= 0 in 4/4) | -0.00019 to +0.00011 (CI upper > 0 in 4/4) | 0.00008-0.00009 | SHRINKAGE_ONLY 4/4 (served C0, CAL, C0, C0) |
| SCRATCH: logit p = 0.012 + b_stat x, b = 0.16/0.45/0.41/0.69 | 0-3 | -0.0093 to -0.0118 | -0.0042 to -0.0055 (e.g. -0.00422 [-0.00515, -0.00319]) | -0.0044 to -0.0057 | 0.00018-0.00021 | CALIBRATED 4/4 |
| WEAK: logit p = -0.10 + 0.16 x | 0-3 | -0.0157 to -0.0186 | -0.00016 to -0.00088 | -0.00032 to -0.00103 | 0.00009-0.00011 | CALIBRATED 2/4, SHRINKAGE_ONLY 2/4 (P3 passes, P2 fails, in both) |

What this shows:
- **P3 holds its size.** It never passed under NOINFO.
- **P3 is a weaker copy of P2.** SH sits above C0 by +0.00013 to +0.00021, the cost of fitting 4 noise coefficients. So
  CAL-SH = CAL-C0 minus about 0.00015. In WEAK, P3 passed while P2 failed twice. The -2 sd bar (about 0.0004) never binds
  before "CI upper < 0" does. P2 is the binding test.
- **Under the scratch-like truth, Stage 1 is CALIBRATED with a wide margin.** CAL-C0 is about 10 CI half-widths below zero. The
  draft's line 224-225 ("P2 and P3 stay the informative Stage 1 tests") therefore overstates what is left to learn (E3).

## Exposure: biased, uninformative, or neither?

- **Not biased toward a pass.** `git log` shows the rule text above the marker changed after 92069c5 (10-12 16:51) only by the
  10-14 disclosure lines (`git diff 7ced3cb 4b55924 -- docs/prereg/LINE_CALIBRATION.md`: additions only, no metric, term, floor
  or verdict touched). Both harness files are unchanged since 92069c5. The scratch was committed at d2a4210, 10-14 23:34. So no
  design choice could have been tuned on the scratch.
- **The one selection effect is the decision to freeze now.** The draft sat unfrozen for two days and was revived after a
  favorable peek. That changes what a pass means, not the statistic.
- **Mostly uninformative on 2024.**
  - P1 is foregone: the gate's real SH-A0 on Pinnacle 2024 is -0.0078, and the scratch DraftKings slope map scored -0.0089.
  - P2/P3 are largely predictable from the same two numbers (implied CAL-SH roughly -0.001 to -0.003 if Pinnacle resembles
    DraftKings) and from my SCRATCH scenario.
  - A Stage 1 FAIL remains informative: it would say that the walk-forward map with z and role does worse than a one-split
    slope.
- **Is "a Stage 1 pass only earns the Stage 2 touch" enough?** It would be, if Stage 2 were clean. It is not (B1).
  - What is still unseen on 2025: any CAL or C0 fit, and the CAL-C0 contrast. That is the only part of Stage 2 that carries
    new evidence.
  - The clean test of "the model carries information at the line beyond the base rate" is the 2026-27 forward log.

## Blockers

**B1 (blocker). The draft says 2025 is unseen and calls Stage 2 "the clean test", but T478/T480 scored A0 at 2025 lines.**
- Where: LINE_CALIBRATION.md:226-227. The same claim is implied at :208-209 ("The clean evidence is Stage 2 and the live log")
  and in the table row at :283.
- Fix: replace :226-227 with this text. It is a disclosure only; no metric, floor, arm or verdict changes.
  > **Where the clean test is.** Season 2025 is partly seen at the comparator level. T478 (the SEQ_FULL_MIX_RT holdout touch) and
  > its line reading T480 (reports/t478_line_reading_2026-10-12.md, c94cca4, 2026-10-12 19:58 CDT, after revision 2) scored
  > production A0's P(over) at 2025 Pinnacle and DraftKings T-60 lines: pts Pinnacle A0 log loss 0.7085 (n 14,620, over rate
  > 0.488), A0 decile slope on pts 0.16 (Pinnacle) and 0.11 (DraftKings), and AVG-arm slopes reb 0.48, ast 0.42, fg3m 0.72. On
  > 2025, P1 and a SHRINKAGE_ONLY confirmation are therefore close to foregone, and the shape that drives P2 is known. No CAL
  > or C0 has been fit or scored on 2025, so Stage 2 is this map's first 2025 touch. It is not a clean test of the claim that
  > production carries information at the line. That clean test is the 2026-27 forward log. A Stage 1 verdict earns the
  > Stage 2 touch and nothing more. A Stage 2 CALIBRATED or SHRINKAGE_ONLY result supports a shadow period, not a promotion.
- Also change :208-209 to "The clean evidence is the 2026-27 live log" and the :283 row's right cell to "a partly exposed
  select stage, and a partly exposed report stage, being taken as confirmation".
- Add T480 to "Lineage" (:81-107). T480's last sentence named this rule as its follow-up.

**B2 (blocker). Under SHRINKAGE_ONLY the rule can serve CAL while telling Devin the model carries no information.**
- Where: the served object is "whichever of CAL and C0 has the lower pooled Pinnacle Stage 1 log loss"
  (LINE_CALIBRATION.md:128, :432; code `research/eval/line_calibration_eval.py:575`). The same section then says the proposal
  states "the model carries no information beyond the base rate" and that the budget tool must report no line edge (:131-133).
- What goes wrong: a served CAL is a non-constant probability built from the model's x. The budget tool would see "edges" from
  a map the rule has just said has no information. My NOINFO seed 1 (truth with zero information) served CAL. So did WEAK
  seeds 0 and 2.
- Fix (safe default): under SHRINKAGE_ONLY the served object is C0, always.
  - Doc :128: "The served object is C0. It must itself pass P1 and P4-P7."
  - Doc :432: "SHRINKAGE_ONLY served object: C0."
  - Code :575: `obj = "C0"`.
  - Add one test case to `test_verdict_classes`.
- Alternative (Devin's call): keep the lower-log-loss choice, delete the "no information" wording, and bar the budget tool from
  reading the served value as an edge.

## Edits (should be applied before the freeze; none changes Stage 1 validity)

- **E1 (major). The priors understate the exposure.** At LINE_CALIBRATION.md:114-117, add a line saying the 10-14 priors (in
  PREDICTIONS_LEDGER) supersede these, or remove these. Two different P(CALIBRATED) values in one frozen file is a
  contradiction.
- **E2 (minor). Say that P3 is about 0.00015 easier than P2 by construction.** In the SH row of the arms table (:236) or under
  "Pass rule details" (:243), add: "SH sits above C0 by about +0.00015 (the cost of fitting four noise coefficients), so P2 is
  the binding test and P3 adds only the permutation framing." No change to the bar.
- **E3 (major). The disclosure overstates what P2/P3 can still teach on 2024.** Replace :224-225 ("P2 ... and P3 ... stay the
  informative Stage 1 tests") with: "P2 and P3 are also largely predictable: the gate's real SH - A0 on Pinnacle 2024 (-0.0078)
  and the scratch's -0.0089 imply CAL - SH of about -0.001 to -0.003 if Pinnacle resembles DraftKings. Only a Stage 1 FAIL
  would be strong evidence."
- **E4 (major). A P2 pass cannot be credited to "the model's own P(over)".** The z term contains the line, a market
  quantity. Rewrite :125 as "does the map built from the model's distribution and the line add anything beyond the base rate?"
  Then declare, under "Secondary results" (:57-63, labels only, no pass), a knockout of CAL without z and CAL without x against
  C0 on Pinnacle, with the per-block coefficients already stored in `blocks_cal`. This is the playbook's "does the arm only
  re-express the base?" check, and my check 4. It cannot be run before the freeze without fitting CAL on real labels.
- **E5 (minor). The freeze does not pin the harness.** The sha covers the doc only. Have `select` and `report` write
  `git rev-parse <freeze>:research/eval/line_calibration_eval.py` (and `_rule.py`), compare them with HEAD's blobs, and refuse on
  a difference. The place is `research/eval/line_calibration_eval.py:948-965` and `:997`. No other prereg does this yet, so this
  is minor; it closes a gap the rule's integrity relies on.
- **E6 (minor). State the C0-fails path.** At LINE_CALIBRATION.md:49-50, add: "If the served object fails its own P1 or P4-P7,
  the verdict is FAIL." That is already what `evaluate` does (`:579`).
- **E7 (minor). Remove the dead constant.** `SLICE_MIN_N = 300` (`research/eval/line_calibration_eval.py:71`) is defined and
  never used. Remove it, or declare and use a minimum slice n.
- **E8 (minor). CI code location.** The CI code is `research/eval/model_clv_eval.Boot`, not `truth/bootstrap.py` (that file does
  not exist; `nba/truth/` holds metrics.py and walkforward.py). Acceptable as inherited from MODEL_CLV; recorded for the
  steward.

## Power and family note
- **Power.** On Pinnacle 2024 (37,528 legs, 1,260 games), the projected 95% half-width of CAL-A0 is 0.00106 against the 0.003
  floor. Contrasts between two maps are tighter: CAL-C0 and CAL-SH half-widths are 0.0002-0.0005. The smallest effect P2
  detects is about -0.0003 to -0.0005.
  - Per stat (secondary), the half-widths are pts 0.0018, reb 0.0026, ast 0.0025 and fg3m 0.0019. A floor-sized per-stat effect
    is detectable for pts and fg3m only.
- **Family.** The claim is one conjunction (P1-P7), primary book fixed to Pinnacle. No correction is needed. P5's four harm tests
  push toward FAIL. The per-stat secondaries use BH over 4. The family actually run on line calibration is wider:
  - CALIBRATED_GRID (T339): 3 maps x 8 stat-seasons, closed;
  - the POVER_BIAS scratch: one split, about 60 descriptive cells;
  - T480: descriptive, on 2025.
  None of these is a claim, so no correction applies to this rule's verdict. They bear on how much evidence it carries, which is
  why B1 and E3 exist.

## Playbook check (quantile and distribution layers; recalibration at the line)
1. **Information or capacity.** Neither. It re-expresses production's distribution and the line; no new pre-tip fact enters.
   The z term carries line (market) information (E4).
2. **Enough data for the class.** Yes. Five coefficients on at least 6,339 fit rows (effective 6,260) per block, with no
   identity blocks in either book. Per-slice calibration is correctly not attempted.
3. **Test validity.** Stage 1 is valid: real tip gating at 64 minutes on every leg, played legs only (as the books settle),
   as-of inputs (`starter10` from strictly prior played games, `context_residual.py:487`; production OOF month-block
   walk-forward, `odds_history_eval.py:328-361`), exact x.5 support, and a sized null. As evidence it is weak, because it is
   exposed. Stage 2 is not clean (B1).
4. **Lineage.** CALIBRATED_GRID (T339) follow-up. T477, T480 and the POVER_BIAS scratch are exposures; T480 is missing from the
   draft (B1). Inherited guards are carried forward; the shuffle guard is correctly re-sized.
5. **Scored against.** Log loss at the line against raw production, the base rate and shuffled maps, with the market as a
   reference only. Class check met: reliability in ten bins at the line, slices fixed in advance.

## Single most likely failure mode if a Stage 1 pass is wrong
The pass is the exposed result re-measured, not evidence. 2024's DraftKings slope map already showed -0.0089, and the shuffled
maps on real Pinnacle 2024 labels already showed -0.0078. A CALIBRATED verdict on 2024 is about what anyone would predict
tonight. If CAL's edge over C0 comes from z (line position), "the model carries information at the line" is the market
speaking through the line.

## What was not verified
- Any CAL or C0 result on real 2024 labels (deliberately not run; it would add to the exposure).
- That T478's A0 grid is byte-identical to `model_rows_2024`'s production grid extended to 2025. Both are described as
  production context_residual; I did not open T478's artifacts.
- Size of P3 beyond 4 seeds per scenario (the estimate is coarse; no false pass in 4).
- The Stage 2 exporter (it does not exist yet; the rule requires review before the holdout row).

## Drafted rows (not appended)
PREDICTIONS_LEDGER (adversary prior, written before any CAL fit on real labels; not blind, having seen the scratch, T480 and
the gate):
`| 2026-10-15 | LINE_CALIBRATION Stage 1 (select, 2024) verdict, adversary prior, written before any CAL fit on the frozen rule (not blind: POVER_BIAS scratch, T480, gate SH-A0 -0.0078 seen) | adversary: P(CALIBRATED) 0.75; P(SHRINKAGE_ONLY) 0.15; P(FAIL) 0.10. Reasoning: synthetic SCRATCH-like truth gives CALIBRATED 4/4 with CAL-C0 about -0.005; real SH-A0 -0.0078 and scratch DK -0.0089 imply CAL-SH about -0.001 to -0.003; main risks P6 bench slice and Pinnacle-DK differences. Expected CAL-C0 at Pinnacle -0.002 (80% -0.004 to +0.0002), n about 37,528 legs | docs/reviews/LINE_CALIBRATION_prefreeze_2026-10-15.md | pending | |`

DECISIONS: none (the freeze remains the main session's act once B1 and B2 are applied).

## Re-check (diff-only), 2026-10-15, revision 76f7907

Scope: `git diff 4b55924 76f7907` (the doc, both harness files, the test file, gate_draft.json and rehearsal/results.json). Checked
in a detached worktree at 76f7907; the data was not re-run beyond the modeler's gate.

Verdict: APPROVED

| check | command / evidence | result | pass |
|---|---|---|---|
| sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' LINE_CALIBRATION.md \| shasum -a 256` at 76f7907 | `0731f17dd65b6dfccbd60bb79c95ebbfeddc20063a891806025a004531401fc2`, equal to the declared value | PASS |
| B1 | doc :234-241, :215-216, :299, :93-94 | the replacement text is verbatim; :208-209 now says the clean evidence is the 2026-27 live log; the :283 row is amended; T480 is in Lineage | PASS |
| B2 | doc :50-52, :134-136, :451-452, :249; code `evaluate` :581 `obj = "C0"`; new test case in `test_verdict_classes` | C0 is always served under SHRINKAGE_ONLY; a served object that fails its own checks gives FAIL (E6 merged here) | PASS |
| E1-E3, E6-E8 | doc :118-119 (priors superseded), :257-258 (P2 binding), :231-233 (P2/P3 predictable), :443 (Boot); `SLICE_MIN_N` removed | as asked | PASS |
| knockout cannot move the main path | `walk_forward(..., cols=None)` builds the same X; the `cal` arm draws no random numbers; `Boot` holds a precomputed resample matrix, so extra `bs.mean` calls consume nothing. Rehearsal `results.json` at 4b55924 and at 76f7907, compared with `knockout_vs_c0` stripped: **0 differing keys**; verdict CALIBRATED / CAL both times; `d_cal_a0` identical | identical | PASS |
| ridge with fewer columns | `fit_logistic` builds `R = eye(k)` with `R[0,0] = 0`; column 0 stays the intercept in both knockouts ([0,1,3,4] and [0,2,3]) | correct | PASS |
| harness pin, logic | fake git runner returning the real doc (sha 0731f17d) and the real harness: accepted, blob sha256 `cf81dc9f...` (eval) and `6e4bd68e...` (rule). The same runner with either file changed by one line: refused with the file named | works | PASS |
| harness pin, real git | `rule.git("show", "HEAD:<file>") == (ROOT/<file>).read_text()` for both files | True, True (text-mode subprocess and read_text agree byte for byte) | PASS |
| report re-checks the pin | `report` compares `select.json["harness_sha256"]` with the current pin and refuses on a difference | present | PASS |
| tests / lint | `pytest -q tests/research/line_calibration --no-cov -p no:cacheprovider`; `ruff check`; `ruff format --check`; `mypy` (both harness files) | 20 passed, exit 0; 0; 0; 0 | PASS |

### What the freeze commit must contain
- `require_harness_pinned` reads the **tree** at the freeze commit (`git show <h>:<file>`). That tree must hold
  `docs/prereg/LINE_CALIBRATION.md` and both harness files exactly as at 76f7907. It does not matter whether the commit's own diff
  touches the harness files.
- If the freeze is made on `main` before this branch's harness changes are merged, the pin refuses at `select`. That failure is
  safe, but it means a wasted freeze. Commit the freeze on top of 76f7907, or merge first.
- The freeze subject must be exactly `prereg(LINE_CALIBRATION): 0731f17dd65b6dfccbd60bb79c95ebbfeddc20063a891806025a004531401fc2`,
  and the commit must stay reachable from HEAD where `select` runs. A squash merge that rewrites the subject makes `select`
  refuse.
- Nothing else is required. The data parquets and the null sd are pinned by the doc's own sha lines, which `select` checks.

### Notes (not blocking; apply before the freeze only if cheap, otherwise leave)
- **R1 (minor; corrects my own E4 premise). The knockouts cannot separate model from line.** x = logit P(Y > line) and
  z = (line - median) / spread are both positions of the line in the model's distribution, so they are near-collinear. On the
  synthetic rehearsal, without_z gives -0.01486 [-0.01669, -0.01311] and without_x gives -0.01487 [-0.01671, -0.01315] against C0
  (n 37,528), effectively the same number. The sentence at doc :66 ("They answer whether the map only re-expresses the base rate
  or the line") overstates what they can show. Suggested text: "x and z are both positions of the line in the model's
  distribution and are near-collinear, so these knockouts measure redundancy, not whether the model or the line carries the
  gain." This is a label-only secondary, so no verdict depends on it. If the text is changed, re-hash before the freeze.
- **R2 (minor). The pin does not cover the CI code.** `HARNESS_FILES` (`line_calibration_rule.py:84-87`) does not include
  `research/eval/model_clv_eval.py`, which holds `Boot` (the primary CI). Stage 1 runs tonight, so there is no practical risk for
  it. For Stage 2, add the file to the tuple **before** the freeze: adding it later changes the pin and makes `report` refuse
  against Stage 1's `select.json`. This is a code-only change; the doc sha is unaffected. Do not pin
  `nba/eval/context_residual_eval.py`: it supplies only the secondary BH, and unrelated edits to it would block the stage.
- **R3 (minor). The test name overclaims.** `test_harness_pin_refuses_a_changed_file` (tests/.../test_line_calibration.py:353)
  actually exercises the "not frozen" path, because the fake doc hashes to e3b0... The accept and changed-file paths were checked
  in this re-check but have no committed test. A fake runner that returns the real doc, as used here, would cover both.

No new contradiction found between the revised sections. The 10-12 "Not seen as of 10-12" line (:214) is explicitly superseded, and
"Unchanged" (:242) is still true: no metric, floor, arm, term or stage changed. The served-object change is disclosed as the main
session's default, with Devin able to switch before the freeze.
