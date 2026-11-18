# PPLAY_CAL_LIVE: is the chance of playing the app shows honest, bin by bin, on the 2026-27 live log? (pre-registration)

Status: **DRAFT, revision 0, written by the modeler Tue 2026-11-17, committed 19:58 CST (clock read by `date` at commit), branch
`agent/modeler-calibration-program`. Not frozen. Nothing fitted, no outcome read: only stated probabilities were counted.**
- **How it becomes the rule:** the main session commits `prereg(PPLAY_CAL_LIVE): <sha256>` of everything above `=== RESULTS BELOW ===`
  (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/PPLAY_CAL_LIVE.md | shasum -a 256`) after an adversary pre-freeze read and
  Devin's confirmation of section 9.
- **Origin:** Devin 2026-11-17 ("calibrated always"); the retrain survey (part 3 row 8: "P(plays) shown and multiplied into card prices: no
  map"; the ECE ~0.01 vs ~0.14 contradiction between `nba/props/forward.py` and PPLAY_INTO_PROPS_LIVE line 35, unresolved). **Follows
  advice.** Chosen over win probability and the under side by the gate check (section 5).
- **Season labels:** `games.season`. Season 2026 live rows only. Season 2025 is never loaded.

## 1. Question
The app shows P(play) for every player and multiplies it into card prices; Kalshi settles a DNP as a miss on a player prop, so P(play)
enters every Kalshi number directly. No calibration map exists (production's Platt map was turned off on 10-11 because it hurt); the shown
value is `p_play_primary` (R `pplay_r_v1` for 1,287 of 1,416 rows tonight, production's hurdle for 129). Tonight 45% of the stated values
sit between 0.1 and 0.9 (0.5-0.7: 23.7% of rows, mean stated 0.66), so this is not a question about the 0.99s.
- **H1.** The shown P(play) is honest: in each of five stated-probability bins the realised play rate is within 0.05 of the mean stated
  value, and overall within 0.02.
- **Null.** At least one bin is off by more than 0.05.
- This rule has no candidate map and claims no improvement; a NOT_HONEST result opens one follow-up (a recalibration, new id).

## 2. The rule in nine lines
1. **Unit.** One row per (game, player): the latest P(play) row with `made_at <= tipoff - 60 min` (real tip), the same rows the app
   shows at T-60 (`p_play` export table: `lead_minutes >= 60`, newest). Season 2026 regular season and Cup group games; play-in and
   playoffs reported separately.
2. **Stated value:** `p_play_primary` as stored (whatever source the app showed: R or production), clipped to [1e-4, 1 - 1e-4].
3. **Outcome:** played = `minutes > 0`, exactly as the frozen PPLAY_INTO_PROPS_LIVE scorer settles it (`forward_pplay_scores.status =
   'scored'`, joined on game and player). Rows that scorer does not settle are counted (`unscored`, by reason) and excluded; no second
   definition of "played" is written here.
4. **Window.** Slates (ET) strictly after this rule's freeze date. Rows before it are excluded from every check.
5. **One look** after PPLAY_INTO_PROPS_LIVE's final looks have been run (reb and pts at the end of the regular season, 2027-04-11),
   on the first date when every window game is settled; not before 2027-04-13; backstop 2027-04-30. Real clock only. (Section 9 choice 1.)
6. **Primary P1 (honesty by bin).** Bins of the stated value: [0, 0.5), [0.5, 0.7), [0.7, 0.9), [0.9, 0.97), [0.97, 1]. Per bin: realised
   play rate minus mean stated, game-clustered ratio-estimator 95% CI (bootstrap over games, B 2,000, seed 0; date CI beside). Pass: every
   bin's CI inside [-0.05, +0.05].
7. **P2 (in the large).** Overall realised minus mean stated, CI inside [-0.02, +0.02].
8. **Verdict.** HONEST: P1 and P2 pass. NOT_HONEST: any bin's CI lies wholly outside [-0.05, +0.05] (names the bin and direction).
   INCONCLUSIVE: otherwise. INSUFFICIENT_DATA: fewer than 600 games in the window at the backstop.
9. **Multiplicity.** The verdict is an intersection of equivalence checks (every bin must pass), so no BH; the per-source (R / production)
   and per-slot rows are labels with BH m 2 and m 2.

## 3. Why these choices
| choice | protects against |
|---|---|
| score what the app shows (`p_play_primary`), not R or production alone | a calibration claim about a number nobody sees |
| T-60 row | the slot the cards and Kalshi comparisons use; the late slot is a label (S2) |
| bins fixed now from label-free shares | choosing bins after seeing where the model is wrong |
| +-0.05 per bin | a check that cannot pass: 0.05 is reachable at 600 games in every bin (section 5); a tighter bar fails by power, not by truth |
| look after PPLAY_INTO_PROPS_LIVE's final looks | an interim look at a frozen test's input (its reb and pts verdicts read the same p and outcomes) |
| game clustering | teammates' play outcomes move together (a rest night, a blowout) |
| reuse the frozen scorer's outcome | a second definition of "played" disagreeing with the first |

## 4. Secondary results (labels; n, games and game CI on every row)
S1 reliability in ten equal-width bins; S2 the latest pre-tip row (late slot, lead < 60) beside the T-60 row; S3 by source (R vs
production fallback); S4 by injury report status at T-60 (OUT excluded upstream; QUESTIONABLE, PROBABLE, DOUBTFUL, none); S5 back-to-back
second night; S6 by projected minutes (< 15, 15-28, > 28); S7 Brier and log loss with the Brier decomposition (reliability, resolution,
uncertainty) so sharpness is read beside calibration; S8 descriptive Platt fit (intercept, slope) on the window, never served; S9 the share
of Kalshi player-prop markets whose player is in each bin (label-free), to say where a miscalibration would bite.

## 5. Minimum-data gate (non-negotiable 8), measured Tue 2026-11-17 19:48-19:52 CST, label-free; and why this and not the other two
Command: `reports/prereg_calibration_program/gate.py` on a scratch copy of the app export (sha256 `750c736e...`, data_version
`6edbcef0c89d`), output sha256 `3629b223...`; schedule parquet (1,200 regular-season games). Power uses synthetic outcomes drawn from the
1,416 real stated values (25.3 rows per game).

| check | measured | bar | pass |
|---|---|---|---|
| rows per game at T-60 | 25.3 (1,416 rows, 56 games, 11-10..17) | >= 20 | yes |
| share by bin | [0, 0.5) 0.100; [0.5, 0.7) 0.237; [0.7, 0.9) 0.121; [0.9, 0.97) 0.109; [0.97, 1] 0.433 | every bin >= 0.05 | yes |
| games in window | 939 scheduled from 2026-11-25 to 2027-04-11 | >= 600 | yes |
| power, all five bins inside +-0.05 | P(pass | calibrated) 0.57 at 300 games, 0.95 at 600, 0.99 at 880; P(pass | every bin off by +0.025) 0.10 / 0.41 / 0.68; P(false pass | +0.08) 0.00. Half-widths at 880 games: 0.020, 0.013, 0.015, 0.009, 0.002 | P(pass | calibrated) >= 0.8 | yes |
| projected look | 600 games reached 2027-02-26; the look waits for PPLAY_INTO_PROPS_LIVE's season-end looks: about **2027-04-13** | before the backstop | yes |

**Gate: PASS.** Capture audit: P(play) rows exist for season 2026 live (this rule), 2022-24 OOF (exposed: T482, PPLAY_CONTEXT, used to
build R); 2025 is the holdout. Nothing repairable changes the power: it is set by the live season's pace.

**Why not the other two candidates (gate checked, same command):**
- **Win probability:** an honesty claim (calibration in the large inside +-0.03) needs about 1,100+ games: P(pass | calibrated) 0.00 at
  500 games, 0.04 at 900, 0.21 at 1,100 (half-width 0.039 / 0.029 / 0.026). 939 games remain after a 11-25 freeze. **Fails its gate this
  season; not drafted.** FORWARD_PREREG section 7 keeps publishing Brier and ECE descriptively; WINNER_RECAL stays closed (FAIL_POWER).
- **The under side at the line:** LINE_CAL_LIVE's P2 is exactly this check (CAL on under-lean legs, |realised - stated| <= 0.03) at its one
  look (about 12-03); its pre-registered PARTIAL follow-up LINE_CAL_SIDE is the next step. A separate rule now would be a second look at a
  locked question. **Not drafted.**

## 6. Interactions with frozen tests
- **PPLAY_INTO_PROPS_LIVE (frozen 3a305482):** same p and same outcomes; its verdict is unconditional integer CRPS of props, not
  reliability. This rule's look is placed after its final looks (rule 5), so no interim reliability number exists while its reb and pts
  verdicts are open. Its fg3m and ast looks may run earlier; that is its own business.
- **LINE_CAL_LIVE and its display lock:** P(play) is not a prop chance at a line; no CAL is read. D3 there counts voided DNP legs by side,
  label-free; no overlap in pass words.
- **UNDERS_FORWARD / BEST_PRICE_FORWARD:** read Kalshi ROI, where DNP settles as a miss; they read prices and settlements, not P(play)
  reliability. No shared pass word.
- **Display lock for this rule:** no surface shows P(play) beside "played / did not play" aggregated over this window until the look
  (section 9 choice 2). A single game's box score is not a reliability display and stays.

## 7. Kill criteria and attack list
- Stop unscored if: the scorer's outcome definition changes inside the window (then the window restarts under a new id); a season other than
  2026 enters; more than 2% of rows have `made_at` after tip - 60 (the writer refuses after tip).
- **Attack before belief:** re-derivation; second seed; drop the top five teams by row count; team clustering; within-date shuffle of p
  (calibration must break); a past-only placebo (each player's previous stated p) to show the bins are not a calendar artefact.

## 8. Playbook check (docs/MODEL_PLAYBOOK.md; class: gradient-boosted trees (P(play) R, production hurdle), judged as a probability)
1. **Information or capacity?** Neither: no model change. It checks whether the information R carries (report tags, recent role; T482)
   is stated honestly.
2. **Enough data?** No fit; 880+ games give half-widths of 0.009-0.020 in the uncertain bins.
3. **Valid test?** Real tips, the frozen scorer's outcome, the shown value, game clustering, synthetic-label power on real stated values.
   Known population issue (playbook, trees): outcome-dependent populations (questionable players, never-dressed players); S4 and the
   `unscored` count expose them. Weak point: the look is late (season end) by design.
4. **Lineage.** PPLAY_CONTEXT (T316-T317: the report tag carries the gain; Platt map miscalibration), PPLAY_MINUTES_VALID (T482: R beats
   production by 0.085-0.092 log loss), PPLAY_INTO_PROPS_LIVE (frozen live CRPS test). Carried: the scorer, game clustering, the shuffle.
5. **What it scores against.** Bin reliability and calibration in the large, with Brier and log loss as labels; no comparator arm.

## 9. Devin's choices (my recommended default first)
1. **Look timing:** *default after PPLAY_INTO_PROPS_LIVE's season-end looks (about 2027-04-13).* Alternative: at 600 games (about 02-26),
   disclosed in PPLAY_INTO_PROPS_LIVE's results as a partial exposure of its reb and pts inputs.
2. **Display until the look:** *default: show P(play) with the label "chance of playing, not yet checked live"* (hiding it would remove the
   number every card price uses). Alternative: hide P(play) until the look.
3. **Bins and margin:** *default the five bins and +-0.05 above.* Alternative: +-0.03 (fails by power in the 0-0.5 bin: half-width 0.020 at
   880 games leaves little room).

## 10. Priors (drafted for PREDICTIONS_LEDGER, not appended)
P(HONEST) 0.30; P(NOT_HONEST) 0.35; P(INCONCLUSIVE) 0.30; P(INSUFFICIENT_DATA) 0.05. P(the [0.5, 0.7) bin realises above its stated
mean by more than 0.05) 0.40 (reason: production's hurdle was underconfident before the Platt map, raw 0.34-0.36 vs Platt 0.41-0.43 in
T317, and the stated 0.66 mass is unusual for rostered, not-OUT players).

=== RESULTS BELOW ===

(none)
