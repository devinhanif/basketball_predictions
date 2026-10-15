# MEDIAN_BIAS: where does production's low median come from? (descriptive diagnosis, 2026-10-15)

DESCRIPTIVE. No rule, no claim, no ledger PASS/FAIL. Seasons 2022-2024 only; season 2025 was never loaded (the DuckDB queries carry `season <= 2024`;
the script asserts it). Script: `research/scratch/median_bias_diag.py` (exit 0, about 6 s); full tables: `reports/median_bias_diag_2026-10-15.json`.
The rule that follows from it is the DRAFT `docs/prereg/MEDIAN_BIAS.md` (not frozen). All labels of 2023 and 2024 were read for the production
arm A0 only; **no candidate arm was scored on a real label**, so the draft's candidate is untested here.

Inputs, read by absolute path from the main checkout, read-only: `reports/lower_tail/components_{pts,reb,ast,fg3m}.npz` (A0 centre `c`, scale `s`
and the walk-forward calibration arrays per month block; 18 blocks over 2023-24; sha256 pinned in the draft), `data/ops/model_clv_pairs/pairs_{2023,2024}.parquet`
(population label only: does the player-game have a DraftKings T-60 main line), `nba.duckdb` (played rows for recency windows and 2022).
Rows: played player-games with at least 5 prior played games, n = 55,320 per stat (27,619 in 2023, 27,701 in 2024), 221,280 stat-rows, 2,638 games.
CIs: game-clustered percentile bootstrap, B = 1,000, seed 2026, per cell, uncorrected (about 90 cells shown, none is a claim).

## Definitions

* A0 integer distribution: the production grid `c + s * quantile(z_cal)` on 199 equal-mass points, mapped by `ceil(q - 0.5)`; `S(k) = P(Y >= k)`.
* **MIDMED gap** (percentage points): with the arm's integer median `m` (the 100th of the 199 sorted points), `R = 1{Y > m} + 0.5 * 1{Y = m}`
  and `E = 0.5 * (S(m) + S(m+1))`, gap = mean(R - E). Zero in expectation for a calibrated forecast whatever the integer rounding (a unit test
  checks this on a Poisson forecast). Positive = outcomes fall above the forecast median = "the median sits low".
* T494 statistic: P(Y > A0 continuous median), median = the 100th point of the continuous grid. For integer Y this is **not 0.5 for a calibrated
  forecast**; the right comparison is the model's own implied value (`impl`), which I report next to it.

## Answer in one paragraph

The low median is **not a shift and not a bug in the centre.** On the whole population A0 is calibrated at its median (all 221,280 stat-rows:
MIDMED -0.11 pp [-0.35, +0.14]; P(Y > median) 0.480 vs implied 0.483). The 0.536 appears only on the players who have a DraftKings line
(n 106,999: MIDMED +3.11 pp [+2.79, +3.46]; P(Y > median) 0.536 vs implied 0.502; this reproduces T494 exactly), and it is offset by the opposite sign
on the players without one (n 114,281: -3.12 pp [-3.50, -2.73]; 0.428 vs 0.465). So A0's forecast median is **biased by sub-population, with a
smooth gradient in projected minutes and level**. Two components, one fixable with no new information, one not:

1. **Shape: one pooled residual distribution for everyone (fixable inside the model).** A0 builds every quantile from one pooled empirical
   distribution of standardized residuals `z = (resid - mu) / s`, taken from the newest 15% of training rows. That distribution is far more
   right-skewed for short-minutes rows than for rotation rows (calibration-window skew of z, pts: 1.61 below 15 projected minutes, 0.84 for 15-25,
   0.42 for 25+; median z -0.36 / -0.23 / -0.11 against the pooled -0.21; same order in reb, ast, fg3m). One median offset serves all, so it is too
   low for high-minutes rows and too high for bench rows. The centre is fine: mean z of observed outcomes among DK-line rows is -0.01 (pts), +0.01 (reb),
   -0.04 (ast), -0.02 (fg3m); the mean residual `y - mean_int` is +0.07 there.
2. **Selection by information the model lacks (not fixable here).** Inside the same projected-minutes cell the DK-line rows still sit 4-6 pp above
   the no-line rows (30+ minutes: +3.15 vs -1.46; 15-24: +3.50 vs -2.62). A book posts a prop when it expects the player to play a normal role;
   A0 only has a recency average of minutes and the injury report. That is minutes information, the one thing the book's line carries and A0 does not
   (the T494 attack's conclusion, seen from the model side). A cell map built from the calibration window moves the pooled median shift only
   60-80% of the way (z-median needed vs cell-wise calibration median, below).

## Tables (A0 MIDMED gap, pp; n = stat-rows)

| cut | level | n | gap [95% CI] | mean(y - mean_int) |
|---|---|---|---|---|
| population | all | 221,280 | -0.11 [-0.35, +0.14] | -0.060 |
| | has DK T-60 line | 106,999 | **+3.11** [+2.79, +3.46] | +0.070 |
| | no DK line | 114,281 | -3.12 [-3.50, -2.73] | -0.182 |
| projected minutes (min10) | < 15 | 51,052 | -3.96 [-4.60, -3.37] | -0.144 |
| | 15-24 | 63,824 | -0.77 [-1.25, -0.32] | -0.080 |
| | 24-30 | 47,660 | +1.41 [+0.94, +1.92] | -0.010 |
| | 30+ | 58,744 | **+2.73** [+2.27, +3.20] | -0.007 |
| role | starter10 >= .5 | 97,516 | +1.86 [+1.48, +2.25] | -0.030 |
| | bench | 123,764 | -1.66 [-2.03, -1.29] | -0.084 |
| level within stat (m_recency tertile) | low / mid / high | 73,760 each | -3.19 / -0.14 / **+3.01** | -0.120 / -0.061 / +0.001 |
| prior played games | 5-19 / 20-49 / 50+ | 11,820 / 22,332 / 187,128 | -4.07 / -3.34 / +0.53 | -0.181 / -0.162 / -0.041 |
| form (h5 vs h40, scaled by s) | cold / flat / hot | 73,760 each | -1.14 / -0.25 / +1.07 | -0.116 / -0.059 / -0.006 |
| trend (h10 vs h100) | below / flat / above long-run | 73,760 each | -0.60 / -0.41 / +0.68 | -0.081 / -0.065 / -0.035 |
| season | 2023 / 2024 | 110,476 / 110,804 | -0.09 [-0.42, +0.24] / -0.13 [-0.49, +0.22] | -0.064 / -0.056 |
| month | 18 months (2023-10 to 2024-06, 2024-10 to 2025-06) | 456 - 19,940 | no pattern; 15 CIs include 0, 3 do not (2024-05 +2.07, 2025-01 -0.84, 2025-04 -1.15) | |

Population x minutes cell (A0 MIDMED, pp): DK line: <15 +8.02 (n 1,558), 15-24 +3.50 (19,350), 24-30 +2.58 (32,781), 30+ +3.15 (53,310); no line:
<15 -4.34 (49,494), 15-24 -2.62 (44,474), 24-30 -1.16 (14,879), 30+ -1.46 (5,434).

Same sign in every stat. DK-line gap by stat (n): pts +3.55 [3.05, 4.07] (30,236), reb +2.69 [2.19, 3.20] (29,800), ast +3.42 [2.82, 4.01] (20,224), fg3m +2.86
[2.41, 3.34] (26,739). By season in the stratum the draft will use (min10 >= 25): pts 3.09 / 2.88, reb 2.84 / 1.23,
ast 2.75 / 0.93, fg3m 2.55 / 1.81 (2023 / 2024; per-cell half-width about 0.8 pp). **The 2024 gaps in reb and ast are already small**, which limits what a
per-stat test can show.

## Checked components, in the order asked

| component | finding | n |
|---|---|---|
| Recency window (half-life 10) | The base `m_recency` reproduces exactly from the DB (max abs diff 0.0 on 55,320 rows, all stats). It is mean-like: P(y > hl10) - P(y < hl10) is -0.08 to -0.23, constant over 2022 / 2023 / 2024 (pts -0.079, -0.114, -0.091), i.e. skew, not drift. Form (h5 vs h40) and long-run trend move the gap by +-1 pp: A0 mildly under-reacts to recent form; second order next to minutes (6.7 pp spread) | 25,110 / 27,619 / 27,701 |
| Shrinkage toward a prior | The gradient by prior games (-4 pp below 20 games) is mostly bench rows (confounded with minutes). The shrinkage that matters is toward the **pooled residual shape**, not toward a mean prior: the mean is unbiased in the strata where the median is off | |
| Minutes | The strongest single axis: gap runs -3.96 -> +2.73 pp across the four min10 cells. min10 is already a feature of the mean and scale heads, but not of the quantile map | 221,280 |
| Residual (mean head) | Centre is right: `y - c` is non-zero only because `c` is the pre-skew centre (mean z is about -0.05); mean z of observed outcomes in the DK stratum is within 0.05 of 0 | |
| Quantile head | **The component.** Needed standardized-median shift for the DK stratum (observed median z minus pooled calibration median): pts +0.104, reb +0.081, ast +0.127, fg3m +0.141. A map by min10 cell recovers 0.064, 0.066, 0.081, 0.084 of it (62%, 81%, 64%, 60%). The cell medians come from calibration rows only | 106,999 |
| Played-only conditioning | Not a source. All rows are played games; the forward log voids DNP legs, the same population. P(X = 0) equals the realised zero rate (scratch of 2026-10-15) | |
| Season trend | None in the gap (2023 vs 2024 overall -0.09 / -0.13; no month pattern). Level varies by stat and season in the DK stratum (2024 reb and ast smaller), unexplained | |

## Data-version finding (for the main session)

`reports/lower_tail/components_*.npz` (2026-10-11) and `reports/odds_history/model_rows_2024.parquet` (2026-10-10, A0 for T493-T495) are **different
A0 versions for 2024**: the components have 27,701 rows and 1,320 games against 27,583 and 1,315 (118 rows added, from games backfilled between the two
dates), and `mean_int` differs by more than 1e-6 in 57-69% of 2024 rows per stat (mean absolute centre difference 0.11 points for pts; 2023 is identical to the digit).
Both equal their DB at their time (`n_prior >= 5` played rows today: 27,619 and 27,701 = the components). T493-T495 were pinned to the older 2024 rows.
The draft pins the components; whether current `main` production reproduces them is not verified here (a regeneration is a 3-minute heavy-lock job; see the draft).

## Playbook check (class: quantile / distribution layers, recalibration at a point; also GBM context residual as the base)

1. **Information or capacity?** The first component (pooled shape) adds **no new pre-tip information**: `min10` is already an input of A0's heads. It changes how
   the existing residuals become quantiles. I say so plainly: this is a bias in the construction, not an information gain, and an architecture-only idea
   has lost here before. The reason this one is still worth one frozen test is that it targets a measured, signed, replicated bias (all four stats, both seasons)
   with a stated mechanism that is visible in calibration data without any test label, it has zero fitted capacity beyond three empirical quantile
   maps, and the claim is about calibration of the median, not CRPS. The second component is real information (the book's expectation of a normal role) and is out of reach.
2. **Enough data?** Smallest (block, cell) calibration pool 854 rows, none below the 200-row fallback; per stat-season stratum rows 12,334-12,489.
3. **Test valid?** Integer support everywhere (MIDMED is exact on the integer grid); played rows; real tip gating inherited from A0; the population label (DK line) is a pre-tip fact used only to describe, never as an input. Not valid yet: I have not scored a candidate.
4. **Lineage.** LOWER_TAIL `mondrian_minutes` / `mondrian_centre` re-estimated only tau < 0.5 and were judged on lower-tail CRPS (T137-T144: -0.0008 to -0.0034, all under the -0.005 floor, so NOT PASS). The upper half and the median were never re-estimated by cell. The T477 / POVER_BIAS slope (0.46) is the same family of symptom at the line; LC2 / LINE_CALIBRATION fixes it with market inputs.
5. **Scored against?** MIDMED gap at the median (calibration) and integer CRPS; never against a price.

## Verdict

PURSUE one frozen test of the cell-wise quantile map (draft `docs/prereg/MEDIAN_BIAS.md`), with the expectation stated up front that it will close
about 60-80% of the standardized median shift, leave roughly 1.5 pp of the DraftKings-population gap (information, not shape), and move CRPS by
a few thousandths. Not verified: whether the same pattern holds for 2022 (A0 has no out-of-fold 2022 rows; 2022 is the warm-up) and whether current
`main` production equals the 2026-10-11 components.
