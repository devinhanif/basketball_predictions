# Red team: CTXRES_V3_LEAKFREE (leak-free per-stat hybrid vs production), T102-T113, commit 89f04ee

Date: 2026-10-09. Reviewer: red team (adversarial, CPU-local, seasons <= 2024 only).
Scope: docs/CTXRES_V3_LEAKFREE.md, run `data/colab/ctxres_v3_leakfree/run_20261009`,
`nba/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py`, `nba/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py` (engine),
`nba/props/context_features_v2.py`, `nba/features/opponent_ridge_v2.py`, `nba/eval/ctxres_v3_leakfree.py`, ledger T102-T113.

## Verdict per stat

| stat | claimed 2024 dCRPS (T102-T105) | fair 2024 dCRPS (both arms on integer support) | fair 2023 | verdict |
|---|---|---|---|---|
| pts | -0.0072 [-0.0120,-0.0021] | **-0.0078 [-0.0128,-0.0029]** | -0.0145 [-0.0203,-0.0088] | **WOUNDED** |
| reb | -0.0086 [-0.0108,-0.0064] | **+0.0022 [+0.0000,+0.0045]** (hybrid worse) | +0.0038 [+0.0014,+0.0062] | **BROKEN** |
| ast | -0.0173 [-0.0190,-0.0155] | **-0.0012 [-0.0029,+0.0005]** | -0.0026 [-0.0044,-0.0007] | **BROKEN** |
| fg3m | -0.0249 [-0.0262,-0.0237] | **-0.0019 [-0.0031,-0.0007]** | -0.0040 [-0.0052,-0.0027] | **BROKEN** |

n = 27,583 (2024) and 27,619 (2023) rows per stat. CIs are 95% game-clustered bootstraps (1,000-2,000 resamples).
Delta = hybrid_lf minus v1_prod, so negative means the hybrid is better.

**No leak was found in the features the hybrid uses.** Missingness, planted-future, time-shift and knockout attacks
all pass. One genuine look-ahead was found in `sch_days_to_next` / `sch_b2b_first` during the postseason; its effect on
the result is at most 0.0005.

**The count-stat win is a scoring artifact.** The hybrid's reb/ast/fg3m arm is a negative binomial (NB), so its
199-quantile grid is integer-valued. Production (`v1_prod`) uses continuous conformal quantiles. CRPS on an integer
outcome penalises any predictive CDF that ramps between integers. To remove that, I put production's quantiles on the
integer support with `q_int = ceil(q - 0.5)`. This is the same N-0.5 continuity convention the repo already uses for
P(>= N) (`p_from_grid`), and it moves no threshold probability by more than 0.5/199. That one change alone improves
production by:

| stat | production gain from `ceil(q - 0.5)`, 2024 (CI half-width) |
|---|---|
| reb | -0.0108 (±0.0001) |
| ast | -0.0160 (±0.0002) |
| fg3m | -0.0230 (±0.0003) |
| pts | -0.0043 |

These figures are 92-126% of the claimed count-stat margins. After this fix, reb is worse than production, and ast and
fg3m fall below the -0.005 floor in both seasons.

**pts survives but is wounded.** About 70% of the pts gain comes from the quantile family, not the new features:
- the same XGBoost quantile recipe on v1 features gives -0.0055;
- the v2 features add only -0.0023 [-0.0042,-0.0003].

In 2024 the whole pts gain sits on rows where the player ends up playing under 5 minutes:
- under 5 minutes tonight: -0.112 (n = 2,304);
- 5 or more minutes: +0.0017 [-0.0035,+0.0068].

The pts gain is also weak in other ways:
- It reverses in the 2024 postseason (+0.024 [+0.007,+0.041]).
- It decays through the season.
- It is 33% one team (PHI). Without PHI it is -0.0054 [-0.0103,-0.0004], at the floor.
- Its upper tail is too thin, which explains the worse pts threshold log loss.

**Most likely failure mode if the claim is wrong:** the keep rule compared an integer-valued forecast against a
continuous one with CRPS on integer outcomes. Most of the "replicated" count-stat gain is therefore a support/scoring
convention that production gets with one line of post-processing, not information from the leak-free v2 features. The
pts claim's failure mode is different. It is a lower-tail shape effect on players who barely play (quantile head vs
location-scale conformal), and it does not hold for rotation minutes in 2024 or in the playoffs.

## Setup and reproduction

- **Scratch scripts.** Everything is in
  `/private/tmp/claude-501/-Users-devin-Downloads-nba-prediction/7ff798e7-932b-4807-b103-45d365238231/scratchpad/rt3/`:
  - `common.py`: stages the run's inputs into scratch and calls the run's own engine `load()`.
  - `light1.py`, `light2.py`, `rot.py`, `tllchk.py`: checkpoint-level attacks.
  - `export_box.py`: one read-only DuckDB export of tonight's minutes, starter flag and result, season <= 2024.
  - `miss.py`, `minclf.py`: missingness and information about low minutes.
  - `heavy.py`: walk-forward refits.
  - `an.py`, `pts_slices.py`: scoring.
  - `plant.py`: planted future through the real builders.
  - `prodcmp.py`, `prodcmp2.py`: `v1_prod` vs the production OOF.
  - `locked.sh`: `mkdir data/ops/heavy.lock`, retry every 120 s, `rmdir` on exit.
- **Commands.**
  - Checkpoint-level and light scripts: `uv run --with xgboost python -W ignore <scratch>/rt3/<script>.py`.
  - Refits: `<scratch>/rt3/locked.sh uv run --with xgboost python -W ignore <scratch>/rt3/heavy.py <arms> <stats> [seasons]`.
  - Scoring: `uv run --with xgboost python -W ignore <scratch>/rt3/an.py <arms>`.
  - Planted test: `<scratch>/rt3/locked.sh uv run python -W ignore <scratch>/rt3/plant.py`.
  - About 3.5 h of CPU in total, all under the heavy lock.
- **Reproduction is exact.**
  - The run checkpoints give the ledger deltas to 4 decimals: pts -0.0072, reb -0.0086, ast -0.0173, fg3m -0.0249 (2024); -0.0138, -0.0072, -0.0183, -0.0274 (2023).
  - My refit of `hybrid_lf` reb through the engine (`heavy.py full reb`) is bit-identical to the run's checkpoints (max |dq| = 0).
  - Every refit arm below uses the frozen recipe, params, seed, blocks and rows. Only the stated element changes.
- **`v1_prod` is a faithful stand-in for production.** I compared it against `reports/context_residual/oof_context_residual.parquet`, which stores only 19 quantiles:
  - the q50 correlation is 0.9994-0.9996 per stat;
  - the median |dq| is 0.01-0.22;
  - the production quantiles are continuous too, so they carry the same integer-support handicap.
- **Rules followed.** `nba.duckdb` was opened read-only twice (box export, planted-test loader) and closed. No source, config,
  registry or ledger file was modified. Nothing was committed.

## Attacks

### 1. Shuffled labels within date. FAIL (by construction); the informative null is the feature shuffle

**Label shuffle.** `heavy.py lshuf2_h,lshuf2_v`: one within-date permutation of all four labels is shared by both arms,
and both arms are refit on shuffled labels.

| stat | hybrid - v1 under shuffled labels, 2024, raw | fair (integer support) |
|---|---|---|
| pts | -0.0637 [-0.0695,-0.0581] | -0.0641 [-0.0699,-0.0585] |
| reb | -0.0350 [-0.0373,-0.0325] | -0.0271 [-0.0294,-0.0248] |
| ast | -0.0325 [-0.0344,-0.0305] | -0.0225 [-0.0242,-0.0206] |
| fg3m | -0.0155 [-0.0166,-0.0143] | -0.0034 [-0.0044,-0.0025] |

2023 is similar. The "gain" does not vanish under shuffled labels. With no player signal at all, the hybrid recipe
(quantile regression / Poisson-NB) fits the skewed, zero-heavy marginal much better than production's location-scale
conformal head. So a recipe-vs-recipe CRPS delta measures model form as much as information. This is not a leak (both
arms see the same shuffled labels), but it means the headline comparison cannot be read as "the v2 features add
information".

**Feature shuffle (`shuf`).** All v2 and rg columns are permuted jointly within date; the v1 columns are untouched. This
is the right null for information.

| stat | cost vs hybrid, 2023 | cost vs hybrid, 2024 |
|---|---|---|
| pts | +0.0068 [+0.0047,+0.0088] | +0.0041 [+0.0021,+0.0060] |
| reb | +0.0017 | +0.0044 [+0.0029,+0.0059] |
| ast | +0.0023 | +0.0025 [+0.0014,+0.0035] |
| fg3m | +0.0019 | +0.0007 [+0.0002,+0.0013] |

**Verdict.** The informational part of the claim is the size of the feature-shuffle cost, and it is small for counts.
**PASS** as a leak test. **FAIL** as support for the claim's magnitude.

### 2. Planted future. PASS

`plant.py` rebuilds v1 + v2 (groups a-j) + the leak-free ridge (ref_fixed config, months 2023-12 and 2024-01) through
the real builders (`build_features`, `build_v2_features`, `MonthlyRidge`) twice. The second build changes:
- every box line, minute count (kept > 0), starter flag, final score and possession count of the 2,049 games dated >= 2024-01-15 (randomised);
- plus a planted POST-TIP "Out" report at 23:30 for all 281 player rows of the 11 games on 2024-01-15.

Results:
- **Rows checked:** 150 numeric feature columns over the 40,563 rows dated <= 2024-01-15, including the 11 games on D itself.
- **Every v1, v2 and rg feature the hybrid uses is unchanged.** The only differences are in `vac_*`/`ret_*`, at a max relative difference <= 3.1e-16. That is float summation-order noise.
- **The planted post-tip report was ignored** (`own_status_ord`, `own_listed`, `own_returning` and `ret_*` are unchanged).
- **The test caught the known leak.** The exp-2 `ridge_*` columns, which the hybrid does not use, went NULL for 28 rows on D whose randomised tonight-minutes fell below 5. This validates the test's sensitivity.
- **Controls changed as expected:** `m10_pts`, `pr_usage`, `n48_pts`, `opp_pos_pts` and `game_total_feat` changed on 99-100% of rows dated after D.

I also read the as-of logic of every group line by line:
- **a** (absence history from PRIOR box rows; tonight's report only): clean.
- **b** (returning teammates, `join_asof` at game_date - 1 day): clean.
- **c** (`ew_pre_post` "pre" sums; league prior via a 1-day-shifted cumulative sum): clean.
- **e** (defender's earlier games only): clean.
- **f** (`asof_total_features` emits before the day's update): clean.
- **g**, **h**: static or published facts.
- **j** (pre sums): clean.
- **rg** (fit on rows dated before the refit month's first day; predicted for every row regardless of minutes): clean.
- **d** is the one exception (next).

**Look-ahead found in group d.** `schedule_features` sets `sch_days_to_next` and `sch_b2b_first` from the next game that
actually exists in `games`. In the postseason, whether a next game exists depends on tonight's result.

| game type | dataset | rows with `sch_days_to_next == 7` |
|---|---|---|
| play-in | `ctxres_v2.parquet` | 69% of losses vs 0% of wins |
| playoffs | `ctxres_v2.parquet` | win rate 0.197 (n = 588) vs 0.535 (n = 4,621) for the rest |
| regular season | `ctxres_v2.parquet` | 2.0% of losses vs 1.8% of wins (no leak) |

The neutralised arm `fixsched` sets both columns to NaN on postseason rows and on each team's last regular-season game
(6,498 rows, train and test). It moves the result by at most 0.0005:

| stat | `fixsched` vs hybrid, 2023 | 2024 |
|---|---|---|
| pts | +0.0002 | -0.0003 |
| reb | -0.0003 | +0.0001 |
| ast | +0.0002 | +0.0003 |
| fg3m | -0.0000 | -0.0002 |

It is a real leak in the feature, immaterial to this claim, and should be fixed (recommendation 3).

### 3. Missingness leak. PASS

`miss.py` covers all 137 model columns over 80,312 rows. Groups:
- tonight's minutes < 5 and < 10;
- starter tonight;
- team won;
- |margin| >= 20;
- stat above median, and stat == 0, for each of the 4 stats.

The flag is |NULL-rate gap| >= 0.1 with both groups >= 200 rows. I also checked the standardised label gap between
NULL and non-NULL rows, and sentinel-zero gaps.

- **Only flag:** `yrs_since_draft`. Its NULL rate gap is 0.161 by starter, and its label gap is -0.56 sd for pts. It is NULL when `draft_year` is missing (a static player fact) and does not depend on tonight.
- **Next largest:** the leak-free ridge `rg_*`, with a NULL gap of at most 0.028 (warm-up months) and a label gap of at most 0.04 sd.
- **NULL-indicator-only test:** a logistic classifier for tonight's minutes < 5, trained on seasons <= 2023 and scored on 2024, has AUC 0.577.
- **Sentinel zeros:** the largest gaps are in the absence-history columns (`absent_prev`, `abs_streak_prev`, `since_return`, `own_returning`), at 0.37 by minutes < 5. These are prior-game facts (a deep-bench player who was DNP-CD last game). Line-by-line audit above: clean.
- **Information about low minutes (`minclf.py`):** the v1 features alone predict tonight's minutes < 5 with AUC 0.932. Adding every v2 group adds +0.0020 AUC (+0.0014 from group a, the legitimate pre-tip status). No group carries hidden tonight-minutes information.

### 4. Feature-group knockout. PASS (no concentration), with an important negative

`heavy.py drop_<g>`, every group, counts both seasons, pts 2024. Largest cost of dropping a group, vs the full hybrid:

| stat | largest knockout cost | season |
|---|---|---|
| reb | +0.0019 (drop_e), +0.0018 (drop_rg) | 2024 |
| ast | +0.0016 (drop_a) | 2023 |
| fg3m | +0.0005 (drop_e) | 2023 |
| pts | +0.0006 (drop_a) | 2024 |

No group carries the gain because there is little feature gain to carry. Decomposition by recipe and feature set
(2024 vs `v1_prod`):

| arm | pts | reb | ast | fg3m |
|---|---|---|---|---|
| hybrid recipe, v1 features only (`v1only`), raw | -0.0055 | -0.0060 | -0.0156 | -0.0240 |
| hybrid minus `v1only` (the v2 + rg feature value) | -0.0023 [-0.0042,-0.0003] | -0.0026 [-0.0042,-0.0009] | -0.0016 [-0.0027,-0.0007] | -0.0009 [-0.0015,-0.0004] |
| production recipe with all 137 features (`prodfull`), raw | -0.0008 [-0.0042,+0.0027] | -0.0026 [-0.0040,-0.0011] | -0.0002 [-0.0013,+0.0010] | -0.0004 [-0.0011,+0.0003] |

In 2023, hybrid minus `v1only` is -0.0079 (pts), -0.0015 (reb), -0.0017 (ast) and -0.0012 (fg3m). `prodfull` in 2023 is
-0.0075 (pts), -0.0013 (reb), -0.0019 (ast) and -0.0012 (fg3m).

On pts rows with minutes < 5 tonight, `drop_a` costs +0.0205 [+0.0158,+0.0247]. Group a (own status and absence
history) carries the feature part of the low-minute gain. Its as-of logic was audited (attack 2) and is clean, subject
to the 19:00 ET report proxy. pts rows with tip < 19:00 show no concentration: -0.0064 [-0.0221,+0.0112] vs -0.0073 at
>= 19:00.

### 5. Time shift. PASS

`lag1` replaces every v2 and rg value with the player's previous row's value (one game staler). Cost vs the hybrid:

| stat | 2023 | 2024 |
|---|---|---|
| pts | n/a | +0.0016 [+0.0001,+0.0031] |
| reb | +0.0008 | +0.0019 |
| ast | +0.0017 | +0.0016 |
| fg3m | +0.0009 | +0.0001 |

Every cost is smaller than the corresponding feature-shuffle cost. There is no sign of information too close to tip.

### 6. Baseline fairness. FAIL: this is where the claim breaks

**Same rows, blocks and training window.** Both arms are fit on all rows strictly before each month block, on identical
rows. The calibration windows are comparable: production's conformal window is the newest 15%, and the NB dispersion
window is also the newest 15%.

**Things the hybrid gets that production lacks:**
- **(a) Integer-support output for count stats.** This is decisive; see the table below.
- **(b) Tuned hyperparameters.** `base_config.json` params come from the exp-2 Optuna search `search_v12` (15 completed trials), selected on 2023 blocks 202310, 202401 and 202404 with the LEAKY exp-2 feature set. Production gets fixed defaults. 2023 is therefore not an independent "consistency gate" for the hybrid.
- **(c) Early stopping up to 600 rounds** vs production's fixed 200.

Effect of (a): both arms on integer support, `q_int = ceil(q - 0.5)`.

| stat | season | raw claim | `v1_prod` integer-support gain | fair delta | raw gain explained by support |
|---|---|---|---|---|---|
| pts | 2024 | -0.0072 | -0.0043 | -0.0078 [-0.0128,-0.0029] (hybrid also discretised) | n/a (both continuous) |
| reb | 2024 | -0.0086 | -0.0108 | +0.0022 [+0.0000,+0.0045] | 126% |
| ast | 2024 | -0.0173 | -0.0160 | -0.0012 [-0.0029,+0.0005] | 92% |
| fg3m | 2024 | -0.0249 | -0.0230 | -0.0019 [-0.0031,-0.0007] | 92% |
| reb | 2023 | -0.0072 | -0.0110 | +0.0038 [+0.0014,+0.0062] | - |
| ast | 2023 | -0.0183 | -0.0157 | -0.0026 [-0.0044,-0.0007] | - |
| fg3m | 2023 | -0.0274 | -0.0234 | -0.0040 [-0.0052,-0.0027] | - |

How the discretisation was chosen and checked:
- `round(q)` is identical to `ceil(q - 0.5)`. `floor(q)` and `ceil(q)` are worse for production in both seasons, and `ceil(q - 0.5)` was also the best of the four on 2023.
- The transform was not tuned. It is the continuity convention `p_from_grid` already uses.
- It changes production's P(>= N) by at most 0.0025 and its threshold log loss by at most +0.0001 (`tllchk.py`).

### 7. Replication. PASS for the artifact and for pts direction; pts margin is thin

**Seed** (`seed2`, XGBoost seed 777): vs the hybrid, pts +0.0001 (2024), reb -0.0001/+0.0006, ast +0.0004/+0.0007,
fg3m +0.0002/0.0000.

**Season swap.** The fair deltas have the same sign in 2023 and 2024 for every stat (table at top).

**Drop the best month (fair deltas):**

| stat | 2023 | 2024 |
|---|---|---|
| pts | -0.0098 | -0.0058 [-0.0116,-0.0002] |
| reb | +0.0045 | +0.0032 |
| ast | -0.0017 | -0.0003 |
| fg3m | -0.0033 | -0.0013 |

**Drop the best team (fair deltas, 2024):**
- pts, without PHI (1610612755, 33% of the 2024 gain): -0.0054 [-0.0103,-0.0004];
- ast, without BKN (61% of its small 2024 gain): -0.0005.

**pts by month (fair, 2024):**

| Oct | Nov | Dec | Jan | Feb | Mar | Apr | May | Jun |
|---|---|---|---|---|---|---|---|---|
| -0.012 | -0.013 | -0.018 | -0.017 | +0.003 | -0.005 | +0.004 | +0.018 | +0.037 |

### 8. Too good to be true. Flag raised and explained

The raw fg3m claim (-0.025 vs production) is 2.4 times production's own confirmed fg3m gain over the recency baseline
(-0.0102, T095). A candidate that beats production by more than production beat the naive baseline, on the noisiest
count stat, is the "too good" fingerprint. Attack 6 explains it.

pts slices (fair):
- y == 0: -0.160 (2024; 11.6% of rows);
- y > 0: +0.012 [+0.007,+0.018];
- 2024 postseason: +0.024.

On a pre-tip rotation slice (min10 >= 20, where prop lines exist), the fair deltas are:

| stat | 2024 | 2023 |
|---|---|---|
| pts | -0.0076 [-0.0138,-0.0009] | -0.0127 |
| reb | -0.0001 | +0.0004 |
| ast | -0.0022 [-0.0046,+0.0002] | -0.0033 |
| fg3m | -0.0033 [-0.0051,-0.0015] | -0.0063 |

### 9. Holdout hygiene. PASS

- The runner refuses season >= 2025 (`LeakStop`).
- `ctxres_v3_with2025.parquet` was last accessed on 2026-10-08 at 19:13:11, 2 s after it was written. That is before this run (2026-10-09 01:09) and before the void of the exp-3 touch. It was never read afterwards.
- `HOLDOUT_ACCESS_LOG.md` has no new row. That is correct, because no 2025 touch happened; the draft row stays a draft.
- All my exports filter `season <= 2024`.
- Process note, already disclosed in the doc: the pre-registration was fingerprinted (sha256 d684efd1...), not committed, before the run.

## Focus questions

**(6) Why is fg3m the largest gain, and is it distributional or informational?** Distributional, and mostly a support
artifact. fg3m has the largest zero pile (42.3% of rows are 0):
- Only 16.6% of production's quantile grid sits at <= 0. The rest of its low quantiles are fractional values in (0, 1).
- The NB grid puts 40.4% at 0.

CRPS charges a continuous forecast for every fraction of a 3-pointer between integers that cannot occur. Splitting the
fair delta by outcome shows the trade-off:
- y == 0: hybrid worse, +0.0115 [+0.0098,+0.0134];
- y > 0: hybrid better, -0.0117.

The informational part is -0.0009 (2024; hybrid minus `v1only`). The genuine distributional part shows up where it
should: threshold log loss at fg3m >= 1 improves (-0.0076 [-0.0099,-0.0054]), because NB P(0) is better calibrated.
That part is real but small.

**(7) Why does threshold log loss get worse for pts and reb?**
- **Thresholds are blind to the CRPS artifact.** Threshold events sit at N - 0.5, so within-integer placement of
  quantile mass is invisible to them. The integer-support advantage that drives the count-stat CRPS win cannot show up in
  threshold log loss. A CRPS win without a threshold-log-loss win is the artifact's signature.
- **pts: the upper tail is too thin.** The quantile arm extrapolates linearly from the q0.90-q0.95 spacing (`ext_tails`).
  - Calibration: P(y <= q0.99) = 0.973 for the hybrid vs 0.992 for production.
  - Spread: the hybrid's predictive variance is 30.7 against a realised MSE of 35.4; production's is 33.9.
  - Threshold log loss deltas by threshold (2024): >=10 -0.0019; >=15 +0.0011; >=20 +0.0029; >=25 +0.0038 [+0.0020,+0.0057]; >=30 +0.0017.
- **reb: the NB is slightly under-dispersed in the upper tail.**
  - The hybrid's P(>= N) sits below the base rate at every threshold: 6+ 0.273 vs 0.280; 8+ 0.147 vs 0.153; 10+ 0.079 vs 0.084.
  - Calibration: P(y <= q0.95) = 0.943 and P(y <= q0.90) = 0.893.

## Recommendations (not applied; maintainer decision)

1. **Do not carry reb/ast/fg3m into a 2025 touch on this evidence.** Restate T102-T105 with the fair deltas as new
   ledger rows. Do not edit the old rows.
2. **Make integer support part of the comparison rule.** For count stats, compute CRPS on integer support for every arm
   (equivalently, RPS over integer thresholds). Alternatively, require non-inferior threshold log loss. Run the
   production model's own `ceil(q - 0.5)` post-processing as its own small pre-registered experiment. It is a free
   2024 CRPS gain of -0.011 (reb), -0.016 (ast), -0.023 (fg3m) and -0.004 (pts), with P(>= N) unchanged to 0.0025.
3. **Fix the schedule look-ahead in `context_features_v2.schedule_features`.** Next-game features must come from the
   schedule as published before tip. Set them NULL in the postseason and for each team's last regular-season game, or
   source them from the cached schedule snapshot.
4. **If pts is pursued, pre-register a narrowed claim:** pts only, an integer-support CRPS comparison, the
   regular season, and an upper-tail fix. Calibrate P(>= 20/25/30) or replace the linear tail extrapolation. Treat 2023
   as tuned-on, not as an independent gate.
5. **Add the label-group NULL check (threshold 0.1) to `datamanifest check`.** It passes here, but it is cheap, and the
   played/unplayed check alone missed the original leak at the label view.
