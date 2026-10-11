# CALIBRATED_GRID: a trailing-window calibration layer on the props integer grid (pre-registration)

Status: DRAFT 2026-10-11 ~17:50 CT, NOT FROZEN; gate measured 2026-10-11 (checks 1-4 pass; see the section; C3 pairs question open) (Devin pre-approved "try everything",
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

## Minimum data checks (fail = stop, no model result). Measured 2026-10-11 by `research/eval/calibrated_grid_eval.py gate --allow-draft` (reports/prereg_calibrated_grid/gate.json; draft sha256 76c46967 before this edit; 18 s, CPU, read-only, seasons 2023-24 only, no calibration map fit on any real outcome):
Inputs used: the stored production OOF rows `reports/odds_history/model_rows_<season>.parquet` (ODDS_HISTORY stage 1, written 2026-10-10 10:16, `p_ge[i] = P(Y >= i+1)`, 199-sample integer grid) and `market_rows_<season>.parquet` DK main lines. These reproduce production exactly (below), so no harness re-run was needed. Blocks are the calendar months production refits on (Oct-Jun incl. playoffs, 9 per season); "warm-up" = the leading blocks whose trailing window holds < 3,000 rows of the stat (identity by the rule).
1. Trailing window >= 3,000 rows: PASS. Per season and stat (identical across stats: 27,619 rows 2023 / 27,583 2024 per stat): 9 blocks, 2 warm-up (Oct: no prior rows; Nov: window 1,062 rows (8 days) in 2023, 1,407 (10 days) in 2024), then 7 of 7 post-warm-up blocks have >= 3,000 rows (100% vs the 95% bar); smallest post-warm-up window 4,006 (2023) / 3,809 (2024) rows (June, playoffs-only tail); December windows cover 38-40 of 60 days. The warm-up blocks hold 20.2% (2023) / 21.9% (2024) of the scored rows, so about 80% of rows get a fitted map and 20% are identity by construction. Every window's latest game_date is before its block start (asserted per block). NOTE on C3 (not a check as drafted): the rule says "3,000 rows" without saying rows or pairs; I count DK main-line PAIRS for C3. At t60 the post-warm-up blocks with >= 3,000 pairs are, 2023: pts 6/7, reb 6/7, ast 0/7 (2,967 / 2,991 / 2,999 / 2,957 in Jan-Apr), fg3m 6/7; 2024: pts 6/7, reb 5/7, ast 4/7, fg3m 5/7. So C3 on assists at t60 is identity for all of 2023 and cannot pass the select season. Devin to decide before the freeze whether C3 pools stats or keeps the per-stat 3,000.
2. A0 reproduces production: PASS. Integer-support CRPS by the production route (quantile grid rebuilt from `p_ge`, 2 x mean pinball) equals the stored A0 value and LOWER_TAIL's stored "off" arm with |diff| = 0.0 for all 8 (stat, season) cells, n identical (2023: 27,619 per stat; 2024: 27,583), e.g. pts 3.152830 / 3.188411, reb 1.292817 / 1.313995, ast 0.905903 / 0.901023, fg3m 0.569049 / 0.592278. The exact survival-vector CRPS used for every arm equals the production route to 0.0 (the midpoint taus are exact on the 199-atom grid). Line log loss of P(over L), A0 vs ODDS_HISTORY `results_<season>.json` h1 `draftkings` cells: all 16 cells (4 stats x t60/t5 x 2 seasons) match with |diff| <= 2.2e-16 and identical n (t60 2023: 14,354 / 15,052 / 9,650 / 14,191; t60 2024: 15,882 / 14,748 / 10,574 / 12,548 for pts/reb/ast/fg3m); the book's log loss matches too. The pair set did not change with the 2026-10-11 clean-up (pairs depend on the stored OOF rows and stored prices, not on the `games` table): n equals, the registry-keys variant is identical, and ODDS_HISTORY's own `pairs_for_season` (read-only DB) gives the same n and log loss in all 16 cells. Caveat: the stored OOF rows predate the clean-up; production today may differ slightly on 2024 rows from 2024-11-04 on (PPLAY_CONTEXT measured 0.0002 CRPS); not re-run here (heavy).
3. Planted-future: PASS. For the first and last mapped block of each (season, stat) (16 probes) the arrays hold the whole season; replacing every label dated on/after the block start (22,035 / 21,552 rows at the first probe, 114 / 159 at the last) with garbage leaves C1, C2 and the A4 control byte-identical, and the positive control (garbage inside the window) moves the maps in all 16. Labels in this test are drawn from each row's own grid, so no real outcome entered any fit. CI test: `tests/research/eval/test_calibrated_grid_eval.py` (also a label dated exactly on the block start stays out).
4. Monotonicity: PASS. 31 maps (6 temperatures 0.25-4, 25 Platt corners and interior values within the allowed bounds) applied to every stored row of all 8 (stat, season) cells: max increase in k = 0.0, no value outside [0, 1], no rank inversion within a row, exact 0 and 1 unchanged. The run asserts it again on every fitted block.
Unstated details fixed before any scored number (listed in the report): fit objective = log loss over every interior (row, k) pair of the window (target 1[y >= k]); temperature in [0.25, 4], Platt slope in [0.25, 4] and intercept in [-2, 2]; probabilities clipped to [0.5/199, 1-0.5/199] before the logit and exact 0/1 left alone; C3 uses scikit-learn isotonic on the window's DK pairs (A0 line probability vs over outcome), outputs clipped to the same eps; A4 permutes the window labels with a seed set by the block date; arms scored per row on the same rows; selection rule = lowest line delta among the arms that pass on 2023, the same arm must pass on 2024; BH per (season, metric, arm) over the 4 stats; the primary line cell is DK at t60 (t5 reported).

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

Freeze: 2026-10-11 15:12 CT, commit 5ad045f, sha256 of everything above the line: b889db00214ce5e8fe0e69e19ad0f0299ec3e752e7f2b5b5ddba63dcffce155e (first 8: b889db00). Verify with the awk command in the header.
