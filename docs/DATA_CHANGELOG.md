# Data changelog (append-only; written by the data-steward agent)

## 2026-10-09 UTC - data_version 92bb30586d64 (baseline, before post-game backfill load)
- First snapshot. 19 tables in nba.duckdb; the post-game tables (tracking, hustle, officials,
  matchups, shots, coaches) are not loaded yet. games 5,269; player_game_stats 138,409;
  possessions 1,049,740; prop_predictions 1,107,272.
- Flags: POSSIBLE_LEAK on 8 `ridge_*` columns of data/colab/ctxres_v2/ctxres_v2.parquet
  (NULL rate 0.0 played vs 1.0 unplayed, n=80,312 rows) - the known opp_adjusted_ridge pattern.
  Nothing flagged in the DB tables.

## 2026-10-09 UTC - data_version 1b5e52b65dcc (repo-wide missingness leak audit)
- Snapshot with 8 parquet files (ctxres_v2, ctxres_v3_with2025, ridge_v2, seq_props_meta, seq_props
  oof_2024, ctxres_v2_sweep oof_2024, reports/context_residual oof, winprob_family oof_all);
  data_version moved from 92bb30586d64 (post-game tables now loaded; cache diffs: data/tracking
  files 1210 -> 1932, data/kalshi 1386 -> 1498, data/backups and data/rehearsal added).
- Scratch audit (not in nba/) over 115 parquet files + data/stack/oof.duckdb (oof_predictions,
  778,622 rows): played split (minutes >= 5, flag at >= 0.5) and label split (above/below median or
  win/loss, flag at >= 0.2). Only flags: the 8 `ridge_*` columns in ctxres_v2.parquet (n=80,312;
  played 73,877 / unplayed 6,435), the byte-identical-schema ctxres_v3_with2025.parquet (n=108,383;
  99,676 / 8,707) and runs_local/ctxres_v2_exp2_20261008/ctxres_v2.parquet (copy). NULL rate 0.0
  played vs 1.0 unplayed. `check` exit 1, 32 POSSIBLE_LEAK (16 per file). Nothing else flags.
- Escalation: - [data-steward] POSSIBLE_LEAK ctxres_v2/ctxres_v3 ridge_* | options: investigate /
  allowlist in configs/datamanifest.yaml (known_asymmetry) | default I will use: investigate
  (already documented in docs/RIDGE_V2.md; details in docs/reviews/leak_audit_2026-10-09.md).

## 2026-10-09 UTC - data_version e9b76c58a2d3 (lineup tracker v2 rebuild)
- `stints` (366,338 -> 297,159 rows) and `possessions.off_players/def_players` (1,049,740 rows; 2 NULL, as before)
  rewritten for all 5,269 games with the look-ahead tracker (ADR 0002). No other column changed.
- Stint minutes within 1.0 min of box minutes: 2022 49.1% -> 98.0%, 2023 47.6% -> 97.6%,
  2024 46.9% -> 97.3%, 2025 44.8% -> 96.6%. Old-vs-new offense five equal on 80.2% of possessions.
- Pre-fix backup: data/backups/lineups_pre_fix_20261010T011007Z_{stints,possessions}.parquet.
  Details: docs/reviews/stint_rebuild_2026-10-09.md. Lineup-based research (F11/F13/F15/F17, rapm) predates this.
