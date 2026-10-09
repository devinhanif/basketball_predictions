# Small backlog items, 2026-10-08

Three independent items. Each rule below was written BEFORE the item was run and is
repeated verbatim in the module docstring. Seasons < 2025 only (season 2025 is never read).
All CIs are 95% bootstraps (2000 resamples, seed 0) resampling whole games.
The results sections are appended after the pre-registration sections; the rules are not edited.

Entrypoint (all three): `uv run python -W ignore -m nba.eval.backlog_small_eval --db nba.duckdb --item all`

## PRE-REGISTRATION

### Item 1: draft-slot rookie minutes prior (`nba/props/rookie_minutes.py`)

* Rookie row: season `s` in {2023, 2024}; the player's first played game in the DB is in `s`
  (knowable as-of: no earlier played game exists); undrafted (both draft fields NULL) or
  `0 <= s - draft_year <= 3` with a known pick; and `n_played_prior < 40` (N = 40).
  Everything else is untouched by construction (asserted: predictions byte-equal).
* Prior: ridge (alpha by 5-fold CV over a fixed grid) on standardised `ln(pick)` (undrafted = ln 61),
  undrafted flag, age on 1 Oct of the season, height, weight, primary-position dummies; target = a
  rookie's first-season mean minutes per played game, trained only on rookies with >= 5 played games
  from classes strictly before the evaluated class (walk-forward by class: 2023 <- class 2022;
  2024 <- classes 2022, 2023). Known bias: the >= 5 game filter keeps rotation survivors.
* Blend: `mu_new = mu_current + k/(n+k) * (prior - default_mu)` with `n = n_played_prior`,
  `k = k_mu` (6), i.e. the same empirical-Bayes shrinkage with the prior replacing the league default
  for rookie rows. No tuning of N or k.
* Metric: minutes MAE of `mu` on played rookie rows (n_played_prior < N), paired against the
  current minutes model (`predict_minutes`, default config), game-clustered CI.
* KEEP iff pooled (2023 + 2024) paired MAE delta (ridge - current) has CI upper bound < 0 AND the
  point estimate is < 0 in each class separately AND non-rookie predictions are unchanged.
* Secondary, descriptive, never decides: delta vs a position-only prior (the screen's comparator),
  player-clustered CI, bias. Downstream props CRPS is NOT run (needs the full recency/context
  pipeline; judged not cheap): a KEEP here is a minutes-level result only.

### Item 2: Poisson total head for parlay totals (`nba/parlay/totals_poisson.py`)

* Head: total ~ k * (Poisson(l1 + l2) + 2 * Poisson(lam3)) from the bivariate-Poisson score model
  (`PoissonScoreArm`, arm `poisson_dc` = no win-prob offset, raw drift variant; the arm behind the documented -0.092), default OFF in
  `TotalsHeadConfig`; the Normal head stays the default.
* Rows: the exact walk-forward rows of `winprob_family_eval` (month blocks, MIN_TRAIN 500,
  both heads available), seasons 2022-2024 frame.
* Primary: total CRPS delta (Poisson - game_model Normal), per-game paired, CI upper bound < 0.
  The Normal incumbent uses its own per-row walk-forward sd (no look-ahead). The documented figure
  (10.555 vs 10.647, which used the mean walk-forward sd over all rows) is reproduced as a check only.
* Total-leg calibration (must be NOT WORSE): synthetic over/under legs at the thresholds
  `round(mu_t) + {-8..8} + 0.5` (the `joint_eval.synth_parlays` rule), outcome = actual total > threshold, both heads on
  identical (game, threshold) pairs; delta log loss (Poisson - Normal), game-clustered. "Not worse"
  = point delta <= 0, or point delta <= +0.0005 with CI containing 0; AND the ECE (10 equal-mass bins)
  must not rise by more than 0.005.
* Joint check (descriptive, never decides): 2-leg total x win parlays under a Gaussian copula (rho from
  prior-month randomized-PIT normal scores), joint log loss delta, game-clustered.
* KEEP iff primary AND total-leg calibration both hold. Otherwise the head stays off.

### Item 3: calibrator diagnosis and exp-3 plan (analysis only)

* Data: `data/colab/runs/ctxres_v2_sweep/20261008_171446/oof/xgb_v12_poisson_nb*.parquet`
  (season 2024 only: the 2023 OOF used for selection was not persisted). No refit, no 2025.
* Diagnoses, all descriptive: (a) equal-mass ECE vs the expected ECE of a PERFECTLY calibrated
  forecast of the same n (simulation) = noise floor; (b) per-threshold calibration gap with event
  counts; (c) month-to-month persistence of the gap (learnable vs noise); (d) integer-grid damage:
  fraction of rows with tied quantiles / IQR at the scale floor, and thr-LL cost of deriving P(>=N)
  from the q19 grid instead of the exact pmf; (e) PIT uniformity of the raw forecast.
* No keep/reject: the output is a proposed pre-registered plan for exp 3.

## RESULTS (run 2026-10-08; rules above unchanged)

Raw JSON: `data/backlog_small/item{1,2,3}_*.json` (not committed). n_boot 2000, seed 0, 95% CIs.

### Item 1: rookie minutes prior. VERDICT: KEEP (minutes-level only)

Played rookie rows with n_played_prior < 40; MAE of projected minutes-given-played, min.

| slice | rows / players | current | position-only prior | ridge (draft slot) | ridge - current [CI] | ridge - position [CI] |
|---|---|---|---|---|---|---|
| class 2023 | 2298 / 108 | 8.23 | 7.26 | 6.64 | -1.59 [-1.81, -1.33] | -0.61 [-0.74, -0.48] |
| class 2024 | 2475 / 105 | 7.76 | 6.66 | 6.46 | -1.30 [-1.53, -1.07] | -0.20 [-0.28, -0.11] |
| pooled | 4773 / 213 | 7.99 | 6.95 | 6.55 | -1.44 [-1.61, -1.27] | -0.40 [-0.47, -0.32] |

* Player-clustered pooled CI (stricter): -1.44 [-1.75, -1.15].
* Bias: current +3.13 min (default 24-minute prior is far above a rookie's 14-16), ridge -0.70.
* Non-rookie predictions identical (asserted on the full real frame).
* Training sizes: class 2022 -> 46 rookies; classes 2022-23 -> 139. Chosen alphas 3 and 10.
* Honest decomposition: about 70% of the gain (-1.04) is just replacing the league default (24) by ANY
  rookie-level mean (position-only). Draft slot adds -0.40 min MAE on top, smaller than the 0.75 in the
  context screen (which scored season means; this scores per-game rows), and shrinking in the second
  class (-0.61 then -0.20), consistent with the training set growing more like the test class.
* Not run: downstream props CRPS (needs the recency/context pipeline). Rookie rows are about 3% of
  player-games, so any CRPS effect is diluted; treat as a minutes-level result only.
* Not wired: `nba/props/minutes.py` is not edited (other owner). `rookie_minutes.apply_prior` takes the
  `predict_minutes` output and returns adjusted dists; the maintainer wires it in.

### Item 2: Poisson total head. VERDICT: KEEP as an optional head (default off), with a caveat on why it wins

n = 3407 walk-forward games (same rows as the family eval). Reproduces the documented 10.555 / 10.647.

| quantity | Normal (per-row sd) | Poisson | delta (Poisson - Normal) [CI] |
|---|---|---|---|
| total CRPS | 10.648 | 10.555 | -0.093 [-0.183, -0.0012] |
| (documented, mean sd) | 10.647 | 10.555 | -0.092 [-0.181, -0.0007] |
| total-leg log loss (57,919 legs, 3407 games) | 0.6721 | 0.6634 | -0.0087 [-0.0143, -0.0025] |
| total-leg Brier | | | -0.0041 [-0.0067, -0.0013] |
| total-leg ECE | 0.0130 | 0.0163 | +0.0032 (limit +0.005) |
| joint total x home win log loss (30,220 parlays, descriptive) | 0.5506 | 0.5479 | -0.0028 [-0.0060, +0.0005] |
| mean bias of total forecast | +0.28 | -0.18 | |

Checks: CRPS CI < 0 pass; leg log loss pass; leg ECE (+0.0032 < 0.005) pass. The CRPS upper bound
(-0.0012) is barely below 0.

Caveat that changes the interpretation: a Normal with the Poisson head's own mean and sd (shape isolated)
ties the Poisson head (CRPS +0.0001 [-0.0031, +0.0035]; leg log loss +0.0001 [-0.0002, +0.0005]) and
carries the whole gain over the incumbent (-0.093 [-0.181, -0.002]). The win comes from the Poisson
model's MEAN (team attack/defence ratings + recency), not from the discrete bivariate-Poisson shape.
The incumbent's mean is the single linear rolling-total feature. If total legs are the only goal, a
Normal head on the Poisson mean is the simpler vehicle; the Poisson head is kept because it passed the
pre-registered rule and also supplies an exact discrete CDF (P(total > x.5) with no continuity fudge).
ECE rises slightly (0.013 -> 0.016) even as log loss improves.

Integration: `nba/parlay/totals_poisson.py` (`TotalsHeadConfig(kind="normal")` default; `make_total_head`,
`PoissonTotal.p_over/cdf/crps/z_of`). `nba/parlay/joint.py` (not mine) still builds `GameCtx` with
Normal mu_t/sd_t; using the Poisson head there is a follow-up edit by its owner.

### Item 3: why the exp-2 calibrators passed only partially (analysis only; no keep/reject)

Persisted OOF is season 2024 only (27,583 rows per stat, 8 months); the 2023 OOF used for tuning was not
saved. ECE = 10 equal-mass bins on pooled threshold events. "Noise floor" = expected ECE of a perfectly
calibrated forecast with the same probabilities (30 simulations, nested events).

| stat | raw ECE | noise floor | raw / floor | achievable ECE gain (raw - floor) | gap persistence r (month to month) | trailing-4-month gap R2 | integer-grid tax (thr-LL, q19 grid vs exact) |
|---|---|---|---|---|---|---|---|
| pts | 0.0119 | 0.0018 | 6.6x | 0.0101 | 0.65 | +0.55 | +0.0153 [+0.0138, +0.0169] |
| reb | 0.0036 | 0.0022 | 1.6x | 0.0014 | 0.25 | -0.13 | +0.0091 [+0.0077, +0.0105] |
| ast | 0.0069 | 0.0021 | 3.3x | 0.0048 | 0.31 | -0.09 | +0.0145 [+0.0125, +0.0163] |
| fg3m | 0.0079 | 0.0023 | 3.4x | 0.0056 | 0.41 | +0.29 | +0.0144 [+0.0126, +0.0164] |

Diagnoses:
1. pts: genuine, persistent miscalibration (raw P(>=15) is 0.029 too low, z = -10.6; recalibration slope
   0.92). A trailing-window mapping predicts next month's gap (R2 0.55), so platt (ECE 0.0119 -> 0.0096)
   and isotonic (-> 0.0042) can learn it. Both pass. Isotonic is better on ECE and thr-LL (-0.0037)
   despite thin tails (P(>=30): 1082 events in the year).
2. reb: raw ECE is only 1.6x the noise floor and the gaps do not persist (trailing R2 < 0): there is
   nothing stable to learn, and every flexible calibrator just adds variance (iso +0.0012, conf
   +0.0048, pit +0.0012). Correct outcome: no calibrator.
3. ast / fg3m: moderate persistent miscalibration (ast P(>=2) +0.010, fg3m P(>=1) +0.017, z = 5.7, the
   zero-inflation gap in report section 6) concentrated at the lowest threshold. Pooled Platt and iso
   both halve ECE but the thr-LL gain (about 0.0001-0.0002) is below the bootstrap noise, so the
   thr-LL route fails; the ECE route needs a 0.005 gain, which is (nearly) unreachable: achievable
   gain is 0.0048 (ast) and 0.0056 (fg3m) and 0.0014 (reb). iso/ast achieved 0.0049, 0.0001 short of
   the floor. The ECE_GAIN = 0.005 constant is a design flaw for these stats, not evidence that the
   calibrators fail.
4. conf and pit (distribution-level recalibration) fail on every stat (ECE rises 2-6x; fg3m +0.0316
   thr-LL). Causes: (a) they map continuous quantiles for integer outcomes: fg3m has 4.4 distinct
   knots out of 19 and 18% of rows have IQR at the scale floor, ast 6.0 knots / 4.7%, so the
   standardised-score (conf) is mostly discreteness, and the quantile ECDF (pit) is fit to randomised
   PIT noise; (b) converting any quantile grid to P(>=N) costs +0.009 to +0.015 thr-LL versus the
   exact pmf (q19 grid here; the sweep used a 199 grid so its tax is smaller but not zero), an order
   of magnitude larger than any calibration gain; (c) raw PIT is already near-uniform for reb/ast/fg3m
   (max decile deviation 0.018-0.021, with the caveat that q19 PITs are coarse), leaving no signal for
   a distribution-level map. pts has real PIT miscalibration, but its fix must not go through the grid.
5. Threshold sparsity matters less than expected: 4-month windows carry >= 1000 events per
   threshold per year, but iso with per-threshold steps adds variance on stats that need none (reb,
   fg3m).

Caveat: all numbers are descriptive, from one season; binomial SEs in the per-threshold z are
optimistic (rows within a game correlate); q19 PIT/grid figures are coarse.

#### Proposed pre-registered calibration plan for the exp-3 hybrid (NOT run; to be frozen before any 2025 touch)

1. Drop distribution-level `conf` and `pit` as arms. If a distribution-level fix is wanted for pts,
   recalibrate the PARAMETERS of the integer family (mean multiplier / dispersion of the NB or
   Poisson), then re-derive P(>=N) from the exact pmf; never go through a quantile grid.
2. Calibrator arms: pooled Platt (logit p and ln N, W = 4 months, half-life 4, C = 10 as tuned in
   exp-2 on 2023) and per-threshold isotonic (W = 4, hl = 4, minbin 50). Add arm C: shared slope with
   per-threshold intercepts (captures the low-threshold zero-inflation gap with 4 parameters).
3. Eligibility gate decided on a burn-in window before the test (first 2 months of the test season): a
   stat is calibrated only if raw ECE >= 2x the simulated noise floor (on exp-2 numbers: pts, ast,
   fg3m eligible; reb not). Ineligible stats are reported with the identity map.
4. Keep rule per eligible stat: thr-LL delta CI upper < 0, OR (ECE gain >= 50% of (raw ECE - noise
   floor) AND thr-LL CI upper <= +0.001). This replaces the absolute 0.005 gain. Post-hoc check on exp-2:
   Platt would pass pts, ast, fg3m; iso would pass pts and ast but fail fg3m (CI upper +0.0017) and reb.
   These pass/fail calls are post-hoc and not evidence by themselves.
5. Report per stat: raw ECE vs noise floor, slope/intercept, gap persistence, game-clustered CIs.
   BH over the 4 stats x number of arms. Confirm once, on 2025, under a separate pre-registration, with
   the plan, arms, windows and gate frozen first.
6. Power note: with about 27.6k rows per stat the thr-LL CI half-width is about 0.0002 for Platt-type
   changes, so effects below 0.0003 cannot be resolved on one season; the plan treats ECE gain with a
   no-harm bound as the practical route for ast and fg3m.
