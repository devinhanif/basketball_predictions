# Data changelog (append-only; written by the data-steward agent)

## 2026-10-09 UTC - data_version 92bb30586d64 (baseline, before post-game backfill load)
- First snapshot. 19 tables in nba.duckdb; the post-game tables (tracking, hustle, officials,
  matchups, shots, coaches) are not loaded yet. games 5,269; player_game_stats 138,409;
  possessions 1,049,740; prop_predictions 1,107,272.
- Flags: POSSIBLE_LEAK on 8 `ridge_*` columns of data/colab/ctxres_v2/ctxres_v2.parquet
  (NULL rate 0.0 played vs 1.0 unplayed, n=80,312 rows) - the known opp_adjusted_ridge pattern.
  Nothing flagged in the DB tables.
