# CALIBRATED_GRID: a trailing-window calibration layer on the props integer grid (pre-registration)

Status: DRAFT 2026-10-11 ~17:50 CT, NOT FROZEN; gate to be measured before any freeze (Devin pre-approved "try everything",
2026-10-11 16:50; DECISIONS ~17:40). Freeze evidence: commit hash plus
`awk '/^=== RESULTS BELOW ===$/{exit} {print}' docs/prereg/CALIBRATED_GRID.md | shasum -a 256`. Seasons as the other rules.

## Why
The production grid is over-confident about lines: where it claims 70%+ for a side it is right 61%; 75% claimed → 61% right
across all confidence bins (FAN_KNOWLEDGE 2026-10-11, 107k DK main-line legs); the market is better calibrated in every cell
(ODDS_HISTORY Q10). Devin: "can it confidently say no?" Not until its probabilities mean what they say. Calibration layers
(temperature scaling, isotonic on a rolling window) are the standard post-hoc fix in forecasting (reading log 2026-10-11): they
change probabilities, not rankings, so the mean forecast and CRPS can only move through the tails.

## Hypothesis
H1: a calibration map fit on the trailing 60 days of OOF rows (walk-forward, never on the scored block) applied to the grid's
exceedance probabilities P(Y ≥ k) lowers the integer-support CRPS and the log loss of P(over L) at the book's main lines on
2024-25, selecting the map family on 2023-24. Null: no change. Prior: ~50% for log loss at lines, ~25% for CRPS.

## Data exposure (honest)
Seen: the over-confidence tables; ODDS_HISTORY market log losses; MINUTES_V2's calibration windows. Not seen: any calibrated-grid
number. Season 2025 never loaded; `season <= 2024` asserted. Select 2023, report 2024.

## Fixed design (constants final)
Rows: production OOF rows (the F11 harness) and, for the line test, the ODDS_HISTORY DK main-line pairs. Map families (fixed):
C1 temperature on the logit of each P(Y ≥ k) with one scalar per stat; C2 Platt (two parameters) per stat; C3 isotonic per stat
on P(over L) at lines only (reported at lines, not on CRPS). Fit window: the trailing 60 calendar days of OOF rows strictly
before the block (minimum 3,000 rows, else identity). Applied to the whole grid (monotone-preserving; renormalised so exceedance
is non-increasing in k).
Arms: A0 production grid; A1 = C1; A2 = C2; A3 (lines only) = C3; A4 control = C1 fit on a SHUFFLED trailing window (labels
permuted) to show the map is learning calibration, not noise.

## Metrics, support, inference
Integer-support CRPS per stat per season (A1, A2 vs A0), game-clustered bootstrap (2000, seed 0), BH over 4 stats per season;
log loss of P(over L) at DK main lines vs A0 and vs the book (A1, A2, A3), same bootstrap; reliability in ten bins; the
"confident bins" table (claimed 70%+ → realised) before and after. MDE printed.

## Minimum data checks (fail = stop)
1. Trailing window has ≥ 3,000 rows for ≥ 95% of blocks after the warm-up.
2. A0 reproduces production to 1e-6; A0's line log loss reproduces ODDS_HISTORY's model log loss to 1e-6 on the same pairs.
3. Planted-future: a label after the block start never enters the map (CI test).
4. Monotonicity of the calibrated grid holds on every row (assert).

## Pass rule (per stat; 2023 selects, 2024 confirms)
Lines: log loss delta vs A0 ≤ −0.002 with CI upper < 0 and BH p < 0.05, AND the control A4 shows no gain (≥ −0.0005).
CRPS: delta ≤ −0.005 with CI upper < 0 (reported; a lines-only pass is still a pass for the line use). A pass → the calibrated
exceedance feeds the budget tool's line probabilities as a shadow column; the stored grid is unchanged; promotion is Devin's.

## Kill criteria
No family passes on 2023: close; no window tuning, no per-tier maps, one follow-up at most.

## Outputs (exact)
`reports/prereg_calibrated_grid/results.json`, `reports/prereg_calibrated_grid.md`; code under `research/` only
(`research/eval/calibrated_grid_eval.py`).

=== RESULTS BELOW ===

(not run)
