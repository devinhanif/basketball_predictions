# MARKET_PRIOR_RESIDUAL: pre-freeze adversary review (2026-10-21)

Reviewed: `docs/prereg/MARKET_PRIOR_RESIDUAL.md` DRAFT revision 1 (text above the marker sha256 `f4c750e3...`, file sha256 `baa01745...`),
`research/eval/market_prior_residual_{rule,eval,gate}.py`, `tests/research/market_prior_residual/`, `reports/prereg_market_prior_residual/gate.json`,
all at main `e02b00e`. Branch `agent/adversary-mpr-prefreeze`. Seasons read: 2023 and 2024 only (every loader asserts `season <= 2024`; season 2025
was never loaded). DuckDB opened read-only (through `load_inputs`, `read_only=True`). No real outcome was read: every scored number below comes from
synthetic labels drawn on the real legs and features, as in the gate.

Verdict: BLOCKED

**Pre-freeze verdict word: CLOSE_WITHOUT_RUN.** Freezing as written is BLOCKED (findings B1-B3). If Devin still wants a run, the edit list below
(all rule-changing ones flagged) is what it takes to reach READY_WITH_EDITS. The pre-authorization ("freeze and run C after the adversary says
READY_TO_FREEZE") is therefore **not** triggered: this goes back to Devin.

**Top finding.** The verdict the draft can actually reach cannot tell the two worlds that matter apart. On the real 37,528 report-season legs,
a world where our features carry H2-sized information (planted w = 0.25) and a world where our features carry **nothing** and only the market is
mis-calibrated by stat and line both come out **`PLACEBO_EXPLAINS`**, with check (b) "significant" in both (W1 0.632 [0.322, 0.974]; W2 0.511
[0.166, 0.869]). `INFO_BELOW_FLOOR`, the draft's "realistic best outcome", is a coin flip at H2's largest weight (R minus PL CI upper +0.00001).
PASS is unreachable by construction (the modeler's own G9).

## 1. Lineage judgment: a second follow-up, not a new question

A stranger's version:
1. **The question has been asked twice on these exact rows.** ODDS_HISTORY H2 (T204-T214, 2026-10-10) asked whether production's forecast adds
   anything to the Pinnacle T-60 line on 2024 props, fitted on 2023. Answer: reb yes (T208, -0.0022 [-0.0035, -0.0009], red-teamed WOUNDED), pts and
   ast no. MARKET_SHRINK (T415-T416) was framed in its own doc as "partly a replication" of H2 (DraftKings, walk-forward, money guard): reb again a
   small log-loss gain that failed the decision guard, pts nothing. That was the one follow-up CLAUDE.md allows.
2. **This draft asks it a third time with the same information and the same data.** R reads production's own as-of features (A0's inputs) and is
   scored on the same 72,674 Pinnacle legs of the same two seasons H2 used (the doc says so, sections 2 and 13). The modeler's lineage line is
   correct: distinct in construction, not in information. A model built from A0's inputs can know at most what those inputs know; G9 shows R recovers
   a third of it at w = 0.25.
3. **What is genuinely new is the instrument** (player-season and week clustering, a placebo, a disagreement regression, a positive control). New
   instruments on old data are a re-analysis, not a new question. No guard of H2 was invalid (it ran after the 2026-10-09 real-tip fix, on played
   games, at x.5 lines), so "an invalid test is not a null" does not reopen it.
4. **No outcome of the run changes anything.** The draft itself says: no 2025 touch whatever the result, not independent evidence, P(keep your
   money) 0.97. The only decision-relevant evidence for "does our model know anything the line does not" is data the market has not already been
   scored on: the 2026-27 live log.

So: **a second follow-up to a closed question (H2 -> MARKET_SHRINK -> this).** CLAUDE.md: "One pre-registered follow-up at most, then closed."

What the positive-control practice (BEST_PRACTICES 2026-10-21) asks for H2's nulls can be met **without a run**: G9 already shows the smallest
effect H2's CIs exclude. At H2's largest fitted weight the oracle gain is -0.0011 per leg pooled; H2's pts CI [-0.0010, +0.0002] and ast CI
[-0.0010, -0.0001] exclude losses beyond about 0.001. A ledger note (drafted in section 8) records that, and the H2 nulls carry their MDE.

## 2. Findings, severity-ranked (each with location and exact fix)

### Blockers (to freeze as written)
**B1. Check (b) fires when our features carry no information.** Doc line 86-87 claims (b) "protects against a log-loss gain that comes from shrinking
or re-centring M ... b is zero if R's departure from the market is noise". False for any market mis-calibration that is not a single global linear
map of `logit p_M`. R receives `line`, `logit_pm` and `stat_code` (eval.py:192, 189), so it learns stat- and line-specific market bias; the regression
`1 + logit p_M + d` (rule.py:317-319) absorbs only one intercept and one slope. Attack W2 (features irrelevant; truth = market with stat intercepts
pts +0.04, reb -0.06, ast +0.02, fg3m -0.08 logit and a -0.04 slope on the within-stat z of the line; seed 11): (b) coefficient 0.511, envelope CI
[0.166, 0.869], excludes 0 under all three clusterings (game [0.166, 0.869], player-season [0.208, 0.814], week [0.233, 0.773]).
*Fix (rule-changing):* replace the (b) design with stat fixed effects and stat-specific market slopes, and measure the disagreement against the
placebo mean instead of the market:
`P(over) ~ sum_s 1[stat=s] (alpha_s + beta_s logit p_M) + gamma (logit p_R - mean_k logit p_PL_k)`, decision on gamma.
Measured on the same worlds (B = 500): W2 gamma 0.018 [-0.395, 0.426] (no false fire), W1 0.523 [0.200, 0.887] (still detects w = 0.25), W0 null
0.125 [-0.347, 0.653]. The FE-only variant with `d = logit p_R - logit p_M` also works (W2 0.081 [-0.353, 0.474]; W1 0.626 [0.311, 0.953]).
Code: `disagreement_design` (rule.py:317) gains the stat codes and the PL logit; doc line 85 and section 14 "Disagreement regression" follow.

**B2. The positive control never measured check (c), and (c) fails in the planted-signal world.** G9 (gate.py:593-647) runs R only; the doc
(line 204) concludes "This is why `INFO_BELOW_FLOOR` exists", but `INFO_BELOW_FLOOR` needs `c_beats_placebo_sign` (rule.py:394-396, 407-408).
Attack W1 (gate's own w = 0.25 world, seed 7, K = 5 placebo draws): R minus PL -0.00031 [-0.00063, **+0.00001**] -> `c_beats_placebo_sign` False ->
verdict `PLACEBO_EXPLAINS`. Reason: PL absorbs half the planted signal (PL minus M -0.00005 in W1 against +0.00035 in the null W0), because the
derangement keeps the market columns and the same-date feature distribution, which together carry part of A0's disagreement. *Fix:* G9 must run the
whole verdict (R, PL with K >= 10, b, c) in the planted world and report the verdict word; a bar "planted w = 0.25 returns the information verdict"
(gate.py:626-640). As drafted it would fail. With B1's gamma test as the information primary, W1 passes and W2 does not, which is the
property the instrument needs.

**B3. Kill criterion (2) is not implemented.** Doc line 130: "The planted future or the A0 reproduction fails in the run: stop, no result."
`run()` (eval.py:660-690) checks the doc hash, the harness pin and the pairs sha256, and runs neither the planted future (gate.py:490) nor G6
(gate.py:249). *Fix:* call `gate.planted_future(frame, y_syn)` and `gate.g6_reproduction()` inside `run()` before `run_stages` and raise on
failure, or strike the clause from line 130. A frozen doc must not promise a guard the harness does not run.

### Majors
**M1. The draft inverts plan item C.** NEXT_SESSION item C: "primary = residual-information test (does our disagreement with the market predict
outcomes beyond the market), secondary = CRPS/Brier vs A0 and vs market-alone ... floors as LINE_CALIBRATION". The draft makes the floor-bound loss
contrast (a) and the floor on the placebo (c) necessary for PASS (lines 80-88, rule.py:405), which makes PASS about 0.01 by construction. A floor is
a loss-scale bar; LINE_CALIBRATION's -0.003 was on a log-loss contrast. *Fix (rule-changing):* PASS = gamma (B1) with binding CI lower > 0, plus
R beats PL by sign, plus the select-season condition (M2); (a) at -0.003 becomes a separate label `MATERIAL` / `NOT_MATERIAL` reported beside it.
This is what the plan asked for and is the only structure in which the rule can say yes at a plausible signal.

**M2. The select-season condition does not gate the reachable verdict.** `stage1_sign` gates only PASS (rule.py:405-406); `INFO_BELOW_FLOOR`
(407-408) ignores it. And as a filter it is weak: under the null rehearsal the 2023 coefficient is +0.092 [-0.196, +0.397] (gate.json
g8_rehearsal.select), so a null passes it about half the time. Not a loophole in intent (the doc's reason, R's measured 2023 overfit +0.00053
[+0.00004, +0.00101], is real and pre-stated), but it is a 50% filter on the one word that can fire. *Fix (rule-changing):* apply it to every
information verdict, and make it the 2023 gamma with binding CI lower > 0 (at w = 0.25 the 2024 CI lower is 0.30 on 37.5k legs; 2023 has 28.1k
scored legs, so a true signal of that size still clears).

**M3. "FAIL_WORSE_THAN_MARKET" is the null's expected word, and reads as a finding about our features.** Null W0: R minus M +0.00032 [+0.00007,
+0.00055] -> `FAIL_WORSE_THAN_MARKET` (G8 shows the same in both seasons). The cost is R's capacity, not information. *Fix:* doc line 105 adds
"(expected under the null: R's overfit cost is +0.0003 to +0.0005, G8)", or the word is renamed `FAIL_NO_GAIN_OVERFIT`.

**M4. G4's half-width is measured in the null world and understates noise under signal.** Doc line 170 and 245 ("Power is not the limit
(half-width 0.00024)"). Under signal R departs further from M: w = 0.25 half-width 0.00041, w = 0.5 0.00088 (gate.json g9). *Fix:* line 245
quotes the signal-world half-widths beside the null one.

### Minors
- m1. gate.json `seeds.synthetic_labels` is 0 while the code uses `SYN_SEED = 20261020` (gate.py:29-30). Fix: `SEEDS = {"synthetic_labels": SYN_SEED, ...}`.
- m2. gate.json `rule_status` is "Status: MISSING": the final gate ran against the 7a52a37 stub, before the DRAFT text existed. Harmless (it is the
  label-free gate), but the doc's line 158 should say so.
- m3. Doc section 14 "ISO week" should fix the date convention: `game_date` is the **ET local date** (0 of 1,249 / 1,260 games differ from the ET
  date of the real tip; 967 / 946 differ from the UTC date), `tipoff_utc` is naive UTC. Add that sentence.
- m4. Doc lines 67-68 omit `subsample_freq 1` and `force_row_wise` from `LGB_PARAMS` (rule.py:60, 65). `subsample_freq` is load-bearing (LightGBM
  ignores `subsample` without it). Add both.
- m5. G6 (doc 173-175) is a data-identity check (the pairs frame equals the frames ODDS_HISTORY published from), not an independent rebuild of A0
  from features. Say so in one clause.
- m6. G9b's bar (>= 0.40) was set after measuring 0.633 (gate.py:596 says so). Synthetic labels, so not a breach; the doc table should say it.

## 3. Check table

All commands from the worktree root; attack script in scratchpad (`scratchpad/attack/mpr_worlds.py`, run
`PYTHONPATH=$PWD uv run --no-sync python -W ignore ../attack/mpr_worlds.py ../attack/worlds.json 5 500`, exit 0, 247 s, K = 5 placebo draws,
B = 500 for the regressions, bootstrap seed 0, synthetic-label seeds W1 7, W2 11, W0 20261020). n = 37,528 report-season legs, 1,260 games /
414 player-seasons / 35 weeks in every world.

| # | check | command / evidence | numbers (n, CI) | pass |
|---|---|---|---|---|
| 1 | Priors before the gate | `git show 7a52a37`; `git log --date=iso` | stub 23:12:59 CDT; harness b7675e6 23:47:04; gate.json started 23:47 at b7675e6; draft 6599bc1 23:52:40 | pass |
| 1 | Rule hash | `awk ... \| shasum -a 256` | f4c750e3... (matches brief) | pass |
| 1 | Completeness | read sections 1-15 | kill criterion (2) unimplemented (B3); no positive control on (c) (B2) | **fail** |
| 2 | Constants doc vs code | FLOOR rule.py:40 / doc 81; N_BOOT, SEED 41-42 / 94; CLUSTERINGS, MIN_WEEKS 43-46 / 95-97; N_MIN_TRAIN 47 / 123; HALF_LIFE 48 / 70; K 49 / 88; LGB 52-68 / 67-68 | all match except m4 | pass (minor) |
| 2 | Verdict logic | rule.py:382-417 vs doc 100-105 | matches the doc; the doc's logic is the problem (B1, B2, M2) | pass as mirror |
| 2 | Refusals | rule.py:93-98, 136-139, 153-167, 178-189; eval.py:112, 284, 662-671 | DRAFT, hash, freeze commit, harness pin, pairs pin, season > 2024 | pass |
| 2 | Known-at filter | rule.py:202-207, eval.py:128; gate G5 | 0 dropped, min lead 62.55 min; ET/UTC conventions consistent (lead 64.4 min would be hours off otherwise) | pass |
| 2 | Envelope = binding | rule.py:272-284, eval.py:434-436, 495-501 | [min lo, max hi], p = max p; at least as wide as the widest, as written | pass |
| 2 | BH family = declared 8 | eval.py:535-554 | 4 per-stat R-M + 4 per-stat coefficients | pass |
| 3 | Placebo validity | W0/W1/W2 | null R-PL -0.00003 [-0.00032, +0.00022] (unbiased in 2024; gate 2023 point -0.00027); W1 PL absorbs half the signal | valid null, **too strong a control** (B2) |
| 3 | Label shuffle needed? | reasoning + G8 | no: a shuffle makes R beat M by shrinking an informative offset; the market-drawn world (G8, W0) is the right label null and exists | n/a |
| 4 | Select-season sign | gate.json g8 select | null 2023 coef +0.092 [-0.196, +0.397]: ~50% of nulls pass | legitimate, weak, not applied to the reachable word (M2) |
| 5 | Data vintage | doc 183-188; eval.py:233-244 | primary R vs M and R vs PL never read A0; R trains and scores on one feature vintage; only R-A0 and H2W mix vintages (disclosed) | pass |
| 6 | CI code | model_clv_eval.py:241-278; line_calibration_eval.py:432, 811 | same `Boot` (paired per-row contrast, multinomial cluster weights, ratio mean, percentile) LINE_CALIBRATION used; player-season key `player_id|season`, week `iso_year-week(game_date)` (rule.py:241-254) | pass (m3) |
| 7 | G4 half-widths | gate.json | null: select 0.00048, report 0.00024 (game 0.000232 / player-season 0.000230 / week 0.000233); signal w=0.25 0.00041, w=0.5 0.00088 | reported (M4) |
| 7 | G9 re-derived | gate.json g9; attack W1 | w=0.25 oracle -0.00110, R-M -0.00036 [-0.00076, +0.00005]; w=0.5 oracle -0.00615, R-M -0.00389 [-0.00478, -0.00302] | reproduced |
| 7 | (b) false-fire, market mis-cal only | attack W2 | doc (b) 0.511 [0.166, 0.869] **fires**; R-PL -0.00003 [-0.00026, +0.00021]; verdict PLACEBO_EXPLAINS | **fail** (B1) |
| 7 | Verdict in planted w=0.25 | attack W1 | (b) 0.632 [0.322, 0.974]; R-PL -0.00031 [-0.00063, +0.00001]; verdict **PLACEBO_EXPLAINS** | **fail** (B2) |
| 7 | Verdict under null | attack W0 | R-M +0.00032 [+0.00007, +0.00055]; (b) -0.107 [-0.587, 0.361]; verdict FAIL_WORSE_THAN_MARKET | expected (M3) |
| 7 | Proposed gamma test | attack W0/W1/W2 | W1 0.523 [0.200, 0.887]; W2 0.018 [-0.395, 0.426]; W0 0.125 [-0.347, 0.653] | separates the worlds |
| 9 | Cost | attack timings | R walk-forward on 2024 ~11-17 s; one PL draw ~10 s a season; regression bootstrap ~2 s per call at B = 500 | see section 6 |
| - | Harness tests | `uv run --no-sync pytest -q tests/research/market_prior_residual/` | 26 passed, exit 1 (exit from the repo-wide 70% coverage floor on a subset, not a test failure) | pass |
| - | Lint/types | `ruff check`, `ruff format --check`, `mypy` on the three harness files | exit 0 / 0 / 0 | pass |

**Multiplicity and power note.** The family actually run in this review: three synthetic worlds x four tests, all synthetic and decision-free, so
no BH is owed. For the rule: the declared secondary family of 8 under BH is implemented as declared. Power: under the null the report-season
95% half-width of R minus M is 0.00024 (MDE at 80% about 0.00034); under signal it is 0.0004-0.0009. At H2's largest fitted weight the whole
oracle effect is 0.0011 and R's is 0.00036: the primary (a) cannot pass at any weight H2 has observed, and the (c) floor likewise. That is
"guards that cannot pass" in MODEL_PLAYBOOK question 3.

**Is INFO_BELOW_FLOOR meaningful?** As drafted, no: it needs (b), which fires on pure market mis-calibration (W2), and (c)-sign, which fails at
H2's weight (W1). It is a consolation word that the plausible-signal world does not reliably reach and that would mean "information" only if it
did. With the gamma test of B1 it becomes the meaningful word, and then it is the plan's primary, not a consolation (M1).

## 4. The Devin question (floor -0.003, -0.0010, or close)

**Recommendation: close without a run.** If Devin wants the question asked anyway, keep -0.003 but move it off the information verdict.
- **Not -0.0010.** Lowering a practical floor because the instrument cannot reach it inverts what a floor is for (the draft's own reason for
  -0.003 is that a 3.5 pp edge is what pays a 7.0% overround). It also does not help: at w = 0.25, R minus M is -0.00036, a third of the way to
  -0.0010.
- **Not "freeze as written".** The written rule returns `PLACEBO_EXPLAINS` both when our features know something and when they know nothing
  (W1, W2). A rule whose likely outputs do not discriminate is not worth 30 minutes of CPU and a ledger block.
- **Close, and move the question to new data.** The draft already names its one follow-up: "the live-capture version, under its own id". Pre-register
  the gamma test (stat FE, stat slopes, disagreement of the served model vs the live T-60 Pinnacle quote) on the 2026-27 forward log, with this harness's
  planted-world gate as its positive control. That is a new question on data nobody has scored. Meanwhile record H2's MDE (section 8).
- **If Devin overrides and wants it run on 2023-24:** apply B1-B3 and M1-M3 (rule-changing: B1, M1, M2), re-run the gate with the full-verdict G9
  and the W2 negative control as new gate checks, then return for a re-review. That is a new rule revision, and it stays a re-analysis of the data H2
  used: label any result exploratory.

## 5. Playbook check (MODEL_PLAYBOOK; class: gradient-boosted trees on a market offset, scored as a binary at the line)
1. **Information or capacity?** Capacity. No input is known before tip that A0 did not already have; the market quote is the comparator's own
   input. The playbook's record predicts a tie or a loss, and G8 measures the cost (+0.0003 to +0.0005 per leg).
2. **Enough data for the class?** For a 4-leaf, 200-tree offset model on 28k-73k pooled legs, yes; the binding limit is signal size, not rows (G9).
3. **Valid test?** Real tips (lead 62.55-79.37 min), played games, x.5 lines, three clusterings, a placebo sized from a null: yes. But the
   information check (b) is not valid as specified (fires on market mis-calibration, W2), the placebo is too strong to pass a planted signal
   (W1), and two of three primary checks cannot pass at any observed weight. An invalid test is not a null, which is one more reason not to run it.
4. **Lineage:** H2 (T204-T214) and MARKET_SHRINK (T415-T416); same information and rows; this is a second follow-up (section 1).
5. **Scored against:** log loss and Brier at the Pinnacle line, the right scale for the question; CRPS correctly dropped.
- **Class checks (trees).** Missingness: G7 max NULL 0.048%, leg selection by line posting, not by outcome; not outcome-asymmetric on the
  columns I could check label-free. Planted future: G8 2 of 2 blocks, but not run inside `run()` (B3). Does the arm only re-express the base?
  By construction it re-expresses A0's inputs; that is the lineage finding.

## 6. Cost (check 9)
The 25-35 minute estimate is plausible from measured pieces: 50 placebo draws x 2 seasons x ~10 s is about 17 minutes; 36 regression bootstraps
at B = 2,000 (~8 s each) about 5 minutes; R, R_NR and DraftKings fits a few minutes more. Peak memory is small (the placebo array is 50 x 37,528
doubles, 15 MB; the frame is 72,674 x ~55). It matters only operationally: it exceeds the 600 s agent watchdog, so the maintainer runs it in the
background under `data/ops/heavy.lock`. Not a reason to block; the reason to close is section 1 and B1-B2.

## 7. Biggest risk if the draft were run as written
A run returning `INFO_BELOW_FLOOR` (or a significant (b) under `PLACEBO_EXPLAINS`) gets read as "our model knows something Pinnacle does not",
when the same output arises from a stat-level calibration bias in the market that the regression's single intercept cannot absorb (W2). That is
the opp_adjusted_ridge pattern in a new form: a check that passes the name test but not the construction test.

## 8. Drafted rows (not appended; the main session acts)
- **TEST_LEDGER (note, next free id after T501):**
  `| T5xx | 2026-10-21 | MARKET_PRIOR_RESIDUAL (DRAFT f4c750e3, not frozen) | pre-freeze adversary review: CLOSE_WITHOUT_RUN recommended. Synthetic worlds on the real 37,528 Pinnacle 2024 legs (K 5, B 500): planted H2 w=0.25 -> PLACEBO_EXPLAINS (R-PL -0.00031 [-0.00063, +0.00001]); market mis-calibration only, features inert -> (b) 0.511 [0.166, 0.869] fires, PLACEBO_EXPLAINS; null -> FAIL_WORSE_THAN_MARKET (R-M +0.00032 [+0.00007, +0.00055]). Positive control for H2's nulls (T204, T212): at H2's largest fitted w=0.25 the oracle gain is -0.0011/leg pooled (gate G9), so H2's CIs exclude effects beyond ~0.001. No real outcome read; season 2025 not loaded. | docs/reviews/MARKET_PRIOR_RESIDUAL_prefreeze_2026-10-21.md |`
- **DECISIONS:** `| 2026-10-21 | Plan item C (MARKET_PRIOR_RESIDUAL): <Devin's choice: closed without a run / revised>. Why: the adversary found it is a second follow-up to H2 on the same information and rows, its reachable verdict does not separate feature information from market mis-calibration (W1 vs W2), and PASS is unreachable by construction. Follow-up, if any: the gamma disagreement test pre-registered on the 2026-27 live log. Reverses if: Devin wants a 2023-24 re-analysis anyway (then B1-B3, M1-M3 and a re-review). |`
- **PREDICTIONS_LEDGER:** the modeler's rows 1-2 stay unscored if closed (no result); note "closed without run" against them.
- **HOLDOUT_ACCESS_LOG:** none.

## 9. Not verified
- The real size of Pinnacle's stat- and line-level mis-calibration in 2023-24 (it needs real labels, out of scope). W2's magnitudes (up to
  0.08 logit, about 2 pp) are an assumption chosen to be modest; the finding is that (b) is not robust to any such bias, not that this one exists.
- K = 5 placebo draws here against K = 50 in the rule; R minus PL CIs are dominated by row noise, so K matters little, but W1's borderline upper
  (+0.00001) could land on either side at K = 50 or another outcome seed. That is the point: it is a coin flip.
- The live T-60 lead distribution (doc line 147), peak RSS of the real run, DraftKings and R_NR paths (not executed here).
