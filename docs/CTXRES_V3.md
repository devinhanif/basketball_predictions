# Context-residual v3 (ctxres_v3) -- experiment 3: per-stat hybrid, PRE-REGISTRATION of the 2025 confirmatory touch

Status: written 2026-10-08 BEFORE any season-2025 row was scored by any arm of this experiment.
The rule below is applied ONCE by `nba/eval/ctxres_v3_eval.py` and is not changed after seeing
results. A negative result is reported as a negative result. Builds on `docs/CTXRES_V2.md`
(exp-2 protocol, keep rule, amendments A1/A1.1) and `docs/CTXRES_V2_EXP2_RESULTS.md`.

## 1. Why this experiment exists, and what it is NOT

Experiment 2 (run `20261008_171446`, season-2023 selection, season-2024 report) ended with a
mixed verdict: the 2023-recorded candidate `xgb_v12_poisson_nb` beat `v1_prod` on reb/ast/fg3m and
passed every check there, but failed pts (starter-slice regression +0.0147, PIT coverage 0.716),
so "v2 kept overall" was False. The descriptive leaderboard showed that on pts the 19-quantile arm
`xgb_v12_quantile` is much better (dCRPS vs v1 -0.0758 vs -0.0299 for the count arm), while the
count arm is best on reb/ast/fg3m. Experiment 3 asks one question: **does the per-stat hybrid
(pts from the quantile arm, reb/ast/fg3m from the count arm) beat the production v1 recipe on
unseen season-2025 data, under the exp-2 keep rule, with the exp-2 configurations frozen?**

Not part of this experiment: any search, any retuning, any calibrator other than the single pts Platt in section 2, any new feature, the
concurrently developed opponent-ridge v2 features (this export uses the exp-2 feature set
unchanged), any other arm on 2025. It is not a model-family comparison; the only decision is the
hybrid vs `v1_prod`.

### Honesty statement (selection history)

* The hybrid's per-stat composition was **chosen after viewing the 2024 results** of experiment 2
  (the pts quantile arm's 2024 delta -0.0758 and the count arm's failure on pts were both visible).
  The composition is also consistent with the season-2023 per-stat CRPS (the selection season):
  pts 3.0934 (quantile) vs 3.1508 (count); reb 1.2630 vs 1.2695, ast 0.8902 vs 0.9056, fg3m
  0.5594 vs 0.5706 (count better) -- but that table was also seen when choosing. Hence a 2024
  re-statement of the hybrid (section 6) is **optimistic by construction and is not a
  confirmation**. This is exactly why the unseen 2025 season is the confirmatory test.
* 2024 results of many experiment-1 and experiment-2 arms were already viewed; see
  `docs/CTXRES_V2.md` disclosures. Season 2025 has never been loaded by any v2-feature model (the
  v2 loader raises on season > 2024) -- the hybrid and its components are **never-evaluated models**
  on 2025. The comparator `v1_prod` is the production recipe whose parent model
  (`props_context_residual`) already had its own clean 2025 touch (HOLDOUT_ACCESS_LOG); re-running
  it here as a comparator is not a new use of the holdout for it. Project-wide, `season=2025` is
  "no virgin holdout" (FDR audit sec. 3) because OTHER models touched it; the clean-touch claim
  below is per model, per rule 1 of the log.
* The forward 2026-27 season remains the real out-of-sample test; one 2025 confirmation does not
  make the hybrid "proven".

## 2. The exact hybrid (frozen from exp-2 artifacts)

Source: `data/colab/runs/ctxres_v2_sweep/20261008_171446/best_config.json`
(sha256 `4ae729a111618993ba700a9d79ce8879c515df51aca2d71229eef4174a58dac8`) and `metrics.json`
(sha256 `5c65faef64934ced77666d0d91ba54c36382905432de8be9296188edc11cc24c`). The values below are
embedded as `FROZEN` in `nba/colab/jobs/ctxres_v3_hybrid/ctxres_v3_hybrid.py`; their canonical-JSON
sha256 is `a1c889e34067c9f4c3a72e92d7e4e59e784662a88623cb2ecf44f4a47c829b1e` and is pinned in the
evaluator (a run made from any other config is rejected).

| stat | arm | recipe |
|---|---|---|
| pts | `xgb_v12_quantile` | XGBoost `reg:quantileerror`, 19 quantile levels 0.05..0.95, target = stat minus as-of recency mean, linear tail extrapolation to the 199-grid, clip >= 0 |
| reb, ast, fg3m | `xgb_v12_poisson_nb` | XGBoost `count:poisson` mean; NegBin dispersion by position group on the newest-15% calibration window, shrunk to the pooled alpha (k = 300); exact NegBin quantiles and survival function |

Both arms: v1+v2 feature set per stat (`feature_spec.json` `v1_features` + `v2_features`; groups
a-j, 155 columns exported; same allow-list / deny-regex static guard), frozen hyperparameters
`search_v12.params`: learning_rate 0.0558198665255319, max_depth 3, subsample 0.7168179865008671,
colsample_bytree 0.7144428763045745, min_child_weight 116.4036974504763, reg_alpha
0.17402858001416036, reg_lambda 4.138732643418021; `n_estimators` cap 600 with early stopping 20
rounds on the time-ordered newest 12% of the fit window; seed 20261008 (GPU XGBoost is not
bit-reproducible; seeds and device are logged); budget `full` (`rounds` 600, `prod_rounds` 200,
`min_train` 1500, `min_cal` 2000). No Optuna, no selection, no pruning, no relative-feature variants.

Engine code: a verbatim copy of the exp-2 sweep script (`ctxres_v2_engine.py`, sha256
`80f67da027347ecf5c57fbbb2b764a7a360945a7c0aba91dddedbf5ad98790f3`, identical to
`ctxres_v2_sweep.py` at the time of copying); v3 only re-points the season roles
(`SELECT_SEASON = REPORT_SEASON = 2025`, load guard threshold 2026). Test
`test_engine_copy_pinned_and_notebook_in_sync` fails if the copy drifts.

**Calibrator decision (the only one, made now, from pre-2025 evidence only):
Platt on the hybrid's pts threshold probabilities ONLY; none for reb, ast, fg3m; conf/pit dropped.**
Evidence (`docs/BACKLOG_SMALL_2026-10-08.md` item 3, 2024 OOF of exp-2): pts raw ECE 0.0119 vs noise
floor 0.0018 and month-to-month gap persistence r = 0.65 (real, learnable; Platt and isotonic passed
the exp-2 calibrator rule for pts); reb ECE 0.0036 is about 1.6x its noise floor with r = 0.25
(nothing to calibrate); ast 0.0069 and fg3m 0.0079 are real but the achievable gain is below the
0.005 floor; conf/pit fail structurally (integer outcomes + coarse 19-grid; converting a grid to
P(>=N) costs +0.009 to +0.015 threshold log loss). Settings are the exp-2 tuned Platt settings
(window W = 4 blocks, half-life 4 blocks, C = 10; `best_config.json` `calibrators.platt`), fit
nested walk-forward INSIDE the 2025 run on earlier 2025 blocks' out-of-fold predictions (the first
block, and any block with fewer than 500 history rows, stays raw). Caveat stated up front: these
settings were tuned on the count arm in 2023, while the hybrid's pts arm is the quantile arm, so the
calibrator is applied slightly off-distribution. Calibrator evaluation (variant
`hybrid_v3|platt_pts`) uses the exp-2 calibrator rule on pts 2025 threshold events (keep iff thr-LL
improves with game-clustered CI upper < 0, or ECE improves >= 0.005 without worse LL), reported as
`calibrator_pts`; it is a separate verdict and does NOT enter the CRPS keep rule below. Raw
(uncalibrated) threshold metrics for all stats are reported alongside.

## 3. Data and walk-forward procedure

Export (flag-gated, additive): `uv run python -m nba.props.context_features_v2 --db nba.duckdb
--out-dir data/colab/ctxres_v3 --include-holdout-2025` writes the SEPARATE
`data/colab/ctxres_v3/ctxres_v3_with2025.parquet` (+ `feature_spec.json`). The default export
(`ctxres_v2.parquet`) is byte-identical (sha256 `6586f4af...a217d6` before and after the change;
test-covered). On the real DB the first 80,312 rows (2022-2024) of the 2025 export equal the v2
export column for column with zero tolerance (checked), i.e. every feature is as-of: adding
2025 rows changes no earlier row. Export: 108,383 played rows (>= 5 prior played games), of which
season 2025: **28,071 rows, 1,316 games** (2025-10-21 .. 2026-06-13); peak RSS 0.84 GB, 6 s.
Parquet sha256 `eb2f778c...164ea97`.

Walk-forward: calendar-month blocks of season 2025 (fold ids 202510 .. 202606; rows 1600, 4653,
4183, 4927, 3556, 5089, 3085, 877, 101). Each block is predicted by models fit on **all played rows
strictly before the block's first date** (2022-2024 and earlier 2025 months); newest 15% of the
fit window is the held-out calibration window (v1 recipe, as in exp-2). Same configs for every
block; no search; no refit choices based on any 2025 result. If the Colab kernel dies the run is
resumed from its checkpoints with the identical frozen configuration; only a complete run's
artifacts are scored, and the evaluator refuses to score a run id twice without `--force`
(logged to a ledger).

Arms in the run: `xgb_v12_quantile` and `xgb_v12_poisson_nb` (all four stats each, so the
alternative composition can be shown descriptively), `hybrid_v3` (assembled by picking the frozen
component per stat; consistency-checked against the component rows), `v1_prod` (comparator),
`recency_normal` (descriptive baseline). One descriptive seed replicate (`NBA_V3_REPLICATES=1`,
seed+1) of the two components reports GPU/seed sensitivity; it never enters the rule.

## 4. Comparator

`v1_prod` = the production recipe (LightGBM residual mean head + |residual| head + split-conformal
quantiles; production config `n_estimators` 200, learning_rate 0.04, num_leaves 15,
min_child_samples 100, colsample 0.8, subsample 0.8, v1 features, newest-15% conformal window),
refit identically in the same walk-forward blocks of 2025, exactly as in exp-1/exp-2.
Caveat: this is the sweep's re-implementation of `props_context_residual` v1, not a call into
`nba/props/context_residual.py`; it is the same comparator the exp-2 keep rule used. The recency
baseline (as-of recency mean / std Normal) is reported descriptively only.

## 5. PRE-REGISTERED KEEP RULE (exp-2 rule, unchanged; applied once)

Candidate `hybrid_v3` vs reference `v1_prod`, season-2025 played rows (n = 28,071 per stat,
expected; the evaluator reports the realized n). A stat is KEPT iff ALL hold:

1. mean CRPS delta (hybrid - v1) <= -0.005;
2. game-clustered 95% bootstrap CI (2,000 resamples) of the delta has upper bound < 0;
3. clustered two-sided p-values survive Benjamini-Hochberg across the 4 stats (m = 4), q = 0.05;
4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games,
   starter vs bench; n >= 300) has a CRPS delta > +0.01;
5. |mean bias| <= 0.5;
6. A1.1 PIT coverage of the 80% interval in 0.75-0.85 (continuity-corrected linear interpolation
   with linear tails on the stored 19-quantile grid). The naive `y in [q10, q90]` coverage is
   reported alongside. For the count arm (integer quantile grid) the documented A1.1 estimator
   uncertainty of about +/-0.035 applies and cannot by itself decide a borderline coverage case.

plus: the run's leak audit is present and passed (static allow-list at load; dynamic top-10 gain /
|SHAP| allow-list, deny-regex and |rank corr| < 0.95 audit of one fit of the frozen config on rows
before the last 2025 block, stored in `metrics.json`). The hybrid is **kept overall only if all
four stats are kept**; otherwise it is reported per stat and production stays v1. The BH family is
exactly these 4 cells; every other number in the report is descriptive and carries no decision.

Power: with n ~ 28k clustered rows the 80%-power minimum detectable CRPS effect in exp-2 (2024,
n = 27,583) was 0.0099 (pts), 0.0054 (reb), 0.0031 (ast), 0.0021 (fg3m); the floor 0.005 is
below the pts MDE, so a true pts effect between -0.005 and about -0.010 could be missed, while the
2024 point estimate is -0.0758. 2025 is also a different, shorter-feature-history regime (drift
table in the exp-2 report); the kept-or-not answer is about this one season.

Descriptive (no decision): hybrid and `v1_prod` vs `recency_normal` (clustered CIs); the
alternative composition per stat (quantile arm on reb/ast/fg3m, count arm on pts) vs the hybrid;
threshold log loss / Brier / ECE (raw; pts also Platt) at the exp-2 thresholds (pts 10/15/20/25/30, reb
4/6/8/10, ast 2/4/6/8, fg3m 1/2/3/4); per-block CRPS; seed replicate; run-to-run GPU variation.

## 6. Descriptive 2024 re-statement of the hybrid (from existing exp-2 OOF; no new information)

`uv run python -m nba.eval.ctxres_v3_eval --restate-2024` assembles the hybrid from the stored
exp-2 2024 OOF and applies the same rule code. Season 2024, n = 27,583 played rows per stat,
game-clustered CIs, BH over the 4 stats. **Optimistic by construction (section 1), not a confirmation.**

| stat | component | dCRPS vs v1_prod [95% CI] | bias | coverage 80% | rule checks |
|---|---|---|---|---|---|
| pts | quantile | -0.0758 [-0.0825, -0.0686] | +0.019 | naive 0.796 (PIT not computable: the exp-2 quantile OOF is the light copy without its quantile grid; provisional) | all pass |
| reb | count | -0.0505 [-0.0541, -0.0466] | +0.045 | PIT 0.793 (naive 0.877) | all pass |
| ast | count | -0.0303 [-0.0324, -0.0281] | +0.048 | PIT 0.792 (naive 0.908) | all pass |
| fg3m | count | -0.0321 [-0.0336, -0.0306] | +0.024 | PIT 0.794 (naive 0.937) | all pass |

BH q = 0.0005 each; no slice regression > +0.01 for any stat. Hybrid would be "kept overall" on
2024 with the pts coverage check provisional. Known risk for 2025: the pts PIT coverage of the
quantile arm has never been measured with A1.1 (it will be, on 2025, for the first time); the
count arm's PIT coverage on 2024 sat at 0.79 for reb/ast/fg3m but its pts coverage was 0.716.

## 7. Mechanics and guards

* Job `ctxres_v3_hybrid` (`nba/colab/jobs/ctxres_v3_hybrid/`): full budget only, prints the GPU,
  expected ~35 min on a T4 (v1_prod ~2-3 min on CPU, count arm ~3 min, quantile arm ~10-12 min,
  leak audit ~1 min, one seed replicate ~15 min; estimates, not measured on 2025 data),
  crash-safe arm checkpoints, small artifacts first, per-variant OOF parquet, `metrics.json`
  `complete: false` until the end.
* **Refuses to run** unless env `NBA_PREREGISTERED_ID` equals the `preregistration_id` staged in
  `job_meta.json` (from `job.yaml`, currently the placeholder `PENDING_SET_AFTER_HOLDOUT_LOG_ROW`,
  always refused) and `NBA_SCORE_HOLDOUT=True` (set by `nba.colab push` for a `touches_holdout`
  job). The check runs before any 2025 row is read.
* Evaluator `nba/eval/ctxres_v3_eval.py`: validates the run (tag, complete, full budget, frozen-config
  and engine hashes, hybrid map, calibrator = platt_pts_only, pre-registration id not a placeholder, leak
  audit), checks hybrid rows equal the mapped component rows, then claims the run id in an
  append-only ledger (`data/colab/ctxres_v3/eval_ledger.jsonl`) and applies the rule. A second
  call on the same run id raises unless `--force`, and every forced use is logged.

## 8. Commands

```
# 1. (maintainer) append the pre-registered row to docs/HOLDOUT_ACCESS_LOG.md (text drafted in the
#    hand-off), then set preregistration_id in nba/colab/jobs/ctxres_v3_hybrid/job.yaml to its id.
uv run python -m nba.props.context_features_v2 --db nba.duckdb \
    --out-dir data/colab/ctxres_v3 --include-holdout-2025
uv run python nba/colab/jobs/ctxres_v3_hybrid/build_notebook.py     # only if the job source changed
make colab-push JOB=ctxres_v3_hybrid
# open the printed notebook link; Runtime > T4 GPU; set NBA_PREREGISTERED_ID in the setup cell
# (value from RUN.md); Run all.  Re-run the same notebook after a disconnect: it resumes.
make colab-status JOB=ctxres_v3_hybrid
make colab-pull JOB=ctxres_v3_hybrid
# 2. score ONCE
uv run python -m nba.eval.ctxres_v3_eval --run data/colab/runs/ctxres_v3_hybrid/<run_id> \
    --prereg-id <id> --out reports/ctxres_v3_verdict.json
# descriptive 2024 re-statement (no 2025 data)
uv run python -m nba.eval.ctxres_v3_eval --restate-2024
```

## 9. What each outcome means

* All 4 stats kept: the hybrid beats the production v1 recipe on an unseen season under the
  frozen rule. It still has to survive the forward 2026-27 season before replacing production
  routing, and production adoption is a separate decision (model-gate).
* Some stats kept: report per stat; do not cherry-pick a composition from the 2025 result and
  call it confirmed (any later re-composition is a new experiment needing new data).
* None kept (or a stat fails on coverage/slices): a negative result, reported as such; v1 stays.
