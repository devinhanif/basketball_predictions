# MARKET_ANCHORED_PROPS: adversary pre-freeze review (2026-10-23)

Reviewer: adversary. Clock: `date` read Fri 2026-10-23 21:27 CDT. Object: the DRAFT pre-registration on branch
`agent/modeler-market-anchored-props` at 976dfdf (commits be9d6f2, 40c8931, 0a773d7, 976dfdf): `docs/prereg/MARKET_ANCHORED_PROPS.md`,
`docs/research/market_anchored_props_2026-10-23.md`, `research/eval/market_anchored_props_{core,gate}.py`, `reports/prereg_market_anchored_props/gate.json`.
Requested by the main session after Devin approved the review (2026-10-23, about 23:20 CDT; this is his override of the T502 close).
Read scope: seasons 2023 and 2024 only. Every outcome I used is synthetic. No real label was selected, and season 2025 was never loaded.
DuckDB was opened read-only only, on a byte copy of `odds_history.duckdb` (see finding R4 for why).

Verdict: NOT_READY

(In the two-word checklist vocabulary this is BLOCKED.) The design is close and its core idea works. Nested arms plus a derangement placebo do keep a planted
model signal and a market signal of MR's own form apart, and I reproduced the gate exactly. But the market-only baseline MR does not include the
main effects of the three covariates that the model terms interact with (`bench`, `tmout`, `disp10`). So a market mis-calibration along any of
those covariates, with no model information at all, flows into `C_mod`, the model word's own number. The gate cannot see this because its
only market-only world is built from exactly MR's columns. Adding three columns fixes it in every world I tried and costs nothing in power
(numbers below). There are seven more edits, two of them blockers. None needs a real outcome, so the rule can be refrozen tonight after a gate rerun.

**Top finding.** In a model-inert world where the market overprices bench overs (oracle 0.003), the draft's `C_mod` CI excludes 0 in 8 of 10
replicates (mean C_mod -0.00052). Only the placebo saves the verdict (PLACEBO_EXPLAINS 8/10), and the market word misses the effect
(C_mkt -0.0007 of an oracle 0.003; FAIL_SELECT_SIGN or DETECTED_BELOW). With bench/tmout/disp10 main effects in MR, the same world gives
model word NONE 10/10 (C_mod +0.00004) and market word MISCALIBRATION_INFO 10/10. Both positive controls are unchanged.

## 1. Is the claim really separated? (the key question)

| question | answer |
|---|---|
| Does a planted model signal land in the model word and not the market word? | Yes. Gate G7c/G7d/G7f reproduced exactly. In my reruns (seeds 888xxx) W_mod 0.0011 gave model word DETECTED 8 + INFO 2 of 10; W_mod 0.0030 gave INFO 10/10, with a false market word 1/10 (MISCALIBRATION_INFO). |
| Does market mis-calibration of MR's own form land in the market word? | Yes (gate G7b: 10/10; model word 0/10). |
| Does market mis-calibration *outside* MR's span stay out of the model word? | **No** (R1). `delta` has mean -0.24 logit (the model's P(over) sits below the market, T477), and its mean differs by group: bench -0.31 vs starter -0.22, tmout -0.20 vs -0.26, hi -0.42 vs -0.19. So `delta x bench` behaves like a bench intercept and `delta x tmout` like a tmout intercept. MR has no such intercepts. |
| Order dependence and shared variance | Handled correctly for the model word (C_mod = MM - MR is the model credited second). The market word guard `C_mkt_after >= 0 -> MODEL_EXPLAINS` is a point-estimate test whose margin in the model worlds is only the overfit cost of the extra MR terms: the smallest committed value was +0.00001 (W_mod 0.0030, replicate 1). The C_mkt_after CI was below 0 in 0 of 20 model-world replicates, so using the CI guard for the consequence-bearing word removes the false INFO at no cost (R6). |
| Is the identity C_total = C_mkt + C_mod = C_mod_first + C_mkt_after exact? | Yes, by construction: per-leg differences of the same four arms on the same mask (core.py 416-428). |

## 2. The six checks

| # | check | command / evidence | n | numbers (CI or rate) | pass |
|---|---|---|---|---|---|
| 1 | Shuffled labels | gate null world W0 (labels drawn from the market = a label shuffle that keeps the price), rerun | 72,674 legs; 37,528 scored 2024; 10 reps | any word fires 0/10; C_mod +0.00005, binding half-width 0.00009; my rerun at K=10, B=500: C_mod +0.00003, hw 0.00011, detected 0/10 | PASS |
| 2 | Planted future | gate G6, rerun: flip every label on or after 2025-01-01 (calendar; season label 2024) | 6,552 test-month legs | max abs change MM 0.0, MR 0.0; a training-window flip moves MM by 0.909 | PASS |
| 2b | As-of of the market-side columns | `count(distinct snapshot_ts)` per game over t60 rows of the four prop markets plus spreads, all books (DB copy) | 1,318 / 1,315 games | max 1 snapshot time per game in both seasons, so the soft books, the spread and Pinnacle share one request; Pinnacle lead min 62.55 min (G5) | PASS |
| 2c | One-day lag | n/a: every input is either the T-64 snapshot or production's as-of frame (`p_model` walk-forward by month, `odds_history_eval.collect_stat_rows` trains on `game_date < start`) | - | - | n/a |
| 3 | Missingness (label-free part) | `attack_map.py` | 72,674 | null `p_model` 0, `starter10` 0, `n_out_rot` 0.05%, `u_mean`/spread 0.38%; `miss_u1` 7.4% bench vs 2.2% starters, 5.2% tmout vs 2.4%: book coverage at T-64, knowable pre-tip | PASS (label-free); outcome-group table not run (no real labels allowed), R5 |
| 4 | Knockout / off-span attribution | `attack_map.py` (5 reps, oracle 0.001) and `attack_map2.py` (10 reps, oracle 0.002-0.003), draft vs fixed MR | 37,528 scored | see table in section 3 | **FAIL (draft)**, PASS (fixed) |
| 5 | Replication | gate rerun from the branch (`gate_wrap.py`, same seeds): 22/22 checks, every value identical to the committed gate.json; my independent seeds reproduce the world summaries within noise | 10 reps per world | W_mod 0.0011 C_mod -0.00064 (committed) vs -0.00079 (mine); null C_total +0.00016 vs +0.00014 | PASS |
| 6 | Integer support | every arm is scored as a binary at an x.5 line on the same legs; `p_model` comes from the integer survival grid at the line | 72,674 | no continuous-vs-grid mixing | PASS (n/a by construction) |

Commands (run from the branch worktree `.claude/worktrees/agent-a3c24a04ee7381800`; scripts are in my scratchpad, not the repo):
```
S=<scratchpad>; P=/Users/devin/Downloads/nba-prediction/.venv/bin/python
cp data/odds/odds_history.duckdb $S/dbcopy/                                   # live file locked by the movement pull (PID 70578)
PYTHONPATH=$PWD $P -W ignore $S/attack_map.py  $S/attack_map.json 5           # exit 0
PYTHONPATH=$PWD $P -W ignore $S/attack_map2.py $S/a2_draft.json draft 10      # exit 0
PYTHONPATH=$PWD $P -W ignore $S/attack_map2.py $S/a2_fixed.json fixed 10      # exit 0
PYTHONPATH=$PWD $P -W ignore $S/gate_wrap.py --k-null 20 --boot 1000 --reps 10 --out $S/gate_rerun.json   # exit 0, all_pass=True, 305.9 s
```
`gate_wrap.py` only points `core.ODDS_DB` at the copy and calls `gate.main`. The "fixed" design monkeypatches `core._market_cov` to add
`bench, tmout, disp10` and widens `BETA0_MR` / `BETA0_MM` (MR 15 parameters, MM 22). Attack runs use K = 10 placebo draws and B = 500.

## 3. Off-span market worlds (the attack behind R1)

Model-inert worlds. Labels are drawn from `eta = m + c * z`, with c bisected so the oracle KL on 2024 hits the target. Report season 2024,
10 replicates, means. "det" is the number of replicates in which C_mod's binding CI is below 0.

| world (oracle) | design | C_mod | det | C_pl | C_mkt | C_mkt_after | model words | market words |
|---|---|---|---|---|---|---|---|---|
| null | draft | +0.00003 | 0 | -0.00001 | +0.00010 | +0.00010 | NONE 10 | NONE 10 |
| null | fixed | +0.00004 | 0 | -0.00000 | +0.00014 | +0.00015 | NONE 10 | NONE 10 |
| bench over-bias, z = -bench (0.002) | draft | **-0.00029** | **7** | -0.00001 | -0.00053 | -0.00029 | PLACEBO_EXPLAINS 7, NONE 3 | DETECTED_BELOW 5, FAIL_SELECT_SIGN 5 |
| same | fixed | +0.00006 | 0 | +0.00002 | -0.00194 | -0.00135 | NONE 10 | MISCALIBRATION_INFO 10 |
| bench over-bias (0.003) | draft | **-0.00052** | **8** | -0.00004 | -0.00070 | -0.00038 | PLACEBO_EXPLAINS 8, NONE 2 | DETECTED_BELOW 7, FAIL_SELECT_SIGN 2, NONE 1 |
| same | fixed | +0.00004 | 0 | -0.00000 | -0.00278 | -0.00190 | NONE 10 | MISCALIBRATION_INFO 10 |
| book dispersion main effect (0.002) | draft | -0.00023 | 5 | +0.00002 | -0.00037 | -0.00022 | NONE 5, PLACEBO_EXPLAINS 5 | NONE 4, FAIL_SELECT_SIGN 6 |
| same | fixed | +0.00008 | 0 | +0.00004 | -0.00208 | -0.00162 | NONE 10 | MISCALIBRATION_INFO 10 |
| heavy depletion main effect (0.002) | draft | -0.00028 | 6 | -0.00002 | -0.00057 | -0.00046 | PLACEBO_EXPLAINS 6, NONE 4 | DETECTED_BELOW 7, NONE 2, FAIL_SELECT_SIGN 1 |
| same | fixed | +0.00003 | 0 | -0.00001 | -0.00185 | -0.00142 | NONE 10 | MISCALIBRATION_INFO 10 |
| W_mod gamma 0.222 (0.0011) | draft | -0.00079 | 10 | -0.00084 | -0.00021 | +0.00009 | DETECTED 8, INFO 2 | NONE 7, MODEL_EXPLAINS 2, DETECTED_BELOW 1 |
| same | fixed | -0.00077 | 10 | -0.00082 | -0.00021 | +0.00011 | DETECTED 10 | NONE 7, MODEL_EXPLAINS 2, DETECTED_BELOW 1 |
| W_mod gamma 0.369 (0.0030) | draft | -0.00187 | 10 | -0.00189 | -0.00092 | +0.00007 | INFO 10 | MODEL_EXPLAINS 9, MISCALIBRATION_INFO 1 |
| same | fixed | -0.00179 | 10 | -0.00183 | -0.00098 | +0.00009 | INFO 10 | MODEL_EXPLAINS 9, MISCALIBRATION_INFO 1 |

At oracle 0.001 (5 reps, draft design) the leak into C_mod was -0.00014 (bench), -0.00011 (dispersion) and -0.00005 (tmout). These are the size of
the rule's own prior for the real C_mod (-0.0002). Curvature in m (`-c m|m|`) and a continuous line level gave C_mod +0.00006 / +0.00002, with
0/5 detected and the market word firing: MR's slope and `hi` absorb those. **Today the placebo is what keeps the draft from a false
model word**, and it works only because `mean(delta) != 0` lets a deranged delta carry the same intercept. That is a lucky accident of the
model's bias, not a designed property. The draft's market word also misses these effects, so the decomposition's two words are both
wrong for a market error MR cannot express. A combined world (true model information plus a bench-market effect) would inflate C_mod
and leave only the placebo between it and a mis-sized INFO word. I did not run that world.

Real-world plausibility: the gate's own design input is Devin's live week, where bench minutes were over-projected by 4.7. The market's
over bias (T477, +1.5 pp) is the classic "public bets bench overs" pattern. An over-bias that varies by role is the most likely
market error on these legs, and exactly the one MR cannot express.

## 4. Required edits (numbered; all label-free; each needs the gate rerun)

1. **(blocker) Give MR the main effects of every covariate the model terms interact with.** In the prereg section 3 table (line 71), MR becomes
   "B0 + {hi, u1, miss_u1, blowout, bench, tmout, disp10}" (15 parameters) and MM 22 (line 73). Section 10 line 209 and section 11 line 222 ("19") follow.
   In code: `core._market_cov` (core.py 212-213) appends `A["bench"][idx], A["tmout"][idx], A["disp10"][idx]`; `BETA0_MR` (line 244) becomes
   `np.concatenate([BETA0_B0, np.zeros(7)])`; `MR_COLS` (60-67) gains the three names. This is also BEST_PRACTICES 2026-10-22 ("baselines must know
   what the model knows") and playbook question 3 ("a comparator missing information production had"). Measured cost: null C_total +0.00018 (2024) /
   +0.00040 (2023), under WORSE_FLOOR and K1. Detection of W_mod 0.0011 stays 10/10.
2. **(blocker) Add off-span market worlds to the gate as a check, so the separation claim is tested outside MR's span.** `core.world_eta` (490-498)
   gains kinds for bench, tmout and centred disp10 main effects, plus curvature `-c m|m|` and a within-stat standardised line level, each sized to an
   oracle of 0.002 by bisection like `calibrate_gamma`. `gate.run` (spec at 327-333) runs them, and a new check G7g requires a false model word
   (DETECTED/INFO/BET) rate <= 0.20 **and** C_mod CI-below-0 rate <= 0.20 in every off-span world. The draft would fail G7g (7/10 and 8/10
   C_mod detections on bench). The text of section 8 and the note's section 3.3 sentence "Mis-calibration cannot be mistaken for model knowledge
   because the market-only arms receive every market-side input the model arm receives" (note line 167-168) must be corrected: that is true only
   for the inputs in MR, and the gate is the evidence.
3. **(blocker) Pin the market-side inputs, which are being rewritten tonight.** `odds_history.duckdb` is under an active writer
   (`nba.odds history-pull --phase movement --season 2024 --kinds m360,m180,m90`, PID 70578). My read-only connection to the live file failed
   (`IOException: Conflicting lock`), and the file now holds 239,296 season-2022 game-market rows and 2024 prop snapshots `m90/m180/m360/m1440`
   (Pinnacle points: 2,392 / 2,454 / 2,384 / 404 rows on about 125 games). The pairs pins cover `p_model` but not `u1`, `disp10` or `blowout`.
   Fix: the freeze commit writes the extras frame from `core.odds_extras` (`game_id, player_id, stat, line, u_mean, u_sd, n_soft, spread_abs`, 2023 and
   2024) to a parquet with its sha256 in the rule text next to the pairs pins. The run reads that file and refuses a mismatch. It never opens the live
   DB, so the single-writer lock is moot. Correct section 9 (lines 195-196: "`odds_history` has 0 rows before 2023" is now false for game markets, though
   still true for props) and section 7 / 9 ("no history of line movement before t60", lines 132 and 196). Pre-t60 movement snapshots are being captured
   right now for 2024. State that they are out of scope here (no select-season copy, a new question, a new id), and list them in the capture audit
   (non-negotiable 8).
4. **(major) The product consequence must require the pool to beat the market.** Lines 99-101 propose replacing `shrink_to_market` with the
   fitted pool on model word INFO / BET / DETECTED+live. But a model word can fire while C_total >= 0, because MR's own fit can cost more than the
   model terms add, and the override only fires at +0.0005. Fix: add "and C_total binding CI upper < 0" to every consequence that changes a displayed
   probability, for both words. Otherwise the record is "information, no product change".
5. **(major) The post-freeze eval harness gets its own adversary check, and must reproduce the gate before it reads a label.** Lines 9-12 say the
   rule and eval files are written after the freeze. Add: (a) `market_anchored_props_eval.py dryrun` re-runs the gate worlds through the eval code
   path and must reproduce `gate.json`'s world summaries exactly (same seeds), (b) the adversary checks the eval against the frozen text before the
   `run`, as in the T510 precedent (L-S2), and (c) before any C_* is computed, the run writes the missingness table on real labels: null / `miss_u1` /
   `n_soft < 3` / spread-missing rates by minutes < 5 vs >= 5, over vs under at the line, and starter vs bench. Any asymmetry is reported first.
   All inputs are T-64 snapshot fields, so an asymmetry is not by itself a leak, but it is the check that broke three experiments here.
6. **(major) Market word: the consequence-bearing tier uses the CI guard.** Lines 92-93 and core.py 469-476: keep `C_mkt_after point >= 0 ->
   MODEL_EXPLAINS`, but `MISCALIBRATION_INFO` additionally needs the C_mkt_after binding CI upper < 0, else
   `MISCALIBRATION_DETECTED_BELOW_INFO_FLOOR`. Evidence: the false MISCALIBRATION_INFO in the W_mod 0.0030 world (1/10, both designs) had
   C_mkt_after CI below 0 in 0/10 replicates. Every off-span market world under the fixed design had it below 0 in 10/10. A point-only guard
   decides with a margin as small as +0.00001 (committed gate, W_mod 0.0030 replicate 1).
7. **(major) Define the secondary p-value; the shared Boot returns the wrong tail.** `Boot._pack` (model_clv_eval.py 267) returns
   `p = (1 + #{bm <= 0}) / (B + 1)`, the p for "mean > 0". This rule's wins are negative. Line 103 must say:
   `p = max over {game, player_season, week} of (1 + #{b: bm_b >= 0}) / (B + 1)`, and drop the undefined word "decisive". Calling `Boot.mean(...)["p"]`
   as written would give a real gain p of about 1 and a real loss p of about 0, which flips the BH family.
8. **(minor) The verdict vocabulary is not exhaustive.** Add `KILLED_K1` (line 119: 2023 C_total > +0.0010, 2024 not scored) and
   `NO_RESULT_INVALID_RUN` (line 128: planted future, leg reproduction, pin or extras-pin failure), and state the precedence the code already uses
   (core.py 449: the override is evaluated first and replaces both words). Re-state the section 12 priors after edits 1-6, since the market word's
   reachable states change. They are still blind to real outcomes, so revising them before the freeze is allowed, with the reason logged.

Recommended (not required): report `LAM` in {20, 100} as descriptive sensitivity with the fit's non-convergence count (`fit_logit` has no
convergence flag at 40 Newton steps). Drop `novig` / `prophetx` from the `u1` pool or slice on 2024-12-19, because the soft-book composition changes mid-report-season.
Rename `BET_FLOOR_PASS` to something that does not read as a bet signal outward (`LARGE_GAIN_FLOOR_PASS`). Report the placebo draw-mean sd next
to the C_pl CI (BEST_PRACTICES 2026-10-12; committed null: 2.5e-5 to 5.1e-5 per replicate at K = 20).

## 5. The modeler's four questions

- (i) Market word guard: too lenient as a point test for the INFO tier (edit 6), about right for MODEL_EXPLAINS.
- (ii) `u1` when books copy Pinnacle: copying drives `u1` toward 0 and makes it inert, not leaky. It is same-snapshot (2b above). The real issue is the
  mid-season exchange entry (recommended edit).
- (iii) `tmout` as a team-quality proxy: as an interaction-only term it was a market-error channel into C_mod (section 3). With its main effect in MR
  (edit 1), any team-quality content goes to the market side, which is where it belongs.
- (iv) 2024 stored `p_model` vintage: the placebo is internally valid either way (same `delta`, deranged). The result is about the pinned vintage
  (`560a3808...`), not today's production model. Say so in the verdict text; Stage 3 is the test of the current model.

## 6. Prior closed tests: does any already answer it?

No. None separated model from market. H2 (T203-T214) and MARKET_SHRINK (T415-T419) measured a one-weight pool (closest to C_total), T493 measured the
model alone (CAL loses to the market by 0.0017), T320-T324 a GBM pool, and T389-T414/T501 game context into the model. T494 is the nearest answer:
model-only terms add -0.0002 on DK/FD rungs after the main line, which matches this rule's prior (-0.0002) and says the honest expectation is NONE or
DETECTED_BELOW. T502 closed because its words could not separate. This design's nesting is the repair, once edit 1 is in. Not CLOSE.

## 7. Holdout, rule integrity, multiplicity, power

- **Holdout:** `rule.assert_seasons` guards `load_legs` and `odds_extras`, and every SQL has `season = s`. My attacks loaded 2023-2024 only (asserted). No
  HOLDOUT_ACCESS_LOG row is needed; the rule declines a 2025 touch, and I agree.
- **Rule integrity:** DRAFT, so there is no hash yet. The branch diff (bfe24f4..976dfdf) is 8 files, all in scope. The gate ran at be9d6f2 (gate.json `git_head`).
- **Multiplicity:** primary, two words from two different contrasts, with no correction between them (acceptable; neither is a best-of). Secondary: BH over 10,
  declared. Design iterations used synthetic labels only (not a hidden family on outcomes). After edit 7 the family is as stated.
- **Power / MDE:** null binding half-width of C_mod 0.00009 (committed) / 0.00011 (my K=10 runs). A planted oracle of 0.0011 is detected 10/10, with the
  half-width growing to about 0.0004 under signal. Oracles below 0.0011 were not measured, so the smallest detectable effect is "<= 0.0011 oracle, about -0.0006
  C_mod". INFO needs an oracle of about 0.0017+, and BET about 0.0045+ (essentially unreachable given H2).

## 8. The two flags

- **Flag 1, parlay deferral gate (VERIFIED).** `configs/parlay.yaml` lines 34-38 (`market_prior: k: 100`, `gate_ci_level: 0.95`).
  `nba/parlay/shadow.py` `track_record` (159-191) scores `raw_model_prob` against `market_mid` on settled YES shadow rows; `gated_skill` (114-125) opens only if
  the CR1 game-clustered lower bound of the paired Brier difference is > 0 over >= 14 dates; `nba/parlay/ev.py` `shrink_to_market` (65-74) then puts
  weight n/(n+100) on the model in a linear pool. So the gate tests the model's own probability alone, not a pool, and the weight grows with the row
  count, not with the precision on the disagreement. The research note's description is correct ("calibrated model" should read "the engine's
  `raw_model_prob`"). Changing it is a live-path change and Devin's decision. Recommendation: no change until this rule's model word is not NONE **and**
  C_total < 0 (edit 4).
- **Flag 2, T510 DNP treatment (VERIFIED, frozen rule, not editable).** All four series' rules text (`data/raw/live_2026-10-22/kalshi_rules/{KXNBAPTS,
  KXNBAREB,KXNBAAST,KXNBA3PT}/20261022T210001Z.json.gz`, `rules_secondary`) say: "If a player is inactive, or active but never takes the court, the market
  settles to a fair price." T510 (`docs/prereg/LINE_MOVE_TOWARD_MODEL.md` lines 22 and 31) defines p_M = P(stat >= k) x `p_play` and "treats a DNP as No".
  The Kalshi mid therefore prices P(Y >= k | plays), while p_M is the unconditional probability, biased low by a factor of `p_play`. Effect: legs with
  `p_play < 1` qualify (|p_M - mid0| >= 0.05) on availability alone, with no reason for the conditional price to move toward M. That dilutes part 1
  toward NULL or HARM. Part 2 mostly cancels because B shares `p_play`. It does not create a false PASS. The engine itself is consistent with the rules
  (`nba/parlay/joint.py` 145-151, default `--dnp-policy last_fair_price`). Since T510 is frozen: read its result alongside the pre-declared L-S6 slice
  (`p_play < 0.9`), and if the main session wants the conditional definition, it is a new id (proposed T510b), not an edit.

## 9. Playbook check

Class: logistic pool on a market offset (linear / logistic class), scored at the line (quantile-layer question).
1. Information or capacity: no new model information. The new inputs are market-side (soft books, spread, line level), and the design question is
   where the model's existing disagreement counts. Fine as stated.
2. Enough data: yes. 28,108 / 37,528 scored legs for 22 ridge parameters after edit 1; measured null cost +0.00018 (2024), +0.00040 (2023).
3. Valid test: not yet. The comparator MR lacks information the model arm uses (bench/tmout/disp10 main effects), which is a playbook-named
   invalidity, and the gate's market-only control is in-span by construction. After edits 1-2 the test is valid on everything I could check label-free.
4. Lineage: section 2 of the rule is accurate. T494's -0.0002 should be cited as the nearest prior answer.
5. Scored against: binary log loss at the Pinnacle line, the T477/T480 question; CRPS is correctly dropped.
Class checks: features as-of (PASS, 2b); era flags (no date-constant column; `blowout`, `u1`, `disp10` vary within date); duplicated signals: the
interaction-without-main-effect is the duplicated-signal failure (R1).

## 10. Biggest risk, not verified, verification

- **Single most likely failure mode if a future model word is wrong:** a role- or depletion-dependent market over-bias (bench overs) entering C_mod
  through `delta x bench` / `delta x tmout`, with the placebo as the only guard (edit 1 removes the channel).
- **Not verified:** the combined world (real model signal plus an off-span market error) under the draft; oracles below 0.0011; the real-label missingness
  table (not allowed here); the eval harness (not written); ruff / format / mypy / pytest on the branch (the main `.venv` has no ruff or pytest:
  `No module named ruff`, `No module named pytest`, exit 1); whether the shadow log's `raw_model_prob` is p_cond or the DNP-mixed p_eff.
- **Drafted rows:** none. This is a pre-freeze review; the ledger row comes with the result.
