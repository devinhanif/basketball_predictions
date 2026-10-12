# LINE_CALIBRATION: a market-free recalibration of production's P(over line) (pre-registration)

Status: **DRAFT, revision 2, written by the modeler Mon Oct 12 16:29 CDT 2026 (clock read by `date`).**
- **How it becomes the rule:** the main session commits `prereg(LINE_CALIBRATION): <sha256>` of everything above the line
  `=== RESULTS BELOW ===`. Hash it with
  `awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/LINE_CALIBRATION.md | shasum -a 256`.
- **Harness:** `research/eval/line_calibration_eval.py`, with the guards in `research/eval/line_calibration_rule.py`. It refuses `select`
  without that commit. It refuses `report` (season 2025) without the commit plus a committed HOLDOUT_ACCESS_LOG row that quotes
  Devin's words.
- **The question:** Devin, 2026-10-12, "when can we recalibrate???". He asked after seeing that, at stored lines, production's P(over)
  log loss is worse than a coin flip.
- **Revision 2 applies Devin's answers of 2026-10-12** ("we need to figure a way to calibrate the model, 2. fair, 3. fair, suggest
  something better, pinnacle is fine, 5. you have to weigh earlier sesasons less than newer"):
  - (1) A failure no longer closes calibration. It records a result and points to the next candidates.
  - (2) Stage 1 being partly exposed is accepted.
  - (3) z comes from the continuous 199-point grid.
  - (4) Pinnacle is primary and DraftKings counts for direction only.
  - (5) Fit rows are time-weighted with one fixed half-life.
- **Season labels** are `games.season`: 2023 = 2023-24, 2024 = 2024-25, and 2025 = 2025-26, the frozen holdout.

## The rule in eight lines
1. **Claim arm CAL.** For each (book, stat, calendar-month block), fit one weighted logistic regression on every earlier T-60 leg of that
   book and stat.
   - Terms: `[1, x, z, bench, bench * x]`.
   - `x = logit p_model`.
   - `z = (line - continuous median) / max((q75 - q25) / 1.349, 0.5)`, clipped to [-5, 5]. It is read from production's continuous
     199-point grid, before integer rounding.
   - `bench = starter10 < 0.5`.
   - Each fit row is weighted `0.5 ** (age_days / 365)`, where age is counted from the block's first day.
   - **Comparator A0** is raw production `p_model` on the same legs. Nothing derived from a price enters a fit.
2. **Primary metric.** Mean per-leg log loss of P(over) at the line, CAL minus A0, on Pinnacle T-60 with the four stats pooled. Every leg
   weighs the same. The CI is a paired 95% percentile bootstrap over 2,000 game resamples (seed 0). A date-clustered CI is reported
   beside it.
3. **Stages.**
   - **Stage 1 (select)** scores season 2024. Each 2024 month is fit on 2023 plus the 2024 months before it. It runs once, after the
     freeze.
   - **Stage 2 (report)** scores season 2025, once. Each month is fit on 2023, 2024 and the 2025 months before it. It runs only after
     Stage 1 returns CALIBRATED or SHRINKAGE_ONLY, and after a committed holdout row with Devin's words.
4. **Checks.**
   - **P1:** CAL - A0 <= **-0.003**, with CI upper < 0.
   - **P2:** CAL - C0 has CI upper < 0. C0 is the weighted base rate of the fit rows.
   - **P3:** CAL - mean(SH_0..49) <= -2 null sd, with CI upper < 0.
   - **P4:** |mean CAL - realised| <= 0.015.
   - **P5:** no stat is significantly worse (CI lower > 0).
   - **P6:** the starter slice and the bench slice each have a point estimate <= 0.
   - **P7:** DraftKings agrees in direction (CAL - A0 point < 0).
5. **Verdicts.**
   - **CALIBRATED:** all of P1-P7 pass.
   - **SHRINKAGE_ONLY:** P1 and P4-P7 pass, but P2 or P3 fails. The map does not beat generic shrinkage, and the served object is chosen
     below.
   - **FAIL:** anything else.
6. **Guards.**
   - Shuffled-outcome null SH_k, K = 50. Its sd is measured before the freeze and declared below.
   - Planted-future check on synthetic labels.
   - A0 reproduces ODDS_HISTORY to 1e-9.
   - The continuous grid reproduces the served P(over) exactly.
7. **Secondary results** carry labels only and no pass. They are:
   - per-stat contrasts with BH over m = 4;
   - the full DraftKings table;
   - Brier;
   - reliability in ten bins;
   - calibration in the large by role;
   - slices by confidence and by month.

   Coin flip and the de-vigged market are reference rows only.
8. **Outcomes.**
   - **CALIBRATED** or **SHRINKAGE_ONLY**, confirmed on 2025: a production proposal goes to Devin.
   - **FAIL:** recorded. Further calibration ideas become new questions under new ids (see "If this fails"). Nothing about this rule
     changes after a result.

## Why
- **The finding.** T477 (reports/market_descriptive_2026-10-12.md, 2023+2024 pooled) found that at the stored T-60 line, production's
  mean P(over) is 4.3 pp below the realised over rate on Pinnacle: -4.3 [-4.7, -3.8], n 72,674 legs, 2,509 games.
  - The gap is -3.8 for starters and -5.9 for bench players.
  - The model's mean is about right, so the error is in the shape of the distribution near the line.
  - As a result, the model picks UNDER on 75% of legs.
- **On Pinnacle 2024 alone,** A0's pooled log loss is 0.6999 (n 37,528). A coin flip scores 0.6931.
- **Devin wants a working calibration.** This rule is the first, cheapest construction. The section "If this fails" lists what comes
  next.

## Lineage (playbook question 4)
- **This rule is CALIBRATED_GRID's follow-up for the logistic map.**
  - CALIBRATED_GRID was frozen at 43f9182c in commit fe1bfe1 and run in ee9b572 (T339). It tried temperature, Platt and isotonic maps on
    DraftKings lines with a trailing 60-day window. It closed with no pass.
  - Its kill criteria allow one follow-up. This is it, for this construction.
  - Other calibration constructions are separate questions with their own ids.
- **What disqualified CALIBRATED_GRID.** Its shuffled-label control A4 gained more than any real map on lines in all 8 stat-seasons,
  because shrinking toward 0.5 helps an over-confident, biased grid. Its fixed control bar (A4 >= -0.0005) therefore failed by
  construction.
- **Guards carried forward:**
  - planted-future check on synthetic labels, with a positive control;
  - A0 reproduction against ODDS_HISTORY to 1e-9;
  - a shuffled-label control;
  - game-clustered bootstrap with seed 0 and B = 2,000;
  - per-stat BH over 4.
- **Guards changed:**
  - The shuffled control is now a sized permutation null (K = 50; BEST_PRACTICES 2026-10-12), not a fixed bar.
  - The deterministic base rate C0 is added, so "beats shuffling" has an exact meaning.
  - A result that beats raw but not shrinkage is now named (SHRINKAGE_ONLY) instead of discarded. See below for what that means for the
    product.
- **Guard dropped:** the monotonicity check on the whole exceedance grid. This map acts only at the line and never rewrites the grid.
- **Construction changes:**
  - (a) It targets T477's location bias. The intercept and the bench terms can move P(over) across 0.5.
  - (b) It fits on all earlier legs with a time decay, walk-forward by month. The old 60-day window left 20% of rows unmapped.
  - (c) It adds role, and the line's position on the continuous grid.
  - (d) The primary book is Pinnacle.
  - (e) The report stage is the 2025 holdout.

## Hypothesis and prior
- **H1.** The walk-forward map lowers line log loss against raw production by at least 0.003 per leg, and it beats shrinkage to the
  base rate. In other words, production carries information at the line once its bias and width are corrected.
- **Null.** The map only reproduces the base rate, or it does not reach the floor.
- **Priors:**
  - about 85% for P1, because the bias is known and large;
  - about 55% for P2 and P3, because model picks hit 53-56% against a base rate near 52.5%;
  - about 45% that 2025 confirms CALIBRATED;
  - about 80% that 2025 gives at least SHRINKAGE_ONLY.

## The shuffled-label check, read plainly, and what is shipped when the map only matches shrinkage
- **What shuffling does.** Shuffling the fit labels destroys every link between the model and the outcome but keeps the base rate. A
  map fit on shuffled labels converges to "predict the base rate", and C0 is that limit exactly.
  - The gate measured this on real 2024 Pinnacle labels: the 50 shuffled maps beat raw production by 0.0078 per leg (sd across seeds
    0.00018).
  - So **raw production at the line is worse than knowing nothing but the base rate.** "Beats raw" (P1) is therefore cheap.
  - P2 and P3 ask the real question: does the model's own P(over) add anything once it is calibrated?
- **If the answer is no (SHRINKAGE_ONLY), the honest product action is still to stop serving the raw number.** A probability that loses
  to the base rate should not reach a user. How that is decided, fixed now:
  - The served object is whichever of CAL and C0 has the lower pooled Pinnacle Stage 1 log loss. It must itself pass P1 and P4-P7
    (computed for both objects).
  - Stage 2 confirms that the same object passes P1 and P4-P7 on 2025.
  - The proposal to Devin then says, in words, that at the line the model carries no information beyond the base rate. The budget tool
    must therefore report no line edge from this source. In practice that means "keep your money" on line bets until a later rule
    shows information.
- **If the answer is yes (CALIBRATED), Stage 2 confirms when:**
  - CAL passes P1 and P4-P7 on 2025, and
  - the 2025 verdict is CALIBRATED.

  If 2025 returns SHRINKAGE_ONLY, the proposal is downgraded to the shrinkage wording above.

## Object and inputs (choices made now)
- **Method: logistic recalibration, not isotonic.**
  - It captures both known errors with two parameters: the intercept for the shift, and the slope on `x` for over-confidence.
  - Isotonic per stat cannot use role or `z`. T477 says bench players are off by more than starters.
  - The logistic fit has low variance: 5 parameters on at least 6,339 fit rows (effective n 6,260 after weighting).
  - Isotonic gives step artifacts and a flat extrapolation at the confident ends, which is where CALIBRATED_GRID found the worst
    over-claims (claimed 0.84, realised 0.47-0.53).
  - Isotonic on these seasons is already seen (CALIBRATED_GRID A3).
- **The continuous 199-point grid, recovered exactly from stored artifacts, with no refit.**
  - Production builds `q_j = max.acc(clip(c + s * zq_j, 0))`. Here `zq` is one sorted standardized grid per (stat, month block). It
    then serves the integer grid `ceil(q - 0.5)`.
  - The ODDS_HISTORY OOF rows store `c`, `s` and the integer survival counts `N_ik = 199 P(Y >= k)`.
  - Because `zq_j > (k - 0.5 - c_i) / s_i` exactly when `N_ik >= 200 - j`, every stored count brackets the grid values. With about
    3,000 rows per block, each needed `zq_j` is pinned to within 0.049 z units in the worst block (June playoffs) and to below 0.003
    in the October-April months.
  - The gate also checks that no block admits contradictory brackets (0 of 72 do). That confirms the one-grid-per-block construction.
- **Distance term.** `z = (line - q50) / max((q75 - q25) / 1.349, 0.5)`.
  - q25, q50 and q75 are interpolated at grid positions 49.25, 99 and 148.75 (0-based; `tau_j = (j + 0.5) / 199`).
  - The median is continuous, so it is not glued to the line. A count leg whose integer median is 1 and whose line is 1.5 still gets a
    graded distance.
  - (q75 - q25)/1.349 is the Gaussian-equivalent sd, robust to the grid's long right tail.
  - The floor of 0.5 is half the outcome's integer resolution: a spread narrower than that cannot be resolved on integer support. It
    binds on 0.28% of 2023 legs and 0.12% of 2024 legs.
- **The calibrated quantity stays production's served integer-grid P(over).**
  - At an x.5 line, `ceil(q - 0.5) >= k + 1` holds exactly when `q > k + 0.5 = line`. So the integer-grid and continuous-grid P(over)
    are the same number.
  - The gate verifies this: 0 of 179,673 legs differ.
  - There is nothing to switch. Only the distance term needs the continuous grid.
- **Time weights.**
  - Every fit row (CAL, C0 and SH alike) gets `w = 0.5 ** ((block start - game_date) / 365 days)`.
  - The half-life of 365 days is one season, fixed from first principles: the production model, players' roles and the books'
    line-setting all turn over on a season cadence. A one-season half-life makes last season count half as much as this one, while
    neighbouring months count nearly the same.
  - It is not chosen from results and is never tuned.
  - Weights shape the fits only; every scored leg weighs 1. Within Stage 1, 2023 rows weigh about 0.3-0.7.
- **Fixed details.**
  - IRLS with ridge 1.0 on the four non-intercept coefficients, starting at 0, tolerance 1e-10.
  - `x` is clipped at 0.5/199, the production grid resolution.
  - A block with fewer than 2,000 fit rows uses the identity (raw p_model) for every arm. Stage 1 has 0 such blocks.

## Data and seasons
- **Legs.** From the MODEL_CLV paired frames `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` (ODDS_HISTORY join, production OOF
  grid reproduced exactly).
  - A leg is one T-60 row of Pinnacle or DraftKings with a main line, unique per (game, player, stat, book).
  - DNP legs are voided upstream. Every line is x.5, so there are no pushes.
- **Grid rows.** From `reports/odds_history/model_rows_{2023,2024}.parquet`; every leg joins one row.
- **What exists.** Stored props lines exist for **2023 and 2024 only**: `odds_history` has 0 rows before 2023. Season 2025 lines were
  pulled but are holdout.
- **Consequence.** Stage 1 fits on 2023 and scores 2024, walk-forward by month.

PAIRS_SHA256_2023: 050dba3e250d43d6d33eec836cd840397b168896b6017725b00d2bfd238b2569
PAIRS_SHA256_2024: 560a3808d7d97110902471895e36a94a3f41d5ed88cb435e147d47f367dfdc5c
MODEL_ROWS_SHA256_2023: 002e53ef1a5fae333e8ff6edc92654de9f449d29c9be1775017e65fc378eea85
MODEL_ROWS_SHA256_2024: ec47dcdf566eac29742f8269b8c3cf4a10845440c9b298b41a40282577b62d70
NULL_SD_PINNACLE_2024: 0.00018281177430901576

## Data exposure (honest; accepted by Devin 2026-10-12, "2. fair")
- **Seen before this draft:**
  - T477's calibration gaps;
  - CALIBRATED_GRID's results on DraftKings 2023/2024. Isotonic A3 2024 gave pts -0.0075, reb -0.0026, ast -0.0039 and fg3m +0.0005;
    the A4 shrink gave -0.004 to -0.009.
  - Devin's table: market 0.6865, production with mixture 0.6977, coin 0.6931.
- **Measured by this draft's gate on real labels:**
  - A0's log loss per cell, which is ODDS_HISTORY's published values;
  - the 50 shuffled maps on Pinnacle 2024: SH - A0 = -0.0078, sd 0.00018.
- **Model-side, no outcomes:** the distribution of z. Its median is +0.18 (2023) and +0.15 (2024), meaning lines sit above the
  continuous median. This is the T477 bias seen from the model side.
- **Not seen:** any CAL or C0 fit on real labels. Every CAL number in the gate uses synthetic labels.
- **What this means.** P1 on 2024 is close to foregone, and P2/P3 are the informative tests. The clean evidence is Stage 2 and the
  live log.

## Arms
| arm | what | stage | role |
|---|---|---|---|
| A0 | raw production `p_model` (equals the continuous-grid P(over) at x.5 lines), clipped at 0.5/199 | 1, 2 | comparator |
| **CAL** | weighted walk-forward logistic map (above) | 1, 2 | **the claim** |
| C0 | weighted walk-forward base rate of the fit rows (same book, stat and block) | 1, 2 | comparator (P2); served object if SHRINKAGE_ONLY and lower log loss |
| SH_k, k = 0..49 | CAL refit after permuting the fit rows' labels (weights stay with the rows), `default_rng([1000+k, stat, block, book])` | 1, 2 | permutation null (P3) |
| COIN, MKT | 0.5; the multiplicative de-vigged T-60 market price | 1, 2 | reference rows only |

## Pass rule details
The primary book is Pinnacle; DraftKings is the consistency book. Each book is fit on its own legs.
- **P1 floor of -0.003.** This is the log-loss value of removing the known bias alone: a 4 pp miss at p near 0.45 costs about
  0.04^2 / (2 x 0.2475) = 0.0032 per leg.
- **P3's null sd** is the value declared above for Stage 1. For Stage 2 it is recomputed inside the touch with the same seeds.
- **Synthetic controls on the real row structure:**
  - planted T477-like bias: **CALIBRATED**;
  - truth where production is already calibrated: **FAIL** (P1, P5, P6 and P7 fail);
  - truth with no information beyond a base rate of 0.475: **SHRINKAGE_ONLY** with C0 served.

  So all three verdicts are reachable, and the rule can say no.

## If this fails (each item is a NEW question under a new id, drafted only after this result is recorded)
1. **Calibrate the grid, not the line.**
   - A PIT recalibration of the continuous 199-point grid: one monotone map from production's taus to calibrated taus, per stat and
     role.
   - It is fit on every OOF row (about 27,600 per stat-season, against about 6,600-13,000 lines).
   - It is scored at the line and on integer-support CRPS.
   - It uses far more data, and it fixes the shape that T477 and T474 both point at (the left half of starters' distributions).
2. **Per-slice shrinkage toward 0.5.**
   - The shuffled-fit finding says shrinkage alone beats raw by about 0.008.
   - A pre-registered shrink weight per stat × role (8 numbers, fit walk-forward) is the minimal honest product. It would be tested on
     whether it beats one global base rate.
3. **A market-free, selection-aware model of where lines sit.**
   - Books post lines where they expect about 50% (T477: market mean 49%).
   - The position of the line relative to the model's continuous median (z, here only a covariate) is evidence about the model's own
     error.
   - A model of z given the model's own pre-tip features, with prices never used, could separate "the line knows something" from "the
     model is biased".
   - This one is closest to market information. It needs a careful leak audit (lines are set after news) and is the most expensive.

## What each choice protects against
| choice | protects against |
|---|---|
| fit strictly before the block's first day; weights only for ages > 0 (asserted) | a later line or outcome shaping an earlier fit (gate check 4, unit tests) |
| no price in the design (`design()` takes no price argument; test) | "calibration" that is secretly the market (that is MARKET_SHRINK, a different question) |
| continuous median and robust spread (the grid is recovered, not re-fit) | a distance term glued to the line by integer rounding; any change in the forecasts being calibrated |
| P(over) kept as served, verified equal to the continuous value | calibrating a number production does not serve |
| one fixed half-life of 365 days | stale seasons pooled equally (BEST_PRACTICES 4), and tuning the decay on results |
| C0 and SH as required comparators; SHRINKAGE_ONLY named | the CALIBRATED_GRID failure (shrinkage passing as calibration), and also throwing away a useful shrinkage |
| null sd measured pre-freeze, K = 50 | a guard that fires by chance (RECENT_ROLE_FULL, T473) |
| P4 calibration in the large | a log-loss gain from sharpening while the bias stays |
| P5-P7 do-no-harm and DraftKings direction | a pooled gain hiding a stat, a role or a book that the map makes worse |
| floor 0.003 = value of the known bias | shipping a map that recovers less than we already know is broken |
| 2025 once, after Stage 1, with Devin's words | a partly exposed select stage being taken as confirmation |
| Pinnacle primary chosen now | picking the book after seeing which one passes |

## Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile and distribution layers, recalibration at the line)
1. **Information or capacity?**
   - Neither in the usual sense. It adds no new pre-tip information, only a correction of how production's information is expressed
     at the line.
   - The playbook names this class's typical failure ("our P(over) 4 pp low at the line while the overall CRPS looks good"), and that
     is the target.
   - The base-rate and shuffle comparators exist so that a correction with no information is labelled as such.
2. **Enough training data for the class?**
   - It is a 5-parameter map. The smallest Stage 1 fit has 6,339 rows (effective 6,260).
   - Per-slice calibration (bench × stat) is not attempted, because bench reb has too few lines per block. Role enters as two
     coefficients instead.
3. **Valid test?**
   - Real tip gating: 100% of T-60 snapshots are at least 60 minutes before the real tip.
   - Played games only (DNP legs voided).
   - Inputs are leak-free: as-of production OOF and stored lines.
   - Support: P(over) at x.5 lines is exact on both grids.
   - Power: projected half-width 0.00106 against the 0.003 floor.
   - Guards sized from a permutation null. The shuffle is not inert: it moves 54 of 54 blocks in the positive control.
   - The comparator is what production serves.
   - Data versions are pinned by sha256 (above).
4. **Lineage:** see the section above.
5. **What it scores against.** Log loss at the market line, which is the question Devin asked. CRPS is not claimed. The reliability
   table is at the line (class check), and slices (stat, role, confidence, month) were fixed in advance.

## Minimum-data gate (measured Mon Oct 12 16:27 CDT 2026, revision 2 code)
- **Command:** `research/eval/line_calibration_eval.py gate --allow-draft`.
- **Output:** reports/prereg_line_calibration/gate_draft.json.
- **Run:** 16 s on CPU, peak RSS 554 MB, read-only, seasons 2023-24 only. No CAL fit used a real label.
- **Result:** all 15 checks pass.

| check | measured | bar | pass |
|---|---|---|---|
| 1 Pinnacle 2024 legs (games) | 37,528 (1,260) | >= 20,000 | yes |
| 1 Pinnacle 2024 legs per stat | pts 13,021, reb 6,645, ast 6,771, fg3m 11,091 | >= 5,000 | yes |
| 1 2024 blocks with < 2,000 fit rows | 0 of 36 Pinnacle, 0 of 36 DK (smallest fit 6,339 / 9,650; effective 6,260 / 9,534) | 0 | yes |
| 2 inputs finite (p_model, continuous median and spread, line); starter10 finite | all; 0 missing | all | yes |
| 2g blocks with contradictory survival brackets | 0 of 72 | 0 | yes |
| 2g widest bracket of a needed grid point | 0.049 z units (June 2024 playoffs; regular season < 0.003) | <= 0.1 | yes |
| 2g continuous-grid P(over line) vs served p_model, legs differing | 0 of 179,673 | 0 | yes |
| 2 T-60 snapshot >= 60 min before the real tip | 100% | 100% | yes |
| 2 lines x.5 | all | all | yes |
| 3 A0 equals ODDS_HISTORY h1 (n and log loss) | 16 of 16 cells, \|diff\| < 1e-9 | 16 | yes |
| 4 planted future: labels on/after block start flipped | 54 of 54 probes identical (CAL, C0, SH; 2 books x 9 blocks) | all | yes |
| 4 positive control: in-window labels flipped | 54 of 54 moved | all | yes |
| 5 projected 95% half-width of P1 (synthetic truth, real rows and games) | 0.00106 (synthetic effect -0.0051 [-0.0061, -0.0040]) | <= 0.0015 | yes |
| 6 permutation null, K = 50, Pinnacle 2024 | sd 0.00018; SH - A0 mean -0.0078 | measured | yes |

- **Power and design effect.** The design effect of a fixed-arm contrast on real labels (coin - A0, Pinnacle 2024) is 1.51, at 29.8 legs
  per game.
  - Per-stat half-widths: pts 0.0018, reb 0.0026, ast 0.0025, fg3m 0.0019.
  - So a per-stat effect at the floor is detectable for pts and fg3m only.
- **Stage 2 data, checked inside the touch before any arm is scored (no outcome read):**
  - at least 20,000 Pinnacle 2025 legs;
  - 0 identity blocks;
  - T-60 lead of at least 60 minutes;
  - x.5 lines;
  - the 2025 continuous grid passes the three 2g checks.

  If any of these fails, Stage 2 stops unscored and the touch is reported as spent.

## Data-capture audit (per BEST_PRACTICES, 2026-10-12)
- **Used:**
  - stored props lines 2023 and 2024 (odds_history; Pinnacle, DraftKings);
  - the stored production OOF rows of the same seasons (c, s and the integer survival vector).
- **Exist but unused:**
  - FanDuel lines: not in the declared family, and pooling books double-counts legs.
  - Consensus rows: a median of books, not something anyone can act on.
  - T-5 rows: the production use is T-60.
  - OOF rows without a line: they have no line to calibrate, but they are the data for follow-up candidate 1.
- **Do not exist here:**
  - Props lines before 2023-24: 0 rows with season < 2023.
  - As far as I know, that vendor's historical props archive starts in 2023 and is paid. **Not verified; no free source was searched.**
- **Could be repaired:** nothing that changes power. Power is set by the scored season, which passes.
- **Era flags:** none. Both seasons are full regular seasons plus playoffs. June blocks are playoffs-only, with the widest grid brackets
  and the fewest legs.

## Production integration, if a stage-2-confirmed proposal exists (a production change and Devin's decision; this draft changes nothing in nba/daily or ops)
- **Store, don't replace.**
  - The daily pipeline would write `p_over_cal` beside the raw `p_over` for every (player, stat, line) it prices, using the served object
    (CAL, or C0 if SHRINKAGE_ONLY).
  - The budget tool and the briefing keep reading the raw value until Devin promotes the calibrated one, after a shadow period on the
    live log.
  - Under SHRINKAGE_ONLY, the briefing states that line probabilities carry no model information beyond the base rate.
- **The map is an artifact.**
  - Coefficients per (book, stat) are refit on the first of each month, with the same 365-day half-life, from every stored T-60 leg with
    an outcome: 2023-25 pairs plus the live log.
  - They are written to a versioned file with its sha recorded.
  - They are applied at T-60 and T-30 with the line known at that time.
  - If there is no stored line, there is no calibrated value; it is never imputed.
  - A Kalshi "N+" ladder maps to the line N - 0.5.
  - The distance term needs production's continuous grid at prediction time. The live pipeline already has it before rounding, so it
    should store q25/q50/q75 rather than rely on recovery.
- **Replay oracle.**
  - Replaying a season with the stored map files must reproduce the stored `p_over_cal` byte for byte.
  - Any change to the production grid (for example a P(play) mixture, as in Devin's 0.6977 rows) invalidates the map until it is refit
    on legs scored by the new grid.
- **Monitoring.**
  - The briefing shows the trailing-30-day calibration in the large, raw against calibrated, with n.
  - A sign flip in the intercept is an alert, not a silent refit.

## Stage 2 prerequisites (the 2025 touch)
- **What does not exist yet.** The 2025 pairs frame (production OOF P(over) at 2025 Pinnacle/DraftKings T-60 lines, with outcomes) and
  the 2025 OOF grid rows (`model_rows_2025`).
  - `research/eval/model_clv_pairs.py` asserts season <= 2024, and ODDS_HISTORY's 2025 path is guard-only.
  - Building them reads 2025 outcomes and prices, so it is part of the touch.
  - Their exporter is reviewed and committed before the holdout row is logged. The row names the exporter commit.
  - Both sha256 values are printed in the report.
- **Holdout row template:**
  `| <date> | research.eval.line_calibration_eval report --sha <64 hex> --pairs-2025 <path> --grid-2025 <path> (rule LINE_CALIBRATION, sha8 <8>, freeze commit <h>, exporter commit <h>) | props line calibration map (CAL / served object vs raw production) | pts, reb, ast, fg3m | PRE-REGISTERED; CLEAN first touch of LINE_CALIBRATION | DEVIN CONFIRMED "<his words>" |`

## Commands (maintainer)
```
M="-m research.eval.line_calibration_eval"
.venv/bin/python -W ignore $M gate --allow-draft                 # re-measure the gate (16 s)
.venv/bin/python -W ignore $M rehearse                           # synthetic labels, 2024 standing in (~25 s)
.venv/bin/python -W ignore $M select --sha <64 hex>              # Stage 1, after `prereg(LINE_CALIBRATION): <sha>` is committed
.venv/bin/python -W ignore $M report --sha <64 hex> --pairs-2025 <parquet> --grid-2025 <parquet>   # Stage 2
```
- The job is light (numpy; peak RSS 554 MB measured on the gate).
- It never takes or removes `data/ops/heavy.lock`. It only records whether the lock is held.
- Outputs:
  - reports/prereg_line_calibration/{gate_draft,gate,select,report_2025}.json
  - reports/prereg_line_calibration/rehearsal/results.json

## Unstated details fixed before any scored number
- **Leg:** a T-60 row of pinnacle or draftkings in the MODEL_CLV pairs frame.
- **Block:** the calendar month of game_date. If a block has fewer than 2,000 fit rows, every arm uses the identity.
- **Grid recovery:**
  - each `zq_j` is the midpoint of its bracket; an open end takes the finite bound;
  - `q_j = max.acc(max(c + s zq_j, 0))`;
  - quantiles are linearly interpolated at the positions above.
- **Clipping:** every model-derived probability is clipped to [0.5/199, 1 - 0.5/199] before log loss and Brier. The market is clipped
  to 1e-9.
- **Bootstrap:**
  - One multinomial game-resample matrix (seed 0, B = 2,000) per (stage, book) is shared by every statistic.
  - The one-sided p for "CAL beats A0" is (1 + #{boot of A0 - CAL <= 0}) / 2001.
  - BH runs over the four per-stat p values.
- **Pooling:** pass contrasts weight legs, not stats.
- **Starter:** starter10 >= 0.5.
- **Confidence buckets** on |p_model - 0.5|: [0, .05), [.05, .10), [.10, .20) and [.20, .5].
- **Reliability:** ten equal-width bins.
- **SHRINKAGE_ONLY served object:** the lower pooled Pinnacle Stage 1 log loss between CAL and C0. Ties go to C0, the simpler object.
- **Synthetic truth** for gate check 5 and the rehearsal: logit p = 0.17 + 0.85 logit p_model (about T477's -4 pp gap). It is never a
  real label.

=== RESULTS BELOW ===
