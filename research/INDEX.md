# research/ — index of archived modules

Wave A of docs/DESIGN_RESTRUCTURE.md (2026-10-10). Every module here reached a verdict recorded in
`docs/TEST_LEDGER.md` (T-ids below) or in the named doc; the code stays importable and its tests run
in CI (`docs/DECISIONS.md` 2026-10-09, Q2) so every ledger result is reproducible. Research imports
live code (`nba.*`); nothing under `nba/` imports `research.*` (`tests/test_layering.py`).

Running a CLI: `python -m research.eval.<module>`, `python -m research.colab push <job>`,
`python -m research.registry route show`, `python -m research.props` (the experiment-1/2 runner).
Appendix A put the Colab tooling under `research/colab_tools/`; it went to `research/colab/` instead so the
package keeps its shape (`jobs.py` finds `jobs/` next to itself and `REPO_ROOT` two levels up).
Colab job dirs keep their layout under `research/colab/jobs/<job>/` and `job.yaml` paths were
rewritten; the notebooks were regenerated only where a path string changed.

Columns: lines = at the time of the move (`wc -l`); tests = test files under `tests/research/`
that import the module (all run in CI's `unit` job, `make test`); the rung-4 step-heads test runs in
its own pytest process (torch/LightGBM OpenMP clash), as before.


## Moved modules (98 files, 40,780 lines)

| old path | new path | lines | verdict (ledger rows / docs) | tests in CI |
|---|---|---|---|---|
| `nba/colab/__init__.py` | `research/colab/__init__.py` | 1 | (colab job asset; see the job's row) | none (module has no test of its own) |
| `nba/colab/__main__.py` | `research/colab/__main__.py` | 69 | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is | `tests/research/colab/test_colab.py` |
| `nba/colab/drive.py` | `research/colab/drive.py` | 81 | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is | `tests/research/colab/test_colab.py` |
| `nba/colab/jobs.py` | `research/colab/jobs.py` | 91 | job tooling | `tests/research/colab/test_colab.py`, `tests/research/features/test_game_sets.py`, `tests/research/features/test_opponent_ridge_v2.py`, `tests/research/features/test_player_sequences.py`, `tests/research/props/test_context_features_v2.py`, `tests/research/props/test_ctxres_v3.py` |
| `nba/colab/jobs/ctxres_v2_sweep/build_notebook.py` | `research/colab/jobs/ctxres_v2_sweep/build_notebook.py` | 79 | T073-T080 (voided by the missingness leak, f1fcb91) | none (module has no test of its own) |
| `nba/colab/jobs/ctxres_v2_sweep/ctxres_v2_sweep.py` | `research/colab/jobs/ctxres_v2_sweep/ctxres_v2_sweep.py` | 1,843 | T073-T080 (voided by the missingness leak, f1fcb91) | `tests/research/props/test_context_features_v2.py`, `tests/research/props/test_ctxres_v3.py` |
| `nba/colab/jobs/ctxres_v3_hybrid/build_notebook.py` | `research/colab/jobs/ctxres_v3_hybrid/build_notebook.py` | 99 | T073-T080 family; superseded by leak-free exp 3 (T102-T118) | none (module has no test of its own) |
| `nba/colab/jobs/ctxres_v3_hybrid/ctxres_v3_hybrid.py` | `research/colab/jobs/ctxres_v3_hybrid/ctxres_v3_hybrid.py` | 413 | T073-T080 family; superseded by leak-free exp 3 (T102-T118) | `tests/research/props/test_ctxres_v3.py` |
| `nba/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py` | `research/colab/jobs/ctxres_v3_leakfree/ctxres_v3_leakfree.py` | 372 | T102-T118 (BROKEN reb/ast/fg3m, WOUNDED pts) | `tests/research/eval/test_ctxres_v3_leakfree.py` |
| `nba/colab/jobs/joint_game_set/build_notebook.py` | `research/colab/jobs/joint_game_set/build_notebook.py` | 75 | T081 (ties Gaussian copula) | none (module has no test of its own) |
| `nba/colab/jobs/joint_game_set/joint_game_set.py` | `research/colab/jobs/joint_game_set/joint_game_set.py` | 1,323 | T081 (ties Gaussian copula) | `tests/research/features/test_game_sets.py` |
| `nba/colab/jobs/pbp_gpt/build_notebook.py` | `research/colab/jobs/pbp_gpt/build_notebook.py` | 74 | T082-T084 (not kept) | none (module has no test of its own) |
| `nba/colab/jobs/pbp_gpt/pbp_gpt.py` | `research/colab/jobs/pbp_gpt/pbp_gpt.py` | 1,270 | T082-T084 (not kept) | `tests/research/features/test_pbp_tokens.py` |
| `nba/colab/jobs/ridge_v2_sweep/build_notebook.py` | `research/colab/jobs/ridge_v2_sweep/build_notebook.py` | 77 | T098-T101 (gain was the leak) | none (module has no test of its own) |
| `nba/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py` | `research/colab/jobs/ridge_v2_sweep/ridge_v2_sweep.py` | 773 | T098-T101 (gain was the leak) | `tests/research/features/test_opponent_ridge_v2.py` |
| `nba/colab/jobs/seq_props/build_notebook.py` | `research/colab/jobs/seq_props/build_notebook.py` | 68 | T065-T072 (not kept) | none (module has no test of its own) |
| `nba/colab/jobs/seq_props/seq_props_train.py` | `research/colab/jobs/seq_props/seq_props_train.py` | 433 | T065-T072 (not kept) | `tests/research/features/test_player_sequences.py` |
| `nba/colab/pull.py` | `research/colab/pull.py` | 99 | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is | `tests/research/colab/test_colab.py` |
| `nba/colab/push.py` | `research/colab/push.py` | 190 | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is | `tests/research/colab/test_colab.py` |
| `nba/colab/status.py` | `research/colab/status.py` | 59 | no GPU job is planned (information beats architecture); push/pull/drive/status only matter if one is | `tests/research/colab/test_colab.py` |
| `nba/coldstart/archetypes.py` | `research/coldstart/archetypes.py` | 692 | M3A T048-T060 (not kept); only props.opponent imports it, itself a research candidate | `tests/research/coldstart/test_archetype_asof.py`, `tests/research/coldstart/test_coldstart_archetypes.py`, `tests/research/props/test_opponent.py` |
| `nba/coldstart/carryover.py` | `research/coldstart/carryover.py` | 62 | TIME_DECAY_2026-10-08.md (not kept); NO ledger row | `tests/research/coldstart/test_coldstart_carryover.py` |
| `nba/coldstart/rookie_priors.py` | `research/coldstart/rookie_priors.py` | 99 | unreferenced; F9 (draft-pick prior) will be rebuilt as a lineup/role feature, not from this | `tests/research/coldstart/test_coldstart_rookie_priors.py` |
| `nba/eval/backlog_small_eval.py` | `research/eval/backlog_small_eval.py` | 410 | BACKLOG_SMALL_2026-10-08.md (T034-T041 family F) | none (module has no test of its own) |
| `nba/eval/context_screen_games.py` | `research/eval/context_screen_games.py` | 302 | CONTEXT_SCREEN_*.md; NO ledger row | `tests/research/features/test_game_context.py` |
| `nba/eval/context_screen_players.py` | `research/eval/context_screen_players.py` | 674 | CONTEXT_SCREEN_*.md; NO ledger row | none (module has no test of its own) |
| `nba/eval/ctxres_v2_coverage_sim.py` | `research/eval/ctxres_v2_coverage_sim.py` | 218 | T073-T080 (voided by leak) | `tests/research/props/test_context_features_v2.py` |
| `nba/eval/ctxres_v2_descriptive.py` | `research/eval/ctxres_v2_descriptive.py` | 700 | T073-T080 (voided by leak) | `tests/research/props/test_ctxres_v2_descriptive.py` |
| `nba/eval/ctxres_v2_eval.py` | `research/eval/ctxres_v2_eval.py` | 420 | T073-T080 (voided by leak) | `tests/research/props/test_context_features_v2.py` |
| `nba/eval/ctxres_v3_eval.py` | `research/eval/ctxres_v3_eval.py` | 368 | T073-T080 family (voided) | `tests/research/props/test_ctxres_v3.py` |
| `nba/eval/ctxres_v3_leakfree.py` | `research/eval/ctxres_v3_leakfree.py` | 505 | T102-T118 | `tests/research/eval/test_ctxres_v3_leakfree.py` |
| `nba/eval/fair_rematch.py` | `research/eval/fair_rematch.py` | 403 | T062-T064 (sim lost every prop) | `tests/research/features/test_played_only.py` |
| `nba/eval/ga_tune.py` | `research/eval/ga_tune.py` | 297 | produced configs/mov_elo_tuned.yaml (config stays, GA code archived); NO ledger row | `tests/research/eval/test_ga_tuner.py` |
| `nba/eval/injury_elo_t30_eval.py` | `research/eval/injury_elo_t30_eval.py` | 232 | T160-T161 (not kept) | `tests/research/models/test_injury_elo_t30.py` |
| `nba/eval/joint_game_set_eval.py` | `research/eval/joint_game_set_eval.py` | 867 | T081 | `tests/research/features/test_game_sets.py` |
| `nba/eval/lineups_known_eval.py` | `research/eval/lineups_known_eval.py` | 527 | T085-T090, T096-T097 (WOUNDED; T-30 lives on as shadow arm) | `tests/research/props/test_lineups_known_eval.py` |
| `nba/eval/minutes_v2_eval.py` | `research/eval/minutes_v2_eval.py` | 904 | T162-T173 (props gain fails the floor in 2024) | `tests/research/props/test_minutes_v2.py` |
| `nba/eval/model_routing.py` | `research/eval/model_routing.py` | 203 | T005-T033 (routing families C-E) | `tests/research/eval/test_model_routing.py` |
| `nba/eval/pbp_gpt_eval.py` | `research/eval/pbp_gpt_eval.py` | 737 | T082-T084 | `tests/research/features/test_pbp_tokens.py` |
| `nba/eval/player_points_sim_eval.py` | `research/eval/player_points_sim_eval.py` | 304 | T003, T061 (sim vs season avg) | `tests/research/eval/test_player_points_sim_eval.py`, `tests/research/features/test_time_decay_tune.py` |
| `nba/eval/player_reb_ast_sim_eval.py` | `research/eval/player_reb_ast_sim_eval.py` | 349 | T001-T002 | `tests/research/eval/test_player_reb_ast_sim_eval.py` |
| `nba/eval/pts_tail_eval.py` | `research/eval/pts_tail_eval.py` | 320 | T129-T135 (no candidate passes) | `tests/research/eval/test_pts_tail.py` |
| `nba/eval/rapm_injury_elo_eval.py` | `research/eval/rapm_injury_elo_eval.py` | 377 | RAPM.md; NO ledger row | none (module has no test of its own) |
| `nba/eval/ridge_v2_eval.py` | `research/eval/ridge_v2_eval.py` | 307 | T098-T101 | `tests/research/features/test_opponent_ridge_v2.py` |
| `nba/eval/routed_eval.py` | `research/eval/routed_eval.py` | 148 | T028-T033 (routing, provisional) | `tests/research/eval/test_routed_eval.py` |
| `nba/eval/seq_props_eval.py` | `research/eval/seq_props_eval.py` | 305 | T065-T072 | `tests/research/features/test_player_sequences.py` |
| `nba/eval/time_decay_tune.py` | `research/eval/time_decay_tune.py` | 411 | TIME_DECAY_2026-10-08.md; NO ledger row | `tests/research/features/test_time_decay_tune.py` |
| `nba/eval/tracking_screen.py` | `research/eval/tracking_screen.py` | 455 | T119-T123, T174-T175 (no family passes) | `tests/research/props/test_tracking_flag.py` |
| `nba/eval/usage_redistribution_eval.py` | `research/eval/usage_redistribution_eval.py` | 803 | T047 (inert null) | `tests/research/eval/test_usage_redistribution_eval.py`, `tests/research/eval/test_usage_redistribution_report_trigger.py` |
| `nba/eval/winprob_family_eval.py` | `research/eval/winprob_family_eval.py` | 675 | WINPROB_FAMILY.md; NO ledger row | `tests/research/models/test_winprob_family.py` |
| `nba/features/game_location_context.py` | `research/features/game_location_context.py` | 227 | unreferenced; CONTEXT_SCREEN_GAMES | `tests/research/features/test_game_context.py` |
| `nba/features/game_sets.py` | `research/features/game_sets.py` | 802 | T081 | `tests/research/features/test_game_sets.py` |
| `nba/features/opponent_ridge_v2.py` | `research/features/opponent_ridge_v2.py` | 1,116 | T098-T101 (the missingness leak lived here) | `tests/research/features/test_opponent_ridge_v2.py` |
| `nba/features/pbp_tokens.py` | `research/features/pbp_tokens.py` | 1,371 | T082-T084 | `tests/research/features/test_pbp_tokens.py` |
| `nba/features/played_baseline.py` | `research/features/played_baseline.py` | 105 | fair-rematch helper (T062-T064); the live recency average is props.baselines | `tests/research/features/test_played_only.py` |
| `nba/features/player_possession_features.py` | `research/features/player_possession_features.py` | 633 | built only for the sim branch of props.forward (SIM_STATS = ()); T001-T033, T061-T064 | `tests/research/features/test_played_only.py`, `tests/research/features/test_player_possession_features.py`, `tests/research/features/test_player_shot_rates_time_decay.py`, `tests/research/features/test_time_decay_tune.py` |
| `nba/features/player_rebound_assist_features.py` | `research/features/player_rebound_assist_features.py` | 363 | same: sim branch only | `tests/research/features/test_played_only.py`, `tests/research/features/test_player_rebound_assist_features.py`, `tests/research/sim/test_player_attribution_reb_ast.py` |
| `nba/features/player_sequences.py` | `research/features/player_sequences.py` | 793 | T065-T072 | `tests/research/features/test_player_sequences.py` |
| `nba/features/possession_step_features.py` | `research/features/possession_step_features.py` | 455 | rung-4 heads; NO ledger row | `tests/research/features/test_rung4_player_features.py`, `tests/research/models/test_rung4_stepheads.py`, `tests/research/sim/test_rung4_learned_heads.py` |
| `nba/features/rapm.py` | `research/features/rapm.py` | 287 | RAPM.md; NO ledger row | `tests/research/features/test_rapm.py` |
| `nba/features/time_decay.py` | `research/features/time_decay.py` | 335 | TIME_DECAY_2026-10-08.md; NO ledger row | `tests/research/features/test_player_shot_rates_time_decay.py`, `tests/research/features/test_time_decay.py`, `tests/research/features/test_time_decay_tune.py` |
| `nba/features/tracking_features.py` | `research/features/tracking_features.py` | 364 | T119-T123, T174-T175 | `tests/research/features/test_tracking_features.py`, `tests/research/props/test_tracking_flag.py` |
| `nba/models/colab/export_training_data.py` | `research/models/colab/export_training_data.py` | 110 | GPU export for rung 4 | none (module has no test of its own) |
| `nba/models/injury_elo_t30.py` | `research/models/injury_elo_t30.py` | 257 | T160-T161 (not kept) | `tests/research/models/test_injury_elo_t30.py` |
| `nba/models/rung4_stepheads.py` | `research/models/rung4_stepheads.py` | 590 | rung-4 heads lost; NO ledger row | `tests/research/models/test_rung4_stepheads.py`, `tests/research/sim/test_rung4_learned_heads.py` |
| `nba/models/winprob_family.py` | `research/models/winprob_family.py` | 1,400 | WINPROB_FAMILY.md; NO ledger row | `tests/research/models/test_winprob_family.py` |
| `nba/parlay/totals_poisson.py` | `research/parlay/totals_poisson.py` | 169 | unreferenced | `tests/research/parlay/test_totals_poisson.py` |
| `nba/preprocess/__init__.py` | `research/preprocess/__init__.py` | 12 | (colab job asset; see the job's row) | none (module has no test of its own) |
| `nba/preprocess/depth_chart.py` | `research/preprocess/depth_chart.py` | 243 | unreferenced; revive if a rookie-role prereg is written | `tests/research/preprocess/test_depth_chart.py` |
| `nba/preprocess/embeddings.py` | `research/preprocess/embeddings.py` | 240 | sequence embeddings, unreferenced; rung 5 idea, closed | `tests/research/preprocess/test_embeddings.py` |
| `nba/props/__main__.py` | `research/props/__main__.py` | 145 | same | `tests/research/props/test_cli.py` |
| `nba/props/coherence.py` | `research/props/coherence.py` | 322 | hierarchical reconciliation (spec phase 2); no ledger row | `tests/research/props/test_coherence.py`, `tests/research/props/test_run_fixture.py` |
| `nba/props/combos.py` | `research/props/combos.py` | 165 | PRA combos; no player-market for them | `tests/research/props/test_combos.py` |
| `nba/props/context_features_v2.py` | `research/props/context_features_v2.py` | 1,080 | T073-T080 (voided) | `tests/research/props/test_context_features_v2.py`, `tests/research/props/test_ctxres_v3.py` |
| `nba/props/dispersion.py` | `research/props/dispersion.py` | 192 | same | `tests/research/props/test_dispersion.py` |
| `nba/props/minutes_v2.py` | `research/props/minutes_v2.py` | 575 | T162-T173 | `tests/research/props/test_minutes_v2.py` |
| `nba/props/opponent.py` | `research/props/opponent.py` | 686 | M3A/T-family F matchup adjustment (not kept); props.config imports its config type - cut | `tests/research/props/test_opponent.py`, `tests/research/props/test_run_fixture.py` |
| `nba/props/report.py` | `research/props/report.py` | 297 | experiment report | `tests/research/props/test_dnp_nan_fix.py`, `tests/research/props/test_run_fixture.py`, `tests/research/props/test_volatility.py` |
| `nba/props/roster_replay.py` | `research/props/roster_replay.py` | 457 | OPENING_WEEK_ROSTERS study | `tests/research/props/test_roster_replay.py` |
| `nba/props/run.py` | `research/props/run.py` | 875 | experiment-1/2 runner (NegBin vs Normal) | `tests/research/eval/test_routed_eval.py`, `tests/research/props/test_dnp_nan_fix.py`, `tests/research/props/test_run_fixture.py`, `tests/research/props/test_volatility.py` |
| `nba/props/stat_models.py` | `research/props/stat_models.py` | 359 | experiment-2 stat models | `tests/research/props/test_points_centering.py`, `tests/research/props/test_stat_models.py` |
| `nba/props/volatility.py` | `research/props/volatility.py` | 270 | volatility buckets for routing, T005-T019 | `tests/research/props/test_volatility.py` |
| `nba/registry/routing.py` | `research/registry/routing.py` | 642 | ROUTING.md/ROUTER_V2.md, T028-T033; daily.predict imports it today | `tests/research/registry/test_routing.py`, `tests/research/stack/test_routing_stack.py` |
| `nba/sim/learned_heads.py` | `research/sim/learned_heads.py` | 484 | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 | `tests/research/sim/test_rung4_learned_heads.py` |
| `nba/sim/player_attribution.py` | `research/sim/player_attribution.py` | 873 | possession sim: T001-T033, T061-T064 (no props value, no win-prob value); sim.learned_heads = rung 4 | `tests/research/eval/test_usage_redistribution_eval.py`, `tests/research/sim/test_player_attribution.py`, `tests/research/sim/test_player_attribution_reb_ast.py`, `tests/research/sim/test_usage_redistribution.py` |
| `nba/sim/usage_redistribution.py` | `research/sim/usage_redistribution.py` | 545 | THE entanglement: 6 live modules import report-trigger code from a research file; remainder (~500 lines, boost/redistribution) to research (T047) | `tests/research/eval/test_usage_redistribution_eval.py`, `tests/research/eval/test_usage_redistribution_report_trigger.py`, `tests/research/features/test_pbp_tokens.py`, `tests/research/props/test_context_features_v2.py`, `tests/research/sim/test_usage_redistribution.py` |
| `nba/stack/adapters.py` | `research/stack/adapters.py` | 142 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/registry/test_routing.py`, `tests/research/stack/test_router.py`, `tests/research/stack/test_routing_stack.py` |
| `nba/stack/artifacts.py` | `research/stack/artifacts.py` | 127 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_routing_stack.py` |
| `nba/stack/cells.py` | `research/stack/cells.py` | 107 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_routing_stack.py` |
| `nba/stack/data.py` | `research/stack/data.py` | 102 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/registry/test_routing.py`, `tests/research/stack/test_router.py`, `tests/research/stack/test_routing_stack.py` |
| `nba/stack/evaluate.py` | `research/stack/evaluate.py` | 229 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_router.py` |
| `nba/stack/frozen.py` | `research/stack/frozen.py` | 119 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/registry/test_routing.py`, `tests/research/stack/test_routing_stack.py` |
| `nba/stack/holdout.py` | `research/stack/holdout.py` | 307 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_routing_stack.py` |
| `nba/stack/populate.py` | `research/stack/populate.py` | 383 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | none (module has no test of its own) |
| `nba/stack/routebuild.py` | `research/stack/routebuild.py` | 364 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/registry/test_routing.py` |
| `nba/stack/router.py` | `research/stack/router.py` | 329 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_router.py` |
| `nba/stack/scoring.py` | `research/stack/scoring.py` | 55 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_router.py` |
| `nba/stack/threshold.py` | `research/stack/threshold.py` | 114 | ROUTER_V2/stack: routes_draft only, none active; daily.predict imports frozen/populate lazily today | `tests/research/stack/test_routing_stack.py` |

## Moved by the Wave A follow-ups (2026-10-10)

The three cuts listed in `docs/NEXT_SESSION.md` ("Wave A follow-ups"), one change each, each gated by
ruff / mypy / the layering test / the full suite / a 20-date replay (hash in `docs/DECISIONS.md`).
Line counts are at the time of the move.

### Cut 1: the rung ladder (win-probability rungs 0-3)

`GateFailure`, `evaluate_gate`, `load_tolerances` and the tolerances path left `model_gate` for the new live
module `nba/registry/gate.py` (`nba/registry/ops.py` and `nba/registry/__main__.py` import it from there;
`research/registry/model_gate.py` re-exports the names). With that edge cut, the ladder that was anchored to
it moved. The production win-probability model is injury-Elo; the ladder is kept as the CI `smoke-backtest`
and `model-gate` fixture run (`make smoke`, `make gate`, now `python -m research.eval` /
`python -m research.registry.model_gate`).

| old path | new path | lines | verdict (ledger rows / docs) | tests in CI |
|---|---|---|---|---|
| `nba/registry/model_gate.py` | `research/registry/model_gate.py` | 201 (141 left; 60 moved to `nba/registry/gate.py`) | CI model-gate: fixture ladder vs. committed baseline | `tests/research/registry/test_model_gate.py` (the comparison itself: `tests/registry/test_gate.py`) |
| `nba/eval/__main__.py` | `research/eval/__main__.py` | 134 | rung-ladder entrypoint; `make smoke`/`backtest`/`report` | `tests/research/eval/test_eval_cli.py`, `tests/registry/test_smoke_guard.py` |
| `nba/eval/run.py` | `research/eval/run.py` | 397 | rung-ladder harness (rungs 0-3, walk-forward + frozen holdout) | `tests/research/eval/test_eval_entrypoint.py`, `tests/research/eval/test_holdout_sequential_eval.py` |
| `nba/eval/report.py` | `research/eval/report.py` | 165 | rung-ladder report.md | `tests/research/eval/test_eval_entrypoint.py` |
| `nba/eval/slices.py` | `research/eval/slices.py` | 65 | rung-ladder slice masks; not on the task list, moved because `run.py` was its only importer | `tests/research/features/test_coldstart_player_rates.py` |
| `nba/models/rung1_logistic.py` | `research/models/rung1_logistic.py` | 63 | rung 1 | `tests/ml/test_models_contract.py`, `tests/ml/test_game_context_features.py` (mixed files, import rewritten) |
| `nba/models/rung2_gbm.py` | `research/models/rung2_gbm.py` | 103 | rung 2 | same, plus `tests/research/eval/test_holdout_sequential_eval.py` |
| `nba/models/rung3_sim.py` | `research/models/rung3_sim.py` | 140 | rung 3 (possession sim; no win-prob value, T061-T064) | `tests/research/models/test_rung3_sim.py` |
| `nba/sim/engine.py` | `research/sim/engine.py` | 190 | possession Monte Carlo (T001-T033, T061-T064) | `tests/research/sim/test_sim_engine.py`, `tests/research/sim/test_rung4_learned_heads.py` |
| `nba/sim/possession_model.py` | `research/sim/possession_model.py` | 120 | possession outcome model (same) | `tests/research/sim/test_sim_possession_model.py` |
| `nba/features/possession_features.py` | `research/features/possession_features.py` | 310 | rung-3 / sim features | `tests/research/features/test_possession_features.py` |
| `nba/features/player_features.py` | `research/features/player_features.py` | 130 | cold-start flags for the ladder's slices | `tests/research/features/test_coldstart_player_rates.py` |
| `nba/coldstart/config.py` | `research/coldstart/config.py` | 85 | `ColdStartConfig` (cold-start threshold); `nba/coldstart/__init__.py` no longer re-exports it | `tests/research/features/test_coldstart_player_rates.py` |

Left in `nba/` on purpose: `nba/registry/smoke_guard.py` (no import of the ladder; it only reads the report
file `make smoke` writes; its test runs the ladder from `research.eval`) and the
`nba/registry/model_gate_config.yaml` tolerances that `nba/registry/gate.py` reads.

## Deleted (the one DELETE)

| path | lines | why |
|---|---|---|
| `nba/colab/jobs/ctxres_v3_hybrid/ctxres_v2_engine.py` | 1,843 | byte-identical to `ctxres_v2_sweep/ctxres_v2_sweep.py` (`cmp` confirmed before deletion); recoverable from tag `pre-restructure-2026-10-10`. `ctxres_v3_hybrid.py` and its `build_notebook.py` now read the engine from the sweep file directly; the recorded `ENGINE_SHA256` (80f67da0...) is unchanged because the sweep file's bytes were left exactly as committed (its docstring still says `nba/eval/ctxres_v2_eval.py`; that is the frozen text). |


## Classified RESEARCH in Appendix A but NOT moved (and why)

Wave A is a pure archive: a module leaves only when nothing left in `nba/` imports it. These still have an
importer in `nba/` (KEEP or EXTRACT code), so moving them would have forced a `nba -> research` import or a
code change on a live module. Each is one Wave-B-style edge cut away.

| module (still in nba/) | lines | anchored by | the cut that frees it |
|---|---|---|---|
| `nba/props/pts_tail.py` | 121 | `nba/eval/lower_tail_eval.py` (KEEP, shadow arm lt) and `nba/eval/f9b_young_pick.py` (F9b, in flight) import `raw_quantiles` | `raw_quantiles` is a live utility: move it into `nba/props/lower_tail.py`, then archive the rest with `research/eval/pts_tail_eval.py` |
| `nba/stack/oof.py`, `nba/stack/__init__.py` | 195 | `nba/eval/context_residual_eval.py` (KEEP, production backtest) imports `write_oof` lazily behind `--write-oof`; `FROZEN_SEASON` is re-exported by `research/stack/__init__.py` | drop the `--write-oof` flag from the production backtest (the OOF store only fed routing), then move both |
| `nba/parlay/independence_check.py`, `nba/parlay/joint_eval.py` | 1,135 | in the live closure: `nba/parlay/__main__.py` and `nba/daily/report.py` import them | not Wave A material at all; Appendix A's RESEARCH mark is wrong while the parlay CLI exposes them |

Not in Appendix A (written after the design doc) and left where they are: `nba/eval/f9_young_pick.py`,
`nba/eval/f9b_young_pick.py`, `nba/eval/f12_closing_risk.py` (active pre-registrations, F9b owned by its
agent tonight). The lt/int shadow-arm evals (`lower_tail_eval`, `integer_quantiles_eval`) and
`injury_elo_eval` / `context_residual_eval` are KEEP per Appendix A.

## Shims left in nba/

One new, for pickles: `nba/sim/usage_redistribution.py` re-exports `ReportTriggerConfig` from
`nba.features.injury_report` (live code only). Context-residual model caches written before 2026-10-10
(`data/models/context_residual/<date>/models.pkl`) pickled that class under the old module path; without the
shim a warm cache unpickles to `ModuleNotFoundError` and the daily job silently falls back to the recency
baseline (`tests/daily/test_review_fixes.py::test_shadow_failures_leave_primary_rows_unchanged` caught it
against the cache the test suite itself leaves under `data/models/`). Delete the shim once every cache on
disk post-dates the move. Otherwise none new. The pre-existing one-step shims stay and keep working: `nba/coldstart/shrinkage.py` and
`nba/coldstart/sb_classification.py` (re-export from `nba.features`), `nba/eval/metrics.py` and
`nba/eval/walkforward.py` (re-export from `nba.truth`). Moved research code was rewritten to the canonical
`nba.features.*` / `nba.truth.*` paths so nothing under `research/` imports a shim. `nba/sim/usage_redistribution.py`
was EXTRACT in Appendix A; its live half already lives in `nba/features/injury_report.py` (Wave B edges 1-3), so
the research remainder moved to `research/sim/usage_redistribution.py` (it keeps its own re-export of the
injury-report names for the research importers that still spell them that way).

`nba/odds/`, `nba/eval/f9b_young_pick.py` and `tests/eval/test_f9b_young_pick.py` import nothing that moved;
no shim was needed for them.

## The `route` subcommand

`python -m nba.registry route {show,build,validate,promote}` moved verbatim to
`python -m research.registry route ...` (`research/registry/__main__.py`), so `nba/registry/__main__.py` no
longer imports `registry.routing` or the stack. Nothing live called it (`ops/*.sh`, launchd plists, Makefile:
none; docs/ROUTING.md documents the old spelling). `tests/research/registry/test_routing.py` exercises the new
entrypoint.

## Tests

Moved tests live under `tests/research/<pkg>/`, mirroring `research/<pkg>/` (`git mv`, imports rewritten):

| from | to |
|---|---|
| `tests/colab/test_colab.py` | `tests/research/colab/` |
| `tests/ml/test_archetype_asof.py`, `test_coldstart_archetypes.py`, `test_coldstart_carryover.py`, `test_coldstart_rookie_priors.py` | `tests/research/coldstart/` |
| `tests/ml/test_ga_tuner.py`, `test_model_routing.py`, `test_routed_eval.py`, `test_player_points_sim_eval.py`, `test_player_reb_ast_sim_eval.py`, `test_usage_redistribution_eval.py`, `test_usage_redistribution_report_trigger.py`; `tests/eval/test_ctxres_v3_leakfree.py`; `tests/props/test_pts_tail.py` | `tests/research/eval/` |
| `tests/ml/test_player_possession_features.py`, `test_player_rebound_assist_features.py`, `test_rung4_player_features.py`; `tests/features/test_game_context.py`, `test_game_sets.py`, `test_opponent_ridge_v2.py`, `test_pbp_tokens.py`, `test_player_sequences.py`, `test_rapm.py`, `test_tracking_features.py`, `test_time_decay.py`, `test_time_decay_tune.py`, `test_player_shot_rates_time_decay.py`, `test_played_only.py` | `tests/research/features/` |
| `tests/ml/test_injury_elo_t30.py`, `test_winprob_family.py`, `test_rung4_stepheads.py` | `tests/research/models/` |
| `tests/parlay/test_totals_poisson.py` | `tests/research/parlay/` |
| `tests/preprocess/*` | `tests/research/preprocess/` |
| `tests/props/test_cli.py`, `test_coherence.py`, `test_combos.py`, `test_context_features_v2.py`, `test_ctxres_v2_descriptive.py`, `test_ctxres_v3.py`, `test_dispersion.py`, `test_dnp_nan_fix.py`, `test_lineups_known_eval.py`, `test_minutes_v2.py`, `test_opponent.py`, `test_points_centering.py`, `test_roster_replay.py`, `test_run_fixture.py`, `test_stat_models.py`, `test_tracking_flag.py`, `test_volatility.py` | `tests/research/props/` |
| `tests/registry/test_routing.py` | `tests/research/registry/` |
| `tests/ml/test_player_attribution.py`, `test_player_attribution_reb_ast.py`, `test_usage_redistribution.py`, `test_rung4_learned_heads.py` | `tests/research/sim/` |
| `tests/stack/*` | `tests/research/stack/` |

Two tests that mix live and research imports stayed put with one import rewritten:
`tests/features/test_player_pedigree.py` (`research.eval.context_screen_players`) and
`tests/props/test_minutes_learned_context_extra_features.py` (`research.preprocess.depth_chart`).
No moved test is skipped. The torch split in `Makefile` and `.github/workflows/ci.yml` now names
`tests/research/models/test_rung4_stepheads.py`; it must stay in its own process and must not load LightGBM
first (a `tests/research/conftest.py` importing lightgbm segfaulted it, so there is none).

## Build configuration touched

`pyproject.toml`: `research*` is a package; `--cov=nba --cov=research` and `source = ["nba", "research"]` keep
the 70% ratchet measured on the same code as before; ruff `src`/`known-first-party` and mypy `files` include
`research`, with `research.registry.*` and `research.parlay.*` strict as their `nba` homes were.
`Makefile`: `lint` covers `research/`; `colab-*` targets call `python -m research.colab`. `.pre-commit-config.yaml`
mypy runs on both trees. `Dockerfile` copies `research/`. `make smoke`, `make backtest`, `make report` and `make gate` call
`python -m research.eval` / `python -m research.registry.model_gate` since the Wave A follow-up cut 1; CI reaches
them through those targets, so `.github/workflows/ci.yml` is unchanged.
