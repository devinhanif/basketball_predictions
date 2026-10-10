# Clustered (per-game) bootstrap for props eval — 2026-10-08

## What changed

Per the adversary's methodology audit (`docs/FDR_AUDIT_2026-10-08.md`, section 0): the
props bootstrap resampled individual player-games, treating teammates in the
same game as independent draws even though they share pace, blowout/
garbage-time effects, foul trouble, and minutes-redistribution shocks. That
makes every reported CI anti-conservative (too narrow) — more "wins" look
significant than really are.

- `nba/props/metrics.py`:
  - Added `_clustered_boot_means` + `_bootstrap_ci_clustered`: a vectorized
    cluster/block bootstrap that resamples whole clusters (e.g. `game_id`)
    with replacement, using per-cluster sufficient statistics (sum, count)
    so the mean of a resampled replicate is exact without materializing
    resampled row arrays. `nba.eval.metrics.bootstrap_ci` (shared with the
    win-probability/Elo path) was **not** touched — the win-prob holdout
    stays on its existing, already-clean bootstrap.
  - `paired_score_delta_ci(score_a, score_b, ..., cluster_ids=None)` and
    `mean_bias_ci(pred_mean, y, ..., cluster_ids=None)` both gained an
    optional `cluster_ids` parameter (one id per row, same length/order as
    the metric arrays). `None` (the default) preserves the exact prior
    row-level behavior — no other caller changes.
- `nba/props/run.py`: threaded `game_id` through as `cluster_ids` for every
  `mean_bias_ci`/`paired_score_delta_ci` call in the base-stat loop
  (`bias_ci`, `crps_vs_season`, `crps_vs_last10`, `ll_vs_season`,
  `ll_vs_last10`) and the combo-stat loop (`bias_ci_c`), masked identically
  to `y_m`/`pred_mean_m` via the existing `valid_mask`/`valid_mask_c`.
  `nba/props/volatility.py`'s internal `paired_score_delta_ci` calls and the
  other eval-module callers (`model_routing.py`, `routed_eval.py`,
  `player_points_sim_eval.py`, `player_reb_ast_sim_eval.py`,
  `usage_redistribution_eval.py`) are unchanged — out of scope for this
  patch, and backward-compatible since `cluster_ids` defaults to `None`.

## Before/after demonstration on synthetic data

Synthetic paired scores for 80 games x 5 players/game, with a shared
per-game random effect (sd=0.5) dominating a small per-player noise term
(sd=0.02) — i.e. strong within-game correlation, the exact failure mode the
audit flagged:

```
            point       lo        hi       half-width
iid     -0.1404   -0.1839   -0.0995    0.0422
clustered -0.1404   -0.2373   -0.0432    0.0970
ratio (clustered / iid half-width): 2.30x
```

The point estimate is identical (both are just `mean(a - b)`); only the CI
width changes, confirming the fix affects inference, not the metric itself.
A degenerate-case unit test also confirms that when every cluster is a
singleton (one row per game), the clustered path reduces to the exact same
arithmetic as the row-level bootstrap (bit-identical `lo`/`hi` under the
same seed).

## Qualitative read for the rebounds-win CI

The rebounds-overall CRPS result flagged in the audit
(`sim - season_avg = -0.014, 95% CI [-0.024, -0.003]`, half-width ~0.0105)
was computed on real pooled player-game data (n~600 games pooled, several
players per game), so its true within-game correlation is unknown without
rerunning on the real DB. Qualitatively: NBA box-score correlation among
teammates (shared pace/blowouts) is real but almost certainly weaker than
the synthetic demo's deliberately extreme game-effect-to-noise ratio (0.5
vs 0.02) — a more realistic widening factor is plausibly in the 1.2x-1.5x
range rather than 2.3x, but this is a qualitative guess, not a rerun number.
Even a 1.3x widening would move the rebounds half-width from ~0.0105 to
~0.0137, i.e. the CI could plausibly approach (but likely not cross) zero
at the upper bound — **this is exactly why the audit said rebounds "has
some room to spare" but should be reverified with the real clustered
bootstrap before being treated as a clean gate-passer for 3A.** No real-data
job was run to produce this number; the maintainer should rerun the props
eval and inspect the actual clustered CI before trusting the rebounds win
at face value.

## Tests

`tests/props/test_clustered_bootstrap.py` (new, fixture-free synthetic
data only):
- Clustered CI is strictly wider than iid CI under strong within-cluster
  correlation, for both `paired_score_delta_ci` and `mean_bias_ci`.
- Singleton-cluster degenerate case matches the iid bootstrap exactly
  (same seed, same RNG consumption path) for both functions.
- Seed-determinism of the clustered path.
- `cluster_ids=None` is byte-identical to omitting the kwarg (no other
  caller's behavior changes).
- Mismatched `cluster_ids` length raises `ValueError`.

All 149 existing `tests/props` tests still pass unchanged.

## Verification run

```
uv run ruff check nba/props/metrics.py nba/props/run.py tests/props/test_clustered_bootstrap.py   # clean
uv run ruff format --check nba/props/metrics.py nba/props/run.py tests/props/test_clustered_bootstrap.py   # clean
uv run mypy nba/props/metrics.py nba/props/run.py   # Success: no issues found in 2 source files
uv run pytest tests/props -q --no-cov   # 149 passed
```

## What this does NOT do

- Does not rerun the real-DB props eval (out of scope per operating
  constraints — no multi-minute real-data jobs from this agent). The
  rebounds-win CI width under the real clustered bootstrap is still
  unknown and must be checked by the maintainer before relying on it for
  the matchup-3A gate.
- Does not touch `nba/eval/metrics.py` (`bootstrap_ci`), so the Elo/
  win-probability frozen-holdout path is unaffected, per the task's
  explicit instruction.
- Does not change `nba/props/volatility.py`'s or any other eval module's
  bootstrap calls to use clustering yet — they still default to
  `cluster_ids=None` (row-level), unchanged behavior. Flagging this as a
  natural follow-up, not done here.
