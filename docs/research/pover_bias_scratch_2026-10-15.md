# SCRATCH: is the production model's P(over) 3-4 pp low at book lines? (2026-10-15)

SCRATCH. Exploration, not a claim: no rule, no ledger PASS/FAIL. Seasons 2023 and 2024 only; season 2025 was never loaded
(hard assert in the script). Script: `research/scratch/pover_bias_scratch.py`; full cell table:
`reports/pover_bias_scratch_2026-10-15.json`. Inputs: `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet` joined to
`reports/odds_history/model_rows_{2023,2024}.parquet` (production props OOF rows, integer survival vector `p_ge`). Books:
DraftKings and Pinnacle; DNP legs already excluded upstream. CIs: 95% percentile, games resampled (2,000 draws, the
MODEL_CLV `Boot`). Gap = mean(model P(over) minus realised over), percentage points; negative = model too low.

## Short answer

1. **Same finding as T477, not a new one.** T477 already reported Pinnacle -4.3 pp [-4.7, -3.8] (bench -5.9, starters -3.8),
   same sign in every stat, season and role. I reproduce it and add DraftKings (-3.25 [-3.61, -2.87], n 106,999 legs / 2,616
   games). The "3-4 pp" in the parlay agent's report is DraftKings/Pinnacle T-60 T477 restated. The mechanism below is what is new.
2. **It is real on the integer support.** Every line is x.5 (0 pushes, 0 non-half lines in 381,133 legs). Recomputing P(over) as the
   production integer survival at floor(line) reproduces the stored `p_model` exactly (max abs diff 0.0), and the label is
   y > floor(line). There is no ceil/floor or grid artifact in the gap.
3. **It is mostly overconfidence (shape), not a mean shift.** A one-split recalibration, logit(p_over) = a + b logit(p_model), fit on 2023
   DraftKings T-60 legs and scored on 2024 (n 53,752 legs): a = +0.012, slope b = 0.46 (pts 0.16, reb 0.45, ast 0.41, fg3m 0.69). The
   intercept is about zero; the slope is far below 1. The aggregate gap goes from about -3.3 to -0.3 pp, log loss improves
   -0.0089 [-0.0102, -0.0076] against raw, and is still 0.0026 [0.0015, 0.0036] worse than the de-vigged DraftKings price.
   (Descriptive, one split, not the LC2 rule.)
4. **It is not a known-at-time or minutes story.** T-60 vs T-5 snapshot gaps are the same (DK -3.25 vs -3.40; Pinnacle -4.27 vs
   -4.60). The DNP-conditioning idea (P(over) not conditioned on playing, legs voided if DNP) is ruled out: model P(X=0) equals the
   realised zero rate among played legs (pts 0.0174 vs 0.0206, reb 0.0288 vs 0.0290), so there is no excess zero mass to remove.
5. The gap is much worse at high lines and short pre-game minutes, and the reliability curve is a clean tilt. That is the
   signature of a miscalibrated spread, which LC2 (LINE_CALIBRATION) was built to fix. LC2 is recorded in NEXT_SESSION as closed
   (DATA, FLAT); I could not find its ledger row (it is not in TEST_LEDGER.md at this checkout), so I do not claim what it measured.

## Numbers (DraftKings T-60 unless stated; n = legs, games clustered)

| cut | n | model gap pp | market (mult de-vig) gap pp |
|---|---|---|---|
| all | 106,999 | -3.25 [-3.61, -2.87] | +1.60 [+1.25, +1.96] |
| pts / reb / ast / fg3m | 30,236 / 29,800 / 20,224 / 26,739 | -3.28 [-3.84, -2.71] / -2.46 [-3.03, -1.86] / -3.21 [-3.88, -2.53] / -4.13 [-4.73, -3.50] | +1.4 / +2.3 / +1.0 / +1.5 |
| starter / bench | 79,047 / 27,952 | -3.06 [-3.50, -2.60] / -3.80 [-4.51, -3.10] | +1.5 / +1.8 |
| pre-game min10 <15 / 15-24 / 24-30 / 30+ | 1,558 / 19,292 / 32,830 / 53,319 | -8.44 [-11.5, -5.25] / -3.52 / -2.81 / -3.28 | |
| DK T-5 (closing proxy), all | 121,931 | -3.40 [-3.75, -3.02] | |
| Pinnacle T-60, all | 72,674 | -4.27 [-4.72, -3.80] | +1.50 |
| Pinnacle, min10 <15 | 789 | -13.8 [-18.0, -9.3] | |

By line level (tertiles of the line within stat; cuts in the JSON), DraftKings T-60, model gap pp:

| stat | low | mid | high |
|---|---|---|---|
| pts | -1.5 [-2.4, -0.7] (12,746) | -3.5 [-4.6, -2.4] (7,510) | -5.4 [-6.4, -4.3] (9,980) |
| reb | -1.0 [-1.8, -0.2] (17,101) | -2.7 [-3.8, -1.4] (6,362) | -6.2 [-7.5, -4.9] (6,337) |
| ast | -1.6 [-2.6, -0.7] (10,544) | -3.7 [-4.9, -2.3] (6,189) | -7.2 [-8.9, -5.5] (3,491) |
| fg3m | -2.8 [-3.5, -2.1] (17,913) | -5.7 [-6.9, -4.5] (6,585) | -10.1 [-12.1, -8.0] (2,241) |

Reliability of `p_model` (DK T-60): p in [0.2,0.3) gap -12.9; [0.3,0.4) -7.9 (n 27,392); [0.4,0.5) -2.8 (n 40,511);
[0.5,0.6) +1.5; [0.6,0.8) +6.8 (n 7,148). The gap is negative for p<0.5 and positive for p>0.5: a tilt, and the aggregate is
negative because 70% of legs sit below 0.5 (the model picks unders on 75% of legs, as T477 found).

Mean shift vs shape: the production mean is nearly right (mean_int minus y: pts -0.15 [-0.22, -0.07], reb -0.07, ast -0.00,
fg3m -0.03; n 20k-30k per stat). Adding one constant shift per stat (+0.54 pts, +0.15 reb, +0.16 ast, +0.12 fg3m units) to zero the
aggregate gap leaves residuals of about +2 pp at low lines and -2.7 (pts), -4.6 (reb), -5.2 (ast), -7.7 (fg3m) pp at high lines, with
starters and bench both about 0. So a location fix does not work; the line-level and probability-level pattern is a spread/tilt.

## Caveats (what I did not do or cannot see)

- Legs are DK/Pinnacle rows for the same player-game-stat across both snapshots; cells at different snapshots or books are not
  independent. The game-clustered CI handles within-game dependence only; all CIs are per cell, uncorrected (about 60 cells shown,
  nothing here is a claim).
- "Minutes bucket" uses pre-game min10 (a recency average), not a projected-minutes forecast; actual-minutes buckets are
  outcome-conditioned (they select on the label, gaps of +34 to -13 pp) and are excluded from any reading.
- The line tertiles are not independent of model scale (the model's own mean sets what "high" means), so part of the high-line gap is
  the same shrinkage seen from another angle, not a separate effect.
- The recalibration split (fit 2023, score 2024) is one split, one specification; it is an existence demonstration, not a tested
  fix, and 2024 was looked at.
- Tracked fg3m gaps at high lines have small n (2,241); the 2023 and 2024 seasons were pooled for the gap tables (per-season sign
  checks were not rerun here; T477's JSON reports them with the same sign).

## Playbook check (docs/MODEL_PLAYBOOK.md; class: quantile/distribution layers, recalibration at the line)

1. Information or capacity: a recalibration adds no pre-tip information. It re-scales the spread of an existing forecast. It cannot beat the
   market's information; it can only stop the forecast from being overconfident. Our record says calibration of this kind is worth
   little, though here it is log-loss -0.009 on a one-split read.
2. Enough data: two parameters per (book, stat) on 25k-50k legs per season is ample; the risk is not size but non-stationarity and
   the fact that 2024 is the only out-of-time test season (2025 is frozen).
3. Test validity: the support is the integer grid, all lines x.5, tip-gated T-60 and T-5 snapshots; DNP legs are voided. Not covered: the label
   uses only played games, so any rule must score the same population the forward log scores (voided DNP legs).
4. Lineage: this is LC2 / LINE_CALIBRATION (logit p_model + line distance + bench) and the T477 / T480 line reading (calibration slope
   0.15-0.19 for pts). Inherited guards: walk-forward month blocks, no price in the fit, shrinkage-only verdict.
5. Scored against: log loss at the line vs the de-vigged price. Even after recalibration it trails the price (+0.0026), so this implies "model
   stops lying about its confidence", not "model beats the book".

Implication for model class checks: reliability at the line (not CRPS) is the object; slices fixed in advance (stat, role, line
tertile, pre-game minutes); report the slope, not just the gap.

## What a rule would look like (DRAFT, not frozen; not in docs/prereg)

- Hypothesis: a market-free two-parameter logistic recalibration of production P(over) per (stat, role) reduces DraftKings
  T-60 log loss by at least 0.005 against raw `p_model`, and the residual mean gap per stat is within +-1.0 pp.
- Fit: walk-forward by month on seasons 2023-24 only; time-weighted; no price enters a fit (this is LC2's design; if LC2 is already
  the frozen rule, this is a duplicate and should not be run, only its forward live version).
- Primary metric: paired log loss difference (calibrated minus raw), game-clustered bootstrap; practical floor -0.005; family =
  4 stats x {DK, Pinnacle}, BH.
- Slices: stat, starter/bench, line tertile, pre-game minutes <15 vs rest. Kill: slope not distinguishable from 1, or the gain is carried
  by one stat or one month block.
- No 2025 touch from this question; forward log only (2026-27). A pass is a recalibration, never a bet: it still trails the price.
- Minimum-data gate to check before freezing: legs per (stat, role, month block) in 2023-24; fg3m high-line and min10<15 cells are
  thin (2,241 and 1,558 legs). This has not been run.

## Prior

P(a frozen test finds a fixable miscalibration >= 0.02): I read "fixable" as a recalibrated residual gap within 1 pp with
a paired log-loss gain of at least 0.005 against raw on 2024 legs. My prior is 0.85, because this descriptive split already shows -0.0089
(a frozen run on cleaner rules should reproduce most of it). P(that recalibrated P beats the de-vigged DraftKings price): 0.07.
