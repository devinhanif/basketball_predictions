# Points upper-tail calibration for production context_residual: pre-registration

Status: PRE-REGISTERED 2026-10-09, committed BEFORE any candidate number was computed.
Everything above the line `=== RESULTS BELOW ===` is frozen; its sha256 is recorded in the
results section and in the ledger rows.

## Origin and honest data-exposure statement

Source: `docs/reviews/redteam_ctxres_v3_leakfree_2026-10-09.md`, item (7): the leak-free
experiment-3 *hybrid* quantile arm for pts had P(y <= q0.99) = 0.973 (production 0.992) and
worse threshold log loss at 20+/25+/30+. The task framing carried that finding over to
production. Step 1 below checks whether production itself has the problem.

What has been looked at before this document was frozen:
* Production OOF (`reports/context_residual_v2_realtip/`, real-tip gate), pts, 2023 and 2024:
  the red team's descriptive numbers (production q0.99 coverage 0.992), and the production-only
  diagnostics of `nba.eval.pts_tail_eval production` (run after `collect`, which stores the
  production heads' centre/scale and calibration windows; fidelity vs the stored OOF 19-grid:
  max abs difference 0.0 over 55,202 rows). Production diagnostics for BOTH seasons are therefore SEEN.
* Candidate arms: NOT computed. The `candidates` stage has not been run; the candidate functions
  have only been exercised by unit tests on synthetic data.
* 2025: never loaded (`MAX_SEASON = 2024` guard, season 2025 refused).

Production-only finding (pts, the 199-grid used for `p_ge`; game-level n = 1,318 / 1,315):

| | 2023 | 2024 |
|---|---|---|
| P(y <= q0.90) / q0.95 / q0.99 | 0.904 / 0.954 / 0.991 | 0.897 / 0.949 / 0.989 |
| mean P(y>=20) pred vs obs | 0.1632 vs 0.1688 | 0.1577 vs 0.1640 |
| mean P(y>=25) | 0.0860 vs 0.0882 | 0.0799 vs 0.0828 |
| mean P(y>=30) | 0.0420 vs 0.0435 | 0.0375 vs 0.0392 |
| mean P(y>=35) | 0.0193 vs 0.0168 | 0.0162 vs 0.0157 |
| mean threshold log loss, thr 15/20/25/30/35 | 0.1996 | 0.2024 |

So production's pts upper tail is NOT thin at the extreme quantiles; the red-team figure
belonged to the hybrid arm. A small residual under-prediction at 20+/25+/30+ (~3-5% relative)
remains. This screen therefore tests whether that residual can be removed; the expected outcome
is a small or null effect.

## Hypothesis

H1: replacing the pooled empirical standardized-residual quantiles for pts by one of three fixed
upper-tail constructions lowers mean threshold log loss over P(pts >= N), N in {15,20,25,30,35},
without raising CRPS by more than 0.002.
Null: no change. Mean (hence mean bias) is not touched by any candidate.

## Fixed candidates (at most 3; no tuning; constants below are final)

All keep production's mean head, scale head and calibration window (newest 15% of training rows
on a date boundary). Let `c = m + mu` (centre), `s` the scale head output, `z` the calibration
standardized residuals `(resid - mu)/s`.

* **sqrt** (a): conformal on the square-root scale. `w = (sqrt(y) - sqrt(c)) / (s / (2 sqrt(c)))`
  on the calibration rows (c floored at 0.5); `q_tau = max(sqrt(c) + s/(2 sqrt(c)) * w_tau, 0)^2`.
* **mondrian_up** (b): separate upper-half (tau > 0.5) conformal widths per predicted-centre
  tercile (edges from the calibration `c`); lower half pooled; bins with < 200 calibration rows
  fall back to pooled.
* **gamma_tail** (c): gamma (loc 0) fitted to exceedances of calibration `z` over its pooled
  q0.75; for tau > 0.75, `z_tau = 0.5 * (q0.75 + Gamma.ppf((tau-0.75)/0.25)) + 0.5 * z_emp_tau`;
  tau <= 0.75 unchanged.

Quantiles are clipped at 0 and made non-decreasing exactly as in production. Implemented in
`nba/props/pts_tail.py` behind `ContextResidualConfig.pts_tail` (default `"off"` = byte-identical).

## Design

* Rows: production walk-forward (month blocks, train strictly before the block, 2022 warm-up),
  pts only, played rows with >= 5 prior games; one fit pass shared by all arms.
* **Selection season 2023; report season 2024.** A candidate is selected on 2023 only if it passes
  all checks below there; the 2024 result is then the confirmation. All three are also reported on
  2024 for transparency; only the selected one can be called a pass.
* P(pts >= N) = share of the 199-quantile grid above N - 0.5 (production definition), clipped to
  [0.5/199, 1 - 0.5/199] for log loss.
* **Primary metric:** per-row mean over the 5 thresholds of the threshold log loss; paired delta
  (candidate - production), game-clustered bootstrap CI (2000 resamples, seed 0).
* **Secondary:** CRPS on the 199-grid (must not worsen by more than +0.002, point estimate);
  coverage P(y <= q) at q0.90, q0.95, q0.99 (and q0.01/0.05/0.10) reported; pooled equal-mass ECE;
  per-threshold deltas; randomized-PIT 10-bin histogram; mean bias (candidates do not change the
  mean: reported for completeness).
* **Multiplicity:** Benjamini-Hochberg over the 3 candidates' primary p-values within a season.
* **Pass:** threshold-log-loss delta point <= -0.002 (floor), clustered CI upper bound < 0,
  BH-adjusted p < 0.05, CRPS delta point <= +0.002.
* No promotion. A candidate passing on 2024 would only get a drafted (not appended) holdout row
  and a recommendation to shadow-log it live, as for the integer variant.
* Out of scope: the lower tail (production coverage at q0.01/q0.05/q0.10 is 0.12-0.18, descriptive
  only), reb/ast/fg3m, season 2025.

=== RESULTS BELOW ===
