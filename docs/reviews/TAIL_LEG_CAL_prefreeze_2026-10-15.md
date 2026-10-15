# TAIL_LEG_CAL pre-freeze review (adversary), 2026-10-15

Reviewed: `docs/prereg/TAIL_LEG_CAL.md` draft revision 1 (main at f49dd81), `research/eval/tail_leg_cal_{eval,rule,gate}.py`,
`tests/eval/test_tail_leg_cal_harness.py`, `reports/prereg_tail_leg_cal/` (gate.json, five rehearsal folders).
Clock: `date` read `Thu Oct 15 08:19:05 CDT 2026` before writing. No real tail-stage label was read, no 2025 row was loaded,
no DuckDB file was opened. Every attack below uses synthetic labels drawn on the real legs and the real CAL q (the same
footing as the draft's own rehearsal).

Verdict: APPROVED WITH EDITS

Text c358951f cannot be frozen as written. Items 1 to 8 below change the word logic, the gate, the stated meaning of
NOT_FIXED and the disclosure, so the frozen text will have a new sha. Once they are applied, the freeze needs a recheck
of the diff only (about 15 minutes, the way the ALT_RUNG_CAL recheck went), not a second full review.

**Top finding.** In a world where the confident tail carries no ordering information at all (the `flat` truth), the rule
issues **TAIL_FIXES in 18.0% of draws [13.3, 23.9], n 200**. The hinge removes the gap by clipping beta at -0.95, which
pushes every tail leg down to about the knee. One check fixes it at no cost: require the median fitted beta to sit above
-0.90 (off the clip). Under that check the flat world gives 0 of 100 positive words, and the smooth and step worlds keep
61 of 61 and 29 of 29.

## Model class and playbook check

The model is a one-parameter offset logistic hinge, refit each month walk-forward, sitting on a frozen logistic recalibration (CAL a5441242)
that sits on a stored LightGBM-residual forecast. Playbook classes: "Quantile and distribution layers" and "Linear and logistic
models". Neural and market classes do not apply.

1. **Information or capacity?** Neither. It re-expresses CAL's own q, so the right test is calibration, not skill. The draft says this (line 138).
   The flat-world finding below shows the word can also reward a map that *removes* information, which is why item 1 exists.
2. **Enough data for this class?** One parameter on 1,178 to 8,557 earlier tail legs per block is fine for the hinge.
   Isotonic as a label is right. Per-stat volume at q >= 0.65 is the real limit: fg3m has 1,524 of 1,746 legs, reb 158 and ast 64.
3. **Test valid?** Mostly. Walk-forward, strict date guard, a paired clustered bootstrap, P0 as a precondition, and an
   honest power note on P2s. Gaps: word-level power was never computed (only per-check marginals; item 2), the flat world
   can earn TAIL_FIXES (item 1), the text oversells what NOT_FIXED means (item 3), and versions are unpinned (item 5).
4. **Lineage and inherited guards:** LINE_CALIBRATION (CAL, blocks, weights, identity), PARLAY_JOINT_CAL (legs, TOP, bootstrap),
   ALT_RUNG_CAL (pin pattern). One inherited guard was dropped silently: PARLAY_JOINT_CAL's package-version check
   (`pj.PINNED_VERSIONS`, `parlay_joint_cal_eval.py:67,875`) is not carried over (item 5).
5. **What it scores against:** calibration gap and log loss of the served leg probability, against CAL (frozen) and A0.
   No price enters, which is correct.

## Process checks (as for ALT_RUNG_CAL and PARLAY_JOINT_CAL)

| # | check | command / evidence | result | pass |
|---|---|---|---|---|
| A | sha of the text above the marker | `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TAIL_LEG_CAL.md \| shasum -a 256` (exit 0) | `c358951f09c514edb8d69c20e93f9a3c5a0afe967cb027a2e0526c2bf74582e3`, equals the caller's c358951f…82e3 | yes |
| B | harness implements every promised output (rule l.119) | read `tail_leg_cal_eval.py:470-626` | gaps, intervals, log loss, contrasts, season, stat, side and band slices, reliability, slopes, nulls, TOP, blocks with beta, counts, rule sha, blobs: all present. Missing: the shuffled null's slope promised at l.38 (only CAL and TAIL_H slopes are computed, `:538`), and package versions | partial (minor) |
| C | refusals run before any tail-stage label | `main` `:793-796`: `require_frozen` → `require_harness_pinned` → `preflight(require_declared=True)` → `run_all` (the only `attach_labels(mode="real")`, `:697`) | order correct. `legs_prefit` holds `hit_leg` in memory during preflight (`:259-260`, deleted), which is the disclosed CAL pre-fit exposure; nothing computes on it | yes |
| D | every number-moving import is inside the pin | traced `load_cal` → `lc.read_grid/attach_grid/to_arrays/walk_forward/design/fit_logistic/age_weights` (line_calibration_eval.py), `pgate.load_legs/_blocks/cal_served/top_candidates/keep_top`, `ahc.make_boot/boot_ratio/cluster_totals/percentile_interval/p_le0/bh_q`: all in the 8 pinned files. `nba.parlay.copula`, `parlay_joint_cal_rule`, `model_clv_eval.Boot`, `nba.eval.context_residual_eval` are imported but not on this path. The q fingerprints pin CAL's output on top of that | numpy/polars/scipy versions not checked or recorded (item 5) | partial |
| E | inputs pinned | `shasum -a 256` of the four parquet files (exit 0): equal to the rule's pins l.102-105. gate.json sha `a6f615be…` equals GATE_SHA256 l.106 | equal | yes |
| F | freeze and pin order stated | rule l.114, `tail_leg_cal_rule.py:14-17,152-153` (pin must descend from the freeze, checked) | stated and enforced. The printed command `git add … && git commit` fails with "nothing to commit" when the files are unchanged. ALT_RUNG_CAL's pin commit ff040aa is empty, so the real procedure is `--allow-empty` (item 7) | yes, with a fix |
| G | each word in its own world (rehearsals) | `reports/prereg_tail_leg_cal/rehearsal/*/results.json` | TAIL_FIXES (smooth), NOT_FIXED (step, flat), NO_TAIL_PROBLEM (calibrated). **TAIL_GAP_FIXED_LL_NOT_SHOWN is never shown**, and `evaluate()` has no unit test (16 tests, none on the word function). A fifth folder `rehearsal/oracle/` is stale: a truth name the harness no longer accepts and no P0 field. It is byte-identical in outcome to `step` (same betas, TAIL_H G65 -0.0262) | partial (item 6) |
| H | lint, types, tests | `ruff check` exit 0. `ruff format --check` exit 0 (4 files). `mypy` (3 files) exit 0. `pytest -q --no-cov tests/eval/test_tail_leg_cal_harness.py`: 16 passed, exit 0 | | yes |
| I | non-negotiable 8: gate re-run on real data | from the review worktree: `.venv/bin/python -W ignore -m research.eval.tail_leg_cal_gate --data-root /Users/devin/Downloads/nba-prediction --draws 100 --out <scratch>/gate_rerun.json` exit 0, 08:08:04 to 08:10:24 CDT | **identical** to the committed gate.json in every field except the two clock fields (recursive diff empty). Counts 4/4 equal T495, lead minimum 62.55 / 64.33 min, scored DK 7,405 / 1,746 | yes |
| J | 2025 never touched | `guard_season` in `legs_prefit`, `preflight`, `pj.read_pairs_cols`, `lc.read_grid(holdout_ok=False)`. The "2025-01…2025-06" block labels are calendar months of season 2024 (2024-25) | | yes |
| K | modeler scratch exposure | read `<session scratchpad>/tlc/{explore,books,show,new_eval,new_score}.py` (the modeler's, 07:44-07:54) | counts and q only. No hit rate, gap or loss on a real label. `new_eval.py` (07:51) has no P0, so P0 and NO_TAIL_PROBLEM were added after the `oracle`(=step) rehearsal returned NOT_FIXED (item 4) | yes (disclosure item) |

## Science attacks (synthetic labels on the real legs and the real q; scratch scripts, not in `nba/`)

Script: `<session scratchpad>/tlc/sim.py`, `sim2.py`, `sim3.py`. Each draw is one uniform per leg (seed `[4242, k, book]`),
then `ev.score_book` on both books, then `ev.evaluate`, with `Clusters(b=1000)` (the rule uses 5,000) and the TOP slice
stubbed out (it is a label). The word logic is unchanged harness code. Command:
`.venv/bin/python -W ignore sim.py <truth> 200 1000`, exit 0 for every truth. n = 200 draws per truth (100 for the
slope/beta read); 95% Wilson intervals.

**Word-level operating characteristics.** The gate reports only per-check marginals. These are the first word-level numbers:

| truth (all built from q only) | TAIL_FIXES | GAP_FIXED_LL_NOT_SHOWN | any positive | NOT_FIXED | NO_TAIL_PROBLEM |
|---|---|---|---|---|---|
| smooth (the draft's main truth) | 39.5% [33.0, 46.4] | 22.5% [17.3, 28.8] | 62.0% [55.1, 68.4] | **34.0% [27.8, 40.8]** | 4.0% |
| step (T495's published steps) | 9.0% [5.8, 13.8] | 20.0% [15.0, 26.1] | 29.0% [23.2, 35.6] | **66.5% [59.7, 72.7]** | 4.5% |
| calibrated | 0.0% [0, 1.9] | 1.0% [0.3, 3.6] | 1.0% [0.3, 3.6] | 2.0% | 97.0% |
| **flat (no order in the tail)** | **18.0% [13.3, 23.9]** | 0.0% | **18.0%** | 82.0% | 0.0% |
| shift (-0.02 above the knee) | 3.0% | 8.0% | 11.0% [7.4, 16.1] | 34.5% | 54.5% |
| gap in 2023 only | 0.0% | 3.5% | 3.5% [1.7, 7.0] | 47.0% | 49.5% |
| fg3m only (smooth slope) | 27.5% | 19.0% | 46.5% [39.7, 53.4] | 46.0% | 7.5% |
| unders only, scaled to T495's pooled gap (slope -0.82) | 4.5% | 1.0% | 5.5% [3.1, 9.6] | 88.0% | 6.5% |
| fg3m unders only, scaled (slope -1.0) | 2.5% | 0.5% | 3.0% [1.4, 6.4] | 92.5% | 4.5% |
| overs under-confident, unders over-confident | 0.0% | 0.0% | 0.0% | 11.5% | **88.5%** |

What this shows:
- **Calibrated, shifted and transient worlds are rejected.** P0 does the work in the calibrated world (positive 1%).
  P4 does it in the 2023-only world, and P7's side check in the side-concentrated worlds: in the unders-only world the
  pooled hinge makes overs under-confident by +0.028 and P7 fails 94.5%. Those guards are real.
- **The flat world is the hole** (item 1). The beta median is at the clip (-0.95) in 100 of 100 flat draws. In the smooth
  and step worlds the 5th-95th percentiles of the beta median are [-0.745, -0.376] and [-0.607, -0.225], so it never
  reaches -0.90. The rule already reports beta per block, so the check costs nothing. The hit-on-shown slope lower bound
  > 0 also separates flat from smooth (1% vs 83%), but it would cost 14 of 61 smooth positives. Beta is the better guard.
- **NOT_FIXED is weak evidence** (item 3). A correct hinge in the draft's own main world returns NOT_FIXED 34% of the
  time; in T495's step world it returns NOT_FIXED 66.5%. Rule l.48 ("NOT_FIXED means a hinge cannot even fit the gap it
  was written to fit") is false at this power. The word is a conjunction of seven checks that each pass about 85-99%
  (smooth: P0 .96, P1 .985, P2n .915, P3 .925, P4 .86, P6 .85, P7 .94), and the conjunction is 62%.
- **The rehearsal story is the lucky draw** (item 3). "smooth: TAIL_FIXES, as it should be" (l.124) is a 39.5% outcome.
  "step … this draw is on the unlucky side" (l.125) is backwards at word level: NOT_FIXED is the modal step outcome (66.5%).
- **Opposite-signed side problems hide under a pooled P0** (88.5% NO_TAIL_PROBLEM). T495 measured a negative pooled gap,
  so this is unlikely on the real rows, but a NO_TAIL_PROBLEM word should not be read without per-side gaps with CIs (item 8).

**Your questions, answered.**
- *Knee, band and hinge chosen after the tail was seen; what can a historical TAIL_FIXES mean?* The exposure section is
  honest about the knee, band, family, clip and MIN_TAIL (l.46-50). It is not honest that "every threshold in the checks
  was fixed from the gate's power numbers … not from a real-label result" (l.50). The synthetic truths are T495's real
  gaps (l.72). The first reference truth (`oracle`, which is T495's step) returned NOT_FIXED. After that, P0 was added and
  a smooth truth was made "the main oracle" (`tail_leg_cal_eval.py:83`). That is legitimate synthetic iteration, but it
  must be disclosed (item 4). P0 is also nearly known in advance: T495 measured G65 -0.034 [-0.062, -0.007] (2023) and
  -0.047 [-0.082, -0.014] (2024) on supersets of these rows. A historical TAIL_FIXES therefore means: on rows already known
  to have the gap, a one-parameter map fit on earlier months removes it, the tail keeps its order (once item 1 is in), and
  no side or season is broken. It is evidence of month-to-month stability and of the shape. It is not out-of-sample and
  not a reason to change the panel. The draft's l.47 sentence is the right ceiling; keep it.
- *Is the q >= 0.60 gap check a weak discriminator?* Yes, and the draft knows it (l.14). CAL passes P1 itself 31% of the
  time under the smooth truth and 48% under the step truth (gate, reproduced). P1 is a guard, not evidence: it fails 70%
  in the flat world and 22.5% in the shift world, and passes 98.5-100% wherever the hinge is right. The discriminator is
  P0 + P3. Keep P1, but label it a guard in the text (folded into item 3).
- *P2s non-required.* Agreed. P2s is 56.5% under smooth and 23.5% under step, so as a required check it would make the
  word mostly a power coin. With items 1 and 3 in, TAIL_GAP_FIXED_LL_NOT_SHOWN is a fair second word. It still rests on
  P3, which is an absence-of-detection test at a half-width of about 0.024; the text says so (l.36), keep that.
- *Composition: is this an fg3m-under problem? Should the words be per stat?* At q >= 0.60 the scored DK tail is fg3m
  76%, unders 76% (fg3m-under 54.9%). **At q >= 0.65, which is where P0 and P3 decide, it is fg3m 87.3% (unders 50.3%,
  overs 36.9%), reb 9.0%, ast 3.7%, and unders only 61.3%** (label-free count, `walk_forward_tail` scored mask). So the
  decisive band is an fg3m problem, not an under problem: 37% of it is fg3m overs. Per-stat words are not worth it.
  fg3m is 1,524 of the 1,746 q >= 0.65 legs, so an fg3m word is nearly the pooled word, and reb (158) and ast (64) have
  no power. But the word should say it is an fg3m statement, and reb/ast should be covered by a no-harm label with an
  interval. In the fg3m-only world the pooled hinge makes reb and ast under-confident by about +0.010 and P7 (log loss,
  BH) passes 85.5%; small, but unseen (item 8). If the real problem is side-concentrated, P7 turns it into NOT_FIXED
  (88-92.5%), which is the right answer. A per-side gap table lets the reader tell "side-specific" from "hinge can't fit".
- *Interaction with MEDIAN_BIAS.* The clause (l.59-64) is sound: words bind to the stamped pair, a new upstream is a new
  id, and there are no additive claims. Two corrections. (a) l.58 says MEDIAN_BIAS "would act on exactly this tail". It
  would not: a low median makes *unders* over-confident, but 37% of the q >= 0.65 band is fg3m *overs*. And CAL already
  refits the level per stat (`lc.design` takes pm, line and median), so what is left after CAL is shape, not level. The
  MEDIAN_BIAS draft (uncommitted on `agent/modeler-median-bias`, read only) itself expects about 1.5 of 3.1 pp to remain
  on DK rows. So the two are partly independent. (b) l.57 says that branch "holds only a stub". It now holds a full
  uncommitted draft with the same pairs pins (050dba3e / 560a3808). Update the sentence; no conflict found.
- *Non-negotiable 8.* The re-run is identical (check I). Every count, width and power field reproduces. The gate passes
  its own per-check bars. It does not measure the word (item 2).

**Further checks.**
- Union interval for "contains 0" (P3) and the larger p for "no harm" (P7) are both the *lenient* direction for a
  positive word. Measured: P3 under the union differs from P3 requiring both intervals to contain 0 in 0 to 8 of 200
  draws per world (smooth 1, s2023_only 8, fg3m-unders-scaled 7). Small, but the fix is free (item 8).
- Shuffled null (P5): reproduced inert. Smooth: SH mean -0.00107 against real -0.00096. It stays a label, which is correct.
- Planted future: the unit test flips labels on or after 2024-04-01 and checks the arms before that date are
  byte-identical, with a positive control after. Sound. No feature lag applies (the map has no features).

## Items (severity; file:line; exact fix)

1. **Major: the flat tail earns TAIL_FIXES 18%.** `research/eval/tail_leg_cal_eval.py:674-685` and rule l.33-41. Add **P8 (the
   correction keeps the tail's order):** the median of `beta` over DraftKings' non-identity blocks is `> -0.90`. Word
   order: NO_TAIL_PROBLEM if P0 fails; else if P1-P7 pass and P8 fails, **TAIL_FLATTENED** ("the gap closes only by
   erasing the tail's ordering; the confident legs carry no detectable extra information"); then TAIL_FIXES and
   TAIL_GAP_FIXED_LL_NOT_SHOWN as now (each also requiring P8); otherwise NOT_FIXED. Add the row to "What each choice
   protects against". Cost measured: 0 of 61 smooth and 0 of 29 step positives lost; flat positives 15 of 100 to 0.
2. **Major: the gate never measures the word.** `research/eval/tail_leg_cal_gate.py:92-150` (`power`). Add per-draw
   `ev.score_book` (k_null 0, TOP off) on both books plus `ev.evaluate`, and record the word distribution for smooth, step,
   calibrated and flat (100 draws, B 1,000 is about 70 s per truth). Add a gate-table row "word reachable: P(positive |
   smooth) >= 0.5; P(positive | calibrated) <= 0.05; P(positive | flat) <= 0.05". With item 1 in, my numbers predict
   about 62% / 1% / 0%.
3. **Major (text): NOT_FIXED is overstated and the rehearsal reads as if typical.** Rule l.48: replace with "NOT_FIXED is
   weak evidence: under the draft's own smooth truth a correct hinge returns NOT_FIXED about 34% of the time, and under
   T495's step truth about 66%; it means 'not shown on these rows', not 'cannot fit'." Rule l.124-125: quote the
   word-level rates from item 2 beside each single draw, and delete "this draw is on the unlucky side". Rule l.34 / l.152:
   call P1 a guard against over-correction and flat maps, not evidence. Priors l.69: re-state after item 2 (TAIL_FIXES 0.22
   sits between smooth 0.395 and step 0.09, plausible; P(P0) 0.85 looks low given T495).
4. **Major (disclosure): synthetic design iteration.** Rule l.50 and l.72. State that the first reference truth was T495's
   step (`oracle`), that it returned NOT_FIXED, and that P0 and the smooth "main" truth were added after it. Replace "Not
   spent: every threshold … not from a real-label result" with "thresholds were set by power under truths built from
   T495's real gaps". Delete `reports/prereg_tail_leg_cal/rehearsal/oracle/` (stale, a truth name the harness rejects),
   or keep it and name it in the rule.
5. **Major (pin): package versions.** `tail_leg_cal_eval.py:735-771` (`preflight`): raise unless
   `pj.versions() == pj.PINNED_VERSIONS` (numpy 2.5.3, polars 1.44.2, scipy 1.18.1; the venv matches today). Add
   `res["versions"] = pj.versions()` in `main` `:796-799`. Add a line to the rule's harness contract l.115.
6. **Minor: `evaluate()` is untested and one word is never exercised.** `tests/eval/test_tail_leg_cal_harness.py`: add a
   test that builds the minimal `primary` / `other` dicts and asserts each of the five words (with item 1), including
   TAIL_GAP_FIXED_LL_NOT_SHOWN, P7's BH direction (`p_pos`), and that P0 failing wins over everything.
7. **Minor: pin command.** `tail_leg_cal_rule.py:16,187` and rule l.114: `git commit --allow-empty -m "harness(TAIL_LEG_CAL):
   <pin>"` (the eight files are already committed by the merge; ALT_RUNG_CAL's ff040aa is an empty commit). Also l.115
   "the five file hashes and gate.json" should read "the four input file hashes and gate.json".
8. **Minor: diagnosability labels and the conservative side of two checks.** (a) `tail_leg_cal_eval.py:522-535`: add the
   clustered interval (`cl.summary`) of the CAL and TAIL_H *gap* for each side and each stat with >= 400 legs, on r60 and
   r65 (labels). (b) `:655`: P3 requires *both* the game and date intervals to contain 0, and `:640`: P7 uses
   `min(p_le0(bg), p_le0(bd))` for "worse". Edit rule l.36 and l.40 to match. (c) Rule l.16 and l.58: give the q >= 0.65
   composition (fg3m 87%, unders 61%), say the words are de facto fg3m statements, and correct the MEDIAN_BIAS sentence
   and the "stub" remark (l.57). (d) Rule l.38: drop "and the calibration slope" for the shuffled null, or compute it.
   (e) `main` `:779`: `run` should ignore `--k` and use `K_NULL` (a maintainer typo should not change a recorded label).

## Freeze and pin steps (after items 1-8 and the diff-only recheck)

1. Modeler, on `agent/modeler-tail-leg-cal` after merging main: apply items 1-8. Then
   `.venv/bin/python -W ignore -m research.eval.tail_leg_cal_gate --data-root /Users/devin/Downloads/nba-prediction --draws 100`,
   paste the new `GATE_SHA256` (and nothing else changes in the pin lines: the input and QCAL fingerprints reproduce).
2. Re-run the four rehearsals (`… tail_leg_cal_eval rehearse --data-root /Users/devin/Downloads/nba-prediction --truth <t> --k 50`
   for smooth, step, calibrated, flat), delete `rehearsal/oracle/`, then `ruff check`, `ruff format --check`, `mypy`,
   `pytest tests/eval/test_tail_leg_cal_harness.py`, unpiped, exit codes quoted.
3. Adversary: diff-only recheck of the revision, which also checks it for new contradictions.
4. Main session merges to main, then with Devin: `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/TAIL_LEG_CAL.md | shasum -a 256`
   and `git commit --allow-empty -m "prereg(TAIL_LEG_CAL): <sha>"` (the doc is already in the tree at that sha).
5. Then `.venv/bin/python -m research.eval.tail_leg_cal_rule pin` and `git commit --allow-empty -m "harness(TAIL_LEG_CAL): <pin>"`.
   The pin commit must descend from the prereg commit (checked by `require_harness_pinned`).
6. Then `… tail_leg_cal_eval check --data-root /Users/devin/Downloads/nba-prediction` (counts 4/4, fingerprints, versions),
   then `… run --data-root /Users/devin/Downloads/nba-prediction --sha <sha>`, once. No holdout row is needed (no 2025).

## Corrected family and power note

- Family: one word arm (TAIL_H), one fixed-order classification. BH is only inside P7 (m = 3 stats). Adding P8 adds a
  guard, not a test. No arm family to correct: TAIL_I, SHIFT and CONST are labels.
- Power: word level, smooth 62% [55, 68], step 29% [23, 36], n 200 each. Per check (gate, reproduced): P3 discriminates
  at 96% (CAL flagged) against 3% (calibrated). MDE for a gap at q >= 0.65 is about ±0.024 with real clustering. At this
  power, "underpowered, cannot conclude" is the honest reading of NOT_FIXED.

## Single most likely failure mode if a positive word is wrong

The tail's order is mostly noise and the hinge "fixes" the gap by flattening the confident legs to the knee (beta at
the clip). The panel's top picks then look honest while carrying no information. Item 1 closes this. The next most
likely is a side- or stat-concentrated gap passing pooled (P7 and the item-8 labels cover it).

## What was not verified

- Real clustering. Synthetic uniforms are independent, so the synthetic widths are binomial. The real design effect
  (T495 about 1.1) shrinks power a little more.
- B = 1,000 in my sims against the rule's 5,000. Word rates carry about ±7 pp Monte Carlo error at n 200.
- The FanDuel count "2023 44,682" (rule l.132). The gate audit shows 34,208 CAL-served FanDuel 2023 legs; the difference
  is presumably identity blocks, not checked.
- The TOP slice code paths under the sims (stubbed for speed). It is a label.
- The MEDIAN_BIAS draft beyond its first 60 lines (uncommitted; read only).

## Drafted rows (not appended; the main session's to append)

- `docs/DECISIONS.md`: "2026-10-15 — TAIL_LEG_CAL pre-freeze review: APPROVED WITH EDITS (adversary). Text c358951f not
  frozen; add P8 beta-off-clip / TAIL_FLATTENED (flat tail earned TAIL_FIXES 18% [13, 24], n 200 synthetic), word-level
  power in the gate, NOT_FIXED re-stated as weak (34% under the smooth truth), version pin, disclosure of synthetic
  iteration. Reverse if: the recheck finds P8 costs more than 5 pp of smooth-world positives."
- `docs/PREDICTIONS_LEDGER.md` (adversary prior, scored when the result lands): P(word is positive on the real rows,
  after items 1-8) = 0.35; P(TAIL_FLATTENED) = 0.05; P(NO_TAIL_PROBLEM) = 0.08.
