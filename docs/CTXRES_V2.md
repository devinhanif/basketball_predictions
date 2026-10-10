# Context-residual v2 (ctxres_v2) -- design and PRE-REGISTRATION

Status: written 2026-10-08 BEFORE any Colab run. The rule below is applied by
`research/eval/ctxres_v2_eval.py` and is not changed after seeing results. A negative result is
reported as a negative result.

## What this is

Production props model = `props_context_residual` v1 (`nba/props/context_residual.py`: played-only
recency average + LightGBM residual and |residual| heads + split-conformal quantiles). v2 asks two
separable questions: (1) do NEW as-of features help; (2) does a different model family, tuning
or calibration help. The job `ctxres_v2_sweep` runs both on a Colab T4; all choices use season
2023 only, season 2024 is report-only, **season 2025 is never loaded** (the loader raises). v2 has
never touched 2025: one clean confirmatory 2025 touch is allowed later under a separate
pre-registration; the forward 2026-27 season is the real test.

## Data and features (`research/props/context_features_v2.py`)

Export: `uv run python -m research.props.context_features_v2 --db nba.duckdb --out-dir data/colab/ctxres_v2`
(about 10 s, read-only DB). Seasons 2022-2024 (2022 = warm-up), PLAYED rows only (minutes > 0, DNP
rows excluded, per docs/DNP_AUDIT_2026-10-08.md) with >= 5 prior played games: 80,312 rows. One
parquet (keys, `game_date`, `season`, `fold_id` = year*100+month, labels pts/reb/ast/fg3m,
`base_m_*`/`base_s_*` recency mean/std, v1 features, v2 features) plus `feature_spec.json`
(feature lists per stat and group, allow-list, deny regex, null shares).

| group | features | as-of / leakage note |
|---|---|---|
| a own status | pre-tip status ordinal (available..out), listed flag, absent last game, OUT on last game's report, absence streak, games since return, last absence length, `own_returning` | status from the latest report stamped <= 19:00 ET proxy tip minus 60 min (`usable_report_rows`); later rows ignored; absence history uses PRIOR rows only; null when the game has no usable report (never imputed) |
| b returning teammates | count, as-of minutes, usage and stat averages of rotation players (>= 12 min long-run) absent/OUT in the team's previous game and not OUT tonight | returner values = state after his last game dated before today; null without a report |
| c possession rates | on-court usage (box fga+0.44 fta+tov over on-court possessions), shot-zone mix, FTA rate, assisted-make share, TOV rate, OREB/DRB per 36, effective exposure | played-only, decayed (half-life 10 games) sums over EARLIER games, shrunk to the as-of league ratio. Possession table lacks shooter ids on TOV and FT-trip possessions, so usage/TOV/FTA come from box-score history and the possession table supplies exposure, zones and assisted makes |
| d schedule | games in last 4/6/7 days, 3-in-4, 4-in-6, b2b second/first night, days to next game, travel miles (this leg and last 7 days), opponent density | public schedule; the next game's DATE is known pre-tip; arena coordinates are approximate and neutral sites count as the home arena |
| e opponent vs position | decayed mean of (stat - player's own recency mean) allowed by the opponent to the player's position group, shrunk (40 pseudo player-games) | defender's EARLIER games only |
| f game environment | as-of team scoring environment (`game_model.asof_total_features`), implied team/opponent points with the MOV-Elo margin | no fit used (affine map irrelevant to trees); the injury-Elo OOF is not used because its OLS map is fit on all seasons <= 2024 |
| g draft | pick (61 = undrafted), ln pick, years since draft, rookie flag, rookie x ln pick | static public facts |
| h TV | national TV flag, major-network flag, TV x home | schedule fact |
| i opponent-adjusted ridge | no-intercept ridge on player and opponent-team effects for per-36 rates, refit monthly on rows strictly before the month (540-day lookback, minutes-weighted, alpha 50), opponent effects centred | refit dates < month start only |
| j overtime normalisation | decayed per-48 means (stat x 48 / game length) and per-minute rates x 48; game length = total minutes / 10 | overtime games cannot inflate baselines; labels stay raw |

Static leak guard: every model input must be in the spec allow-list (v1 + v2 names, or a `rel__`
transform of one) and match none of the deny patterns (same-game box-score names, `minutes`,
`starter`, `actual|post|tonight|final` tokens). Dynamic leak detector: after the tuned
XGBoost arm, the run FAILS (raises) if any top-10 feature by gain or by mean |SHAP|
(`pred_contribs`) is not allow-listed, matches the deny patterns, or has |rank correlation| >= 0.95
with the same-game label. The audit result is stored in `metrics.json` (`leak_audit`); a run
without it is invalid under this pre-registration.

Tests (`tests/props/test_context_features_v2.py`): planted future rows change nothing at or before
the cut date; a report stamped after tip-60 is ignored (with a control showing an earlier report is
used); DNP rows do not enter possession rates; allow-list rejects same-game names.

## Protocol (`research/colab/jobs/ctxres_v2_sweep/ctxres_v2_sweep.py`)

* Walk-forward: every calendar-month block is predicted by models fit on rows dated strictly
  before the block; the newest 15% of the training window is the held-out calibration window
  (v1 recipe). Test blocks are the months of seasons 2023 and 2024.
* **Selection uses 2023 blocks only**: Optuna search, best arm, top-3 blend members, pruned-feature
  set, calibrator settings. **2024 is report-only.** Selection objective = mean over the 4 stats of
  CRPS / CRPS of the recency-Normal baseline on the same rows (scale-free so pts does not dominate).
* Search space (regularised trees): max_depth 3-5, subsample 0.6-0.8, colsample 0.6-0.8,
  min_child_weight 20-300, L1 0.01-10, L2 1-50, learning rate 0.05-0.2, early stopping on the
  time-ordered tail of the fit window; fast budget searches 12 trials per feature set on every 4th
  2023 block with half the rows; the chosen config is then refit once per walk-forward block.
* Arms: `v1_prod` (production LightGBM config, v1 features); `xgb_v1_prodcfg`, `xgb_v12_prodcfg`
  (production recipe on XGBoost; separates engine and features at a fixed config); `xgb_v1_tuned`,
  `xgb_v12_tuned` (separates tuning from features); `xgb_v12_rel` / `xgb_v12_both` (season-relative
  = feature minus the as-of same-season league mean, falling back to the prior season early;
  raw+relative); `xgb_v12_pruned` (top 30 features by mean gain on 2023 blocks; never PCA);
  `xgb_v12_quantile` (multi-quantile); `xgb_v12_poisson_nb` and `glm_poisson_nb` (Poisson mean +
  NegBin dispersion per position group, shrunk; zero-inflation check for fg3m reported);
  `logit_thr` (L2 logistic per threshold, monotone in N; thresholds only); `mlp_quantile`
  (monotone 19-quantile head, pinball, AMP on GPU); `blend_top3` (equal-weight quantile and
  probability average of the top 3 distribution arms ranked on 2023).
* Threshold events P(stat >= N): pts 10/15/20/25/30, reb 4/6/8/10, ast 2/4/6/8, fg3m 1/2/3/4.
  Distribution arms are converted by linear interpolation on the 199-quantile grid (count arms use
  the exact NegBin survival function). Threshold log loss, Brier, ECE (10 equal-mass bins).
* Calibrators (nested walk-forward: fit on EARLIER blocks' out-of-fold predictions, applied to the
  next block; settings tuned on the 2023-selected best arm using 2023 rows only):
  Platt (pooled, features logit p and ln N) and per-threshold isotonic with a minimum bin size
  (monotone across N afterwards); distributions: standardized-score recalibration (`conf`) and
  PIT-ECDF recalibration (`pit`) versus none. Tuned: history window, recency half-life,
  isotonic min-bin, Platt C, minimum history rows.
* Reported but not used for decisions: ablation (drop-one group a-j, 2024), per-block CRPS,
  drift table (pace/3PA/FTA/usage proxies and target shifts by season), normalization arms.
* Budget: single `NBA_BUDGET` knob. `full` is the default from experiment 2; `fast` measured ~30-35 min on a T4 (per-arm estimates
  printed at the top of the run; optional arms are skipped once 80% of a 35 min budget is spent and
  are then missing from the report); `full` is longer.
  GPU XGBoost is not bit-reproducible; seeds are logged.

## PRE-REGISTERED KEEP RULE

Candidate = `best_arm_2023` (chosen on 2023 only). Reference = `v1_prod` re-run inside the same
walk-forward. Data = season-2024 played rows. v2 is KEPT for a stat iff ALL hold:

1. mean CRPS delta (candidate - v1) <= -0.005 (floor);
2. game-clustered 95% bootstrap CI of the delta excludes 0 (upper bound < 0);
3. clustered two-sided p-values survive Benjamini-Hochberg across the 4 stats at q = 0.05;
4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games,
   starter vs bench; n >= 300) has a CRPS delta > +0.01;
5. mean bias within +/-0.5;
6. 80% interval coverage within 0.75-0.85;

and the leak audit is present and passed. v2 replaces production only if all four stats are kept.

**Calibrator rule** (candidate arm, per calibrator, per stat, 2024 threshold events): kept only if
threshold log loss improves with the clustered CI excluding 0, OR the equal-mass-bin ECE improves
by >= 0.005 without worsening log loss (CI upper bound <= +0.001).

Contrasts that are descriptive only (no keep decision): features vs tuning vs engine
(`xgb_v1_prodcfg` / `xgb_v12_prodcfg` / `*_tuned`), raw vs season-relative vs both, full vs pruned,
blend vs best single arm, family leaderboard on threshold log loss.

## Completion step (procedural fix, added after the first fast run)

The first fast run (20261008_131747) finished all arms except that the per-arm time guard (240 s)
cut `xgb_v12_quantile` after its 2023 blocks: it had the best 2023 selection ratio (0.9396) and was
therefore the pre-registered candidate, but it has no 2024 rows (nor does `blend_top3`), so the
rule could not be applied. The sweep's real runtime was ~30+ min, not 15-20 (the per-arm timings
are in `metrics.json` `timings_s`; estimates in the notebook are now the measured ones).

Fix, procedural only: `NBA_MODE=complete` (auto-selected when a plan `completion_prev.json` is
staged next to the parquet) runs NO search and NO selection. It refits only the listed arms
(`NBA_COMPLETE_ARMS`, default `xgb_v12_quantile,blend_top3,v1_prod`) with the configs the first run
chose on 2023, on the 2024 walk-forward blocks, plus the fixed 2023 top-3 members for the blend
(`xgb_v12_prodcfg`, `xgb_v12_rel`), plus an optional tail (`NBA_COMPLETE_CAL=0` skips it) that refits
the candidate on 2023 blocks as nested-calibrator history and builds the four calibrator variants
with the first run's tuned settings. Core ~9 min, tail ~5.5 min on a T4 (from measured arm times).
The hard per-arm cap is now 900 s and an arm that cannot finish is dropped, never stored half-done,
so an incomplete arm can no longer win a selection.

Honesty note: the candidate (`xgb_v12_quantile`) and the top-3 were fixed from 2023 results before
any 2024 result of those arms existed, and the pre-registered rule is unchanged. However, 2024
results of OTHER arms (v1_prod, xgb_v12_prodcfg, xgb_v12_tuned, ...) from the first run HAD been
viewed before this step was written; they did not and cannot influence the candidate, the
configs or the rule. The eval merges the runs by variant (the completion run supersedes the first
run's lighter copy of the same variant; the first run's `best_config.json` supplies the candidate).

## EXPERIMENT 2 -- full-breadth sweep (pre-registered 2026-10-08, BEFORE its run)

Family tag `ctxres_v2_exp2_full_breadth` (job tag `experiment: 2`; stored in `metrics.json` and
`best_config.json`). It re-runs the whole model-family sweep with a broader, strict budget. It is
a SECOND experiment, not a re-selection of experiment 1.

* Protocol unchanged: select on 2023 blocks only (Optuna, best arm, top-3 blend, pruned set,
  calibrator settings), report on 2024 blocks, season 2025 never loaded, same keep rule vs
  `v1_prod`, same calibrator rule, same BH over the 4 stats (see PRE-REGISTERED KEEP RULE above).
* Changes vs experiment 1 (breadth, not rule): `NBA_BUDGET=full` is now the default and is STRICT:
  no optional arm is skipped and no arm may be dropped; a hard cap or a missing arm raises. 60 Optuna
  trials per tuned feature set (v1, v1+v2) searched on every 3rd 2023 block with all training rows and
  600-round cap; a CatBoost-GPU residual arm (`catboost_v12`, same chosen tree settings mapped to
  CatBoost: depth = max_depth + 1, L2 = reg_lambda, Bernoulli subsample) is added if `catboost`
  installs (recorded in `catboost_available`; absence is reported, not hidden); every arm of the plan
  (xgb v1/v12 prodcfg + tuned, rel/both/pruned, multi-quantile, xgb count:poisson + NegBin, Poisson GLM
  + NegBin, threshold logistic, monotone-quantile MLP, CatBoost, blend of the 2023 top-3), all four
  calibrators on every arm, and the drop-one ablation on ALL 2024 blocks (no stride).
* GPU: XGBoost `device='cuda'`, CatBoost `task_type='GPU'`, torch AMP for the MLP. The Poisson GLM and
  logistic arms stay on CPU (cheap).
* Expected runtime ~65-85 min on a T4 (the per-arm estimates and a total are printed at the top of
  the run; v1_prod, XGBoost arms and the quantile arm are scaled from the measured fast run, the
  count/GLM/logit/MLP/CatBoost arms and calibrators are guesses).
* Honest disclosure: 2024 results of experiment-1 arms (v1_prod, xgb_v12_prodcfg, xgb_v12_tuned,
  xgb_v1_tuned, rel, both, pruned, and the experiment-1 completion step) were ALREADY viewed before
  this experiment was written. Experiment 2 therefore is not a clean out-of-sample test of the v2
  feature groups' value (that is already visible); what it adds is the broader model-family and
  calibration comparison under the same rule, and any new keep decision is computed only from its own
  2023-selected candidate. The clean tests remain one confirmatory 2025 touch (separate
  pre-registration) and forward 2026-27.
* Staging: the plan file must be `{}` for a normal sweep: `uv run python -m research.eval.ctxres_v2_eval --clear`
  (the job's producer also creates `{}` if the file is missing).

## Commands

```
make colab-push JOB=ctxres_v2_sweep        # produces the parquet + spec + empty plan, stages the notebook
# open the printed notebook link on Colab, Runtime > T4 GPU, Run all (NBA_BUDGET=fast)
make colab-status JOB=ctxres_v2_sweep
make colab-pull JOB=ctxres_v2_sweep
uv run python -m research.eval.ctxres_v2_eval --run data/colab/runs/ctxres_v2_sweep/<run_id> \
    --out reports/ctxres_v2_verdict.json

# completion step (after a first run whose candidate lacks 2024 rows)
uv run python -m research.eval.ctxres_v2_eval --stage-completion data/colab/runs/ctxres_v2_sweep/<first_run>
make colab-push JOB=ctxres_v2_sweep   # notebook auto-switches to the completion step
make colab-pull JOB=ctxres_v2_sweep
uv run python -m research.eval.ctxres_v2_eval --run data/colab/runs/ctxres_v2_sweep/<first_run> \
    data/colab/runs/ctxres_v2_sweep/<completion_run> --out reports/ctxres_v2_verdict.json
uv run python -m research.eval.ctxres_v2_eval --clear   # back to a normal sweep for the next push
```

## Amendment A1 — coverage check for discrete stats (registered 2026-10-08, BEFORE any experiment-2 result exists)

Diagnosis from experiment 1 (fg3m): the central-interval check `y in [q10, q90]` is biased upward for
integer outcomes with a point mass at zero (42% of fg3m rows are 0). The candidate's upper tail was
on target (9.3% above q90) while only 4.0% fell below q10, because y cannot be below 0 when q10 = 0.
Interval width was not larger than v1 (2.67 vs 2.58).

For **experiment 2 and later**, the coverage check in the keep rule uses the **randomized PIT**:
for each row, u ~ Uniform(F(y-1), F(y)) from the candidate's predictive CDF (linear interpolation of
the 19-quantile grid, F(-1) = 0, fixed seed); coverage80 = mean(0.1 <= u <= 0.9); pass window
unchanged at 0.75-0.85. Applied to all four stats (all are integer-valued). The naive
`y in [q10, q90]` coverage is still reported alongside, descriptively. All other checks unchanged.
Experiment 1's verdict is NOT revisited under this amendment.

Implementation (`research/eval/ctxres_v2_eval.py`, `cdf_from_q19`, `randomized_pit_coverage80`): F is the
right-continuous linear interpolation of the 19 knots (tied knots resolve to the largest level).
Tail handling: F = 0 below the 0.05 quantile (that 0.05 mass sits AT the knot) and F = 1 above the
0.95 quantile (F at the knot = 0.95); F(-1) = 0 (quantiles are clipped at 0); seed 20261008. Experiment
number is read from `metrics.json` (`experiment_tag` containing `exp2`); `--coverage
{auto,naive,randomized_pit}` overrides. Both coverages are always printed (`naive/rPIT`). On the
experiment-1 runs this check would give rPIT coverage 0.77/0.75/0.73/0.73 (pts/reb/ast/fg3m) vs naive
0.80/0.80/0.84/0.87, i.e. the naive numbers were inflated for every integer stat, not only fg3m;
experiment 1's verdict is not revisited and the original check is still what it reports.

## Amendment A1.1 -- coverage construction chosen by simulation (registered 2026-10-08 14:44, BEFORE any experiment-2 result exists; supersedes the CDF construction of A1)

Why: A1's randomized-PIT with flat tails and an atom at the knots moved NEAR-CONTINUOUS points coverage
down by ~3 points on experiment 1 (0.799 naive vs 0.772), a signature of a biased estimator rather than
of the models. The simulation below confirms it: A1's construction under-covers by 0.035 on average
for continuous-latent grids (up to 0.12-0.68 at some means). The construction is therefore chosen ONLY
by simulation with known truth (`research/eval/ctxres_v2_coverage_sim.py`, seed fixed, n = 50,000 per case);
no experiment-1 or experiment-2 model output was used to choose it.

Harness: truths = Poisson and NegBin (var = mu + alpha mu^2) with mu in {0.3, 0.7, 1.5, 3, 6, 12, 25, 60}
and alpha in {0, 0.15, 0.4} (24 cases per grid type), plus a rounded-Gamma truth (near-continuous at
large mu). The "model" is correctly specified and emits its true 19-quantile grid in three forms:
`exact` (integer quantiles of the integer law), `smooth` (quantiles of the continuous latent Y + U(-.5,.5),
clipped at 0: what the residual / multi-quantile / MLP arms emit), `gamma_round` (moment-matched Gamma
grid, truth rounded). Criterion: |coverage - 0.80| <= 0.01; plus detection of a deliberately too-wide
(spread x1.4 about the median, coverage must exceed 0.84) and too-narrow (x0.7, below 0.76) model.
Estimator for all candidates: Czado-Gneiting-Held non-randomized PIT mass in [0.1, 0.9].

Candidates: (a) A1 (flat tails, atom at knots); (b) linear interpolation + linear tail extrapolation from
the two outer knots, clipped to [0,1], F = 0 below 0; (c1) continuity correction F_cc(y) = F(y+0.5),
F_cc(y-1) = F(y-0.5) with flat tails; (c2) continuity correction with (b)'s linear tails; (d) per-row
parametric NegBin/Poisson matched to the grid by lattice least squares.

| grid | statistic | a flat | b lin | c1 cc+flat | **c2 cc+lin** | d param |
|---|---|---|---|---|---|---|
| smooth | max abs error (24 cases) | 0.119 | 0.119 | 0.026 | **0.012** | 0.033 |
| smooth | mean error | -0.035 | -0.034 | -0.001 | **+0.005** | -0.003 |
| smooth | cases within 0.01 | 7 | 7 | 17 | **22** | 21 |
| gamma_round | max abs error | 0.678 | 0.671 | 0.021 | **0.005** | 0.035 |
| gamma_round | cases within 0.01 | 6 | 6 | 18 | **24** | 12 |
| exact (integer grid) | max abs error | 0.041 | 0.041 | 0.045 | 0.032 | 0.041 |
| exact | cases within 0.01 | 11 | 12 | 11 | 12 | 16 |
| smooth + gamma_round | too-wide > 0.84 (48 cases) | 32 | 32 | 44 | **44** | 41 |
| smooth + gamma_round | too-narrow < 0.76 (48 cases) | 48 | 48 | 44 | **44** | 37 |

c2 by mean (min-max coverage over the three alphas): smooth 0.800-0.812 at every mean; gamma_round
0.796-0.805; exact 0.768-0.779 at mu 0.3, 0.791-0.818 at 0.7, 0.780-0.803 at 3, 0.800-0.815 for mu >= 1.5
otherwise. The 4 missed detections (of 48 each side) are all at mu = 0.3-0.7 where the law is nearly all
mass at 0/1.

Choice: **c2** (continuity-corrected linear interpolation with linear tail extrapolation, floored at 0).
It passes the criterion on gamma_round (24/24) and smooth (22/24; the two misses are 0.812, i.e. 0.002
over) and detects mis-widths in 44/48 each way; the flat-tail constructions fail badly and (d) is more
complex and worse. Simpler (c1) fails more cases. A documented limit: for grids that are exactly integer
(Poisson/NegBin arms' `ppf` quantiles) NO candidate reaches +/-0.01 -- 19 integer knots with ties cannot
reveal the pmf -- so c2's error there is up to 0.032 (mean about 0). If the 2023-selected candidate is such
an arm the coverage verdict is reported with that +/-0.035 uncertainty and cannot by itself decide a
borderline case (the other checks are unaffected).

Implementation (experiment 2+; `metrics.json` `experiment_tag` containing `exp2`, or `--coverage pit`):
`research/eval/ctxres_v2_eval.py::pit_coverage80` / `cdf_from_q19`; check 6 of the keep rule is
`0.75 <= cov80_pit <= 0.85`; naive `y in [q10, q90]` coverage is still reported (`naive/PIT` column).
`--coverage naive` restores the original check; experiment-1 runs keep the original check and its
verdict is not revisited. The A1 implementation note above and its experiment-1 rPIT numbers are
superseded (they came from the flawed construction).


## Experiment 2 — selection record from crashed run 20261008_140943 (recorded 2026-10-08, before any 2024 result exists)

The full-budget run completed every arm and the 2023 selection, then the kernel died while writing
artifacts (likely OOM assembling the combined OOF frame); the artifacts folder is empty, so NO 2024
result from this run was ever seen. 2023 selection ratios (CRPS / recency-Normal CRPS, mean of 4 stats):
xgb_v12_poisson_nb 0.9331 (best), xgb_v12_quantile 0.9384, xgb_v12_prodcfg 0.9472, catboost_v12 0.9482,
xgb_v12_tuned 0.9490, xgb_v12_both 0.9493, xgb_v12_rel 0.9494, xgb_v12_pruned 0.9498, mlp_quantile 0.9519,
xgb_v1_prodcfg 0.9611, v1_prod 0.9622, xgb_v1_tuned 0.9626, glm_poisson_nb 0.9676 (logit_thr: threshold
metric only). Top-3: xgb_v12_poisson_nb, xgb_v12_quantile, xgb_v12_prodcfg.

**The experiment-2 candidate is fixed as `xgb_v12_poisson_nb`** (selected on 2023 only). The rerun exists
to produce 2024 OOF; if its own 2023 selection differs (GPU nondeterminism), both are reported and this
recorded candidate is the one the keep rule is applied to. Note for the coverage check: this arm emits
NegBin ppf (integer) quantiles, so amendment A1.1's documented ~±0.035 coverage uncertainty applies.
