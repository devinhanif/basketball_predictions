# ALT_RUNG_CAL pre-freeze review (adversary, 2026-10-15)

Verdict: BLOCKED

Reviewed: `docs/prereg/ALT_RUNG_CAL.md` (draft rev 1, merged to main at 3c6ce08), `research/eval/alt_rung_cal_gate.py`,
`reports/prereg_alt_rung_cal/gate.json`, the DECISIONS row of 2026-10-15 00:58 CDT (`docs/DECISIONS.md:1234-1236`), and the
inherited machinery (`research/eval/line_calibration_eval.py`, `research/eval/line_calibration_rule.py`,
`research/eval/alt_ladder_hist_gate.py`). Main was at 29d124a at review time; none of the reviewed files changed after cc3ac06.
Seasons 2023 and 2024 only. No 2025 row was read. No real rung label was read: every simulation below uses synthetic labels.
Model class: a logistic recalibration layer over a stored gradient-boosted forecast (playbook classes "Quantile and distribution
layers" and "Linear and logistic models").

**Short reading.** The rule hashes to the expected sha, the gate re-runs bit-for-bit, and the rung frame is clean (no
post-snapshot line, real tips, the defect window applied as declared). Two things block the freeze, and both are edit-sized:
(B1) the comparator C0, as specified, is a step table that pays a built-in log-loss cost of +0.00055 per rung even with
unlimited data. That cost alone is 5.5 times the prior CAL-rung − C0 effect, so P3 ("the map adds to a table") and the
RUNG_REFIT_FIXES / TABLE_FIXES split could come out in the map's favour by construction. (B2) The harness does not exist, and
the doc leaves about a dozen choices to whoever writes it after the freeze. The rule cannot be run as frozen until those
choices are pinned. Beyond those: the P4 shuffle null cannot fail (M1), the C0 P1 contrast sits outside the declared
multiplicity family (M2), and the DECISIONS record contradicts the doc on FanDuel (M3).

## Per-check table

| # | check | command (scratch scripts in `$S` = `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/763dc211-fdb9-4487-b696-02f28aa88347/scratchpad`) | n | result | pass |
|---|---|---|---|---|---|
| 1 | rule sha | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/ALT_RUNG_CAL.md \| shasum -a 256` (exit 0) | 1 file | `0c5a15140696f9323f5b926ada69d977f92248095a32a50b1c0aff3bfb91d774`, as expected (the whole-file sha is 4bbd86e9; that is not the rule) | PASS |
| 2 | NN8: gate re-run on real data | `.venv/bin/python -W ignore -m research.eval.alt_rung_cal_gate --data-root /Users/devin/Downloads/nba-prediction --out $S/gate_rerun.json` (exit 0, 16.7 s, 0.93 GB) then a key-by-key diff against the committed gate.json | 4,721 keys | 2 keys differ (`date_cdt`, `cost/wall_seconds`). All input sha256s and all counts are equal. 13 of 14 checks pass; the failure is the select-2023 cell test, as the doc states | PASS |
| 3 | planted future: rung frame | `$S/build_frame.py` (exit 0) rebuilds the DK+FD primary-arm frame with the gate's own loaders, without y/over/price, followed by the inline checks shown in the run log | 407,015 rungs (DK 162,745, FD 244,270; equals the gate) | 0 rungs with snapshot ≥ tip; lead 64.2 to 64.4 min. 113,953 rungs carry `last_update` after `snapshot_ts` by at most 1.4 min, so every quote is still ≥ 62.8 min before the real tip (inside the cutoff, and no price enters any term). The main line comes from the same `t60` snapshot (`alt_ladder_hist_gate.py:123-152`). The lag-a-day test does not apply: every input is read at a single snapshot | PASS |
| 4 | real tip, not proxy | same run | 2,224 games | 28 distinct tip times; 27.6% at 23:00 or 00:00 UTC (normal 7 pm ET tips) | PASS |
| 5 | defect window | same run | DK rungs on 2023-12-07..19 | DK pts 0 (excluded). DK ast 760 and reb 27 are kept under the frozen ALT_LADDER_HIST rule. They sit in identity months and are never scored, but they are fit rows for CAL-rung and C0 from 2024-02 onward | PASS (disclose, m1) |
| 6 | missingness: no main line | same run | DK 162,745 | DK 2024 bench 15.5% vs starter 3.3%; DK 2023 0.8%; 2025-04 14.2%; by bin 6.4% (55) to 1.6% (95). Whether a main market exists is known at the snapshot, so this is not a leak. It does change who gets scored: 2024 bench rows are under-represented | PASS (disclose, m2) |
| 7 | integer support | gate + frame | all | Binary events at half-point rungs; continuous P equals the served P at every rung (0 mismatches); every main point is a half point; the `r` (±8) and `zm` (±5) clips never bind (ranges −2.46..0.92 and −1.09..1.53) | PASS |
| 8 | P2 / P8 cannot false-fail a perfect arm | `$S/sim_guards.py` part A (exit 0): X = oracle truth T (A0 + T491 gap), one uniform per unit, R = 400 | 134,462 scored; 2023 36,145 | Per-bin gap sd: pooled 0.0025-0.0033, 2023 0.0046-0.0075, 2024 0.0028-0.0036. P(any bin \|gap\| > 0.03) = 0.000 in every season, including with ×sqrt(1.51). Pooled 95% hw by bin: game 0.0049-0.0061, date 0.0047-0.0066 | PASS (not over-firing) |
| 9 | P4 shuffle null is informative | `$S/sim_guards.py` part B (exit 0): the world where A0 is calibrated (Y drawn from each unit's own grid), CAL-rung fit on true labels vs the unit-permuted SH, K = 5, 8 blocks (4 stats × 2024-03, 2025-01) | 1,445-6,782 scored per block | CAL-rung − A0 = −0.00003 to +0.00115 (the map adds nothing, as it should). CAL-rung − SH = **−0.015 to −0.057** (30 to 110 times the 0.0005 bar; SH sd 0.0007-0.006). P4 passes for an arm that carries no information | **FAIL (inert)** M1 |
| 10 | C0 is a fair, strong baseline | `$S/c0_resolution.py` (exit 0): the expected log-loss cost of the ideal (infinite-data) C0 against the truth, label-free | 134,462 | Truth = oracle: A0 0.00421, **C0 step 0.00055**, C0 shift 0.00000. By bin, step table: 0.00043 / 0.00047 / **0.00056** / 0.00070 / 0.00083 (bins 55-95). Truth = A0 calibrated: step 0.00064 (bin 95: 0.00163), shift 0 | **FAIL** B1 |
| 11 | multiplicity family matches the words | read of `ALT_RUNG_CAL.md:33,45,48` | 3 P1 contrasts can issue a word | The family is declared as m = 2 (CAL-main, CAL-rung), but C0 also issues TABLE_FIXES through its own P1 at p < 0.025. The family actually run is 3 | FAIL M2 |
| 12 | record matches the rule | `docs/DECISIONS.md:1236` vs `ALT_RUNG_CAL.md:39,43-45,164` | 1 | DECISIONS records "FanDuel label only". The doc's default makes P7 a **pass** check inside every word | FAIL M3 |
| 13 | exposure stated in full | `reports/prereg_line_calibration/select.json` (counted the `"beta"` keys only; never opened the values) | 72 stored betas | CAL-main for the 9 DK 2024-season blocks × 4 stats is the T493 fit itself (same legs, same machinery), and its coefficients are on disk. With T491's published per-season hit rates, CAL-main's 2024 per-bin gaps can be computed without a new label. Not disclosed | FAIL (disclosure) M4 |
| 14 | departure from select/report is honest | items 2 and 8 | n/a | The 2023 cells fail only on the inherited conservative bound (all 74 rungs of a game perfectly correlated). Under synthetic unit coupling the 2023 sd is 0.0046-0.0075 (hw about 0.009-0.015; ×sqrt(1.51) about 0.011-0.018), which would pass 0.03. The departure is still right, but on reasons (c) and (d) (`:55`), not (a) | WOUNDED M5 |
| 15 | holdout hygiene | read `docs/HOLDOUT_ACCESS_LOG.md:86` | n/a | The modeler's 2025 counts query is disclosed and logged. This review read no 2025 data | PASS |
| 16 | rule integrity | `git log` | n/a | No prereg commit yet. Draft a310c92; gate.json is label-free; there are no result artifacts | PASS (pre-freeze) |
| 17 | knockout / replication / shuffled labels on results | n/a | n/a | Nothing has been scored. These are declared as labels (`:50`) and P7/P8 | n/a |

## Findings (severity-ranked; each names the line and the fix)

**B1 (blocker): C0 is a step table, which biases P3, P6 and the word split in the map's favour.** `ALT_RUNG_CAL.md:24` ("each
bin's time-weighted hit rate") and `:152`. Replacing A0 with a single constant inside each 0.05 bin throws away A0's
resolution within the bin. Even with unlimited fit data and the oracle truth, this costs +0.00055 per rung pooled, which is
13% of the oracle gain and 5.5 times the prior CAL-rung − C0 effect of −0.0001 (`:62`). In bin 75 the structural cost is
+0.00056 against an oracle gain of 0.00007, so C0 sits near the P6 bar of +0.001 before any estimation noise. The result is
that P3 can pass, and RUNG_REFIT_FIXES can be issued over TABLE_FIXES, because the table is handicapped and not because the
map knows more. This is the "map credited for what a lookup does" failure that `:129` says the rule guards against.
**Fix:** C0 = clip(A0 + g, EPS, 1−EPS), where g is the bin's time-weighted (hit rate − mean A0) over the fit rungs of the
(stat, block, 0.05 bin), and g = 0 below 100 fit rungs. This shift table is still a function of P alone and has zero
structural cost (check 10). Keep the step table only as a label if wanted.

**B2 (blocker): the harness is not pinned, and the doc leaves choices to its author.** `ALT_RUNG_CAL.md:7` only requires the
harness to refuse without the prereg commit, reuse the LC fit code, and assert season ≤ 2024. LINE_CALIBRATION froze with its
harness in the tree and pinned (`line_calibration_rule.py:84-104`, review 5beab51). This rule freezes the doc alone, so every
item below would otherwise be decided after the freeze. **Fix:** add a section "Harness contract (pinned)" with these items:
1. **Freeze and pin.** Write `research/eval/alt_rung_cal_eval.py` and `alt_rung_cal_rule.py` (a copy of
   `line_calibration_rule.py` with ID and DOC changed). Run an end-to-end **synthetic rehearsal**: labels drawn as U_unit < T
   with T = oracle and with T = A0, and every word computed. Then send the harness to the adversary and commit
   `harness(ALT_RUNG_CAL): <blob sha256s>` **before any real rung label is read**. `require_harness_pinned` must refuse if the
   files on disk differ from that commit.
2. **Inputs.** Write the full 64-hex sha256 of the six gate inputs into the rule text (`:82` gives 12-hex prefixes only; LC
   pinned `PAIRS_SHA256_*` lines, `LINE_CALIBRATION.md:199-200`). `nba.duckdb` changes daily, so pin the derived set instead:
   window games 39 (2023) and 62 (2024), plus a sha256 of the sorted window `game_id` list. The harness refuses on any mismatch.
3. **Labels.** Y = `reports/odds_history/model_rows_{2023,2024}.parquet` column `y`; assert that it is integer-valued and
   non-null on every scored unit. Hit = Y ≥ point + 0.5.
4. **Count equalities, asserted before any label is read.** DK S = 162,745. DK scored = 134,462, with bins 38,122 / 31,992 /
   27,930 / 24,325 / 12,093. Drops: 21,507 identity, 6,776 no main line. 64,984 units. The identity-block list must equal
   gate.json. FD scored = 221,776 with 5 identity blocks of 64.
5. **CAL-main reproduction.** For every DK block of season 2024, the CAL-main coefficients must equal the DK `beta` vectors
   in `reports/prereg_line_calibration/select.json` (T493) to 1e-9. This proves "as is". Blocks in season 2023 are new fits
   from the same functions (`lc.design`, `lc.age_weights`, `lc.fit_logistic`). `lc.walk_forward` cannot apply a leg fit to
   rungs, so name the functions the harness calls.
6. **Fit rows.** CAL-rung and C0 fit on S rows that have a DK main line, same book and stat, `game_date` before the block
   start, with no season restriction other than ≤ 2024. This includes rungs in the identity months and the 787 ast/reb rungs
   on the defect dates (state it). CAL-main fits on DK `t60` MODEL_CLV legs exactly as in LC, window games included.
7. **C0 / BASE details.** Pin the B1 shift form, the clip value (EPS = 0.5/199), whether the 100-rung threshold counts raw
   rows (recommended: raw), and the bin edges with tolerance 1e-12.
8. **SH for CAL-main** (`:36` says "SH for both arms is reported" but defines only CAL-rung's): pin it as LC's leg-label
   permutation with seeds `[1000 + k, stat, block, book]`, or drop it.
9. **FanDuel (P7).** Scored rows = FD rungs in blocks with ≥ 2,000 FD fit rungs and ≥ 2,000 DK fit legs, with an FD main
   line. FD CAL-rung and FD C0 fit on FD rungs; FD CAL-main is the DK map unchanged. Pin the FD seeds.
10. **Bootstrap code.** `nba/truth/bootstrap.py` does not exist. Name the CI code (LC used `research.eval.model_clv_eval.Boot`)
    and pin it in the harness files. Pin the estimator (ratio of resampled per-game sums), the date-resample B (5,000), and
    the rule that P2 per-bin and P8 per-season intervals reuse the same shared matrix restricted to their rows.
11. **Per-row arrays.** Save an npz of rung key, game, date, season, stat, bin, bench, \|r\| tertile, A0, CAL-main, CAL-rung,
    C0, BASE, SH mean and hit for DK and FD, so the post-result attack can re-derive every number without a re-run.
12. **Run budget.** State the expected wall time (50 × 64 CAL-rung refits plus the CAL-main SH plus FD). Run it in the
    background with a progress log; the 600 s watchdog applies.

**M1 (major): P4 cannot fail for CAL-rung.** `ALT_RUNG_CAL.md:36`. Permuting the realised stat across units of different
players destroys the scale of every ladder, so SH is 0.015 to 0.057 per rung worse than A0. CAL-rung clears the P4 bars even
in a world where it adds nothing (check 9). The doc already makes this argument for CAL-main; it applies to CAL-rung as well.
It does not change the word outcome today, because P3 is the binding information test, but it is a guard that can only pass
(playbook Q3). **Fix (safe default):** make P4 label-only for both arms. Rewrite `:44` as "CAL-rung passes P1, P2, P3, P5-P8"
and `:45` as "fails P3". State that P3 and the knockouts carry the information question. Alternative: replace SH with a
line-position null that permutes the (zm, r) vectors among fit units within (stat, block, 0.05 A0 bin) and refits. That null
keeps A0 and the table, and destroys only the main-line information.

**M2 (major): the multiplicity family is smaller than the family run.** `:33` and `:48` declare m = 2, but `:45` lets C0 issue
TABLE_FIXES through its own P1 at p < 0.025, which makes three contrasts that can each issue a non-null word (FWER up to
0.075). **Fix:** Bonferroni m = 3 (p < 0.0167) over CAL-main, CAL-rung and C0. The projected hw of 0.0007 against a floor of
0.002 means the floor binds, not the p, so the cost is near zero.

**M3 (major): the record and the rule disagree on FanDuel.** `docs/DECISIONS.md:1236` says the defaults include "FanDuel label
only". The doc's default (`:39`, `:43-45`, `:164`) makes P7 a pass check in every word. **Safe default:** freeze P7 as a pass
check. Devin's go was "the drafts' own defaults", and the stricter check guards against a one-book artifact. Then append a
DECISIONS correction row (drafted below). Also replace "What Devin must sign (open questions)" (`:159-166`, inside the hashed
text) with the answers as recorded, so the frozen text has no open questions. If Devin meant label-only, P7 must leave the
three words, and the doc must be re-hashed.

**M4 (major, disclosure): CAL-main's 2024 behaviour can already be computed.** `:64-71`. The 36 DK maps for 2024 blocks are
the T493 fits (`reports/prereg_line_calibration/select.json`, 72 stored betas across both books). T491 published the
per-season A0 hit rates by bin. So CAL-main's 2024 per-bin gaps (P2, P8) and its mean prediction per bin can be computed
before the run, without a new label. This review did not compute them. **Fix:** add this to "Data exposure", and state that
nobody computed CAL-main on a rung. It is one more reason MAIN_MAP_FIXES is a near-foregone screen (prior 0.04), which the
doc already concedes.

**M5 (major, honesty of the departure): fact 1 and reason (a) overstate.** `:13` ("cannot be run as a test of the pattern")
and `:55(a)`. The 2023 cells fail only the inherited conservative bound, which assumes all 74 rungs of a game are perfectly
correlated. Check 8 gives 2023 per-bin hw of about 0.011-0.018 under LC's measured design effect, which passes 0.03. **Fix:**
say that 2023 fails the inherited conservative cell gate, that a realistic width would likely pass, and that the departure
rests on (c) "nothing is selected" and (d) "both seasons already A0-exposed". Also add a power statement to P8 (`:40`): with
2023 per-bin sd 0.005-0.0075, P8 never false-fails a perfect arm (0 of 400) but cannot detect per-season gaps below about 0.02.
The 2023 replication rows are February to June 2024 only (36,145 rungs, 580 games). P8 is a sign-and-band replication, not a
held-out one: the 2024 fits use 2023 labels.

**m1 (minor):** `:20` says the DraftKings **points** defect window is excluded, which matches the code. The 760 ast and 27 reb
DK rungs on those dates stay in as fit rows from 2024-02 onward. T492 found that the excluded "points" ladders were rebounds
ladders, so name these rungs in S3, or add one label: CAL-rung refit without them.
**m2 (minor):** S2 (`:50`) should report the no-main-line share by role and month (DK 2024 bench 15.5%, 2025-04 14.2%), and
should state that CAL-rung's served object is undefined on those rows.
**m3 (minor):** add "drop the best month" to the secondary labels (six checks, item 5). The oracle gain is 62% in P ≥ 0.90,
and the no-main-line drop spikes in April.
**m4 (minor):** state the MDE of P3. CAL-rung − C0 has a prior of −0.0001 (80%: −0.0008..+0.0006), so P3 is underpowered for
the expected effect, and a P3 fail should be read as "not shown", which is what TABLE_FIXES already says.
**m5 (minor):** every edit above changes the sha. After the edits, re-run the hash and the gate (the gate does not read C0 or
P4, so its numbers stand), and commit `prereg(ALT_RUNG_CAL): <new sha>`. This review approves 0c5a1514 only with B1-M5 applied.

## Multiplicity and power (corrected)
- Family actually run for word-issuing P1 contrasts: CAL-main, CAL-rung and C0, so m = 3, Bonferroni p < 0.0167 (M2). P5 BH
  m = 4 per arm, labels. The 20 stat × bin cells use BH over the cells that pass the width gate (17 of 20), labels.
- P1 projected hw 0.00071 pooled against floor 0.002 (gate, reproduced). The floor is 47% of the oracle gain of 0.00421;
  reasonable, and the floor binds before the p does. P2 bins: hw 0.005-0.007 against a 0.03 margin. P8 2023: sd 0.005-0.0075.
  P3: MDE not stated (m4).

## Playbook check
1. **Information or capacity?** Mostly re-expression: a recalibration of production's own P. The one added input is the
   book's main line (a line, not a price) in CAL-rung. With the B1 fix, P3 against a shift table becomes a fair test of
   whether that line adds information. Without it, P3 partly measures C0's handicap.
2. **Enough data for this class?** Yes. There are 5-7 parameters, ≥ 3,044 fit rungs per fitted block, and 64,984 units. C0 has
   up to 40 cells per block with ≥ 100 rows.
3. **Valid test?** Real-tip gating is verified (lead ≥ 64.2 min, quotes ≥ 62.8 min before tip). Played rows only are the
   same for every arm. Integer support passes. P2/P8 do not over-fire (check 8). Two invalid parts: the inert P4 (M1, "an
   inert shuffle: only false passes") and a comparator handicapped by construction (B1, "a comparator missing information
   production had": C0 discards A0's within-bin information).
4. **Lineage.** LINE_CALIBRATION (fit machinery and T493 maps; the harness-pin guard was dropped, see B2), ALT_LADDER_HIST (rung
   set, filters, margins), ALT_LINE_70, CALIBRATED_GRID. The guard that changed (unit-level null) turned out inert (M1).
5. **Scored against:** binary log loss and reliability at rung thresholds, not CRPS and not money. Correct for the question.
   Class checks (quantile/distribution layers): the reliability table sits at the points that matter, and the slices are
   fixed in advance. Pass.

## The single biggest risk
If the rule were frozen as written, the most likely false result is **RUNG_REFIT_FIXES issued because C0 is a handicapped
table**. CAL-rung would "beat the table" by about the 0.0005 the step function gives away, and the headline would credit the
main line with information it may not carry.

## What I did not verify
- That production's stored OOF A0 (`model_rows_*`) is as-of. This is inherited from ODDS_HISTORY, T491 and T493 (all
  SURVIVED); not re-audited here.
- The ladder-integrity filter logic (`off_ladders`). It is frozen under ALT_LADDER_HIST and was reproduced by count (4 of 4).
- The between-player dependence inside a game beyond LC's design effect of 1.51. The real P2/P8 widths lie between my
  unit-coupled numbers and the conservative bound.
- Whether nobody has computed CAL-main on rungs (M4). Only the modeler's statement exists.
- No real rung label was read, so no real-data shuffle, knockout or replication was run (nothing has been scored).

## Drafted rows (not appended)
- **DECISIONS (correction, for the main session to append):** `2026-10-15 — Correction to 00:58 item 2: ALT_RUNG_CAL's own
  default makes P7 (FanDuel) a pass check in every word, not "label only" as recorded; the freeze follows the draft's default
  (Devin's go was "the drafts' own defaults"). Pre-freeze review (docs/reviews/ALT_RUNG_CAL_prefreeze_2026-10-15.md) BLOCKED on
  B1 (C0 step table) and B2 (harness contract); fixes B1, B2, M1-M5 applied before the freeze; a new sha replaces 0c5a1514.
  Reverses: Devin stating label-only before the freeze commit.`
- Ledger: none (pre-freeze review). Predictions: none.

---

# Re-check of revision 2 (adversary, 2026-10-15 01:30 CDT)

Verdict: BLOCKED

Reviewed: `origin/agent/modeler-alt-rung-cal-2` at 8a4413a (rule, `research/eval/alt_rung_cal_eval.py`,
`research/eval/alt_rung_cal_rule.py`, `tests/eval/test_alt_rung_cal_harness.py`, both rehearsal `results.json`), plus the
DECISIONS correction 7e9a81d. No real rung label was read. DraftKings main-line **leg** labels (`pairs_*.over`, already
scored in T493) were read once, to reproduce the T493 coefficients. Seasons 2023 and 2024 only.

**Short reading.** The science is right now. The shift-table C0, the m = 3 family, P4 as a label, the FanDuel record and
the disclosures are all in place. The harness reproduces T493 exactly, runs its preflight before any label, and can issue all
four words in the right worlds. What blocks the pin: the harness does not produce outputs the rule text promises (S2 and
the dropped-row reliability; the extrapolation share by bin), the P5 decision depends on a file outside the pin, and one
sentence in the hashed text misstates where a number came from. All are small. Two of them change the rule sha (R1, R3) and
R2, R4 and R6 change the pin. Fix them, then re-hash and re-pin; a re-check of the diff should take minutes.

## Every item from the first review

| item | rev 2 | verified how | status |
|---|---|---|---|
| B1 shift-table C0 | rule `:24`, `:184`; `shift_table` (eval `:327-342`) is clip(A0 + g) with g = weighted mean(hit − A0) per 0.05 bin, 0 below 100 raw | code read; unit tests `test_shift_table_*` pass | FIXED |
| B2.1 freeze and pin | `alt_rung_cal_rule.py` (`require_frozen`, `require_harness_pinned`, `pin`); `run` calls `require_ready` first (eval `:848-850`) | code read; pin recomputed on the branch files: `cf723452d4130334a4eaa33ecd4869c74938f8e3d38e9d15589fab27d55c3f6e` (exit 0), equal to the stated pin | FIXED, see R2, R5 |
| B2.2 inputs | full 64-hex lines `:112-119`; `verify_inputs` refuses on mismatch | my driver: `inputs verified True` against the main-checkout data | FIXED |
| B2.3 labels | `attach_real_labels` (eval `:246-283`): `y` non-null and integer, hit = Y ≥ point + 0.5 | code read | FIXED |
| B2.4 preflight before any label | `run` path (eval `:867-874`): walk-forward bookkeeping on synthetic A0 labels, then `preflight`, then `attach_real_labels` | ran the same path label-free: all 11 pinned counts equal (DK 162,745 / 134,462 / bins 38,122-12,093 / 21,507 / 6,776 / 64,984; FD 244,270 / 221,776 / 5; 64 blocks; window games 39 / 62), identity list equal to gate.json | FIXED |
| B2.5 T493 reproduction | `check_t493` (eval `:510-527`) | my driver refit CAL-main for every DK 2024 block from the harness frames and the real leg labels: **36 blocks, max \|Δβ\| = 0.000e+00** against `select.json` | FIXED, see R4 (ordering) |
| B2.6 fit rows | `run_book` `fit_r = stat & has_main & date < b0`, all seasons ≤ 2024; legs as LC | code read | FIXED |
| B2.7 C0/BASE details | constants eval `:55`; `c0_bin` tolerance 1e-12 | code read | FIXED |
| B2.8 SH for CAL-main | LC leg permutation, seeds `[1000 + k, si, bi, 0]` (eval `:441-442`) | code read | FIXED |
| B2.9 FanDuel | `run_book` on FD rungs with DK legs (book swap); FD main line required | code read; FD scored 221,776 | FIXED |
| B2.10 bootstrap | `model_clv_eval.Boot`: `p = (1 + #{boot ≤ 0}) / (B + 1)`, so `mean(-d).p` is the one-sided p for "arm beats" (P1, P3) and `mean(d).p` the p for "worse" (P5): directions correct | code read | FIXED |
| B2.11 per-row arrays | `save_arrays` (eval `:775-798`), sha256 in results.json | code read | FIXED |
| B2.12 run budget | 85.5 / 87.7 s recorded | rehearsal results.json `wall_seconds` | FIXED |
| M1 P4 label | rule `:36`, `:44-45`; `evaluate` (eval `:752-764`) never reads P4 | code read | FIXED, see R3 |
| M2 m = 3 | rule `:33`, `:48`; `ALPHA_FAMILY = 0.05 / 3` | code read | FIXED |
| M3 FanDuel record | DECISIONS 01:09 correction (7e9a81d); rule `:191-192` records the answers | read | FIXED |
| M4 exposure | rule `:69` | read | FIXED |
| M5 departure, P8 power | rule `:13`, `:40`, `:55` | read | FIXED |
| m1 defect-date refit | `CAL_rung_nodefect` | code read | FIXED (label; see R6) |
| m2 S2 | rule `:50` promises it | **not implemented** | OPEN, R1 |
| m3 drop best month | `drop_best_month` | code read | FIXED |
| m4 P3 MDE | rule `:35`, `:135`: hw about 0.0002 against −0.0001 | rehearsal numbers | FIXED |
| m5 re-hash | new sha `7a88f9afae8a2e6b8fdfbc6625ecddad8edc8c79f62432401e67407a098c10e1` | `git show origin/agent/modeler-alt-rung-cal-2:docs/prereg/ALT_RUNG_CAL.md \| awk '/^=== RESULTS BELOW ===$/{exit} {print}' \| shasum -a 256` (exit 0): equal | FIXED |

## Rehearsals: do the guards behave?

| world (synthetic labels, real rows, folds and bootstrap) | word | key numbers (DK, n 134,462, game-clustered 95% CI) | reads as |
|---|---|---|---|
| truth = T491 oracle (a function of A0's bin and stat only) | TABLE_FIXES | CAL-rung − A0 −0.00400 [−0.00461, −0.00342]; C0 − A0 −0.00411 [−0.00471, −0.00354]; CAL-rung − C0 +0.00012 [−0.00010, +0.00033] (P3 fails); CAL-main −0.00114, bins 85 / 95 OVERCONFIDENT (−0.035 / −0.056) | correct |
| truth = A0 (nothing to fix) | NOT_FIXED | every arm worse than A0: CAL-main +0.00113, CAL-rung +0.00019 [+0.00003, +0.00033], C0 +0.00026; P1, P7, P8 fail | correct; the guards can say no |
| **adversary, added:** truth = oracle shifted by the main line's position, logit + 0.5 zm (the line carries information A0 lacks); K = 0 | **RUNG_REFIT_FIXES** | CAL-rung − A0 −0.00724 [−0.00806, −0.00643]; **CAL-rung − C0 −0.00190 [−0.00239, −0.00141]**, p 0.0002; C0 − A0 −0.00534; CAL-main −0.00185 (fails the floor and P2) | correct: the harness can issue the map word when the map really adds |

Command for the third row and the two checks above: `.venv/bin/python -W ignore $S/recheck_driver.py $S/rev2 $S` (exit 0;
`$S` is this session's scratchpad; the script reads no rung label). The three worlds hit three different words, and in each
case the word the construction implies. MAIN_MAP_FIXES was not exercised (it would need a world where a main-line map
transports to x of 2 to 5). That is acceptable at prior 0.04.

Verification on the branch (unpiped): `ruff check` exit 0; `ruff format --check` exit 0 (3 files); `mypy` exit 0 (2 files);
`pytest tests/eval/test_alt_rung_cal_harness.py` gives 7 passed, exit 1 **only** from the repo-wide 70% coverage floor on a
single-file run. With `--no-cov` it is 7 passed, exit 0.

## Removal of S1, S3 and Brier
Acceptable. They were labels. ALT_LADDER_HIST already reports S1 (window games) and S3 (integrity filters added back) for
A0, and A0 is the only arm whose behaviour on those rows is interpretable without new fits. Brier adds nothing that log loss
and the reliability tables do not show. Removing labels before a freeze is not a rule change.

## Required before freeze and pin (numbered; file:line; exact fix)
- **R1 (major, harness vs rule).** Rule `:25` ("Rows dropped by either condition are counted, never hidden, and their A0
  reliability is reported (S2, label only)") and `:50` (S2: no-main-line share by role and month, A0 reliability, the
  statement that CAL-rung's served object is undefined there). `alt_rung_cal_eval.py` computes none of this: `score()`
  (`:562-723`) sees only scored rows. Once the harness is pinned, the frozen rule would promise outputs the pinned code cannot
  produce. **Fix (code only):** in `pipeline`, add `res["dropped"]` per book with: counts and A0 reliability (hit − A0 by the
  five bins, plus calibration in the large) for (a) identity-block rows and (b) no-main-line rows; and the no-main share by
  role and by month. Add the row text "CAL-rung's served object is undefined on rows without a main line".
- **R2 (major, pin hole).** `alt_rung_cal_eval.py:30` imports `bh_adjust` from `nba/eval/context_residual_eval.py`, and
  P5's pass/fail runs through it (`:632`). That file is not in `HARNESS_FILES` (`alt_rung_cal_rule.py:32-40`) and lives
  under `nba/`, which changes for production reasons. **Fix:** inline a 10-line `bh_adjust` in `alt_rung_cal_eval.py`
  (byte-identical logic) so the pin covers it, and add a unit test against `nba.eval.context_residual_eval.bh_adjust`. Do not
  add an `nba/` file to the pin. The other unpinned imports (`model_clv_eval` → `f11_lineup_context`,
  `minutes_hazard_eval`, `odds_history_eval`) are import-time only and do not touch any number: no action.
- **R3 (minor, a false sentence in the hashed text).** Rule `:137`: "On real labels the review measured the unit null 0.015
  to 0.057 per rung worse than A0 in a calibrated-A0 world". The review read no real label. Check 9 drew Y from each unit's
  own production grid (a synthetic, A0-calibrated world). **Fix:** "Under synthetic labels drawn from each unit's own grid
  (an A0-calibrated world), the review measured...". This changes the rule sha; re-hash.
- **R4 (minor, ordering).** `pipeline` (`:809-815`) runs `check_t493` after real rung labels have been joined and every arm
  fitted. If it ever failed, rung labels would already be read and any harness fix would be a post-label change. **Fix:** in
  `main` `run` (`:867-874`), after `preflight` and before `attach_real_labels`, attach the real **leg** labels only, refit
  CAL-main on the DK 2024 blocks and call `check_t493`. Read rung labels only after it passes. (I measured that it will
  pass: max |Δβ| = 0.)
- **R5 (minor, pin check too weak).** `alt_rung_cal_rule.py:138`: `h in rev-list fc..h` is true for any pin commit that is
  not an ancestor of the freeze, including one on a parallel line. **Fix:** require
  `git merge-base --is-ancestor <fc> <h>` (exit 0), and add the case to `test_freeze_and_pin_refusals_with_a_fake_git`.
- **R6 (minor, label).** `CAL_rung_nodefect` filters on `game_date` 2023-12-07..19 (`:409`), but the vendor window is
  defined on the snapshot date (`alt_ladder_hist_gate.py:319-326`). The two differ for late tips, and the refit also runs for
  FanDuel (`book_has_defect` is not book-specific). **Fix:** filter on the rung's snapshot date and restrict the refit to
  DraftKings (carry `snapshot_ts` into `rung_arrays`), or state the `game_date` definition in rule `:20`.
- **R7 (minor, rule vs output).** Rule `:43` says MAIN_MAP_FIXES is "always printed with the share of rows outside the
  fit-leg band by bin" and carries "a table does as well" when P3 fails. `evaluate` (`:726-769`) emits neither. **Fix:** add
  `inband` share by bin to `score()` and the qualifier string to `evaluate()`.
- **R8 (minor, negative tests).** There is no test that `preflight` refuses on a count mismatch, that `check_t493` refuses on
  a coefficient mismatch, or that `run` refuses before `require_ready`. **Fix:** three small tests with fakes.

After R1-R8: re-hash the rule (R3, and R6 if worded in the rule), recompute the pin (R1, R2, R4, R5, R6, R7 change harness
files), re-run both rehearsals (their `results.json` must be regenerated by the pinned code), and send the diff for a
re-check. The 01:09 DECISIONS correction already covers the FanDuel answer; no new DECISIONS row is needed for these
mechanical fixes.

## Playbook check (re-check)
Same class and the same five answers as above, with question 3 now passing on the points that failed. The comparator is
fair (the shift table has zero structural cost and loses to the map only where the map really knows more, as the line
world shows). The inert nulls are labels. Every word is reachable in a world built to produce it. What remains is code
hygiene between the rule and the pinned harness, not test validity.

## The single biggest risk (now)
The frozen rule's promise of an S2 report, and the P5 BH step, could be served by code that is not what was pinned (R1 and
R2). That is a quiet rule-vs-code divergence, not a leak.

## Not verified (re-check)
- The rehearsal `results.json` on the branch was not regenerated by me. My own run of the same code paths (preflight,
  pipeline in a third world) agrees with them in structure; their numbers match the doc text.
- A real-label run was not done, by design.
