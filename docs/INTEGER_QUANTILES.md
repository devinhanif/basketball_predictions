# Integer-support quantiles for production count-stat props: pre-registration

Status: PRE-REGISTERED 2026-10-09, committed BEFORE any new number is computed for this experiment.
Everything above the line `=== RESULTS BELOW ===` is frozen; its sha256 is recorded in the results
section and in the ledger rows.

## Origin and honest data-exposure statement

Source: `docs/reviews/redteam_ctxres_v3_leakfree_2026-10-09.md` (ledger T118). While red-teaming
leak-free experiment 3, the reviewer applied `q_int = ceil(q - 0.5)` to the *local refit* of
production (`v1_prod`) and found large descriptive CRPS gains on 2024: reb -0.0108, ast -0.0160,
fg3m -0.0230 (pts -0.0043), with P(>= N) moving <= 0.0025 and threshold log loss <= +0.0001.

What has been looked at:
* 2024: the red-team numbers above (local `v1_prod` refit, plus a production-OOF vs refit
  fidelity check, `prodcmp.py`). SEEN.
* 2023: the red team ALSO reported the `v1_prod` integer-support gain on 2023 (reb -0.0110,
  ast -0.0157, fg3m -0.0234) and that `ceil(q - 0.5)` was the best of {floor, ceil, round,
  ceil(q-0.5)} on 2023. So 2023 is SEEN too, on the refit, not on the stored production OOF file.
  The 2023 numbers computed here from `reports/context_residual/oof_context_residual.parquet`
  are a re-computation of an already-seen quantity on a nearly identical forecast. They are NOT
  independent evidence and are labelled descriptive. No new data is touched by them.
* 2025: never evaluated for this transform. Not touched in this document.

## Hypothesis

H1: for the integer-valued count stats reb, ast, fg3m, replacing each production quantile `q` by
`ceil(q - 0.5)` (equivalently `round(q)` with halves up; never below 0) lowers CRPS on integer
support (equivalently RPS over integer thresholds) with no practically relevant change in the
threshold probabilities P(stat >= N), threshold log loss, calibration, or mean bias.

Mechanism: CRPS on an integer outcome penalises predictive mass between integers, which cannot
occur. Mapping to integer support removes that penalty. It is a scoring-convention gain, NOT new
information, and must never be reported as model skill.

Null: no CRPS change. Expected outcome is rejection (a gain), since the red team already saw it;
what this document pre-registers is the transform, the guard rails and the confirmation plan.

## Exact transform (no tuning, no alternatives)

For a non-decreasing quantile vector `q` (any grid, any length):

    q_int = ceil(q - 0.5)        # numpy; result >= 0 because q >= 0 (production clips at 0)
    q_int = maximum.accumulate(q_int)   # already monotone; kept as a guard

Applied to `q_grid` (19-grid stored, 199-grid for scoring), and therefore `q10`, `q50`, `q90`
(grid entries). Not applied to `mean`, `std` of dist_params, `mean_uncond`: these remain the
continuous model expectation (so mean bias is unchanged by construction). `p_ge[N]` keeps its
definition `mean(q199 > N - 0.5)`; computed on the transformed 199-grid it is
`mean(q199_int >= N)`. Numerical guard: max |p_ge(int) - p_ge(as-is)| must be <= 0.0025
(documented red-team bound; 0.5/199 = 0.0025) on every threshold; if it exceeds 0.003 on the OOF
the transform is rejected for that stat.

## Stats

* Primary family (decision): reb, ast, fg3m.
* pts: included DESCRIPTIVELY only (reported, no decision weight, not in the default flag set).
  Justification: pts is integer-valued too (sum of 1/2/3-point events), so the same logic holds,
  but it was not the finding being acted on, its gain is ~4-5x smaller (-0.0043) because the
  predictive spread (sd ~ 7) dwarfs the unit lattice, and a narrow confirmatory claim is cleaner.
  The code supports pts through `integer_support_stats`; the maintainer may add it after the
  descriptive result and the 2025 touch below.

## Metrics (arms: production as-is vs production rounded; same rows, same model, only the transform differs)

* Primary: CRPS from the 199-quantile grid with taus `(i + 0.5)/199`
  (`CRPS = 2 * mean pinball`), per player-game, delta = rounded - as-is, game-clustered 95%
  bootstrap CI (2,000 resamples, seed 0, `nba.props.metrics.paired_score_delta_ci`).
  Because actuals are integers, grid CRPS of integer quantiles equals RPS up to the grid
  resolution. Production-as-is vs production-rounded is the comparison; any comparator
  (recency, hybrid, ...) must be scored on integer support too (BEST_PRACTICES row).
* Secondary (non-inferiority guard rails):
  - threshold log loss at the market thresholds `nba.props.config.THRESHOLDS`, averaged over
    thresholds, delta CI; guard: delta <= +0.0005;
  - max and mean |P(>= N) rounded - as-is|; guard: max <= 0.0025;
  - pooled threshold ECE of P(>= N), equal-width 10 bins; guard: delta <= +0.002;
  - mean bias (mean - y) with clustered bootstrap CI (identical by construction; reported to
    prove the mean is untouched) and CLAUDE.md target |bias| <= 0.5;
  - 80% interval coverage: naive `y in [q10, q90]`. The A1.1 PIT estimator is NOT recomputed for
    the integer grid; its documented +/-0.035 uncertainty on integer grids means it cannot
    decide anything here (stated, not hidden). Naive coverage can move up (integer quantiles
    widen the closed interval) and is reported both ways.

## Decision rule (for adopting `integer_support=True` as production default)

Adopt for a stat iff on the confirmatory season (2025, one touch below): CRPS delta upper CI < 0
AND point delta <= -0.005 (the exp-2 practical floor) AND all four guard rails hold. Per-stat,
no multiplicity family beyond the 3 primary stats (BH m = 3, q = 0.05 reported). The maintainer
decides; this document does not change production. Until then the flag defaults to False.

## Evidence plan

(a) 2023 production OOF (`reports/context_residual/oof_context_residual.parquet`, model
`context_residual`, season 2023, played rows, 199-grid): descriptive, labelled SEEN, CPU only,
labels read from `player_game_stats` through a `read_only=True` connection that is closed
immediately. The 2024 OOF is not recomputed here (the red team already reported it and 2024
is seen). Season 2025 is never loaded.

(b) One pre-registered 2025 confirmatory touch is DRAFTED below for maintainer approval. It is NOT
appended to `docs/HOLDOUT_ACCESS_LOG.md` and NOT run. Procedure: reuse the frozen production
context_residual 2025 walk-forward predictions (the already-touched production model, so no new
model is evaluated); only the deterministic transform above is applied post hoc. Because the
transform has no parameters and no fitted component, the touch consumes only the "transform
applied to an already-scored forecast" degree of freedom.

Draft holdout row:

    | <date approved> | post-hoc transform of stored production predictions (`integer_support`, docs/INTEGER_QUANTILES.md sha256 <frozen-sha>) | props: context_residual with q_int = ceil(q-0.5) vs the same predictions as-is | reb, ast, fg3m (pts descriptive) | NOT a clean first touch: season 2025 predictions of the production model were already scored (T-rows of the 2026-10-08 context_residual confirmatory touch); this adds only a parameter-free scoring-convention transform | PRE-REGISTERED before running. Frozen: transform ceil(q-0.5) on q_grid/q10/q50/q90; metrics and guard rails as docs/INTEGER_QUANTILES.md; adopt per stat iff CRPS delta point <= -0.005, clustered 95% CI upper < 0, and all guard rails hold. Run once, no variants (floor/ceil/round are not to be re-tried). Result: _pending_ |

(c) Forward: from the maintainer's go-ahead, log both variants in 2026-27 via the daily pipeline
(`props_context_residual` primary unchanged; extra comparison model rows
`props_context_residual_int`). Score both with `forward_scores` (settle uses the stored 19-grid).
Report with the number of settled player-games; no claim from fewer than ~1,000 settled rows per stat.

## What would count as a negative result

CRPS CI spanning 0 or a guard rail failing on 2023 or 2025 -> flag stays off for that stat and the
failure is logged. A gain that merely reproduces the red-team number is a scoring-convention
confirmation, not evidence of better forecasting.

=== RESULTS BELOW ===
