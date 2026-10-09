# Missingness leak audit, 2026-10-09 (data steward, read-only)

Method: for every file, NULL rate per column split by played (`player_game_stats.minutes >= 5`) vs
unplayed (`minutes < 5`), flag at asymmetry >= 0.5 (manifest rule, both groups >= 200 rows); and by
label group (label above/below median of the file's own label column, per stat for OOF files;
home win/loss for game-level files), flag at >= 0.2. The manifest tool has no label-group check,
so both checks were run by a scratch script outside the repo (DuckDB, 1.5 GB cap, one file at a
time, nba.duckdb exported once read-only and released). Official `snapshot --parquet` + `check`
was also run on the 8 key files (data_version 1b5e52b65dcc) and agrees on the played split.

## Scope
- Scanned 116 sources: 114 parquet under data/ (data/colab, data/games, data/injury_elo,
  data/matchups, data/officials, data/rapm, data/schedule, data/shots, data/winprob_family),
  reports/context_residual/oof_context_residual.parquet (production props OOF), and
  data/stack/oof.duckdb table oof_predictions (read-only). Largest file 126 MB; no file > 2 GB.
- Not scanned: data/colab/rclone_stage (168 files, md5-identical to the scanned copies under
  data/colab/runs, data/colab/*), data/players_static (891) , data/national-tv (825) and
  data/coaches (3) small per-entity ingest caches, data/boxscore, pbp, tracking, hustle, kalshi.
  No data/props or data/rapm player-game parquet exists (rapm/snapshots.parquet is player-date).
- Heavy lock data/ops/heavy.lock was held during the scans and released.

## Flags (3 files, same 8 columns each)
| file | rows (played / unplayed) | columns | NULL played vs unplayed | asymmetry |
|---|---|---|---|---|
| data/colab/ctxres_v2/ctxres_v2.parquet | 80,312 (73,877 / 6,435) | ridge_player_{pts,reb,ast,fg3m}, ridge_opp_{pts,reb,ast,fg3m} | 0.000 vs 1.000 | 1.00 |
| data/colab/runs_local/ctxres_v2_exp2_20261008/ctxres_v2.parquet (identical copy) | same | same | same | 1.00 |
| data/colab/ctxres_v3/ctxres_v3_with2025.parquet | 108,383 (99,676 / 8,707) | same 8 | 0.000 vs 1.000 | 1.00 |

Mechanism confirmed in code: `nba/props/context_features_v2.py` (~line 725) filters the ridge frame
to `minutes >= 5` (tonight's minutes) before computing the monthly ridge, so the left join leaves
NaN for every low-minute row. Already documented in docs/RIDGE_V2.md section 0.

Label view (ctxres_v2, n=80,312): the label-group check at 0.2 does NOT fire, because only 8% of
rows are NULL. Gaps: ridge_player_pts NULL 0.154 (pts <= 9, n=41,759) vs 0.0001 (pts > 9, n=38,553);
ast 0.128 vs 0.0005 (n=50,150 / 30,162); fg3m 0.168 (fg3m = 0, n=34,982) vs 0.012 (fg3m > 0, n=45,330).
Suggest lowering the label threshold to ~0.1 or testing NULL-vs-label standardised gap instead.

Consumers (grep of nba/ and docs/):
- ctxres_v2.parquet + feature_spec (group i = ridge_*, in v2_features): nba/colab/jobs/ctxres_v2_sweep
  (exp-2 run 20261008_171446; metrics.json leak_audit lists ridge_player_pts/ridge_opp_pts in top gain
  and SHAP and did not catch it), nba/eval/ctxres_v2_eval.py, ctxres_v2_descriptive.py,
  ctxres_v2_coverage_sim.py, backlog_small_eval.py, nba/colab/jobs/ctxres_v3_hybrid (reads
  ctxres_v2.parquet via ctxres_v2_engine.py, same spec, "exp-2 feature set unchanged") and
  nba/eval/ctxres_v3_eval.py (2025 touch).
- Downstream stores: oof.duckdb models ctxres_v2_xgb_v12_poisson_nb (27,583 rows per stat) and the
  ctxres_v2 route in docs/ROUTING.md (`ext` set, pts/reb/ast/fg3m, 2024-only), exp-2 verdict
  (docs/CTXRES_V2_EXP2_RESULTS.md, group-i ablation "helps" +0.0664 pts), the exp-3 hybrid result
  logged in docs/HOLDOUT_ACCESS_LOG.md. docs/CTXRES_V2_EXP2_RESULTS.md does not mention the leak;
  docs/RIDGE_V2.md says the exp-2 verdict was computed with the leaky column.
- Replacement: data/colab/ridge_v2/ridge_v2.parquet (rg_* columns) is NOT flagged: NULL 0.061 played
  vs 0.034 unplayed (asym 0.027; warm-up months only), 0.057 vs 0.060 across pts groups.

Production impact: NONE found.
- props_context_residual (production): its OOF reports/context_residual/oof_context_residual.parquet
  (220,808 rows; 202,060 played / 18,748 unplayed, box join 100%) has no flagged column; the module
  nba/props/context_residual.py and nba/daily/predict.py contain no reference to ridge_* or ctxres_v2
  (predict.py uses CONTEXT_PROPS_VERSION ctxres-v1; ctxres_v2 is opt-in routing only, no active route).
- rung0_injury_elo (production win): data/injury_elo/oof_predictions.parquet (7,902 rows) and
  winprob_family/oof_all.parquet (3,951 games; 2,204 home wins / 1,747 losses) have no NULL pattern by
  outcome. oof.duckdb rung0 rows: p non-null, p_play/mean/q_grid NULL for all 7,902 (constant by model).
- Affected (research, not production): ctxres_v2 exp-2 candidate, ctxres_v3 hybrid, and any router
  or stack built on them. Their reported gains, especially pts, are suspect until re-run on
  ref_fixed ridge_v2 features.

## Sub-threshold watch items (not flags)
- ctxres_v2/v3 `yrs_since_draft`: NULL 0.148 played vs 0.305 unplayed (asym 0.157); by pts group
  0.225 (<= 9) vs 0.090 (> 9) (gap 0.135). Static player attribute (undrafted), not tonight-dependent,
  but correlated with role. RIDGE_V2.md already lists it as the next largest gap.
- oof.duckdb: p and p_play NULL patterns are per model/target constants (props models p NULL 100%;
  season_avg_* p_play non-null 100%); played 0.575 vs unplayed 0.584 for p_play NULL (asym 0.01).
- ctxres_sweep oof_2024 (1,323,984 and 992,988 rows) and 73 exp-2 OOF variant files (110,332 rows each;
  101,116 played / 9,216 unplayed): q19/p_ge NULL for whole variants (structural), identical across
  played/unplayed and y groups. possession_steps.parquet (1,049,738 rows): shot_zone NULL 21.5%,
  no win/loss gap >= 0.2. matchups def_team_id 100% NULL (all rows). schedule raw_* pointsLeaders_*
  >92% NULL (pre-game schedule feed, not a model input).
- Files with no player or game key (eval_legs, eval_singles, *_preds, rapm snapshots): only
  overall NULL scan; nothing > 5% except those above.

## Clean
113 of 116 sources have no flag (3 flagged: the two ctxres_v2 copies and ctxres_v3).
No data, parquet, nba.duckdb, or earlier changelog entry was modified. nba/ and configs untouched.

## Proposals (not applied)
- Add ctxres_v2.parquet ridge_* and ctxres_v3 equivalents to `known_asymmetry` only after a decision to
  keep the files for reference; otherwise mark them deprecated in docs/CTXRES_V2_EXP2_RESULTS.md.
- Add a label-group NULL check (standardised gap) to `datamanifest check`.
