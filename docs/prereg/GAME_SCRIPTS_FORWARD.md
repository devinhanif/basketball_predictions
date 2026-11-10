# GAME_SCRIPTS_FORWARD: do the four game scripts happen as often as the model says, and do script-conditional legs hold, on the 2026-27 live log? (pre-registration)

Status: **DRAFT, revision 1 (revision 0 13:05 CST; revision 1 Tue 2026-11-10 afternoon CST: Devin's five choices written in, harness
written, text aligned with what the harness computes; section 13). Not frozen. Nothing in this file has
been scored on any game after the freeze, and no 2026 script outcome has been computed by anyone for this draft (section 8).**
- **How it becomes the rule:** the main session commits `prereg(GAME_SCRIPTS_FORWARD): <sha256>` of everything above the line
  `=== RESULTS BELOW ===` (`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/GAME_SCRIPTS_FORWARD.md | shasum -a 256`), with the
  status line changed to FROZEN in the same commit. Devin's choices are settled (section 11) and the harness is written (section 10);
  what remains before the freeze is an adversary pre-freeze review returning READY_TO_FREEZE.
- **Origin:** Devin, 2026-11-10 12:52 (DECISIONS 56adf8f7), piece 4 of "how a game can play out". **Follows advice** (Claude recommended
  a forward test before any script number loses its `exploratory` label). Supersedes the draft `docs/contracts/combo_script_forward_rule_DRAFT.md`
  (adversary B1 of `docs/reviews/combo_math_2026-11-10.md`); its parts are kept, changed or dropped as listed in section 9.
- **Season labels** are `games.season`. Scored: season 2026 regular-season games (game id prefix `002`) dated after the freeze. Season 2025
  is never scored, selected on or compared with any forecast (section 4.4 on the one way its scores enter as an as-of input).

## 1. Hypotheses
- **H-P (primary, calibration of P(script)).** For each script s in {blowout, close, shootout, grind}, the pre-tip probability the combo
  payload shows (`nba.parlay.combo.script_prior`) matches how often s happens: |realised share - mean P(s)| is within 0.05.
- **H-D (secondary, does the game-to-game variation mean anything).** For blowout and close (the two whose P varies by game), P(s) scores
  better than a constant equal to its own window mean.
- **H-S (secondary, conditional legs).** For each script, the script-conditional P(over at the main line | s happened) the app shows when a
  user picks s scores better than the unconditional calibrated P(over) on the legs of games that realised s, and is calibrated within 0.03.
- **Not claimed either way:** that anyone can predict the script, or any edge. Scripts have no price in this rule and none is compared.

## 2. The rule in twelve lines
1. **Games.** Season 2026, game id prefix `002`, slate ET date strictly after the ET calendar date of the freeze commit's committer time
   (read from git; the main session merges with `--no-ff`). Scored iff final (`home_pts > 0 AND away_pts > 0`) and a production
   `rung0_injury_elo` `win_prob_home` row exists with `made_at < tipoff` where `tipoff` equals the schedule's real tip (facts `tips_at`).
   The latest such row is used. A final game without one is listed `unforecast`.
2. **Forecast (all frozen, recomputed by code pinned at the freeze commit).** `mu_m = margin_b + margin_s * logit(p_home)`, `mu_t = total_a + total_c * f`, `f` the
   as-of total feature from every game dated before the slate date (`nba/parlay/slate.py` path: `game_model.slate_total_feature`),
   `sd_m`, `sd_t` and the five numbers from `data/parlay/joint_model.json` sha256 `a2b4c4a9` (fit 2022-24, no refit ever inside this rule).
   P(s) = `combo.script_prior(s, game, shared)` with definitions `{15, 5, 12.0}` (`combo_params_v1.json` sha256 `1a85bb31`).
3. **Outcomes.** From the final score and the same `mu_t`: blowout `|margin| >= 15`; close `|margin| <= 5`; shootout
   `total >= ceil(mu_t + 12)`; grind `total <= floor(mu_t - 12)`. Overtime counts (final score).
4. **Primary metric (family P, m = 4).** Per script, `gap_s = realised share - mean P(s)` over scored games. 95% CI: percentile bootstrap
   over slate DATES (B = 2,000, seed 20261110); the game-level CI is printed beside it (each game is one row, so it is the iid CI).
5. **Primary words, per script.** CALIBRATED: the date CI lies inside [-0.05, +0.05]. MISCALIBRATED: the two-sided date-bootstrap p-value
   for gap = 0, BH-adjusted over the four scripts, is < 0.05 and |gap| >= 0.02. INCONCLUSIVE: neither. If both hold (possible only with a
   CI half-width below 0.015, not reachable this season), the word is CALIBRATED and the offset is printed beside it.
6. **One look.** At the first slate date D on which the window holds >= 750 scored games (projected 2027-03-07, section 5), counting only
   dates before the run date; run after every game through D is settled (final, or listed `unplayed_or_postponed` 36 h after its tip).
   Backstop: if 750 is not reached by 2027-03-28, D = 2027-03-28. Below 600 scored games at D: every word is INSUFFICIENT_DATA.
   `run` reads the real ET clock only and refuses before D. No interim look at any outcome number of this rule.
7. **Family D (m = 2: blowout, close).** dBrier = Brier(P(s)) - Brier(c_s), c_s = the window mean of P(s) over scored games (label-free).
   INFORMATIVE: dBrier <= -0.002 and the date CI upper < 0, with the one-sided p BH-adjusted over the two; else NOT_SHOWN (underpowered,
   section 5; NOT_SHOWN is not "no information").
8. **Family S legs (m = 4).** DraftKings main x.5 over legs exactly as the `line_cal` export defines them (`docs/contracts/line_cal_export_v1.md`:
   the newest two-sided non-alternate DraftKings pair at or before the production row's own `made_at` and before tip), stats pts, reb,
   ast, fg3m, played only (DNP voided), on games that realised s. Unconditional U = `p_over_cal`. Conditional C_s = `1 - Phi((a - mu) / sigma)`,
   `a = Phi^-1(1 - U)`, `(mu, sigma)` from `combo_params_v1.json` `script_params[(s, stat, role)]`, role `starter` iff the served
   `starter_rate >= 0.5` (as `combo.evaluate(..., script=s)` computes a one-leg combo).
9. **Family S checks, per script.** (S-i) paired Brier C_s - U, 95% GAME-clustered CI (B 2,000, seed 20261110) upper < 0, one-sided p
   BH-adjusted over the four scripts < 0.05. (S-ii) realised over rate - mean C_s, game-clustered CI inside [-0.03, +0.03]. Words:
   CONDITIONAL_CALIBRATED (S-i and S-ii), IMPROVES_BUT_OFF (S-i only), NOT_SHOWN (else), INSUFFICIENT_DATA (< 150 realised games of s or
   < 2,000 legs). The same-player joint ratio is printed (descriptive, section 4.3), with no word.
10. **S waits for LINE_CAL_LIVE.** Family S scores DraftKings `p_over_cal` outcomes on 2026 legs, which is LINE_CAL_LIVE's S3 cell. S is
    computed only after LINE_CAL_LIVE's look is recorded (its frozen harness output `reports/prereg_line_cal_live/results.json` exists);
    if it is not, the primary run writes S as DEFERRED and `run-s` computes S later at the same D, never early.
11. **Multiplicity.** Three declared families, BH within each (P: m = 4 on MISCALIBRATED; D: m = 2; S: m = 4 on S-i). The CALIBRATED and
    CONDITIONAL_CALIBRATED words are per-script equivalence (intersection-union) labels, so BH is not applied to them; the family size is
    stated. No word in one family depends on another.
12. **Nothing changes after the look.** No definition, threshold, parameter, band or window changes. A new question gets a new id; at most
    one pre-registered follow-up (section 9).

## 3. Why these choices (what each protects against)
| choice | protects against |
|---|---|
| forward only, after the freeze; pre-freeze 2026 games reported once at the look, labelled PRE_FREEZE, no word | a rule written by someone who could have seen the season's scoring level; the post-amendment second look that sank grind's 2023-24 label (B1) |
| frozen game model, definitions and script params; recomputed by code pinned at the freeze | a "calibrated" label for a number that was tuned on the season it is judged on; a wiring branch changing what is scored |
| latest production stamp strictly before the real tip; tip equal to the schedule | post-tip inputs; a proxy tip (BEST_PRACTICES: real tip times) |
| outcome thresholds use the same pre-tip `mu_t` | a realised definition that moves with information the forecast did not have |
| tolerance 0.05 (practical floor) | a "calibrated" word for a number off by more than one game in twenty; of the tolerances considered (0.04, 0.05, 0.06), 0.05 is the smallest with power >= 0.58 at a true gap of 0 for all four scripts at the look (section 5). A 0.04 tolerance cannot pass this season |
| MISCALIBRATED needs BH and |gap| >= 0.02 | four chances at a false alarm; a "miscalibrated" word for a gap nobody would notice |
| date-clustered CI for P and D | nightly shared conditions (scoring environment, officiating) moving several games at once; the cross-game rule of BEST_PRACTICES |
| game-clustered CI for S | legs of one game sharing minutes and pace (row-level CIs were anti-conservative) |
| one look at a declared n, a backstop, a real clock, settled dates | peeking; a test that never ends; a date-dependent result |
| D against the window mean of P, not the 2023-24 base rate | mixing resolution with calibration-in-the-large (a constant from another season carries its own offset) |
| S scores the conditional on its condition | it is the definition of the number the app shows ("if this game is a blowout"); both arms are fixed before tip, the condition only selects which games score. It cannot be read as pre-tip skill and is not |
| S-ii tolerance 0.03, not the old 0.02 | a check that cannot pass: at the forward volume (16 legs per game against 41 in 2023-24) the S-ii half-width is about 0.017 to 0.019 (section 5), so 0.02 leaves almost no room (non-negotiable 8) |
| same-player joint ratio descriptive only | the old criterion (iii) "CI contains 1" was a non-rejection test (B1); its equivalence replacement [0.95, 1.05] cannot pass at the forward volume (half-width about 0.09) |
| S waits for LINE_CAL_LIVE's look | exposing a frozen rule's DraftKings cell before its own look (the 2026-11-09 23:02 disclosure) |
| regular season only | play-in and playoff games have different rotations and stakes; the scripts were fit on regular seasons |

## 4. Details that are fixed now
### 4.1 Integrity (any one voids every word: INTEGRITY_FAIL, results printed, no word)
- more than 5% of final window games `unforecast`;
- any scored game whose stamp `tipoff` differs from the schedule tip, or whose stamp is at or after the tip;
- `joint_model.json` or `combo_params_v1.json` at run time not matching the sha256 prefixes in rule line 2 (the run refuses);
- the pinned `script_prior` disagreeing with a closed-form recomputation (the gate's `p_blowout`/`p_close`/`p_shootout`/`p_grind`) by more
  than 1e-9 on any game.
- Not a void: the production win model changing version inside the window. The words are then computed on the games of the version in
  force at the freeze only (>= 600 needed), and the other segment is printed.
### 4.2 Reported with every word (descriptive, no word)
- n games, n dates, realised count and share, mean P, gap with date and game CIs, Brier, per script.
- Reliability, fixed bins: blowout P [0, .30, .35, .40, .50, 1], close P [0, .20, .25, .30, 1]: n, mean P, realised share, game CI.
- Calibration slope and intercept (logistic of outcome on logit P(s)) for blowout and close, date CI.
- For shootout and grind (P is a constant band 0.253-0.271 by construction): mean(total - mu_t) with its date CI and sd(total - mu_t)
  against the frozen `sd_t` 18.82. These two numbers decide both scripts; a miss is read through them.
- Slices: calendar month; the first and second half of the window (drift); |mu_m| < 5, 5-10, >= 10 for blowout and close (gap per
  cell, no CI). The Cup slice of revision 0 is dropped: no Cup flag exists in the data.
- Integrity counts: final games, `unforecast` and its share, stamps with a bad tip or at/after tip, win-model versions, the largest
  closed-form deviation.
- Dropped in revision 1: the comparison with a nightly stamped combo block (no such stamp exists yet; under O3a the scored number is the
  recomputed one).
- PRE_FREEZE: the same table on 2026-10-20 through the freeze date, once, labelled, no word.
### 4.3 Family S, descriptive
- Per script: legs, games, realised over rate, mean U, mean C_s, Brier of each; the same table by stat and starter/bench (eight
  cells, each cell's own S table, descriptive: no cell word is read or acted on).
- The unconditional gap (realised - mean U) on ALL window legs is printed beside S-ii, so a map that does not transport in 2026 is not
  read as a script failure.
- Same-player pairs (different stats, both legs main-line overs): realised joint / copula joint under s, game-clustered CI, n pairs.
- Legs the app labels "above the tested range" (calibrated P > 0.75 or raw >= 0.80): counted inside S, also printed as a slice.
- Combos of three or more legs: not scored (the app labels them "model estimate, not checked against results").
### 4.4 Season 2025
No 2025 game is scored, selected, or compared with a forecast. The one way 2025 data enters is the as-of total feature `f`: the live
engine (`nba/parlay/slate.py`) builds it from every final game dated before the slate, so early-window `mu_t` carries a decaying weight on
2025-26 totals, exactly as the number the app shows does. This is an input to a 2026 forecast, not a holdout evaluation; Devin's call (O3).

## 5. Minimum-data gate (non-negotiable 8), measured Tue 2026-11-10 13:00 CST on the data the rule will run on
- **Command:** `.venv-1/bin/python -W ignore -m research.eval.game_scripts_forward_gate --data-root /Users/devin/Downloads/nba-prediction --until 2026-11-09 --freeze 2026-11-12 --out reports/prereg_game_scripts_forward/gate_draft.json`
  (about 2 s, read-only connections only; output sha256 `0a48439f`).
- **Data version:** `nba.duckdb` mtime 2026-11-10 12:51; `data/markets/market_asof.duckdb` mtime 13:00 (live capture); `data/facts/facts.duckdb`
  mtime 06:02 (2026-27 schedule); `joint_model.json` `a2b4c4a9`; `combo_params_v1.json` `1a85bb31`; 2023-24 `reports/odds_history/game_rows_{2023,2024}.parquet`.
- **Label-free for 2026:** no 2026 margin, total or player stat was read; finals are counted by the presence of a score. Part B reads
  2023-24 outcomes, already exposed by `combo_eval`.

| check (2026, label-free) | measured | bar | pass |
|---|---|---|---|
| finals through 2026-11-09, by ET week | 154: 43 (10-19 week, 6 dates), 49, 54, 8 (partial) | >= 40 per full week | yes |
| scheduled regular season (facts, latest `tips_at`) | 1,200; 51-55 a week Dec-Apr, except 29 (11-30), 14 (12-07, Cup knockout not yet listed), 30 (02-15, All-Star), 32 (02-22) | n/a | n/a |
| production win-prob stamp before the real tip | 154 of 154 final games (100%); stamp tip equals the schedule tip on 154 of 154; lead 55.0 to 115.0 min, median 69.9 | >= 95% | yes |
| P(blowout) from the stamps | 0.288 to 0.613; p10 0.290, p50 0.321, p90 0.431; mean 0.340 | varies by game | yes |
| P(close) from the stamps | 0.136 to 0.313; p10 0.229, p50 0.293, p90 0.312; mean 0.282 | varies by game | yes |
| P(shootout), P(grind) | constant band 0.253 to 0.271 (mean 0.262) by construction (the threshold moves with `mu_t`, `sd_t` is fixed) | n/a | n/a; D excludes them |
| expected script games a week at 52 games | blowout about 17.7; close 12.7 (2023-24 realised share 0.244) to 14.7 (model mean 0.282); shootout and grind 11.5 to 16 (realised 0.22 to 0.31 by season) | n/a | n/a |
| projected scored games after a freeze on 2026-11-12, at 100% stamp coverage | 300 by 12-31; 450 by 01-20; 600 by 02-09; **750 by 03-07**; 900 by 03-27 | the declared n before the backstop (03-28) | yes |
| DraftKings main x.5 legs paired to the production row (S legs; approximates the `line_cal` rule; before DNP removal) | 1,547 legs on 70 of 154 games through 11-09; since 11-02: 998 legs on 40 of 62 games, **16.1 per scheduled game** (25 per covered game); DraftKings posts lines on nearly every game, the loss is the pairing to the production row's `made_at` | >= 2,000 legs per script at the look | yes (about 2,900 to 4,000 per script) |

**History (2023-24, labelled, exposed; game model in-sample there):** n 1,318 / 1,315 games, 207 / 212 dates.
| script | realised share 2023 / 2024 | mean P | gap 2023 [95% date CI] | gap 2024 | mean(total - mu_t) |
|---|---|---|---|---|---|
| blowout | 0.328 / 0.342 | 0.351 / 0.355 | -0.024 [-0.050, +0.003] | -0.013 [-0.037, +0.010] | |
| close | 0.235 / 0.253 | 0.276 / 0.274 | -0.041 [-0.064, -0.016] | -0.022 [-0.047, +0.004] | |
| shootout | 0.221 / 0.287 | 0.262 / 0.262 | -0.041 [-0.065, -0.017] | +0.025 [+0.000, +0.047] | -2.43 / +2.14 |
| grind | 0.311 / 0.227 | 0.262 / 0.262 | +0.049 [+0.025, +0.075] | -0.035 [-0.057, -0.013] | (same) |

Reliability (2024, blowout): P 0.40-0.50 bin, n 183, said 0.441, happened 0.404; P >= 0.50, n 96, said 0.580, happened 0.521: the model's
blowout probabilities run high in mismatches. Close runs 2 to 4 points high in both seasons.
D, 2023 / 2024, dBrier against the season's own mean P: blowout -0.0026 [-0.0063, +0.0010] / -0.0038 [-0.0082, +0.0007]; close -0.0017
[-0.0035, +0.0001] / -0.0018 [-0.0038, +0.0003] (date CIs).

**Power at the look (normal approximation; per-game sd and date design effect from the larger of 2023 and 2024):**
| script | sd per game | date design effect | CI half-width at 600 / 750 / 900 | P(CALIBRATED) at 750, true gap 0 / 0.02 / 0.04 | P(reject gap = 0) at 750, true gap 0.04 |
|---|---|---|---|---|---|
| blowout | 0.471 | 1.06 | 0.039 / 0.035 / 0.032 | 0.62 / 0.38 / 0.08 | 0.62 |
| close | 0.433 | 1.11 | 0.036 / 0.033 / 0.030 | 0.71 / 0.43 / 0.09 | 0.67 |
| shootout | 0.452 | 1.21 | 0.040 / 0.036 / 0.032 | 0.58 / 0.35 / 0.08 | 0.60 |
| grind | 0.463 | 1.03 | 0.038 / 0.034 / 0.031 | 0.66 / 0.40 / 0.08 | 0.64 |
- At 600 games P(CALIBRATED | gap 0) is 0.39 to 0.54; at 450 it is 0.14 to 0.29. Hence the declared n of 750 and the 600 floor.
- With a 0.04 tolerance, P(CALIBRATED | gap 0) at 750 is about 0.2: that rule could not pass its own gate, so it is not offered as default.
- **D** half-width at 750: blowout 0.0058, close 0.0026. MDE at 80% power: about -0.008 (blowout), -0.0037 (close). The 2023-24 effects
  (-0.003, -0.002) sit below both: P(INFORMATIVE) is low by design and NOT_SHOWN means underpowered, not "no information".
- **S** (scaled from the 2023-24 fold-A half-widths, about 0.0085 at 291-444 games and 41 legs per game, by sqrt(legs) at 16 legs per game;
  expected realised games at 750: blowout about 250, close about 185, shootout and grind about 195): S-ii half-width about 0.017 (grind) to
  0.019 (shootout), so P(S-ii passes | conditional gap 0) about 0.8 with 0.03, about 0.2 with 0.02. S-i half-width about 0.0017 to 0.0020
  against 2023-24 effects of -0.0005 (close) to -0.0046 (grind). Same-player joint ratio half-width about 0.09 (descriptive only).
  The exact S counts are recomputed by the harness with the `line_cal` code at the freeze (a gate re-check, label-free).

**Capture audit (before any gate could be declared failed; none failed, recorded anyway).** Game outcomes: complete for every season
held (`nba.duckdb` 2022-2026, `data/history/` 2019-21); nothing to add. The forward volume is set by the schedule (about 52 games a week),
which no source can raise. S legs: DraftKings lines are captured on nearly every game (`live_book_odds`: a pre-tip line on every scheduled
game on 19 of 20 slate dates; 10-23 had 6 of 12); the S volume loss (40 of 62 games) is the pairing to the production run's `made_at` (the 2026-11-10 02:47 pairing fix moved
the cards, not `line_cal`). Repairable at a cost: pairing `line_cal` through the newest covering run (a live-path export change, Devin's
call, not this rule's), which would roughly double S legs; this rule scores the `line_cal` rule as served at the freeze.

## 6. Kill criteria and what is never done
- INTEGRITY_FAIL (4.1) voids the words. A crash before any outcome is read may be rerun; a run that read an outcome is final.
- No 2025 game scored; no refit; no change to a definition, threshold, parameter, window, look date or tolerance after the freeze.
- No aggregate realised-share-vs-P(script) number for post-freeze 2026 games is computed or shown anywhere (app, JARVIS, scratch, a
  session) before the look. Per-game display of the realised script (the live tracker, piece 3) is allowed: it shows one game, not a
  calibration. Any accidental aggregate exposure is recorded in DECISIONS the day it happens, and the look still runs as declared.

## 7. Diagnostics (descriptive, no word)
Listed in 4.2 and 4.3. The two that explain a P miss: for blowout and close, the reliability bins and slope (is the spread `sd_m` 13.63 too
narrow or too wide); for shootout and grind, mean and sd of total - mu_t (is the season's scoring level off the as-of feature).

## 8. Data exposure (honest)
- **Seen before this draft:** the 2023-24 script results (`combo_eval` runs 1 and 2, the adversary's reproduction, this gate's part B).
- **Computed by this draft on 2026 (label-free):** game counts, stamp coverage, P(script) from the stamps, DraftKings leg counts.
- **Not computed by this draft:** any 2026 final margin, total, total - mu_t, realised script, player stat or leg outcome. Asked and answered
  (main session, 2026-11-10): no session computed 2026 script shares (section 10). The window starts after the freeze either way.
- **Overlap:** S scores DraftKings `p_over_cal` on 2026 legs (LINE_CAL_LIVE S3); handled by rule line 10.

## 9. Lineage, playbook check, and what a result changes
**From the contract draft** (`combo_script_forward_rule_DRAFT.md`): kept the forward-only window, the frozen params, the 150-game floor per
script for S, the Brier criterion (as S-i), the calibration criterion (as S-ii, tolerance 0.02 -> 0.03 by the gate), and the
no-change-after-look clause. Changed: the primary question is now P(script) itself (Devin's piece 4), which the draft did not test.
Dropped from the word: the same-player joint ratio (unreachable, section 5). The draft's "at least 20 games" start is replaced by the
declared n.

**Playbook check** (docs/MODEL_PLAYBOOK.md). Classes: the P(script) part is a game-level distribution layer (Normal margin and total on
top of a rating, `game_model.py`: "quantile and distribution layers" with the ratings class underneath); the S part is the set / joint
model of combo mode (Gaussian copula plus a per-script latent shift).
1. *Information or capacity?* Neither is added: it is a calibration audit of numbers already built. P(script) carries only the win
   model's information (injury-Elo) and an as-of scoring level; shootout and grind carry none game-to-game (constant P). The S shift is
   real information only after the fact (the script is a realised outcome).
2. *Enough data for the class?* The game model is five numbers on 3,951 games; nothing is fitted here. The forward test is limited by the
   schedule: 750 is the smallest tabulated n with power above 0.5 for every script at a perfect calibration (section 5). Script cells for S get about 185-250
   games each, against 291-444 per fold in 2023-24.
3. *Valid test?* Real tips (154 of 154), stamps strictly before tip, frozen inputs, integer-aware thresholds identical in forecast and
   outcome, date clustering for a cross-game question, game clustering for legs, both S arms on the same legs, played legs only. The
   class's named failure (shape wrong at the point that matters while the average looks fine) is covered by the reliability bins and
   the slope. Known limits: one season is one draw of the scoring level, which decides shootout and grind; no CI covers that. D is
   underpowered and says so. A comparator with less information than production: none (D compares P with its own mean).
4. *Lineage and inherited guards.* `combo_eval` (fd81aaf, amended 4fa66a1; all four exploratory, grind's pass a second look);
   adversary combo_math B1 (forward rule, equivalence instead of "contains 1"); `joint_eval` (copula kept over independence, same-player
   only); T490 (different players independent). Carried: the frozen params, train/test separation, holdout refusal, game-clustered
   bootstrap with 2,000 resamples. Dropped: two folds (forward replaces them), criterion (iii) as a word.
5. *What it scores against.* P: calibration-in-the-large with a tolerance (and Brier and reliability reported); D: Brier against the
   forecast's own mean; S: Brier and calibration at the posted DraftKings main line against the unconditional calibrated leg. No CRPS, no
   price.

**What a result changes** (proposals to Devin; app wiring is not this rule's):
- P CALIBRATED for s: the app may replace "exploratory" beside P(s) with "checked on 2026-27: n games, said x%, happened y% [CI]". Only the
  P(s) number; the conditional legs follow S.
- S CONDITIONAL_CALIBRATED for s: the app may drop "exploratory" for s's one- and two-leg conditional numbers at main lines inside the
  calibrated range; k >= 3 stays "model estimate". IMPROVES_BUT_OFF: the label stays, with the measured offset shown in Analyst mode.
- MISCALIBRATED / NOT_SHOWN / INCONCLUSIVE / INSUFFICIENT_DATA: the label stays `exploratory` and the numbers are shown with the realised
  share next to them. One pre-registered follow-up is allowed under a new id, chosen now: **GAME_SCRIPTS_RECENTRE** (a season-level
  recentring of `mu_t` and, for close, a refit of `sd_m`, both estimated on games before a cut date, tested forward on the 2027-28 season).
  No other follow-up.

## 10. Harness and preconditions
- **Harness** `research/eval/game_scripts_forward_eval.py` (pin in section 13; tests `tests/research/prereg_gates/test_game_scripts_forward_eval.py`):
  `status` (label-free counts and the projected look; finals are a SQL boolean, no score value is read; `--s-legs` counts S legs through
  the export's own `line_cal` code), `dryrun` (synthetic games and legs only, no database), `run` (the one look), `run-s` (family S at the
  same D after LINE_CAL_LIVE's results exist). No command takes a date: the look reads the real ET clock. `run` refuses unless
  `RULE_SHA256` and `FREEZE_COMMIT` are set and match this text, the look is open, the pinned inputs match, and no results file exists.
  Every function that reads a score or a player stat of a window game requires an open look state; only season 2026 (prefix `002`) is
  ever scored and any other season in a scored frame raises.
- **At the freeze:** set `RULE_SHA256` (this text) in the freeze commit and `FREEZE_COMMIT` right after it (a harness change only, no
  rule text change), and re-run `status --assume-freeze <date> --s-legs` (label-free) to record the exact S leg rate.
- **Exposure answer (main session, 2026-11-10):** no session computed 2026 script shares; the 11-10 team recap computed per-team
  game-winner records only, and the market agent building pieces 1-3 is told not to aggregate realised scripts.

## 11. Devin's choices (settled 2026-11-10, all five defaults)
- **O1 look size:** 750 scored games, backstop 2027-03-28 (rule line 6).
- **O2 tolerance for P:** 0.05 (rule line 5).
- **O3 the as-of total feature:** as the live engine builds it, from every final game before the slate, including 2025-26 scores as a
  decaying input to 2026 forecasts only; no 2025 game is evaluated (section 4.4).
- **O4 family S:** included, computed only after LINE_CAL_LIVE's look, tolerance 0.03 (rule lines 8-10).
- **O5 window:** after the freeze only (rule line 1).
Alternatives that were offered and not taken: 600 games / 02-28 or the full season; 0.06 or 0.04; a 2026-only feature or the export
stamp; S dropped or descriptive; the pre-freeze games scored.

## 12. Priors (stated before any 2026 outcome; copied to docs/PREDICTIONS_LEDGER.md)
- P words at the look (defaults): P(blowout CALIBRATED) 0.45; P(close CALIBRATED) 0.20, P(close MISCALIBRATED) 0.35; P(shootout
  CALIBRATED) 0.25; P(grind CALIBRATED) 0.25; P(shootout and grind both CALIBRATED) 0.12; P(all four CALIBRATED) 0.03; P(at least one
  MISCALIBRATED) 0.55.
- Expected gaps (80% intervals): blowout -0.015 (-0.045 to +0.015); close -0.030 (-0.055 to -0.005); shootout 0.000 (-0.045 to +0.045);
  grind 0.000 (-0.050 to +0.050); shootout and grind gaps of opposite sign: 0.80.
- D: P(blowout INFORMATIVE) 0.15; P(close INFORMATIVE) 0.05.
- S: P(CONDITIONAL_CALIBRATED) blowout 0.25, close 0.03, shootout 0.08, grind 0.35; P(S-i passes) blowout 0.45, close 0.10, shootout 0.65,
  grind 0.85. Expected S-i dBrier: blowout -0.0018, close -0.0006, shootout -0.0024, grind -0.0040 (each 80% +-0.002).
- Logistics: P(750 scored games reached by the backstop) 0.90; P(INTEGRITY_FAIL) 0.05; P(LINE_CAL_LIVE's look recorded before this look) 0.95.

## 13. Revision log and harness pin
- Revision 0 (13:05 CST, text e9779757): the draft with open choices.
- Revision 1: Devin's choices (section 11); the exposure answer (section 10); the LINE_CAL_LIVE condition made checkable (rule line 10);
  descriptive items aligned with the harness (Cup slice and the export-stamp comparison dropped, sd(total - mu_t) without a CI, stat x
  role cells descriptive); the harness and its pin. S-leg gate re-check through the export's own `line_cal` code (`status
  --assume-freeze 2026-10-19 --s-legs`, label-free, 85 s, 2026-11-10): 1,543 legs on 69 of 154 final games through 11-09, against the
  gate's approximation of 1,547 on 70; section 5's S numbers stand. No 2026 outcome was read for this revision; the harness's only runs were on
  synthetic data and a temporary test database.
- Harness pin: see the commit that carries this revision (`git log -1 -- research/eval/game_scripts_forward_eval.py`); the file's sha256
  is recorded in the hand-back and in the freeze commit message.

=== RESULTS BELOW ===

(empty until the look)
