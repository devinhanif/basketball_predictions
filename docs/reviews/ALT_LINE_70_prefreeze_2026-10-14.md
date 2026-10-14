# ALT_LINE_70 pre-freeze review (adversary), 2026-10-14

Reviewed: `docs/prereg/ALT_LINE_70.md` (DRAFT, commit 585294d, merged at 1fe929c), `research/eval/alt_line_70_gate.py`,
`tests/research/eval/test_alt_line_70_gate.py`, `reports/prereg_alt_line_70/gate.json` (sha256 9b37c931..., matches the draft).
Question (Devin): "at 70% over on all alt lines, can you be profitable with $20 a week?" No outcome exists; none was computed
here. No `y` column was read; season 2025 was not loaded (season labels checked: 2024 ends 2025-06-22 in both the model rows and
the odds store). All DuckDB connections `read_only=True`. Attack scripts: scratchpad `a1.py`, `a2.py` (not in the repo).

**Overall:** the design is sound and the gate is real. Three text defects in the rule have to be fixed before the hash, because
nothing above `=== RESULTS BELOW ===` can change after it. All three are cheap. Five non-blocking items follow.

## Blocking (fix before hashing)

**B1. The follow-ups contradict a decision Devin already made (lines 88-89).** DECISIONS 2026-10-14 12:31 says the START 5M plan
stays all season and that the 2023-24 historical `*_alternate` pull is approved ("feeds ALT_LINE_70 Part 3"). The draft was
committed later (13:02) but still says "the 5M plan is cancelled after the month ... START 100K at $59/month" (line 88) and "Vendor,
plan and spend are Devin's" with an 80k-credit estimate (line 89). The decision also names a Part 3 that this rule does not have.
*Fix:* rewrite lines 88-89 to cite the 12:31 decision. State that the test on historical alternates is a **separate rule id**
(proposed `ALT_LADDER_HIST`), not Part 3 of this one. Reasons: the data arrives after this rule's gate and hash, so adding a part
later would change a frozen rule (non-negotiable 4). The deep N+ milestone markets are also a different population, with their
own vig and their own gate. The main session should add a new dated DECISIONS line correcting "feeds ALT_LINE_70 Part 3" to
"feeds a new rule id" (draft below), not edit the old line.

**B2. The equivalence margin was set from the counts, and the draft says the opposite (lines 36, 140, 165).** Line 140 says the
margin "is sized against the conservative half-width (0.022)". Line 165 says the thresholds "would not have changed with the counts".
The numbers show the margin did depend on them: under Devin's sketch (an interval inside +-0.02), the conservative band half-widths
(0.0215 to 0.0225, gate table, line 101-108) are wider than 0.02, so the gate **fails in 8 of 8 cells** on the conservative
bound. The bootstrap interval may be narrower, but that is unknown until scoring. Counts are not outcomes, so this forking path is
acceptable if it is disclosed. It is not acceptable while the text denies it.
*Fix:* replace the line-165 sentence with: "The 0.03 margin and gate threshold were chosen after the conservative half-widths
(0.0215-0.0225) were counted; under Devin's +-0.02 the conservative-bound gate fails 8 of 8 cells. Devin chooses +-0.02 or +-0.03
before the freeze." Add the same sentence to "What Devin must sign" item 2 (line 178).

**B3. The p-value uses the narrower cluster while the interval uses the union (line 29; applies to line 72).** OVERCONFIDENT and
UNDERCONFIDENT need "interval (union) ... AND BH q < 0.05". The p behind q comes "from the game bootstrap" only. The date-clustered
bootstrap is run because nights share injuries and pace, so its p is the honest one. Taking p from the game bootstrap alone is
anti-conservative relative to the rule's own interval.
*Fix:* line 29: "p = max(p_game, p_date), each two-sided with the +1 correction". Line 72: "stat-level BH q uses the same max-p".

## Non-blocking (do before the harness runs; none changes a decision word)

- **N1 (major): the model sees up to about 4 minutes of news the price does not.** The model's report cutoff is real tip - 60
  (`research/eval/odds_history_eval.py:72`, `LEAD_MIN = 60`). BetRivers t60 snapshots sit at a median of 64.3 minutes before tip
  (2023: min 62.5, max 79.4; 2024: 64.3-64.4; `a1.py`). The stored 2023-24 official reports are stamped 11AM or 05PM ET only (823
  files). So when tip falls in [6:00, 6:05) or [12:00, 12:05) ET, a report can enter the model after the price was taken. That is
  **50 of 1,318 games (2023) and 62 of 1,315 (2024)**. The 19:00-proxy precedent had the same shape and only ever pushes toward
  PROFITABLE. *Fix:* add attack (vii) to line 78: "drop games with a report stamp in (snapshot_ts, tip - 60]; the ROI sign must hold".
  The harness should also store the per-row lead.
- **N2 (major, Devin's call on order): running this rule first would contaminate the alternates rule.** Part 1 reports hit rates at
  every k on 2023-24, which are exactly the player-games the alternate pull will price. If ALT_LINE_70 is scored before
  `ALT_LADDER_HIST` is frozen, that rule is designed after seeing calibration at its own lines. *Safe default:* freeze
  `ALT_LADDER_HIST`'s decision words (they can wait on its own data gate) before ALT_LINE_70's harness runs.
- **N3: the decision estimand weights rungs, but the $20 plan picks units.** E1 weights each rung (2023: 3,070 rungs over 2,392
  units, 1.28 per unit; 2024: 1.18). The $20 plan draws one rung per unit. *Fix:* add E1u, the mean over units of each unit's mean
  rung profit, with the same CIs, descriptive only, at line 64. That ROI matches the plan Devin asked about.
- **N4: the gate's de-duplication is nondeterministic (gate line 224).** `over.join(under).unique(key)` keeps an arbitrary row. In
  2023, mybookieag has 861 duplicate t60 keys (624 with differing prices) and bovada has 4. BetRivers has 0 in both seasons, so arm A
  is unaffected; arm B's best price can pick an arbitrary duplicate. *Fix in the harness:* assert zero duplicates per (book, key) for
  arm A. For arm B, drop duplicated (book, key) rows or keep the latest `last_update`.
- **N5: Part 1 and Part 2 cover different players.** BetRivers posts a points quote for 77% (2023) and 83% (2024) of starter
  player-games, against 7% and 12% of bench ones (`a2.py`). The quote is visible at T-64, so this is a legitimate selection, not
  a leak. But a Part 1 CALIBRATED word does not carry over to the bet set. *Fix:* add a starter/bench split (starter10 >= 0.5) to the
  descriptive decomposition at line 41.
- **Minor:** `pytest` on the single test file exits 1 only because of the repo-wide 70% coverage floor; with `--no-cov`: 6 passed,
  exit 0. `last_update` runs up to 0.9 minutes after `snapshot_ts` (clock skew), so quotes are not stale: snapshot minus
  `last_update` has a median of 0.6 / 0.1 minutes and a maximum of 12.2 / 13.9.

## Answers to the brief

1. **Forking paths.** Setting gate thresholds after seeing counts (no outcomes) is acceptable under non-negotiable 8, but only with
   honest disclosure (B2). The ROI threshold of 0.05 is not tuned to pass: it is derived from a -0.03 loss. Using the real vig-sized
   loss of about -0.045 would have given a looser 0.065. The **+-0.03 margin against Devin's +-0.02 is flagged for Devin**: under
   +-0.02 the conservative gate fails.
2. **Part 1.** The 0.70-0.90 band is justified by price, not by outcome. A BH family of 8 is right for the directional words. The
   all-8 CALIBRATED conjunction is intersection-union and needs no correction. Game clusters nest the correlated rungs, so the CI is
   honest about rungs within a player-game, and the union with date clusters covers same-night shocks. The p-value is fixed in B3.
   The off-main P(over) matches production: gate `p_over_at` (k = point + 0.5, 0 above the support) equals
   `nba/shadow_llm/payload.py` `p_over_from_row` (k = floor(line) + 1, 0 above the support). The grid is `pge_matrix` on 199 integer
   quantiles (`odds_history_eval.py:283`). One difference, stated: the stored forecasts refit in month blocks, while production
   refits daily.
3. **Part 2.**
   - BetRivers selection is pre-tip-knowable (N5).
   - T-60 alignment: 0 snapshots fall after tip - 60, verified, but there is the 4-minute window (N1).
   - De-duplication: arm A is clean; fix arm B (N4).
   - The pooled decision with per-season point signs is consistent. It is a conjunction, so it only makes PROFITABLE harder, and no
     2024-only inference is claimed. The pooled result is 72% 2023.
   - Fees: 0, with the overround priced (8.5-9.2%).
   - Settlement: half-point lines on integer y with whole lines dropped, so there are no pushes. DNPs void, matching the played-only
     model rows.
   - $2 x 10 is fine. Use E1u for the plan (N3).
   - A floor of +2% with the lower bound above 0 is fine.
4. **What the rule cannot say.** It says plainly that it does not hold deep N+ alternates. Part 3 should be a new id (B1, N2).
5. **Prior.** `docs/PREDICTIONS_LEDGER.md` line 21 is present, with P(PROFITABLE) 0.03 and an expected ROI of -0.06.

## Six checks (pre-result; applicable ones)

| # | Check | Command | n | Result | Pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels | n/a: nothing is fitted and no result exists; the draft's attack (ii) covers it after a result | - | - | n/a |
| 2 | Planted future / as-of | `a1.py` (snapshot lead vs real tip); report stamp tally of `data/availability_backfill` | 119,448 / 77,490 BR over rows; 1,318 / 1,315 games | lead >= 62.5 min, 0 violations; 50 / 62 games inside the 4-minute window | pass with N1 |
| 3 | Missingness | `a2.py` (quote presence by role, teammate out, report, n_prior) | 27,619 / 27,583 pts rows | presence follows role (pre-tip); DNP is a void on both sides; `y` not read | pass |
| 4 | Knockout | n/a (no feature model) | - | - | n/a |
| 5 | Replication | pre-result: per-season sign required; Unibet replicate and arm B in the attack list | - | - | n/a |
| 6 | Integer support | gate `p_over_at` vs production `p_over_from_row`; binary hit at half-point lines | all rungs | same convention | pass |

Verification (unpiped): `.venv/bin/python -m pytest -q tests/research/eval/test_alt_line_70_gate.py --no-cov -p no:cacheprovider`
gave 6 passed, exit 0 (with coverage on it exits 1 on the repo coverage floor only). `ruff check` exit 0, `ruff format --check`
exit 0, `mypy research/eval/alt_line_70_gate.py` exit 0. `shasum -a 256 reports/prereg_alt_line_70/gate.json` = 9b37c931...,
which matches the draft.

## Power and family

- Part 1: 8 cells with BH. The band n is 8,987 to 76,389 line-rows across about 1,315 games. The conservative half-width is
  0.0215-0.0225; the true one is unknown until the bootstrap runs.
- Part 2: pooled 4,277 rungs over 1,407 games. Under the conservative half-width of 0.041, PROFITABLE needs a point ROI of about
  +0.041 and reaches 80% power only at a true ROI of about +0.06; under the independent value of 0.0185 the bars are +0.019 and
  +0.027. INCONCLUSIVE is the likely outcome for a true ROI between -0.02 and +0.04. That is a valid answer, not "no effect".

## Playbook check

The model class is "Quantile and distribution layers" (a stored 199-point integer grid from the GBT context-residual). Nothing is
trained.

1. **Information or capacity?** Neither: an existing forecast is tested at a new decision point.
2. **Enough data?** Part 1 has enough. Part 2 has enough only pooled.
3. **Test validity?** Real tip, integer support, DNP as a void, and 2025 never read are all verified. The open threat is the
   4-minute news gap (N1).
4. **Lineage.** ODDS_HISTORY / CALIBRATED_GRID / DECISION_SCORING guards are carried. The shuffle guard is dropped correctly,
   because nothing is fitted.
5. **What it scores against.** Reliability at the threshold and ROI at the price, not CRPS. That is correct.

Class check: the reliability table is at the point that matters, and the slices are fixed in advance. Adding the starter/bench
slice is recommended (N5).

## Single biggest risk

If PROFITABLE ever appears, the most likely reason it is false is the model knowing news the T-64 price did not (N1). The second
most likely is that the model's confidence at posted starter rungs differs from the Part 1 grid (N5).

## Not verified

- The modeler's four scratch scripts were not seen, so I cannot confirm that none of them read `y`.
- The actual publication lag of the 5PM report.
- Whether Devin can bet at BetRivers.
- The real bootstrap widths.

## Drafted DECISIONS row (not appended)

`2026-10-14 — The historical alternate-line pull feeds a new rule id (ALT_LADDER_HIST), not ALT_LINE_70 Part 3 (correction to 12:31).
What: ALT_LINE_70 has two parts and freezes without the alternates; ALT_LADDER_HIST gets its own gate on the pulled data and its decision
words are frozen before ALT_LINE_70's harness runs. Why: adding a part after a freeze changes a frozen rule, and ALT_LINE_70 Part 1 would
reveal calibration at the same 2023-24 player-games. What would reverse it: Devin choosing to score ALT_LINE_70 first and accept a
result-informed ALT_LADDER_HIST, logged as an override.`

Verdict: BLOCKED
