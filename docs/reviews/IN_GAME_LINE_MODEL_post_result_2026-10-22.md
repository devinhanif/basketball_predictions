# IN_GAME_LINE_MODEL (T508) post-result attack, 2026-10-22

Adversary review of the frozen run (text 469861e6, harness pin e8226ee1; prereg c956fe4, pin 91ed97d, result a90a6f6;
`reports/prereg_in_game_line/results.json`). Seasons 2019-2024 only. No 2025 read, no write connection (every DuckDB open is
`read_only=True` through the harness readers), nothing under `nba/`, `configs/` or `research/` written. Attack scripts and cached frames are in
the session scratchpad (`.../scratchpad/igl/`: `cache.py`, `lib.py`, `alib.py`, `prep.py`, `repro.py`, `b0e.py`, `a1.py`, `a2.py`, `a3.py`,
`a4.py`, `s5.py`). They import the pinned harness modules read-only and call `.venv/bin/python -W ignore <script>` from the repo root.

Attack outcome: **WOUNDED**. This is not a leak. It is real information, but the "~8x the floor" size is mostly a weak baseline.

Verdict: BLOCKED

What BLOCKED covers: the claim "M beats linear pace by ~0.04 log loss" as evidence of in-game edge, and any forward-shadow sizing or
promotion argument built on it. The pre-registered PASS stands as recorded (rules are not re-scored after a result). Its meaning changes:
the frozen B0 does not know who the player is. A player-aware pace baseline, built here before it was scored and with one design and no
iteration, leaves M **-0.0024** log loss on pts (2023), **-0.0025** (2024) and **-0.0021** (2022). That is 6% of the claimed gain and below the
rule's own 0.005 floor in every season and every stat.

## Top finding (the single most likely failure mode)
**B0 is a straw at early checkpoints: it prices every player at the league rate (`RATE_PRIOR`, `in_game_line_rule.py:168`, 0.50 pts/min
shrunk with 10 pseudo-minutes) with a starter/bench minutes prior, and never uses the player's own scoring rate or minutes.** At e = 6, raw
B0's log loss is 0.631, worse than a constant. M receives `pregame_mean` and, through stage A, `rec_minutes`. Most of M - B0 is "M knows
the player is Jokic". B1 knows the player but ignores the in-game pace. Neither baseline combines the two, and that combination is the
obvious fair baseline. Per-checkpoint, M - pooled B0 is -0.131 at e = 6 and falls to -0.011 at e = 42. M - fair GLM is -0.003 / +0.001 / +0.000 /
+0.000 / -0.007 / -0.005 / -0.004 for e = 6 ... 42. **What survives is a second-half effect** (margin x Q4, fouls, minutes), about 0.004 to
0.007, which matches the rule's own motivation (Q4 blowout benching).

I missed this in both pre-freeze reviews: M4 caught the calibration handicap, not the information handicap. The playbook's GBM check
("does the arm only re-express the base?" and "residual targets over a strong base (recency average)") would have caught it.

## Re-derivation (pulled the rows, recomputed)
`repro.py` rebuilt the pts 2023 select fit from cached states/finals with the pinned functions. It reproduces results.json exactly: M 0.23075,
B0 0.27178, B0e 0.44154, B1e 0.26598, M0 0.26494, raw B0 0.29866 (n = 700,781 rows, 1,230 games, 535 player-seasons). 2024 also
reproduces: M 0.23365, B0 0.27324, B0e 0.45347, n = 705,323.

## Per-check table (pts unless stated; contrast = M minus comparator, mean log loss, negative = M better; 95% CI = paired cluster bootstrap, widest of game / player-season / ISO-week binds, 2,000 draws, seed 0)

| # | check | command | n | result (CI) | pass/fail |
|---|---|---|---|---|---|
| 6 | B0e anomaly | `b0e.py` | 2023 train 2,657,812 | `rule.platt_fit` (undamped Newton, 25 steps, `in_game_line_rule.py:306-318`) **diverges at e = 6**: (a, b) = (-1.76e11, 2.16e11), test LL at e = 6 2.179 (Nelder-Mead optimum (-0.80, 0.41), LL 0.482). e = 12..42 identical to a converged fit. Fixed B0e: 0.26838 (2023), 0.26957 (2024), 0.26617 (2022) | **bug** (BLOCKER for future use of `platt_fit`; claim unaffected) |
| 6 | M vs fixed B0e | `a1.py pts 2023/2024` | 700,781 / 705,323 | -0.0376 [-0.0426, -0.0335] / -0.0359 [-0.0398, -0.0320] | still passes the floor (binding B0 was 0.0034 too weak) |
| 7 | M vs per-checkpoint isotonic B0 | `a1.py` | same | -0.0358 [-0.0408, -0.0318] / -0.0340 [-0.0378, -0.0301] | passes; calibration is not the story |
| 7 | M vs player-prior pace B0p (B0 with rate prior = pregame_mean/rec_minutes and share prior = rec_minutes/48; K chosen on train from {5,10,20,40} = 40; NegBin; per-e Platt) | `a1.py` | same | -0.0092 [-0.0103, -0.0081] / -0.0090 [-0.0099, -0.0080] | 78% of the gain gone |
| 7 | M vs remaining-production Poisson with player recency rate (per-e Platt) | `a1.py` | same | -0.0126 [-0.0137, -0.0115] / -0.0123 [-0.0133, -0.0114] | 69% gone |
| 6/7 | **M vs fair strong baseline `glm_e`**: per-checkpoint logistic on [logit B0p, logit B0, logit B1, log1p(need)], train rows only | `a1.py` | 2023 700,781; 2024 705,323; 2022 707,606 | **-0.00242 [-0.00311, -0.00177]; -0.00247 [-0.00363, -0.00157]; -0.00208 [-0.00287, -0.00140]** | **94% of the gain gone; below the 0.005 floor (CI lower bounds above -0.005) in every season** |
| 7 | M vs M0 + pregame_mean (same learner, add the player prior only) | `a1.py` | same | -0.0043 [-0.0048, -0.0037] / -0.0040 [-0.0045, -0.0035] / -0.0033 (2022) | 90% of the gain is pregame_mean |
| 7 | M + b0p_logit vs M | `a1.py` | same | -0.0011 [-0.0015, -0.0007] / -0.0013 [-0.0017, -0.0009] | M does not fully absorb the player-aware pace |
| 1 | Knockout (refit M without each group; Mdrop - M) | `a2.py pts 2023` | 700,781 | recency (pregame_mean) +0.0146 [+0.0121, +0.0174]; cur/need +0.0017; stage A +0.0006; margin/Q4 +0.0005; starter +0.0004; minutes +0.0002; fouls +0.00004; b0_logit -0.0005 (drop helps) | no single group carries everything; recency carries the largest share. Without pregame_mean M still beats B0 by 0.026 because stage A carries rec_minutes, a player minutes prior. Consistent with the top finding, no leak signature |
| 2 | Shuffled labels within checkpoint x line (train), refit M | `a2.py` | 700,781 | +0.122 [+0.110, +0.136] vs B0 | gain vanishes (fires, but trivially) |
| 2 | Shuffled labels within checkpoint x line x current stat | `a2.py` | 700,781 | -0.0012 [-0.0036, +0.0012] vs B0 | **PASS**: gain vanishes within noise; no structural leak in feature construction |
| 3 | Planted future, sensitivity: true remaining minutes (stint y_rem_min) in place of stage A's prediction | `a2.py` | 700,781 | Mplant - M -0.0351 [-0.0373, -0.0328] | **PASS**: M would jump by 0.035 if it saw the future minutes, so it does not see them now |
| 3 | Planted future: final box minutes added as a feature | `a2.py` | 700,781 | -0.0310 [-0.0332, -0.0288] | **PASS** (same reading) |
| 3 | Lag: state taken 30 / 60 / 180 s before each checkpoint, M and stage A fitted on normal data | `a3.py` | 697,474 / 694,784 / 689,599 | M LL 0.2336 / 0.2375 / 0.2578 (lag 0: 0.2308); M - B0 -0.0418 / -0.0429 / -0.0540 | **PASS**: smooth decay, no cliff at T |
| 3 | Stage A audit | code read + `s5.py` | 150 games of 2023, 18,809 player-checkpoints | Features `e, minutes, starter, fouls, margin, abs_margin, q4_rem, blow_x_q4, rec_minutes` are all as-of. Target `y_rem_min` (= stint minutes at 48 - minutes at T) is used only as a training label. Stage A is fitted on the training seasons only (`run_pipeline` stage_sel = 2019-22, stage_rep = 2019-23). Stints rebuilt with `track_lineups_with_report` from play-by-play cut at T (same box starters, roster, names): 0 presence differences, 42 minute differences (0.22%), all at mid-period checkpoints e = 18/30/42 (look-ahead period openers), size 0.9 to 6 min | **PASS** with caveat S5: on-court identity is observable live, and 0.22% cannot carry 0.04 |
| 4 | Missingness | `a3.py` | 2023 test / train | 0 nulls in every M column (train and test). Zero-rate gaps by outcome (`cur`, `fouls`) are monotone and knowable (players who score more play more and foul more), `margin` = 0 shows no gap (0.034 / 0.026 at e = 42). Every unit has a positive-minute final row (0 of 708,702 missing). A0 intersection drops 1,069 rows (y-rate 0.102 vs 0.178 kept), equal on all arms | **PASS**: no NULL-pattern signature |
| 5 | Replication: second LightGBM seed | `a4.py` | 700,781 | seed 1 LL 0.230739 vs seed 0 0.230750 | PASS |
| 5 | Replication: 2022 as an extra out-of-sample season (train 2019-21; no A0 intersection, which 2022 lacks) | `a1.py pts 2022` | 707,606 | M - B0 -0.0402 [-0.0444, -0.0360]; M - glm_e -0.0021 [-0.0029, -0.0014] | same sign and size as 2023/2024 for both readings |
| 5 | Drop the best month (December) | `a3.py` | 581,801 | M - B0 -0.0402 [-0.0443, -0.0361]; M - glm_e -0.0024 [-0.0032, -0.0017] | PASS; every month has M - B0 between -0.031 and -0.045 |
| 5 | Concentration | `a3.py` | 535 player-seasons | top-5 player-season share of the gain over glm_e 0.068 (vs B0, harness 0.082) | PASS |
| 6 | Integer support | code read | n/a | Binary event scored by log loss. B0/B1 are NegBin on the integer grid; M is a classifier. No continuous-vs-grid mismatch | n/a |
| sec | reb / ast / fg3m 2024 vs glm_e | `prep.py <stat> 2024; a1.py <stat> 2024` | 615,537 / 530,593 / 498,560 | -0.00315 [-0.00389, -0.00237] / -0.00168 [-0.00245, -0.00092] / -0.00195 [-0.00247, -0.00142]. M vs fixed B0e -0.039 / -0.041 / -0.048 (their binding B0e was not broken) | all three secondary "wins" (~0.04) shrink below the floor |
| R | Rule integrity | `git diff --stat c956fe4 a90a6f6 -- docs/prereg/IN_GAME_LINE_MODEL.md research/eval/` (exit 0, empty); worktree harness vs a90a6f6 empty; text sha 469861e6 recomputed | n/a | prereg and pin both committed 01:05:47 CDT; run started 01:06:04 | PASS |
| H | Holdout | ledger and holdout log | n/a | no 2025 touch by the run or by this attack | PASS |

Also noted: `e42_invariant` reports `ok: false` for all four stats in the real results. It is a dryrun-only invariant (raw B0 is the truth
only in the dryrun); on real data it just says raw B0 is miscalibrated at e = 42. It should not be emitted, or should be labelled, in a real
run (minor, `in_game_line_eval.py:499`).

## Corrected family and power
- Family actually run in this attack: 4 stats x up to 11 comparators x 3 seasons (pts), plus 9 knockouts, 2 shuffles, 2 plants and 3 lags.
  Every M - comparator contrast except the shuffled-within-cur control has p at the bootstrap floor (1/2001). So BH over the whole family
  changes nothing: every contrast is significant. **The question is size, not significance.**
- Power: the binding half-width of M - glm_e is 0.0007 (2023), 0.0010 (2024), 0.0007 (2022); MDE about 0.002. The residual is resolved and
  **decisively below the 0.005 floor** (CI lower bounds -0.0031 / -0.0036 / -0.0029). This is not "underpowered". It is a real gain of about
  0.002 to 0.003 log loss overall and 0.004 to 0.007 at e >= 30.
- Caveat: these attack numbers are exploratory. The comparator was built after the result, and 2022-2024 are now seen for it. They wound
  the interpretation; they are not a frozen claim in either direction.

## Playbook check (model class: two-stage gradient-boosted trees, LightGBM; plus a recalibration layer)
1. **Information or capacity.** Mostly information that is old: the player's pregame rate and minutes, which B0 lacks. The genuinely in-game
   part (margin x Q4, fouls, minutes) is worth about 0.002 to 0.003 overall and about 0.005 in the second half.
2. **Enough data.** Yes: 2.0-3.4M training rows, half-widths 0.0007-0.0010.
3. **Test validity.** Leak-side valid (truncation, planted-future, lag, shuffle-within-cur, missingness and the S5 rebuild all pass). Baseline-side
   invalid for the magnitude claim: B0 is not a strong base. "Linear pace" was implemented without the player's own pace prior. An invalid
   baseline is not a null either; it inflates the effect.
4. **Lineage and inherited guards.** The re-expression control M0 was inherited and passed, but M0 only sees B0's logit and cannot test
   "re-expresses B0 plus the recency average". The strong-base guard of the GBM class was not applied.
5. **Scored against.** B0 (straw at early checkpoints), B1e (no in-game pace). No market. The fair comparator is a player-aware pace model
   (glm_e here), and against it M is below the floor.
Class checks (GBM): missingness beyond as-of PASS; planted future PASS; "does the arm only re-express the base?" **FAIL in spirit**: it largely
re-expresses B0 + recency mean + rec_minutes.

## Findings (severity-ranked, exact line and fix)
1. **Major, interpretation.** B0 uses a league-constant rate prior (`in_game_line_rule.py:166-170`, `RATE_PRIOR`, `RATE_K`) and a constant
   share prior (`:153-157`). Fix: no change to the frozen rule. The T508-result ledger row should carry the attack numbers, and any forward
   shadow or follow-up rule must bind on a player-aware pace baseline (rate prior = pregame_mean / rec_minutes, share prior = rec_minutes / 48,
   per-checkpoint calibration, or the glm_e construction here).
2. **Blocker for reuse, not for T508.** `platt_fit` and `calibration_slope` (`in_game_line_rule.py:291-318`) take undamped Newton steps with
   no convergence check. Per checkpoint at e = 6 they diverge to |coef| ~ 1e11. Fix: step-halving on the log likelihood plus a
   max-|step| guard and a convergence assert, or `sklearn.linear_model.LogisticRegression(C=1e6)` as used in `alib.platt_robust`. Add a test with
   4.8% of inputs at the 1e-4 clip that requires the fitted loss to be no worse than (0, 1). `calibration_slope` on M was 1.003, so the
   reported slope is unaffected.
3. **Minor.** `evaluate` picks the binding baseline with `min` (`in_game_line_eval.py:459`), which silently masked a 0.44 baseline. Fix: assert
   every recalibrated arm is no worse than its raw arm on the training rows, and refuse otherwise.
4. **Minor.** `e42_invariant` in the real run (`in_game_line_eval.py:499`). Fix: emit only under `synth_seed`.
5. **Minor (known, S5).** The stint openers use look-ahead: 0.22% of player-checkpoints carry minutes the tracker cut at T would not have. Fix:
   a forward shadow takes on-court status from the live feed (already stated in the rule).
6. **Not a finding:** no leak in the state, stage A, recency or presence (every leak check passes).

## Not verified
- Knockout, shuffle and plant checks were run on 2023 only (2024 got the fair-baseline battery). A season-swap (train later, score 2019) was not run.
- The glm_e and B0p designs are mine. A stronger fair baseline is likely: K hit the top of the {5,10,20,40} grid, and no margin or fouls were
  in the GLM. So the residual of 0.002 to 0.003 is an upper bound on M's edge over a fair simple model, not a lower bound.
- The secondary stats' knockout and shuffle were not run.
- `truth/bootstrap.py` does not exist; the harness's local `rule.binding_ci` was used (N2 of the pre-freeze review).
- Tests were not run: this review changes no code. Verification fields are n/a.

## Recommended changes (main session acts; not applied)
- Amend the T508-result ledger row (draft below). Mark the 0.04 as "vs a player-agnostic pace baseline".
- Score the Pending priors as written (they were about B0 as defined, so they resolve as PASS). Add a PREDICTIONS_LEDGER note that "the
  effect is 8x the floor" was a baseline artifact.
- Before any forward shadow: Devin's call whether the second-half residual (about 0.005 at e >= 30) is worth a new pre-registered rule.
  That rule would bind M against a player-aware pace baseline, with the live 2026 log as the only clean test. 2022-2024 are seen for that
  comparison, and a 2025 touch would be Devin's decision.
- Fix `platt_fit` before any other harness reuses it (several preregs share the pattern; grep for undamped Newton Platt).

## Drafted rows (not appended)
TEST_LEDGER:
`| T508-attack | 2026-10-22 | IN_GAME_LINE_MODEL post-result adversary attack (docs/reviews/IN_GAME_LINE_MODEL_post_result_2026-10-22.md) | is the T508 PASS a leak or an artifact? | leak checks all pass (shuffle within e x line x cur -0.0012 [-0.0036, +0.0012]; planted true remaining minutes would add -0.035; lag 30/60/180 s smooth; 0 nulls; causal stint rebuild 0.22% minute diffs, 0 presence diffs). B0e bug: undamped Newton Platt diverges at e = 6 (fixed B0e 0.2684 vs pooled 0.2718; M - fixed B0e -0.0376). Magnitude: B0 has no player prior; M - player-aware per-checkpoint logistic (glm_e) pts -0.0024 [-0.0031, -0.0018] (2023, n 700,781), -0.0025 [-0.0036, -0.0016] (2024, n 705,323), -0.0021 [-0.0029, -0.0014] (2022, n 707,606); reb/ast/fg3m 2024 -0.0032 / -0.0017 / -0.0020; residual concentrated at e >= 30 (-0.004 to -0.007). | clustered game / player-season / week, widest binds, 2,000 draws | exploratory (comparator built post-result) | **WOUNDED**: PASS stands as pre-registered; ~94% of the 0.04 is the baseline lacking the player's own rate and minutes; edge over a fair simple baseline is below the 0.005 floor. | no 2025 touch |`

DECISIONS (draft, Devin's call):
`2026-10-22 | In-game line model: any forward shadow or follow-up binds on a player-aware pace baseline, not T508's B0 | T508's 0.04 gain is 94% baseline weakness (attack review) | would reverse if a frozen rule shows M beating a player-aware baseline by >= 0.005 on the live log`
