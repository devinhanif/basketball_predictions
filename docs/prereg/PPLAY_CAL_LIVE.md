# PPLAY_CAL_LIVE: is the chance of playing the app shows honest, bin by bin? Backtest on 2023-24 first, then confirm on the 2026-27 live log. (pre-registration)

Status: **DRAFT, revision 1 (two-stage), branch `agent/modeler-calibration-program`. Revision 0 committed Tue 2026-11-17 19:58 CST; revision 1
written after Devin's decision relayed by the coordinator the same evening ("backtest first, then confirm only what survives"). Not frozen.
Nothing fitted, no outcome read: only stated probabilities (2026) and file schemas (2023-24) were counted.**
- **How it becomes the rule:** the main session commits `prereg(PPLAY_CAL_LIVE): <sha256>` of everything above `=== RESULTS BELOW ===`
  (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PPLAY_CAL_LIVE.md | shasum -a 256`) after an adversary pre-freeze read and
  Devin's confirmation of section 10.
- **Origin:** Devin 2026-11-17 ("calibrated always"); retrain survey part 3 row 8 ("P(plays) shown and multiplied into card prices: no map";
  the ECE ~0.01 vs ~0.14 contradiction between `nba/props/forward.py` and PPLAY_INTO_PROPS_LIVE line 35, unresolved). **Follows advice.**
  Chosen over win probability and the under side by the gate check (section 6). The two-stage shape is **Devin's decision**.
- **Season labels:** `games.season`. Stage 1: 2022 (fit rows for the candidate map only), 2023 (select), 2024 (report). Stage 2: season 2026
  live rows. **Season 2025 is never loaded** (section 5 says why no 2025 touch is eligible).
- **A stage-1 pass is selection, not a claim.**

## 1. Question
The app shows P(play) for every player and multiplies it into card prices; Kalshi settles a DNP as a miss on a player prop, so P(play)
enters every Kalshi number directly. No calibration map exists (production's Platt map was turned off on 10-11 because it hurt). The shown
value is `p_play_primary`: the P(play) shadow R (`pplay_r_v1`, the 2022-start R, DECISIONS 2026-10-12 "the 2022-start R (not R19)") for
1,287 of 1,416 rows tonight, production's hurdle for 129. 45% of tonight's stated values sit between 0.1 and 0.9 (0.5-0.7: 23.7% of rows,
mean stated 0.66), so this is not a question about the 0.99s.
- **H1.** The shown P(play), or a two-parameter recalibration of it selected in stage 1, is honest: in each of five stated-probability bins
  the realised play rate is within 0.05 of the mean stated value, and overall within 0.02.
- **Null.** At least one bin is off by more than 0.05 for every candidate.

## 2. The object and metrics (common to both stages)
1. **Unit.** One row per (game, player): the P(play) forecast as of tip - 60 min (real tip).
2. **Candidates.** A = R as stated (raw). B = R recalibrated by Platt: logit p_B = a + b logit p_A, ridge 1.0 on (a, b - 1), fit by calendar
   month block on every earlier row (never on the month it scores). No other candidate.
3. **Outcome.** played = `minutes > 0`. Historical rows: the outcome stored with the backtest arrays (section 3). Live rows: exactly as the
   frozen PPLAY_INTO_PROPS_LIVE scorer settles it (`forward_pplay_scores.status = 'scored'`, joined on game and player); rows that scorer
   does not settle are counted (`unscored`, by reason) and excluded; no second definition of "played" is written here.
4. **P1 (honesty by bin).** Bins of the stated value: [0, 0.5), [0.5, 0.7), [0.7, 0.9), [0.9, 0.97), [0.97, 1]. Per bin: realised play rate
   minus mean stated, game-clustered ratio-estimator 95% CI (bootstrap over games, B 2,000, seed 0; date CI beside). Pass: every bin's CI
   inside [-0.05, +0.05]. **P2 (in the large):** overall realised minus mean stated, CI inside [-0.02, +0.02].
5. Labels beside every stage: Brier and log loss, the Brier decomposition (reliability, resolution, uncertainty), so sharpness is read with
   calibration; ten-bin reliability.

## 3. Stage 1: walk-forward backtest on 2023-24 (selection, not a claim)
**Rows (exactly).** The stored P(play) backtest arrays of PPLAY_MINUTES_VALID (frozen 91d82de7; T482):
`reports/prereg_pplay_minutes_valid/scores.npz`, 128,931 rows with keys `gid, pid, season, date, y, p_P0, p_P0c, p_P0p, p_R19, p_C19, p_Cp19`
and context columns (schema read only; no value read). Its results report 2023: 33,295 rows / 1,318 games; 2024: 33,158 / 1,320 (T482), and
earlier seasons used as warm-up and fit rows.
- **The live arm is not in that file.** It stores R19 (the offline winner) and production's P0, not the 2022-start R that the app shows. The
  2022-start R was computed in the same run (its results table reports R19 minus R22 on 2023, n 33,093) but no per-row R22 array is in
  `scores.npz`. Stage 1 therefore needs R22 rows: **locate them, or regenerate them with the PPLAY_MINUTES_VALID harness** (same code, same
  folds). Until then, stage 1 cannot be run on the object the app shows; running it on R19 would test the wrong model (the T484 lesson: the
  live arm is the 2022-start R). The 129 production-fallback rows per week are tested on `p_P0` as a label.
- **Were these rows used to fit something this draft depends on?** The forecasts are walk-forward out-of-fold: each month block's model is
  fit on earlier rows only, so no row's p came from a model trained on it. But model selection saw these seasons: R's recent-role columns
  were chosen on 2022-24 (RECENT_ROLE), and T482 and PPLAY_CONTEXT scored log loss, Brier and ECE on 2023-24. Candidate B's Platt map is
  fit walk-forward inside this stage (2023 months on 2022 plus earlier 2023 months; 2024 months on 2022-23 plus earlier 2024 months), so B
  never scores rows it was fit on. What cannot be avoided: 2023-24 are exposed for this model's calibration, so stage 1 selects; it does not
  claim.

**Select on 2023, report on 2024.** Select: if A passes P1 and P2 on 2023, choose A; else if B passes, choose B; else **stop** (the rule
closes at stage 1; the follow-up is a richer map under a new id). Report: the chosen candidate must pass P1 and P2 on 2024, or the rule closes.
Attacks reported with the result: within-date shuffle of p (calibration must break), a past-only placebo (each player's previous p) to show
the bins are not a calendar artefact, drop the top five teams, team clustering, a second seed.

**Stage-1 gate (non-negotiable 8), arithmetic.** 2023: 33,093 R22 rows on 1,318 games (25.1 per game); 2024: about 33,000 on 1,320. With
tonight's live bin shares (the historical shares are not measured; they need a label-free read of the arrays' p columns), the smallest
bins, [0, 0.5) at 10% and [0.9, 0.97) at 10.9%, hold about 3,300 and 3,600 rows per season. The live synthetic simulation gave half-widths
at 880 games of 0.020 ([0, 0.5)), 0.013, 0.015, 0.009, 0.002; scaled to 1,318 games by sqrt(880 / 1,318) = 0.82: 0.016, 0.011, 0.012,
0.008, 0.002, all well inside the +-0.05 band. P(pass given calibrated) at 1,318 games: above 0.99 (0.99 already at 880). **Gate: PASS on
counts, BLOCKED on the missing R22 per-row array** (locate or regenerate before freeze; that is an engineering step, not a data failure).

**Cost and where it runs.** If the R22 arrays exist: numpy on one file, seconds, Mac, any time. If they must be regenerated: the
PPLAY_MINUTES_VALID harness's LightGBM walk-forward on about 33,000 rows per season by month block, Mac CPU (trees run faster locally than on
a T4), outside game windows, under `data/ops/heavy.lock`, read-only `nba.duckdb` copy; runtime not timed here (its results file does not
record it); my estimate is under 30 minutes. No credits. Not Colab.

## 4. Stage 2: forward confirmation on the live log, run only if stage 1 passes
1. **Object.** The candidate stage 1 chose. If A: `p_play_primary` as stored. If B: the Platt map refit once on all 2022-24 rows, written
   with its sha before the window opens, applied by the harness to the stored `p_play_primary` (and served only if Devin chooses; until
   then the app keeps A with its label).
2. **Rows.** The latest P(play) row with `made_at <= tipoff - 60 min` (the `p_play` export rows with `lead_minutes >= 60`, newest), season
   2026 regular season and Cup group games; play-in and playoffs reported separately.
3. **Window.** Slates strictly after the later of this rule's freeze and the stage-1 result.
4. **One look** after PPLAY_INTO_PROPS_LIVE's final looks have been run (reb and pts at the end of the regular season, 2027-04-11), on the
   first date every window game is settled; not before 2027-04-13; backstop 2027-04-30; real clock only (section 10 choice 1).
5. **Verdict.** HONEST: P1 and P2 pass. NOT_HONEST: any bin's CI lies wholly outside [-0.05, +0.05] (names the bin and direction).
   INCONCLUSIVE: otherwise. INSUFFICIENT_DATA: fewer than 600 games in the window at the backstop.
6. **Multiplicity.** The verdict is an intersection of equivalence checks, so no BH; per-source (R vs production fallback) and per-slot rows
   are labels with BH m 2 each.

## 5. Why no 2025 touch (instead of or in addition to stage 2)
CLAUDE.md allows one logged touch per never-evaluated model. Neither candidate qualifies:
- **A (R as stated):** R has spent its 2025 touch (HOLDOUT_ACCESS_LOG 2026-10-12, RECENT_ROLE_HOLDOUT, sha8 302bc693), and an adversary
  diagnostic later scored further R arms on 2025 (same log, disclosure row). Production's P(play) has 2025 exposure too (the T043-T046
  calibration split likely included 2025, DECISIONS 2026-10-12).
- **B (Platt of R):** a two-parameter monotone map of R. Its 2025 calibration is almost entirely determined by R's 2025 behaviour, which
  was read. It is not, in substance, a never-evaluated model, and a 2025 touch would confirm little that was not already seen.
- So the confirmation is the live log only. That is why the look is late, and why stage 1 matters: it is the only early answer.

## 6. Why this and not the other two candidates (gate checked, label-free)
Command: `reports/prereg_calibration_program/gate.py` on a scratch copy of the app export (sha256 `750c736e...`, data_version `6edbcef0c89d`),
output sha256 `3629b223...`; schedule parquet (1,200 regular-season games). Power uses synthetic outcomes from real stated values.
- **Win probability:** an honesty claim (calibration in the large inside +-0.03) needs about 1,100+ games: P(pass given calibrated) 0.00 at
  500 games, 0.04 at 900, 0.21 at 1,100 (half-width 0.039 / 0.029 / 0.026); 939 games remain after an 11-25 freeze. **Fails its gate this
  season; not drafted.** A 2023-24 backtest exists already (T178: ECE 0.024, no calibrator kept); FORWARD_PREREG section 7 publishes Brier and
  ECE descriptively; WINNER_RECAL stays closed.
- **The under side at the line:** LINE_CAL_LIVE's P2 is exactly this check at its one look (about 12-03); LINE_CAL_SIDE is its pre-registered
  follow-up. A separate rule now would be a second look. Not drafted.

## 7. Stage-2 minimum-data gate, measured Tue 2026-11-17 19:48-19:52 CST, label-free
| check | measured | bar | pass |
|---|---|---|---|
| rows per game at T-60 | 25.3 (1,416 rows, 56 games, 11-10..17) | >= 20 | yes |
| share by bin | [0, 0.5) 0.100; [0.5, 0.7) 0.237; [0.7, 0.9) 0.121; [0.9, 0.97) 0.109; [0.97, 1] 0.433 | every bin >= 0.05 | yes |
| games in window | 939 scheduled from 2026-11-25 to 2027-04-11 | >= 600 | yes |
| power, all five bins inside +-0.05 | P(pass given calibrated) 0.57 at 300 games, 0.95 at 600, 0.99 at 880; given every bin off by +0.025: 0.10 / 0.41 / 0.68; false pass given +0.08: 0.00. Half-widths at 880 games 0.020, 0.013, 0.015, 0.009, 0.002 | P(pass given calibrated) >= 0.8 | yes |
| projected look | 600 games reached 2027-02-26; the look waits for PPLAY_INTO_PROPS_LIVE's season-end looks: about **2027-04-13** | before the backstop | yes |

**Stage-2 gate: PASS.**

## 8. Interactions with frozen tests
- **PPLAY_INTO_PROPS_LIVE (frozen 3a305482):** stage 1 reads no 2026 row. Stage 2 reads the same p and outcomes; its verdict is unconditional
  integer CRPS, not reliability; this rule's look comes after its final looks.
- **LINE_CAL_LIVE and its display lock:** P(play) is not a prop chance at a line; no CAL is read.
- **UNDERS_FORWARD / BEST_PRICE_FORWARD:** read Kalshi ROI (DNP settles as a miss); no shared pass word.
- **Display lock for this rule:** no surface shows P(play) beside "played / did not play" aggregated over the stage-2 window until the look
  (section 10 choice 2). A single game's box score stays.

## 9. Kill criteria, attack list, playbook check
- Stop unscored if: the live scorer's outcome definition changes inside the window (the window restarts under a new id); a season other than
  2026 enters stage 2 or other than 2022-24 enters stage 1; more than 2% of live rows have `made_at` after tip - 60.
- **Attack before belief (stage 2):** re-derivation; second seed; drop the top five teams; team clustering; within-date shuffle of p; past-only
  placebo.
- **Playbook (class: gradient-boosted trees, R and production hurdle, judged as a probability).** (1) Information or capacity: neither; it
  checks whether R's information (report tags, recent role; T482) is stated honestly. (2) Enough data: stage 1 about 33,000 rows per season;
  stage 2 880+ games. (3) Valid test: real tips, as-of report gating (T-60), walk-forward folds, the shown object (R22, not R19), game
  clustering; known outcome-dependent populations (questionable, never-dressed players) shown by the `unscored` count and the report-status
  slice. (4) Lineage: PPLAY_CONTEXT (T316-T317), PPLAY_MINUTES_VALID (T482/T484: the live arm is R22), PPLAY_INTO_PROPS_LIVE. (5) Scores
  against: bin reliability and calibration in the large; Brier and log loss as labels.

Secondary labels (stage 2): reliability in ten bins; the latest pre-tip row (lead < 60) beside the T-60 row; by source; by injury report
status at T-60; back-to-back second night; projected minutes (< 15, 15-28, > 28); the share of Kalshi prop markets whose player is in each bin.

## 10. Devin's choices (defaults marked; his answers recorded here when relayed)
1. **Stage-2 look timing:** *default after PPLAY_INTO_PROPS_LIVE's season-end looks (about 2027-04-13).* Alternative: at 600 games (about
   02-26), disclosed in PPLAY_INTO_PROPS_LIVE's results as a partial exposure of its reb and pts inputs. Answer: pending.
2. **Display until the look:** *default "chance of playing, not yet checked live"* (hiding it removes the number every card price uses);
   after a stage-1 pass the label may read "checked on 2023-24, not yet live". Alternative: hide P(play). Answer: pending.
3. **Bins and margin:** *default the five bins and +-0.05.* Alternative +-0.03 (fails by power in the [0, 0.5) bin). Answer: pending.
4. **If stage 1 chooses B, serve it before stage 2?** *default no* (serve A labelled; B is computed by the harness only, so stage 2 tests
   it without a serving change during the window). Alternative: serve B from the window's start (a live-path change: oracle, review, his go).
   Answer: pending.

## 11. Priors (drafted for PREDICTIONS_LEDGER, not appended)
P(stage 1 chooses A) 0.35; P(stage 1 chooses B) 0.35; P(stage 1 stops) 0.30; P(stage-2 HONEST given stage 1 passes) 0.45; P([0.5, 0.7) bin
realises above its stated mean by more than 0.05 in stage 1 on A) 0.40 (reason: production's hurdle was underconfident before its Platt
map, raw 0.34-0.36 vs Platt 0.41-0.43 in T317, and a stated 0.66 mass is unusual for rostered, not-OUT players).

=== RESULTS BELOW ===

(none)
