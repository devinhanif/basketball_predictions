# Router v2: learned routing across candidate predictors

Status: infrastructure only. No real-data run has been made. The decision rule
below is fixed BEFORE any real run and may not be edited after seeing results
(amend only via a dated addendum).

## Components (`nba/stack/`)
- `oof.py`: out-of-fold store `data/stack/oof.duckdb` (gitignored), table
  `oof_predictions`. `write_oof` validates (`made_with_data_through < game_date`,
  `p` in [0,1], monotone 19-level quantile grid 0.05..0.95, no duplicate keys)
  and replaces rows idempotently by `(model, model_version)`.
- `adapters.py`: `from_dist_frame` (mean + std + family normal|gamma|nbinom) and
  `season_avg_oof` (played-only recency-weighted average, same formula as
  `nba.props.forward.recency_weighted_dist`, strictly as-of).
- `router.py`: gates (a) `SoftmaxGate`, linear-softmax trained on pooled grid
  CRPS (props) / pooled log loss (win) with analytic gradients and L-BFGS;
  (b) `LGBMGate`, classifier of the argmin-score candidate, probabilities
  floored toward uniform. Pooling: weighted average of quantile grids
  (props), weighted logit average (win). Optional Platt/isotonic for win,
  fit on the latest `calib_days` of the training window (gate fit on earlier
  part). Props calibration is NOT implemented.
- Walk-forward: expanding window, 30-day date blocks; a block is scored by a
  gate fit only on rows with `game_date` strictly before the block start
  (asserted). Season >= 2025 raises. Baselines: each single candidate,
  equal-weight pool, walk-forward hard-bucket route (per-bucket best candidate
  by train mean score; the existing `route_by_bucket` logic made causal).
- `evaluate.py`: paired differences, game-clustered bootstrap CI
  (`paired_score_delta_ci(cluster_ids=game_id)`), clustered-bootstrap p,
  Benjamini-Hochberg over the family.

Score: props = grid CRPS (2 x mean pinball over the 19 levels; coarse
quadrature, identical for all candidates, so comparable but not numerically
equal to the 199-level `crps_array`); win = log loss.

## Pre-registered decision rule
Family (per target, per stat): routers {softmax, lgbm} x comparisons
{best single, equal pool, hard route} = 6 tests, BH-adjusted together at
alpha 0.05. The best single candidate is chosen in hindsight on the scored rows
(conservative). A router is KEPT only if ALL hold vs the best single:
1. mean delta <= -0.005 (effect floor, CRPS units / nats) and clustered 95% CI
   upper bound < 0;
2. BH q < 0.05;
3. no slice group with n >= 200 regresses by more than +0.01 vs the best
   single, over: cold-start bucket, season phase (first 15 team-player games
   vs rest), starter vs bench. A missing slice column makes the decision
   "undecidable" = not kept.
Otherwise the router is not kept and the existing route stays. A negative
result is reported as such. Beating equal pool / hard route is reported but
not sufficient. Gate type is not selected on test data; both are reported.

## Data hygiene
- Seasons <= 2024 only, for fitting, calibration and selection. Season 2025 is
  frozen and rejected by code; it may only be touched once by a separate,
  pre-announced final run.
- Gate context features must be as-of (games played, projected minutes,
  teammates out, SB bucket, prior start rate, Elo spread). The actual
  `starter` flag is a slice label only, never a gate feature.
- Candidate OOF rows must themselves be walk-forward (their own
  `made_with_data_through < game_date`); the store enforces this per row.
- Caveat: the SB buckets are DNP-contaminated (docs/DNP_AUDIT_2026-10-08.md);
  prefer prior-games-played and prior-start-rate as gate features.

## Commands
```
uv run python -m nba.stack.populate season-avg --stats pts reb ast fg3m
uv run python -m nba.stack.populate context --out data/stack/context.parquet
```
Other candidates (sim, last-10, ...): produce a frame with game_id, player_id,
game_date, season, mean, std (or q_grid), made_with_data_through and call
`nba.stack.adapters.from_dist_frame(df, stat, family)` then
`nba.stack.oof.write_oof(frame, model, version)`. Then
`read_oof` each candidate, `build_stack_data(target, frames, outcomes, context)`,
`walk_forward(data, SoftmaxGate | LGBMGate)`, `hard_bucket_walk_forward`,
`evaluate_stack(...).to_markdown()`.
