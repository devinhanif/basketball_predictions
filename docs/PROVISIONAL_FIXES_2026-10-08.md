# Closing the two PROVISIONAL prerequisites — 2026-10-08

Per `docs/ACCEPTANCE_CRITERIA_2026-10-08.md` and `docs/BOOTSTRAP_CLUSTERED_2026-10-08.md`,
two code-gap prerequisites blocked any routing/holdout result from graduating past
PROVISIONAL. Both are closed now, plus the QA audit's major note on the clustered
bootstrap's reliability gate.

1. **Clustered bootstrap threaded into routing.** `nba/eval/model_routing.py::route_by_bucket`
   and `nba/eval/routed_eval.py::evaluate_routing` both gained an optional `cluster_ids`
   parameter, mirroring `nba.props.metrics.paired_score_delta_ci`'s existing contract
   exactly: `None` (default) is byte-identical to the prior row-level behavior; passing
   `game_id`-aligned ids switches to the cluster/block bootstrap. `route_by_bucket` slices
   `cluster_ids` by the same per-bucket boolean mask used for the CRPS arrays;
   `evaluate_routing` slices it by `test_mask`, matching `nba/props/run.py`'s existing
   mask-alignment pattern. Neither `nba/props/metrics.py` nor `nba/props/run.py` was
   touched (already done, out of scope per the task).

2. **Frozen holdout wired into props/routing.** Added `filter_by_holdout_mode(df,
   holdout_season, mode)` to `nba/eval/walkforward.py` (`"all"` no-op default,
   `"exclude_holdout"` = tunable pool, `"holdout_only"` = the frozen season only) as a thin
   wrapper around the existing `split_frozen_holdout`. `PropsConfig` gained
   `holdout_season: int | None = None` and `holdout_mode: str = "all"`; `run_props_experiment`
   applies the filter to `target` right after building it (one line, no change to the core
   per-stat loop, no conflict with the existing `cluster_ids` wiring in that file). CLI flags
   `--holdout-season` / `--holdout-mode` were added to `nba/props/__main__.py`. Default
   behavior (`holdout_season=None`) is unchanged for every existing caller.

3. **QA audit major fix.** `nba/props/metrics.py::_bootstrap_ci_clustered`'s "insufficient
   data" note now gates on `g` (distinct `cluster_ids` count) instead of `n` (row count), so
   a few-games/many-players slate (e.g. 5 games x 10 players = n=50, g=5) is correctly
   flagged as unreliable instead of silently clearing the old row-count-only threshold.

Tests added: `tests/props/test_clustered_bootstrap.py` (g-based gate fires/clears
correctly), `tests/ml/test_model_routing.py` + `tests/ml/test_routed_eval.py` (clustered CI
strictly wider than row-level under planted within-game correlation, `cluster_ids=None`
backward-compat), `tests/ml/test_walkforward_harness.py` (`filter_by_holdout_mode` all three
modes + unknown-mode error), `tests/props/test_run_fixture.py` (end-to-end `PropsConfig`
holdout wiring on the fixture). All fixture/synthetic-only, no real-DB job.

**Not done / still open:** this does not itself re-run any of the three
acceptance-criteria tests (3A archetype gate, points n_sims=2000 retest, rung-4 grid) — it
only removes the two code-gap blockers those tests needed first. No numeric CI from a
real-DB run is claimed here. `docs/HOLDOUT_ACCESS_LOG.md` still needs to be created and
the one-permitted-touch ledger discipline followed before anyone runs `holdout_mode=
"holdout_only"` against the real DB with `holdout_season=2025`.
