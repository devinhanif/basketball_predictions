# Win-probability model-family bake-off (2026-10-08)

Question: with the injury-report signal in hand, does any model family or calibrator beat
production `rung0_injury_elo` (MOV-Elo logit + ridge on value OUT/DOUBTFUL)?
Answer: **no arm or calibrator meets the pre-registered replacement rule.** One stack of
offset-based arms is a real-looking but small improvement that fails the playoffs-slice veto.

Code: `research/models/winprob_family.py`, `research/eval/winprob_family_eval.py`,
`tests/ml/test_winprob_family.py`. Scored universe: the 3951 out-of-fold games of 2022-24
(season 2025 never loaded). Month-block walk-forward; every refit uses games strictly
before the block; arms need >= 500 prior games, else they output the injury-Elo probability.

## Pre-registered rule (also the module docstring of the eval file)

R1, replace production: paired log-loss delta (candidate - production) with game-clustered
95% CI wholly below 0 AND Benjamini-Hochberg q=0.05 across ALL ~210 candidate rows; Brier
delta <= 0; equal-mass ECE delta <= +0.01; no slice (rotation OUT 0/1/2+, early season,
playoffs) worse by > 0.005 log loss (n >= 100).
R2, keep a calibrator (vs UNCALIBRATED production): log-loss CI wholly below 0, OR ECE
improves >= 0.005 with log-loss CI upper bound <= +0.001.
Calibrators (platt, platt_slope, platt_int, beta, iso = isotonic on equal-mass bins blended
with Platt) are fit on the arm's earlier out-of-fold predictions only; window / half-life /
ridge / min-bin / blend chosen by an inner walk-forward on the two latest prior months.
Additions registered before the full run: home/away swap augmentation (`_sw` arms), GLM
team-strength margin feature and arms, Brier-loss MLP, season-relative drift variant,
leak detector (static allow-list plus top-10 gain check, raises `LeakError`).

Disclosure: a smoke run of the arms (no calibration) was looked at before the full run. It
was used for debugging and led to one change: MLP lr/epochs 3e-3/40 -> 1e-3/15 (gross
overfit). LightGBM replaces XGBoost (not installed). Other hyper-parameters were fixed in code
beforehand; nothing else was tuned on scored games.

## Leaderboard (log loss, lower better; delta vs production 0.6167, Brier 0.2144, ECE 0.0240)

| candidate | ll | dLL [95% CI] | R1 |
|---|---|---|---|
| stack (logit_offset, gbm_init, poisson_dc_offset, mlp_offset), raw | 0.6146 | -0.0021 [-0.0037, -0.0005] | no: playoffs slice +0.0060 (n=250) |
| stack, season-relative | 0.6146 | -0.0021 [-0.0038, -0.0005] | no: playoffs +0.0059 |
| gbm_init_sw (swap-augmented, trees on offset) | 0.6157 | -0.0010 [-0.0022, +0.0001] | no |
| mlp_offset_sw | 0.6161 | -0.0006 [-0.0030, +0.0017] | no |
| mlp_offset (BCE) / mlp_offset_brier | 0.6162 / 0.6164 | -0.0004 / -0.0003 (CIs span 0) | no |
| logit_offset (raw / swap) | 0.6171 / 0.6168 | +0.0004 / +0.0001 | no |
| poisson_dc_offset / poisson_dc | 0.6180 / 0.6176 | +0.0013 / +0.0010 | no |
| glm_offset (injury-Elo + GLM margin only) | 0.6178 | +0.0011 [-0.0006, +0.0026] | no |
| logit_nooffset (raw / swap) | 0.6214 / 0.6187 | +0.0047 / +0.0021 | no |
| gbm_feat (raw / swap) | 0.6284 / 0.6210 | +0.0118 / +0.0043 | no (worse) |
| gbm_noff (raw / swap) | 0.6302 / 0.6229 | +0.0135 / +0.0062 | no (worse) |
| glm_only | 0.6343 | +0.0176 [+0.0111, +0.0239] | no (worse) |

Full table (all 210 rows, season-relative variants, every calibrator) is in
`data/winprob_family/results.json`; OOF probabilities in `oof_all.parquet`.

## Findings

* Nothing replaces production. Elo-family models are hard to beat on ~4k games.
* Stack: the only CI-excluding-0 win; weights lean on poisson_dc_offset (0.36) and
  mlp_offset (0.17). It is worse in playoffs (+0.006, n=250) and early season (+0.0008). Treat as a
  hypothesis for fresh data, not a replacement.
* Swap augmentation clearly helps un-anchored arms (gbm_noff -0.007, logit_nooffset -0.003) and
  mildly helps gbm_init (-0.0016); never enough to reach production.
* GLM team strength adds nothing beyond injury-Elo (glm_offset +0.0011, CI spans 0); alone it is
  far worse (+0.018).
* Brier-loss MLP vs BCE: no difference in ll/Brier/ECE.
* Season-relative rates vs raw: ties or slightly worse everywhere. Drift in 2022-24 is small:
  home win rate 58.1% -> 54.7% -> 54.6%, 3PA rate 0.388 -> 0.395 -> 0.420, FTA rate 0.265 -> 0.244 ->
  0.245, pace 100.7 -> 99.9 -> 100.4, mean total 228.7 -> 227.3 -> 227.0. Re-test when a 2026-27
  season exists; the variant is cheap to keep.
* Calibration: production is already calibrated (ECE 0.024). Platt slope-only is the best case
  (-0.0002, CI spans 0); isotonic hurts (+0.0034, CI above 0). No calibrator is kept on
  production. R2 keeps were only on the stack (platt_slope) and gbm_init_sw (platt_slope), via the
  ECE route.
* Leak detector: top gain features for the GBM arms are glm_margin, offset, d_out, wp, drtg;
  no violation.
* Poisson arm vs `nba/parlay/game_model.py` (both walk-forward, n=3407): margin CRPS 7.727 vs
  7.704 (+0.023, CI [-0.031, +0.078], tie); total CRPS 10.555 vs 10.647 (-0.092, CI [-0.183,
  -0.0002], p=0.05, marginal win for the Poisson total). The comparator uses the mean
  walk-forward residual sd.

## Caveats

Poisson arm: the scaled bivariate Poisson dispersion is set by method of moments; win
probability from the Skellam margin has a tie mass split 50/50. `cross-season carryover` in the
GLM is the recency half-life (120d) only. The stack and MLP rows carry a small selection
caveat (above). Existing `build_game_context_matchup_features` `games_into_season` / win%
columns look cumulative across seasons (mean 175, max 386); this module uses its own
season-reset standings instead. Worth a check by the owner of that module.

## Commands

    uv run python -W ignore -m research.eval.winprob_family_eval --db nba.duckdb \
        --oof data/injury_elo/oof_predictions.parquet --out-dir data/winprob_family
    uv run pytest --no-cov tests/ml/test_winprob_family.py
    uv run ruff check nba tests/ml/test_winprob_family.py && uv run ruff format --check nba && uv run mypy nba

Runtime about 3 minutes, single process, under 1 GB. OMP threads are pinned to 1 inside the
module because torch and lightgbm in one process deadlock otherwise on macOS.
