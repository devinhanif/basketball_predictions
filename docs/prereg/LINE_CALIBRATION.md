# LINE_CALIBRATION: a market-free recalibration of production's P(over line) (pre-registration)

Status: **DRAFT, written by the modeler Mon Oct 12 16:04 CDT 2026 (clock read by `date`).** It becomes the rule only when the main
session commits `prereg(LINE_CALIBRATION): <sha256>` of everything above the line `=== RESULTS BELOW ===`
(`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CALIBRATION.md | shasum -a 256`). The harness
(`research/eval/line_calibration_eval.py`, guards in `research/eval/line_calibration_rule.py`) refuses `select` without that
commit and refuses `report` (season 2025) without it plus a committed HOLDOUT_ACCESS_LOG row quoting Devin's words. Question:
Devin, 2026-10-12, "when can we recalibrate???", after seeing that at stored lines production's P(over) log loss is worse than a
coin flip. Season labels are `games.season` (2023 = 2023-24, 2024 = 2024-25, 2025 = 2025-26, the frozen holdout).

## The rule in eight lines
1. **Claim arm CAL:** per (book, stat, calendar-month block), one logistic regression fit on every earlier T-60 leg of that book
   and stat. Terms: `[1, x, z, bench, bench * x]`. Here `x = logit p_model`, `z = (line - model integer-grid mean) / max(model sd, 0.5)`
   clipped to [-5, 5], and `bench = starter10 < 0.5`. **Comparator A0** = raw production `p_model` on the same legs. Nothing derived
   from a price enters a fit.
2. **Primary:** mean per-leg log loss of P(over) at the line, CAL minus A0. Pinnacle T-60, the four stats pooled, every leg weighted
   equally. Paired 95% percentile bootstrap, 2,000 game resamples (seed 0). A date-clustered CI is reported beside it.
3. **Stage 1 (select) = season 2024 scored.** Each 2024 month is fit on all of 2023 plus the 2024 months before it. It runs once, after the freeze.
   **Stage 2 (report) = season 2025, once.** Each 2025 month is fit on 2023, 2024 and the 2025 months before it. It runs only after a
   Stage 1 pass and a committed holdout row with Devin's words.
4. **Pass (each stage, all required):** P1 CAL - A0 <= **-0.003** with CI upper < 0. P2 CAL - C0 CI upper < 0 (C0 = the fit rows' base
   rate: the map must carry model information, not only the base rate). P3 CAL - mean(SH_0..49) <= -2 null sd with CI upper < 0.
   P4 |mean CAL - realised| <= 0.015. P5 no stat significantly worse (CI lower > 0). P6 starter and bench slices point <= 0.
   P7 DraftKings direction agrees (CAL - A0 point < 0).
5. **Guards:** shuffled-outcome placebo SH_k (K = 50; the null sd was measured before the freeze and is declared below). The
   planted-future check runs on synthetic labels in every gate. The A0 reproduction runs against ODDS_HISTORY to 1e-9.
6. **Secondary (labels only, no pass):** per stat CAL - A0 with BH over m = 4; DraftKings full table; Brier; reliability in ten
   bins; calibration in the large by role; slices by confidence and month. **Reference rows (never criteria):** coin flip and the
   de-vigged market.
7. **Both stages pass:** a production proposal goes to Devin (shadow column first; see "Production integration"). **Stage 1 fails:**
   closed, with no 2025 touch.
8. This rule is the **one follow-up allowed** after CALIBRATED_GRID closed (T339). After it, line calibration is closed whatever the
   result. Nothing about this rule changes after a result; a new question gets a new id.

## Why, and what is different from CALIBRATED_GRID
T477 (reports/market_descriptive_2026-10-12.md, 2023+2024 pooled) found that at the stored T-60 line production's mean P(over) is
4.3 pp below the realised over rate on Pinnacle (-4.3 [-4.7, -3.8], n 72,674 legs, 2,509 games). The gap is -3.8 for starters and
-5.9 for bench players. The model's mean is about right, so the error is in the shape of the distribution near the line. As a
result the model picks UNDER on 75% of legs. On Pinnacle 2024 alone, A0's pooled log loss is 0.6999 (n 37,528). A coin flip
scores 0.6931 by construction.

CALIBRATED_GRID (frozen 43f9182c, fe1bfe1; run ee9b572; T339) tried temperature, Platt and isotonic maps on DK lines with a
trailing 60-day window and closed with no pass. Its shuffled-label control A4 gained more than any real map, because shrinking
toward 0.5 helps an over-confident, biased grid. Its kill criteria allow one follow-up. This is that follow-up, and it differs in
five ways:
- (a) It targets the location bias that T477 found, not only confidence. The logistic intercept and the bench terms can move the
  probability across 0.5.
- (b) It fits on all earlier legs, walk-forward by month, instead of a 60-day window that left 20% of rows unmapped.
- (c) It adds the terms that T477 points at (role, and how far the line sits from the model mean in model sd units).
- (d) The control is redesigned. C0 (base rate) and the permutation null are now **required** comparators. "Beats raw" alone is
  known to be cheap here: the shuffled-label maps beat A0 by 0.0078 on Pinnacle 2024, as measured by the gate below.
- (e) The primary book is Pinnacle, and the report stage is the 2025 holdout.

## Hypothesis and prior
H1: the walk-forward map lowers line log loss against raw production by at least 0.003 per leg, and it beats the base rate. That
means production carries real information at the line once its bias and width are corrected. Null: the map only reproduces the
base rate (P2 fails), or it does not reach the floor. Prior: about 85% for P1 (see data exposure: the bias is known and large).
About 55% for P2, because model picks hit 53-56% against a base rate near 52.5%, which is weak. About 45% that both pass on 2025.

## Method (one choice, made now): logistic recalibration, not isotonic
- It captures both known errors with two parameters: the intercept for the shift and the slope on `x` for over-confidence. Isotonic
  per stat can also shift, but it cannot use role or `z`, and T477 says bench players are off by more than starters.
- It has low variance: 5 parameters per (book, stat, block) on at least 6,339 fit rows (the gate minimum). Isotonic on a growing
  window gives step artifacts and a flat extrapolation at the ends, which matters for exactly the confident legs that were
  over-claimed (CALIBRATED_GRID: claimed 0.84, realised 0.47-0.53).
- Isotonic has already been run on these seasons (CALIBRATED_GRID A3), and its numbers are seen. Re-running it would be variant
  chasing.
- Location `z` uses the grid **mean** (`mean_int`), not the median as the brief said. The mean is in the sha-pinned pairs frame. The
  integer median ties with `line +/- 0.5` on most count legs (fg3m median 1 or 2 against a 1.5 line), which would make `z` nearly a
  two-valued indicator. **Devin to confirm.**
- Fixed details: IRLS with ridge 1.0 on the four non-intercept coefficients, start at 0, tolerance 1e-10. `x` is clipped at
  0.5/199 (production grid resolution). No weighting by age: the map has 5 parameters, and the T477 bias has the same sign in both
  seasons, both books and both roles. This departs from "don't pool stale seasons equally" deliberately, and is recorded here.

## Data and seasons
Legs come from the MODEL_CLV paired frames `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` (ODDS_HISTORY join, production OOF
grid reproduced exactly). Each leg is a T-60 row of Pinnacle or DraftKings with a main line, unique per (game, player, stat,
book). DNP legs are voided upstream and every line is x.5, so no pushes. Stored props lines exist for **2023 and 2024 only** (the
`odds_history` table has 0 rows before 2023). Season 2025 lines were pulled but are holdout. So there is no 2022 fit season. Stage 1
fits on 2023 and scores 2024 walk-forward by month. Calibration may drift within a season, so each 2024 month also sees the 2024
months before it.

PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c
NULL_SD_PINNACLE_2024: 0.00016927178215808044

## Data exposure (honest)
- **Seen before this draft:**
  - T477's calibration gaps (2023+2024 pooled, both books).
  - CALIBRATED_GRID's results on DK 2023/2024. Isotonic A3 2024 deltas were pts -0.0075, reb -0.0026, ast -0.0039 and fg3m +0.0005.
    The A4 shrink control gained -0.004 to -0.009.
  - Devin's table (2024 rehearsal rows, Pinnacle, all stats): market 0.6865, production with mixture 0.6977, coin 0.6931.
- **Measured by this draft's gate on real labels:**
  - A0's log loss per cell. These are ODDS_HISTORY's published values.
  - The 50 shuffled-label maps on Pinnacle 2024: SH - A0 = -0.0078 on average, null sd 0.00017. Those maps are about the base rate.
    That is what a permutation null is, and it is the number P3 needs.
- **Not seen:** any CAL or C0 fit on real labels. Every CAL number in the gate is on synthetic labels.
- **Consequence:** Stage 1 is a partly exposed select stage, because the bias it should fix is known. **P1 on 2024 is close to
  foregone, and P2 is the informative test.** The only clean evidence will be Stage 2 (2025) and the live forward log.

## Arms
| arm | what | stage | role |
|---|---|---|---|
| A0 | raw production `p_model`, clipped at 0.5/199 | 1, 2 | comparator |
| **CAL** | walk-forward logistic map (above) | 1, 2 | **the claim** |
| C0 | walk-forward base rate of the fit rows (same book, stat, block) | 1, 2 | comparator (P2) |
| SH_k, k = 0..49 | CAL refit after permuting the fit rows' labels, `default_rng([1000+k, stat, block, book])` | 1, 2 | permutation null (P3) |
| COIN, MKT | 0.5; the multiplicative de-vigged T-60 market price | 1, 2 | reference rows only |

## Pass rule
Both stages use the same rule. Pinnacle is the primary book and DraftKings is the consistency book. Each book is fit on its own legs.
- **P1** CAL - A0 <= -0.003 and game-clustered CI upper < 0. The floor equals the log-loss value of removing the known bias alone:
  a 4 pp miss at p near 0.45 costs about 0.04^2 / (2 x 0.2475) = 0.0032 per leg. A map that does not recover at least that much has
  not fixed what we know is broken. CALIBRATED_GRID used 0.002. This floor is higher because the bias is now measured.
- **P2** CAL - C0 CI upper < 0. Without it, "recalibrated" could mean "replaced by a constant".
- **P3** CAL - mean_k(SH_k) <= -2 x NULL_SD and CI upper < 0. NULL_SD is measured before the freeze (above) for Stage 1, and
  recomputed inside the touch for Stage 2.
- **P4** |mean(CAL) - realised over rate| <= 0.015. The point of a calibration layer is calibration in the large; A0 is at -0.043.
- **P5** No stat with CAL - A0 CI lower > 0.
- **P6** Starter and bench slices have CAL - A0 point <= 0.
- **P7** DraftKings CAL - A0 point < 0.

A rehearsal with a calibrated synthetic truth (A0 already right) fails P1, P5, P6 and P7, so the rule can say no. A rehearsal with
the planted T477-like bias passes all seven.

## Kill criteria
Any of P1-P7 fails on Stage 1: closed, no 2025 touch, and no window, term or per-tier variants. Stage 2 fails: closed, and the
live log can reopen it only under a new id.

## Minimum-data gate (measured Mon Oct 12 16:03 CDT 2026)
Command: `research/eval/line_calibration_eval.py gate --allow-draft`. Output: reports/prereg_line_calibration/gate_draft.json. The
run took 13 s on CPU, read-only, seasons 2023-24 only, and no CAL fit used a real label. All checks pass.

| check | measured | bar | pass |
|---|---|---|---|
| 1 Pinnacle 2024 legs (games) | 37,528 (1,260) | >= 20,000 | yes |
| 1 Pinnacle 2024 legs per stat | pts 13,021, reb 6,645, ast 6,771, fg3m 11,091 | >= 5,000 | yes |
| 1 2024 blocks with < 2,000 fit rows | 0 of 36 Pinnacle, 0 of 36 DK (smallest fit 6,339 / 9,650) | 0 | yes |
| 2 inputs finite; starter10 finite | all; 0 missing | all | yes |
| 2 T-60 snapshot >= 60 min before the real tip | 100% | 100% | yes |
| 2 lines x.5 | all | all | yes |
| 3 A0 equals ODDS_HISTORY h1 (n and log loss) | 16 of 16 cells, \|diff\| < 1e-9 | 16 | yes |
| 4 planted future: labels on/after block start flipped | 54 of 54 probes identical (CAL, C0, SH; 2 books x 9 blocks) | all | yes |
| 4 positive control: in-window labels flipped | 54 of 54 moved | all | yes |
| 5 projected 95% half-width of P1 (synthetic truth, real rows and games) | 0.00106 (synthetic effect -0.0051 [-0.0061, -0.0040]; date-clustered the same) | <= 0.0015 | yes |
| 6 permutation null, K = 50, Pinnacle 2024 | sd 0.00017; SH - A0 mean -0.0078 | measured | yes |

- **Power, and the design effect on a real contrast.** The design effect of a fixed-arm contrast on real labels (coin - A0,
  Pinnacle 2024) is 1.51, at 29.8 legs per game. The per-stat half-widths (secondary family) are pts 0.0018, reb 0.0026, ast 0.0024
  and fg3m 0.0020. A per-stat effect at the floor is therefore detectable only for pts and fg3m.
- **Stage 2 data.** These cannot be measured without touching 2025, so they are checked inside the touch: 2025 Pinnacle legs
  >= 20,000 and 0 identity blocks. **If the 2025 Pinnacle legs fall below 20,000, Stage 2 stops unscored** and the touch is reported
  as spent.

## Data-capture audit (per BEST_PRACTICES, 2026-10-12)
- **Used:** stored props lines 2023 and 2024 (odds_history; Pinnacle, DraftKings) joined to the stored production OOF grid.
- **Exist but unused:**
  - FanDuel lines (2023-24). They would add a third book, but they are not in the declared family, and pooling books would
    double-count legs.
  - The consensus rows: a median of books, so not a book that anyone can act on.
  - T-5 rows. The production use is T-60.
- **Do not exist here:** props lines before 2023-24. The odds table has 0 rows with season < 2023. The vendor used for ODDS_HISTORY
  is cancelled. As far as I know, historical player-props archives from that vendor start in 2023, and they are paid. **I could not
  verify that, and I did not search for a free source.** Box-score history (2019-2022) cannot help, because there is no line to
  calibrate against.
- **Could be repaired:** nothing that changes power. Power is set by the scored season, and the gate already passes on it.
- **Era flags:** none. Both seasons are full regular seasons plus playoffs.

## What each choice protects against
| choice | protects against |
|---|---|
| fit strictly before the block's first day, month blocks | a later line or outcome shaping an earlier fit (gate check 4, unit tests) |
| no price in the design (`design()` takes no price argument; test) | "calibration" that is secretly the market (that would be MARKET_SHRINK, a different question) |
| C0 and SH as required comparators | the CALIBRATED_GRID failure: a shrink toward the base rate passing as calibration |
| null sd measured pre-freeze, K = 50 | a guard that fires by chance (RECENT_ROLE_FULL, T473) |
| P4 calibration in the large | a log-loss gain from sharpening while the bias stays |
| P5-P7 do-no-harm and DK direction | a pooled gain hiding a stat, a role or a book that the map makes worse |
| floor 0.003 = value of the known bias | shipping a map that recovers less than we already know is broken |
| 2025 once, after Stage 1, with Devin's words | a partly exposed select stage (above) being taken as confirmation |
| Pinnacle primary chosen now | picking the book after seeing which one passes |

## Production integration, if both stages pass (a production change: Devin's decision; nothing here changes nba/daily or ops)
- **Store, don't replace.** The daily pipeline would write `p_over_cal` beside the raw `p_over` for every (player, stat, line) it
  prices. The budget tool and the briefing keep reading the raw value until Devin promotes the calibrated one, after a shadow
  period on the live log.
- **The map is an artifact.** Coefficients per (book, stat) are refit on the first of each month from every stored T-60 leg with an
  outcome: 2023-25 pairs plus the live log. They are written to a versioned file (sha recorded), and applied at T-60 and T-30 with
  the line known at that time. If there is no stored line, there is no calibrated value; it is never imputed. A Kalshi "N+" ladder
  maps to line N - 0.5.
- **Replay oracle.** Replaying a season through the pipeline with the stored map files must reproduce the stored `p_over_cal`
  byte for byte. Any change to the production grid (for example a P(play) mixture, as in Devin's 0.6977 rows) invalidates the map
  until it is refit on legs scored by the new grid. The map calibrates a specific grid.
- **Monitoring.** The morning briefing would show the trailing-30-day calibration in the large, raw against calibrated, with n. A
  sign flip in the intercept is an alert, not a silent refit.

## Stage 2 prerequisites (the 2025 touch)
- **The 2025 pairs frame does not exist yet.** It would hold production OOF P(over) at 2025 Pinnacle/DK T-60 lines, with outcomes.
  `research/eval/model_clv_pairs.py` asserts season <= 2024, and ODDS_HISTORY's 2025 path is guard-only. Building it reads 2025
  outcomes and prices, so it is part of the touch. Its exporter code is reviewed and committed before the holdout row is logged.
  The row names the exporter commit and the frame's sha256 is printed in the report.
- **Holdout row template:**
  `| <date> | research.eval.line_calibration_eval report --sha <64 hex> --pairs-2025 <path> (rule LINE_CALIBRATION, sha8 <8>, freeze commit <h>) | props line calibration map (CAL vs raw production) | pts, reb, ast, fg3m | PRE-REGISTERED; CLEAN first touch of LINE_CALIBRATION | DEVIN CONFIRMED "<his words>" |`

## Commands (maintainer)
```
M="-m research.eval.line_calibration_eval"
.venv/bin/python -W ignore $M gate --allow-draft                 # re-measure the gate (13 s)
.venv/bin/python -W ignore $M rehearse                           # synthetic labels, 2024 standing in (~20 s)
.venv/bin/python -W ignore $M select --sha <64 hex>              # Stage 1, after `prereg(LINE_CALIBRATION): <sha>` is committed
.venv/bin/python -W ignore $M report --sha <64 hex> --pairs-2025 <parquet>   # Stage 2, after a Stage 1 pass and the holdout row
```
The job is light (numpy; peak RSS 476 MB measured on the gate). It never takes or removes `data/ops/heavy.lock` and only records whether the lock is held.
Outputs:
- reports/prereg_line_calibration/{gate_draft,gate,select,report_2025}.json
- reports/prereg_line_calibration/rehearsal/results.json

## Unstated details fixed before any scored number
- A leg is a t60 row of pinnacle or draftkings in the MODEL_CLV pairs frame.
- A block is the calendar month of game_date. If a block has fewer than 2,000 fit rows, every arm uses the identity (raw p_model);
  the gate shows 0 such blocks in 2024.
- Every model-derived probability is clipped to [0.5/199, 1 - 0.5/199] before log loss and Brier. The market is clipped to 1e-9.
- One multinomial game-resample matrix (seed 0, B = 2,000) per (stage, book) is shared by every statistic. One-sided p for "CAL
  beats A0" is (1 + #{boot of A0 - CAL <= 0}) / 2001, and BH runs over the four per-stat p values.
- Pass contrasts weight legs, not stats.
- starter means starter10 >= 0.5.
- Confidence buckets on |p_model - 0.5| are [0, .05), [.05, .10), [.10, .20) and [.20, .5].
- Reliability uses ten equal-width bins.
- The synthetic truth for gate check 5 and the rehearsal is logit p = 0.17 + 0.85 logit p_model (about T477's -4 pp gap). It is
  never a real label.

=== RESULTS BELOW ===
