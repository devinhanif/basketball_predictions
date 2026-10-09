# Context-residual v3 leak-free (experiment 3, leak-fixed) -- PRE-REGISTRATION of a CPU replication

Status: written 2026-10-09 BEFORE any arm of this experiment was fit or scored. Everything above the marker
line `<!-- PREREG-END -->` is the pre-registration; its sha256 (text up to and including the marker) is
computed by the runner at start-up and stored in the run header, so an edit after the fact is detectable.
Results are appended BELOW the marker only. A negative result is reported as a negative result.

Supersedes the voided `docs/CTXRES_V3.md` design (its feature set contained the leaky `ridge_*` columns; that
experiment was never run and its 2025 touch was never logged as used). This document keeps the per-stat hybrid
idea and replaces the features.

## 1. What this is, and what it is NOT (honesty statement)

* **2024 has already been SEEN for these feature sets and this exact recipe.** `docs/RIDGE_V2.md` section 9
  (descriptive follow-up) reports the leak-free v2 feature set (`ref_fixed`: pts quantile arm, reb/ast/fg3m
  Poisson/NB arm, fixed exp-2 params) against production `v1_prod` on identical 2024 rows:
  pts -0.0074 [-0.0122, -0.0025], reb -0.0081 [-0.0103, -0.0058], ast -0.0164 [-0.0182, -0.0146],
  fg3m -0.0251 [-0.0264, -0.0239] (CRPS delta, negative = leak-free v2 better). The per-stat composition
  (quantile for pts, count for the rest) was chosen from exp-2 2023 AND 2024 leaderboards that were both visible.
  The model here IS the `ref_fixed` arm of the ridge_v2 job (same columns, params, recipe).
* Therefore the 2024 numbers produced here are a **REPLICATION / SANITY CHECK** (independent re-fit on CPU with a
  different XGBoost build, a locally re-fit `v1_prod`, the full exp-2 decision rule applied cleanly), **NOT a
  confirmation**. Passing the rule on 2024 cannot upgrade the claim to "confirmed".
* **Selection, if any, is on 2023 only.** There is nothing to select among (one frozen recipe), so 2023 is used as
  a second, independent-season consistency gate (section 5), not for tuning.
* **Confirmation can only come from** (a) a single pre-registered season-2025 holdout touch that the MAINTAINER
  must approve and log in `docs/HOLDOUT_ACCESS_LOG.md` BEFORE running (draft row in section 9, NOT appended), or
  (b) the 2026-27 forward log. **Season 2025 is not loaded, not read and not touched by this experiment**: the
  loader refuses any season >= 2025 and the runner never opens the `ctxres_v3_with2025.parquet` export.
* Not part of this experiment: any search, retuning, new feature, calibrator, or any registry registration /
  promotion. It is not a model-family comparison; it asks one question about one frozen recipe.

## 2. Hypothesis and frozen recipe

H: the leak-free per-stat hybrid (`hybrid_lf`) has lower CRPS than production `v1_prod` on identical played rows
of 2023 and 2024, per stat, by at least the practical floor.

Features (per stat): v1 features + v2 groups a-h and j from `data/colab/ctxres_v2/feature_spec.json`
(`v1_features` + `v2_features` minus group (i)), plus the two LEAK-FREE ridge columns of the `ref_fixed` definition
of `data/colab/ridge_v2/ridge_v2_spec.json`: `rg_f3b8321a_p_<stat>` and `rg_f3b8321a_o_<stat>` (the experiment-2
opponent-adjusted ridge, alpha_p = alpha_o = 50, evaluated for EVERY candidate row regardless of tonight's
minutes). The leaky exp-2 columns `ridge_player_<stat>` / `ridge_opp_<stat>` (NULL iff tonight's minutes < 5) are
excluded. Test: no feature name starting with `ridge_` may appear in any arm's list (names `rg_*` only).

Models (frozen, `data/colab/ridge_v2/base_config.json`, copied from exp-2 run 20261008_171446 `search_v12`):

| stat | family | recipe |
|---|---|---|
| pts | quantile | XGBoost `reg:quantileerror`, 19 levels 0.05..0.95, target = stat minus as-of recency mean, linear tail extrapolation to the 199-grid, clip >= 0 |
| reb, ast, fg3m | poisson_nb | XGBoost `count:poisson` mean; NegBin dispersion by position group on the newest 15% calibration window, shrunk to the pooled alpha (k = 300) |

XGBoost params: learning_rate 0.0558198665255319, max_depth 3, subsample 0.7168179865008671, colsample_bytree
0.7144428763045745, min_child_weight 116.4036974504763, reg_alpha 0.17402858001416036, reg_lambda
4.138732643418021; n_estimators cap 600, early stopping 20 rounds on the time-ordered newest 12% of the fit window;
seed 20261008. CPU `hist` (the original ridge_v2 run was on an L4 GPU and a different XGBoost build; CPU XGBoost
3.4.1 is what this run uses; small prediction differences vs the stored GPU run are expected and are reported,
descriptively, as a replication diagnostic).

Position is a categorical code (`pos_code`). Tree arms may split on it. **Any linear / MLP arm must use one-hot
position, never the numeric code**: `nba.eval.ctxres_v3_leakfree.linear_design` enforces this and is tested. The
minimal run contains no linear / MLP arm (they are not part of the per-stat hybrid), so none is run.

## 3. Arms

Primary / decision:
* `hybrid_lf` -- pts <- quantile family, reb/ast/fg3m <- poisson_nb family, features of section 2.
* `v1_prod` -- the production recipe re-fit locally in the same blocks: LightGBM (n_estimators 200, learning_rate
  0.04, num_leaves 15, min_child_samples 100, colsample 0.8, subsample 0.8) residual mean head + |residual| head on
  v1 features, split-conformal quantiles from the newest-15% window (a port of the exp-2 sweep `resid_block`
  with `kind="v1"`, `engine="lgb"`; the same comparator the exp-2 keep rule used). Same rows, same blocks.

Descriptive only (never in the rule): `recency_normal` (as-of recency mean / std Normal); `alt_lf` (the OTHER
family per stat: count arm for pts, quantile arm for reb/ast/fg3m) to show whether the composition matters;
comparison of the CPU `hybrid_lf` 2024 OOF against the stored GPU `ref_fixed` OOF of run `20261008_194547` (per-row
CRPS difference, clustered CI); the stored exp-2 `v1_prod` 2024 OOF vs the local re-fit.

Walk-forward: calendar-month blocks of seasons 2023 and 2024 (warm-up season 2022 is training only); each block is
predicted by models fit on all played rows (>= 5 prior played games, as exported) strictly before the block's
first date; newest 15% of the fit window is the calibration window. Same configuration for every block. Expected
rows (played): 2023 27,619, 2024 27,583 per stat.

## 4. Data guards (STOP conditions)

1. Static allow-list + deny-regex at load (`audit_names`: v1/v2 allow-list or an `rg_<hash>_...` ridge_v2 name),
   plus the explicit assertion that no used feature name starts with `ridge_` (tested).
2. **Missingness audit of the FINAL feature frame** (the exact columns fed to the models, written to a parquet) with
   `nba.datamanifest` machinery against `player_game_stats` (read-only DB, held only for the audit): any column
   whose NULL rate differs between played (tonight's minutes >= 5) and unplayed rows by >= 0.5
   (`configs/datamanifest.yaml thresholds.asymmetry`) is a STOP: the runner raises before any fit. The
   in-run label audit of `ridge_v2_sweep.null_label_audit` (null-vs-nonnull label gap < 0.35 sd, |rank corr with
   same-game label| < 0.95) also must pass.
3. Planted-future test (unit): a perturbation of labels in a later block must not change any earlier block's
   predictions; a feature equal to a same-game label is rejected by the allow-list.
4. Season >= 2025 present in the input -> raise.

Known limitation (applies to candidate and comparator alike): the v1/v2 injury-report features are gated with
the 19:00 ET tip proxy (`nba/props/context_features_v2.py`), not the real tip time; this can mildly flatter both
arms (red-team estimate about 0.003 pts CRPS for the production model) and cancels in the paired delta only to the
extent both arms use the report features identically. Not changed here (no new features).

## 5. PRE-REGISTERED DECISION RULE (exp-2 rule, unchanged; applied once per season)

Rule code: `nba.eval.ctxres_v2_eval.keep_rule(..., coverage="pit")` (Amendment A1.1). Candidate `hybrid_lf` vs
reference `v1_prod`, identical rows (inner join on game_id, player_id), per stat, per season. A stat PASSES a season iff:

1. mean CRPS delta (hybrid_lf - v1_prod) <= -0.005 (floor);
2. game-clustered 95% bootstrap CI (2,000 resamples) upper bound < 0;
3. clustered two-sided p-values survive Benjamini-Hochberg across the 4 stats (m = 4), q = 0.05;
4. no slice (teammate-out report status, minutes-change bucket, first-15 vs rest team games, starter vs bench;
   n >= 300) has a CRPS delta > +0.01;
5. |mean bias| <= 0.5;
6. A1.1 continuity-corrected PIT coverage of the 80% interval in 0.75-0.85 (the naive `y in [q10, q90]` coverage
   is reported alongside; for the integer count arm the +/-0.035 estimator uncertainty applies and cannot by
   itself decide a borderline case).

plus: the run's leak guards (section 4) passed.

Classification (computed by the evaluator, not by hand):
* **REPLICATES (stat)**: the stat PASSES on 2024 (the replication season) AND its 2023 clustered delta CI upper
  bound is < 0 (the selection season shows the same direction). Season 2023 is evaluated with the same rule code
  but its BH family is the 4 stats of 2023 alone; only the CI-direction part of the 2023 result is used in the
  classification, the other 2023 checks are reported.
* **REPLICATES (overall)**: all four stats replicate. Otherwise report per stat; no composition re-selection.
* Even REPLICATES overall is labelled "replication of a previously seen result", never "confirmed", and does NOT
  authorize registration or promotion (model-gate is a separate decision).

Decision-bearing numbers are exactly: the 4 per-stat 2024 deltas + CIs + BH, bias, PIT coverage, slice maxima, and
the 4 per-stat 2023 CI upper bounds. Everything else (threshold log loss / Brier / ECE at the exp-2 thresholds
pts 10/15/20/25/30, reb 4/6/8/10, ast 2/4/6/8, fg3m 1/2/3/4; per-block CRPS; alt_lf; recency; GPU-vs-CPU
diagnostics) is DESCRIPTIVE. Multiplicity: BH over m = 4 per season; the two seasons are not pooled (2024 is a
replication, 2023 a consistency gate).

Power note: n ~ 27.6k clustered rows; exp-2's 80%-power minimum detectable CRPS effects were pts 0.0099, reb 0.0054,
ast 0.0031, fg3m 0.0021. The pts floor (0.005) is below the pts MDE, so a pts effect near -0.007 can be missed.

## 6. Compute plan, scheduling and what may be cut

CPU only (6 cores, 8 GB RAM shared with other jobs). Before any heavy fit the runner takes
`mkdir data/ops/heavy.lock` (retries every 120 s, `rmdir` in a `finally`). Work is checkpointed per
(variant, stat, block) as small npz files; per-variant OOF parquet files are streamed one at a time (no giant
frame); a re-run resumes. `nba.duckdb` is opened read-only only for the missingness audit and closed.
Order and cut priority (later items are cut first if the ~5 h CPU budget would be exceeded; any cut is documented
in the results): 1 `v1_prod` + `hybrid_lf` (the minimal pre-registered set: pts quantile, reb/ast/fg3m NB, plus
the production baseline on identical rows) ; 2 `recency_normal`; 3 `alt_lf`; 4 GPU-vs-CPU diagnostic (needs no
fit). Estimated (from the L4 timing 214 s for `ref_fixed`) well under the budget; not measured on CPU yet.

## 7. Outputs

`data/colab/ctxres_v3_leakfree/` (run directory, not committed), `reports/ctxres_v3_leakfree.md`, rows in
`docs/TEST_LEDGER.md` (next free T-numbers, appended at write time), a results section below the marker. No
registry registration or promotion.

## 8. What each outcome means

* REPLICATES overall: the leak-free hybrid reproduces a gain over production on two seasons already seen. It is
  worth ONE pre-registered 2025 touch (section 9) and a forward 2026-27 shadow log, nothing more.
* Some stats replicate: report per stat. The pts gain (leak-dominated in exp-2) is the likeliest failure.
* None: v1 stays; the exp-2 v2 advantage was a leak artifact; stop pursuing v2 features.

## 9. DRAFT HOLDOUT_ACCESS_LOG row (NOT appended; maintainer approval required)

| date | run_id / command | model family | stat(s) | mode | result summary |
|---|---|---|---|---|---|
| DRAFT (to be dated on approval) | `<runner> --score-2025 --prereg-id EXP3LF-2025-<first 8 hex of the sha256 of this file's pre-registration text>` (procedure frozen at the commit that adds this row; recipe of `docs/CTXRES_V3_LEAKFREE.md`) | props: leak-free per-stat hybrid `hybrid_lf` (pts quantile; reb/ast/fg3m Poisson/NB; v1 + v2 a-h,j features + leak-free `rg_f3b8321a_*` ridge columns; ridge_v2 job `ref_fixed` definition) vs production `v1_prod` refit identically | pts, reb, ast, fg3m | **First 2025 touch of THIS model** (the feature set and recipe were never evaluated on season=2025; 2022-2024 were seen, see section 1, and the project-wide 2025 season is not virgin per the FDR audit; the touch is clean for this model only; `v1_prod` is a comparator refit) | PRE-REGISTERED before running. Month-block walk-forward over 2025, each block fit on all played rows strictly before it (2022-2024 plus earlier 2025 months); frozen configs; metric = paired per-row CRPS delta vs `v1_prod`, played rows with >= 5 prior played games, game-clustered 95% CI (2,000 resamples), exp-2 keep rule (floor -0.005, CI upper < 0, BH m = 4, slices <= +0.01, |bias| <= 0.5, A1.1 PIT coverage 0.75-0.85); the injury-report features inherit the 19:00 ET tip PROXY (about 7.7% of games tip at or before 17:00, red-team 2026-10-09); this applies equally to `v1_prod` (same report features) and is a stated limitation of every number here, not a fixed one. CONFIRM per stat iff all checks pass; otherwise reported as not confirmed. No tuning on the result. |

<!-- PREREG-END -->

## Results (appended after the run)

Run `run_20261009` (`data/colab/ctxres_v3_leakfree/`, CPU, XGBoost 3.4.1, LightGBM 4.7.0, 4 threads, seed
20261008), 18 monthly blocks (2023-10 .. 2025-06 calendar, seasons 2023 and 2024), scored by
`uv run python -m nba.eval.ctxres_v3_leakfree --run-dir data/colab/ctxres_v3_leakfree/run_20261009`.
Full report: `reports/ctxres_v3_leakfree.md`. Pre-registration fingerprint (sha256 of the text above the marker, taken
before the first fit and stored in `run_header.json`): `d684efd134082e20606d00de189efd0d23088011b6ab4a1c9ec2d29ced5a5183`.
Process deviation: the working-tree rules of this run forbid committing, so the pre-registration was fingerprinted
rather than committed before the run; the maintainer should commit this file (the hash is over the unchanged text
above the marker, so it can be re-verified with `nba.eval.ctxres_v3_leakfree.prereg_sha256`).
Guards: static allow-list passed; no `ridge_*` column loaded (137 feature columns); missingness audit of the final
feature frame (80,312 rows; 73,877 played / 6,435 unplayed): max played/unplayed NULL asymmetry 0.157
(`yrs_since_draft`, the known static draft fact; leak-free ridge columns 0.027) << 0.5, no STOP; null-vs-label audit
max gap 0.04 sd. Nothing was cut: all four variants (`v1_prod`, `hybrid_lf`, `recency_normal`, `alt_lf`) completed
(about 59 min of CPU in total).

**Pre-registered classification: all four stats REPLICATE** (pass the full exp-2 rule on 2024 and 2023 CI upper < 0).
This is a replication of a previously seen result, not a confirmation.

| stat | 2024 dCRPS vs `v1_prod` [95% CI], n = 27,583 | BH q | bias | PIT cov80 | slice max | 2023 dCRPS [95% CI], n = 27,619 |
|---|---|---|---|---|---|---|
| pts | -0.0072 [-0.0120, -0.0021] | 0.0070 | +0.025 | 0.791 | no slice > +0.01 | -0.0138 [-0.0196, -0.0080] |
| reb | -0.0086 [-0.0108, -0.0064] | 0.0007 | +0.038 | 0.785 | none | -0.0072 [-0.0096, -0.0047] |
| ast | -0.0173 [-0.0190, -0.0155] | 0.0007 | +0.054 | 0.789 | none | -0.0183 [-0.0202, -0.0163] |
| fg3m | -0.0249 [-0.0262, -0.0237] | 0.0007 | +0.020 | 0.793 | none | -0.0274 [-0.0287, -0.0262] |

Replication diagnostics (descriptive): the local `v1_prod` is bit-identical to the stored exp-2 `v1_prod` OOF (CRPS
delta 0.00000 on all four stats); the CPU `hybrid_lf` vs the stored GPU `ref_fixed` OOF differ by at most 0.00085 CRPS
(pts +0.0003, reb -0.0005, ast -0.0009, fg3m +0.0002), and the 2024 deltas above match the RIDGE_V2 follow-up
(-0.0074, -0.0081, -0.0164, -0.0251) to within 0.001. So the earlier descriptive follow-up reproduces on CPU.

Honest cautions (descriptive, post-hoc, not in the rule; ledger T110-T113):

* **Threshold log loss does not follow CRPS for pts and reb in 2024**: hybrid - production = pts +0.0015
  [+0.0007, +0.0024] (worse), reb +0.0011 [+0.0001, +0.0021] (worse), ast -0.0007 [-0.0016, +0.0002], fg3m -0.0028
  [-0.0038, -0.0019] (better). In 2023 the pts/reb threshold log loss deltas are null (0.0000 and +0.0004). The
  parlay use (products of P(>= N)) cares about these probabilities; the CRPS gain on pts/reb is therefore not a
  parlay-probability gain. Raw ECE: pts 0.0048 vs 0.0060 (hybrid better), reb 0.0064 vs 0.0038 (hybrid worse),
  ast 0.0072 vs 0.0061, fg3m 0.0079 vs 0.0137.
* **pts gain decays across the 2024 season**: per-block dCRPS is -0.011 .. -0.018 for Oct-Jan, then +0.003 (Feb),
  -0.005, +0.005, +0.019 (May, n = 842), +0.038 (Jun, n = 159). Largest gain in the first-15-team-games slice
  (-0.0131). A pts gain concentrated early in a season is plausible (the ridge and v2 features carry more
  information before the recency baseline stabilises) but a late-season sign flip is a drift warning for the pts head.
* pts is the weakest cell: its 2024 CI upper bound (-0.0021) is close to 0, its floor margin is small
  (-0.0072 vs -0.005), and the stat is below the 80%-power MDE (0.0099). It passes, but the pts claim is the one most
  likely not to survive a new season.
* Composition: `alt_lf` is worse than `hybrid_lf` on every stat in both seasons (2024: pts +0.0675, reb +0.0058,
  ast +0.0131, fg3m +0.0096; 2023 reb +0.0020 [-0.0002, +0.0040] is a tie), so the per-stat family choice is supported.
* Both arms beat the recency baseline strongly (2024 hybrid pts -0.119, reb -0.052, ast -0.050, fg3m -0.042).
* Bias and PIT coverage are within the rule everywhere; the naive 80% coverage of the count arm is 0.87-0.94
  (integer quantile grid, expected; A1.1 PIT corrects for it).

No linear / MLP arm was run (not part of the hybrid), so the one-hot-position guard (`linear_design`) was unit-tested
but not exercised on a real arm. Season 2025 was not loaded. Nothing was registered or promoted.

## Recommendation

1. Treat this as: the leak-free v2 feature set + per-stat hybrid beats production CRPS on two seasons already seen,
   by -0.007 .. -0.025. The earlier leak (-0.07 pts) is gone, and the true margin is small for pts.
2. Do not promote. The only valid confirmations are (a) ONE maintainer-approved 2025 holdout touch (draft row in
   section 9 of this document; it must be appended to `HOLDOUT_ACCESS_LOG.md` and the job re-run in 2025 mode BEFORE
   anything is scored; the 2025 loader path does not exist in this runner by design) or (b) the 2026-27 forward log
   (add `hybrid_lf` as a shadow comparator alongside production).
3. Before any 2025 touch consider restricting the claim to reb/ast/fg3m (pts is the thinnest and shows late-season
   decay and a threshold-log-loss regression); that restriction must be decided BEFORE the touch, in the new row.
4. If used for parlays, calibrate the count heads' threshold probabilities (ECE is not better than production on
   reb/ast) in a separate pre-registered step; the CRPS win alone does not establish better P(>= N).

