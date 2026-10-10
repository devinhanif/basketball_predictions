# Data changelog (append-only; written by the archivist agent)

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

## 2026-10-10 UTC - data_version ff1db81f173a (lineup tracker v2 in game order; F9b player columns)
- `stints` (297,159 -> 298,394 rows) and `possessions.off_players/def_players` rewritten again for all
  5,269 games with events ordered by (period, clock) instead of `action_number` (ADR 0002 item 5): the
  first v2 rebuild had 957 negative-duration stints from corrections the feed appends after the game.
  Stint minutes within 1.0 min of box minutes: 2022 98.0% -> 99.1%, 2023 97.6% -> 98.7%,
  2024 97.3% -> 98.6%, 2025 96.6% -> 98.4%. Negative-duration stints 957 -> 0. About 1% of possession
  lineups changed versus e9b76c58a2d3. Pre-write snapshot: `data/backups/lineups_pre_fix_20261010T014851Z_*`.
- `players_static`: columns `first_season` (CommonPlayerInfo FROM_YEAR) and `draft_status`
  ('drafted' | 'undrafted' | 'unknown'; NULL = not re-pulled) added; 687 rows upserted from a 660-player
  re-pull. Active players (>= 20 games in 2023-24, n = 498): first_season 100%, draft_status 100%
  (394 drafted, 104 undrafted). Three active players are 'drafted' with no stored pick.
- Flags: none.

## 2026-10-10 UTC - data_version 85d8a41277ce (possessions in game order)
- `possessions` (1,049,740 -> 1,047,292 rows, -2,448) re-derived for all 5,269 games from the cached
  play-by-play ordered by (period, clock, substitutions first, action number) instead of
  `action_number` (ADR 0002 item 5, roadmap item 8; `python -m nba.parse.rebuild_possessions --write`).
  `poss_idx` is renumbered in 4,374 games (360,765 rows change identity; 2,987 games change count);
  `off_players`/`def_players` re-attached from the stored (clock-ordered) `stints`. `stints` unchanged
  (298,394 rows). No other table changed.
- Possessions ending after they start 5,789 -> 0; starting after the previous one ended 3,939 -> 0;
  games with such a defect 4,258 -> 0. Two-team balance mean |A-B| 1.347 -> 1.145 (median 1, p95 3);
  possessions per team-game 99.60 -> 99.36; formula `FGA+0.44FTA+TOV-OREB` mean abs error 2.007 -> 2.067
  (signed -1.49 -> -1.72; the formula is the noisy side, ADR 0001); parsed points equal final score 99.25%
  before and after; rows with a NULL lineup side 2 -> 2 (the same two team-level possessions).
- Flags: `make data-check` ROW_DROP possessions 1,049,740 -> 1,047,292, intended (spurious trips cut by the
  displaced events are gone); not allowlisted. The preceding snapshot d496326499e4 (2026-10-10 14:46Z)
  differs from the previously logged ff1db81f173a by changes this entry does not describe.
- Pre-write backup: `data/backups/possessions_pre_order_fix_20261010T145505Z_possessions.parquet`
  (full table, 17 columns). Stale: `data/colab/possession_steps.parquet` (keyed on the old `poss_idx`) and
  any research output joined on `(game_id, poss_idx)`. Details: docs/reviews/possession_order_2026-10-10.md.

## 2026-10-10 UTC - data_version d7dea7140c8f (game_officials: four duplicate official_ids merged)
- DRAFT (committing is the main session's act). `game_officials`: 6 rows rewritten, nothing else. Four
  officials carried two `official_id`s each (gate G0 item 2, ledger T202); the minority id of each pair
  was replaced by the id with more game slots (tie: lower id). Agon Abazi 196295108 -> 1629171 (1 row),
  Biniam Maru 11629177 -> 1629177 (1), Brent Haskill 29012 -> 1629175 (2), Intae Hwang 29032 -> 1630886 (2).
  No game listed both ids of a pair. Mapping: `configs/officials_id_merges.yaml`; the collector
  (`OfficialIndex.from_rows`) and the postgame loader (`load_frames`, source `officials`) apply it to new rows.
- Rows 12,031 -> 12,031; games 3,953 -> 3,953; distinct ids 87 -> 83; distinct names 83 -> 83. Per-name slot counts
  and the (game_id, name, jersey) multiset unchanged; 12,025 untouched rows hash identically before and after.
  Every name now maps to one id and every id to one name. Update took 0.4 s under `data/ops/heavy.lock`.
- Only `game_officials` differs in the table fingerprints between 85d8a41277ce and d7dea7140c8f.
- Flags: none (`make data-check`: 0 flags).
- Pre-write backup: `data/backups/game_officials_pre_id_merge_20261010T183149Z.parquet` (full table).
  Stale: `data/refs/official_ids.json` (cache lists the old ids; canonicalised on read, refreshed on the next
  successful `load_official_rows`) and any research output keyed on the old minority ids (only
  `reports/referees/g0.json`, which recorded the failure).

## 2026-10-10 UTC - data_version 1c5b0901afdf (possessions: n_oreb, n_dreb counts; oreb flag corrected)
- `possessions` gains `n_oreb` (player-credited offensive rebounds of the offense in the trip) and `n_dreb` (1 if a
  player-credited defensive rebound by the defense ended the trip); `oreb` now equals `n_oreb > 0` (team/dead-ball
  rebounds no longer count: the old flag was 1.33x box OREB). All 1,047,292 rows updated in place; no other column
  changed (verified); backup `data/backups/possessions_pre_oreb_fix_20261010T191702Z_possessions.parquet`.
- Reconciliation with box scores, seasons 2022-24: OREB 27,524 / 27,741 / 29,219 vs 27,534 / 27,749 / 29,223;
  DREB 87,096 / 86,748 / 86,506 vs 87,100 / 86,753 / 86,507. Flags: COLUMN_ADDED x2 expected; nothing else.

- 2026-10-10: `data/odds/odds_history.duckdb` gains market `player_steals` for seasons 2023 and 2024 (520,220 rows; raw cache under
  data/odds/raw/the_odds_api keyed by market set). 2,682 rows unresolved (vendor name variants, mostly 2023), not guessed.

## 2026-10-10 UTC - facts store (new derived DB; nba.duckdb unchanged)
- DRAFT (committing is the main session's act). New `data/facts/facts.duckdb` (gitignored, rebuildable) from
  `python -m nba.facts build`; no manifest registered, no table in `nba.duckdb` read for write. Source opened read_only,
  season <= 2024 only. Facts per predicate: tips_at 3,961; home_team 3,962; away_team 3,962; listed_status 87,530;
  announced_starter 2,884; played_minutes 103,785; position 683; height_in 683; first_season 683; head_coach 87.
  Entities 5,042 (players 1,008, games 3,962, coaches 42, teams 30).
- Known gaps: 208 players_static rows lack first_season (no static facts); head_coach confidence 0.5 (table wrong in
  14 of 87 team-seasons). known_at choices are in `docs/FACTS_STORE.md`.
