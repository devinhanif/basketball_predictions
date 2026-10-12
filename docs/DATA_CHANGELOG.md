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
- 2026-10-11: configs/kalshi_aliases.yaml gains 61 reviewed vendor spellings from the steals pull (Devin: "paste AUTO"); both steals
  seasons reparsed from cache (0 credits). Unresolved player_steals rows 2,682 → 348 (the 11 REVIEW names, 2023 only).
- 2026-10-11: 9 REVIEW vendor names added under Devin's team rule; 2023 steals reparsed (0 credits). Unresolved player_steals rows
  348 → 92 (J. Green, 90 rows: Jalen and Jeff Green on the same HOU roster; bare "Thompson", 2 rows). Drive folder
  gdrive:nba_colab/ridge_v2_sweep (4.397 GiB) deleted on Devin's instruction.

## 2026-10-11 UTC - F2 neutral-site games (data_version: assigned at the end-of-pass snapshot, pending)
- DRAFT (archivist; the main session commits). `games` 5,269 -> 5,279: the 10 neutral-site regular-season
  games absent since ingest (2024: 0022400147 Mexico City, 0022400621 and 0022400633 Paris, 0022401229 and
  0022401230 Cup semifinals; 2025: 0022500147 Mexico City, 0022500578 Berlin, 0022500602 London, 0022501229
  and 0022501230 Cup semifinals). Cause: `_normalize_games_frame` split home/away on "@", but neutral-site
  rows carry the same "A @ B" string on both teams, so the join dropped them. Fixed by deriving home from
  the abbreviation after "@" (test `tests/ingest/test_games_neutral.py`). Loaded through the existing
  ingest functions (2 LeagueGameFinder + 10 box + 10 play-by-play requests, 6 s apart); `player_game_stats`
  +273 rows, possessions/stints parsed for the 10 games with the parser of that moment (stints are
  rebuilt for all games in the F4 step).
- Gate before -> after: regular-season games per team = 82 for 2022-25, teams off 15 -> 0; possession points
  == box points in the 10 new games 0/0 -> 20/20 team-games; `player_availability` rows with NULL `game_id`
  336 -> 188 (148 resolved to the new games by player team as of the report date). The audit's claim that all
  336 were neutral-site games was wrong: the remaining 188 are other cases (136 have no game for the player's
  team on the report date or the next day, for example postponed games, 52 have a candidate game but did not
  resolve uniquely). Left NULL, to be investigated; not a gate of this fix.
- Pre-fix backups: data/backups/games_pre_f2_20261010T235642Z.parquet (5,269 rows),
  data/backups/player_availability_pre_f2_20261010T235642Z.parquet (the 336 NULL-game rows).
- Season 2025 rows (5 games) were added by the same code; they were not evaluated or scored (not a holdout touch).

## 2026-10-11 UTC - F6/F7 possession parser fixes (data_version: assigned at the end-of-pass snapshot, pending)
- DRAFT (archivist; the main session commits). `possessions` rows replaced for 81 games only (the games whose
  re-parse differs from the stored rows, checked: every one is a Missi free-throw game (79) or a team-0 game (2);
  no other game's non-lineup columns changed). F6: `_is_miss` read "Missi Free Throw ..." as a missed free
  throw (`startswith("MISS")`), now requires the `MISS ` token. F7: a team-level turnover with team_id 0 and
  player_id 0 no longer opens an `off_team = 0` possession (attached to the open trip's offense, else skipped).
- Gate before -> after: team-games with possession points == box points 10,479 -> 10,558 of 10,558 (100%);
  possession rows with team 0 or NULL `off_players` 2 -> 0; team-games under 80 possessions 2 -> 0.
  Lineup columns of those games re-attached from the stored (unchanged) stints.
- Pre-fix backup: data/backups/possessions_pre_f6f7_20261011T001158Z.parquet (16,148 rows, the 81 games).
- Season 2025 rows were re-parsed by the same code and not evaluated (not a holdout touch).

## 2026-10-11 UTC - F4/F8 stints rebuilt with season-wide names and stint_idx (data_version 1bee33d02ff2)
- DRAFT. `stints` 299,083 -> 300,087 rows for all 5,279 games, new column `stint_idx` (0-based order within
  game, team, period); `possessions.off_players/def_players` re-attached (no other possession column touched).
  Cause fixed: a garbage-time substitute who never acts in a game had no name in that game's play-by-play, so
  his "SUB: <surname> FOR ..." entry was unresolvable and he never entered the tracked five; the name book is
  now seeded with the name the feed uses for him in other games (`nba/parse/rebuild_lineups.build_name_map`).
- Gate before -> after: played player-games with box >= 1 min and no stint row 671 -> 91 (target revised from
  40 to 91 after the full run; the remainder is the documented same-surname-teammates case, a later fix);
  within 1.0 min of box 2022 99.10% -> 99.92%, 2023 98.71% -> 99.89%, 2024 98.60% -> 99.58%,
  2025 98.38% -> 99.43% (overall 99.70%); team-period tiling exact for 42,838 of 42,838; stint key
  (game, team, period, stint_idx) duplicates 0; DNP players with a stint 0; unresolved subs 175, desyncs 99.
- Pre-fix backups: data/backups/lineups_pre_fix_20261011T001445Z_stints.parquet and
  data/backups/lineups_pre_fix_20261011T001445Z_possessions.parquet. The write ran 19:14-19:16 local, so the
  last chunks overlapped the xx:15 pretip window by about a minute (the window check ran before the backup).
- Season 2025 rows rebuilt by the same code, not evaluated (not a holdout touch).

## 2026-10-11 UTC - F5 team_coach_games (data_version 1bee33d02ff2)
- DRAFT. New table `team_coach_games` (10,303 rows; 7,916 verified) built by `nba/data/coaches.py` from the
  reviewed `nba/data/head_coach_reference.csv` (Basketball-Reference, 2022-24, hand-copied, unverified beyond
  82-game sums); `known_at` = real tip for verified rows, NULL for the 2,387 unverified 2025 rows (taken from
  `team_coaches`, 3 teams missing). `team_coaches` untouched.
- Gate: 87 team-seasons compared and exactly 14 disagreements with `team_coaches` (11 of them the next
  season's coach); every 2022-24 game has exactly 2 coach rows (3,958 games); every reference team-season's
  rows equal its games (90 of 90). New table, so no backup; rollback = DROP/DELETE of the table.

## 2026-10-11 UTC - F10/F11 players_static refresh and cache quarantine (data_version 1bee33d02ff2)
- DRAFT. 204 legacy rows re-pulled (CommonPlayerInfo, 204 requests at 6 s, 0 failures) and 208 rows upserted.
  `first_season` NULL 208 -> 4 (undrafted players with no FROM_YEAR from the feed), `draft_status` NULL
  204 -> 0. 30 zero-column cache files (ids 12737..12766, a team id passed as a player id) moved to
  data/quarantine/players_static_zero_column_20261011/; the puller now refuses to cache an empty frame.
- Pre-fix backup: data/backups/players_static_pre_f10_20261011T005643Z.parquet (891 rows).

## 2026-10-11 UTC - F1 lineup_snapshots_latest view (data_version 1bee33d02ff2)
- DRAFT. View in data/lineups/lineups.duckdb (no table or row changed; the raw snapshots stay append-only):
  one row per (snapshot, game, team, player), starter = OR over duplicates, status from the latest source_ts.
  Gate: duplicate groups 240 (60 team-snapshots at the end) -> 0 in the view; every team-snapshot has exactly
  5 starters. The `t30.py` reader change is the steward's.

## 2026-10-11 UTC - end-of-pass snapshot: data_version 1bee33d02ff2
- Covers the entries above and the F2 and F6/F7 entries of this pass, whose data_version was pending when
  written (the append-only log is not edited). `make data-check` exits 2 with 2 flags, both intended:
  NULL_SHIFT players_static.draft_status 0.229 -> 0.0 and players_static.first_season 0.233 -> 0.0045 (F10).
  Not allowlisted. Replay-oracle golden hash needs re-baselining (data changed, code did not).
- 2026-10-11: replay-oracle golden re-baselined on data_version 1bee33d02ff2: sha256 030804df… (46,658 predictions, 44,562 scores;
  docs/reviews/replay_oracle_2026-10-09.md). Code unchanged; the delta is the restored games and rebuilt lineups.
- 2026-10-11 02:56 CT: `game_era_flags` materialised in nba.duckdb: 5,279 rows (one per game, seasons 2022–2025), all four flags
  False, equal to `compute_era_flags` (rule version: nba/ingest/era_flags.py at commit fdda2fd, file sha256 6a483d1b; rule: 2019-20
  shortened_season, bubble and no_fans from 2020-07-30; 2020-21 shortened_season and blanket limited_fans; all later seasons
  False). History-DB rows for 2019–21 and the record parquet follow when the ingest queue releases the history file (HISTORY_WINDOW
  gate G0.2). No evaluation; 2025 rows are flags only.

## 2026-10-11 UTC - F4b same-surname stint rule: stints rebuilt (data_version 418510d401d3)
- DRAFT. `_NameBook` gains alias evidence (nba/parse/lineups.py): the out side of every substitution carries the exact
  player_id and the text the feed prints for him, so a corpus of those texts (`build_sub_alias_map` in
  nba/parse/rebuild_lineups.py, all 5,279 games) separates same-surname teammates by observed initial ("T. Martin" /
  "J. Martin", "C./M./I. Jones", "G./T. Antetokounmpo") and names a sub-in whose text differs from the feed's name for him
  (the feed calls Yang Hansen "Yang"; the text says "Hansen"). It only eliminates candidates with contrary observations and
  never guesses on absence; jersey numbers are not in the feed. Existing rules (game-wide initials, off-floor, roster
  elimination) run unchanged when alias evidence is silent.
- Gate (data/scratch/data_fix_2026-10-11/f4b_stints.py): played player-games (box >= 1 min) with no stint row 91 -> 1 (gate
  <= 20); within-1-minute share by season 2022/23/24/25 99.92/99.89/99.58/99.43% -> 99.98/99.97/99.98/99.95% (no season lower);
  tiling exact (0 bad of 42,838 team-periods); (game, team, period, stint_idx) duplicates 0; DNP players with a stint 0;
  unresolved subs 175 -> 7, desyncs 99 -> 15; stint rows 300,087 -> 300,339; possessions with NULL off_players 0.
  Residual: 0022501160 ("A. Antetokounmpo" in Q4; the one remaining missing player-game).
- Pre-fix backups: data/backups/lineups_pre_fix_20261011T082617Z_stints.parquet and
  data/backups/lineups_pre_fix_20261011T082617Z_possessions.parquet. Written 03:26-03:27 local, outside both pretip windows,
  under data/ops/heavy.lock, through `connect_with_retry`.
- Season 2025 rows rebuilt by the same code, not evaluated (not a holdout touch). Anything cached from the previous stints
  (replay-oracle golden, minutes-hazard frames) predates this rebuild.

## 2026-10-11 UTC - F2b player_availability: 21 of 188 NULL game_id rows resolved (data_version 418510d401d3)
- DRAFT. Evidence: the official report PDF's own (game date, matchup, team) for each row (188 of 188 rows map to exactly one
  record), not a team-as-of-box guess. Rule: (a) the report's (date, away@home) is a game in `games`; else (b) exactly one
  schedule game of that home/away pair dated within 7 days after the report date and present in `games`. 21 rows set: 0022400147
  (MIA@WAS, 6; a neutral-site game restored by F2 whose rows the earlier team-based pass missed), 0022500578 (MEM@ORL, 8),
  0022500602 (ORL@MEM, 7).
- Still NULL, 167 rows by reason: rescheduled_beyond_7d 156 (WAS@DET 2023-02-01 21; GSW@UTA 2024-01-17 9; CHA@LAL 2025-01-09 10;
  HOU@ATL 2025-01-11 14; MIL@NOP 2025-01-22 35; MIA@CHI 2026-01-08 39; DEN@MEM 2026-01-25 22; DAL@MIL 2026-01-25 6; each
  postponed 5-70 days, to a game whose as_of-to-tip gap would put a stale status on it); multiple_in_window 7 (GSW@MIN
  2026-01-24: 0022500644 on 01-25 and 0022500663 on 01-26 are both MIN-GSW; game-id order puts 0022500644 in the 01-24 slot, an
  inference left for Devin); game_type_not_in_games 4 (MIL@OKC 2024-12-17 is the NBA Cup final 0062400001, a 006 game
  that `games` does not keep).
- Gate: previously resolved rows changed 0 (118,050 compared); every new game_id is in `games`, involves the report's team, and
  is dated 0-1 days after the report date; table rows 118,238 -> 118,238; NULL game_id 188 -> 167.
- Pre-fix backup: data/backups/player_availability_pre_f2b_20261011T082721Z.parquet (the 188 rows). Written 03:27 local.
- 2026-10-11 03:27 CT: end-of-pass snapshot data_version 418510d401d3 covers both entries above; `make data-check` 0 flags
  (exit 0). The replay-oracle golden hash (030804df...) was computed before this rebuild and needs re-baselining.
- 2026-10-11: replay golden re-checked on data_version 418510d401d3: unchanged (030804df…; oracle_golden3).

## 2026-10-11 11:04 CT - history injury reports now start 2019-10-21 (early-2019 PDF layouts parsed; +7,554 rows; COMMITTED)
- Cause of the gap: the first weeks of 2019-20 used different report layouts (A: `Category / Reason / Current Status / Previous
  Status`, 2019-10-21..11-14; B: category folded into Reason, 11-15..11-19; C: `Current Status / Reason / Previous Status`,
  11-20..12-17; D: the modern `Current Status / Reason` from 12-18). The parser assumed Current Status left of Reason and
  returned no rows for A and B, so the history load began 2019-11-20.
- Parser (nba/parse/availability.py, commit c00ff84): additive layout detector for A/B; Category joined into reason in the later
  layouts' style; Previous Status ignored; "NOT YET SUBMITTED" teams yield no record. Later layouts byte-identical: regression
  fixture of stored records plus 0 differences re-parsing 510 PDFs (10 random 2024 production, 500 random history ≥ 2019-11-20).
- Load: 90 files, 30 dates (2019-10-21..2019-11-19), +7,554 rows (354,974 → 362,528), 0 files failed, 0 unmatched names; rows
  added only in the window; the fingerprint of the 354,974 previously loaded rows is unchanged. The opening day 2019-10-21 has
  24 rows (two games on the report; season opened 10-22) and was waived from the per-date range check explicitly
  (`--waive-low-dates=2019-10-21`, logged). Backup: data/backups/history_player_availability_pre_early2019_20261011_110412.parquet.
- Aliases added to data/history/availability_backfill/aliases.csv (the history resolver's file; separate from
  configs/kalshi_aliases.yaml on purpose), each a unique players_static identity with the team on the report: Enes Kanter →
  202683 (static "Enes Freedom"), Nene Hilario → 2403 (static "Nene"), Gerald Green → 101123, Ivan Rabb → 1628397, Kobi Simmons
  → 1628424 (the last three never played in the history window: injured or cut, so no box-score pool could hold them).
- Known residue (loose end, docs/NEXT_SESSION.md): in layout C rows already loaded (2019-11-20..12-17) the trailing Previous
  Status words are appended to `reason`; status is correct. Separate fix later.
- Implication: HISTORY_WINDOW (T315) stopped partly on 2019-20 report coverage (82%); with these rows coverage rises, but the
  era-identity STOP (AUC 0.80 with sign flips) is a separate finding and the history-window direction is closed for 2026-27
  (DECISIONS 2026-10-11 ~13:10). data_version: the history DB is outside the manifest; nba.duckdb unchanged.

## 2026-10-11 13:35 CT - history injury `reason` text cleaned for layout C (2019-11-20..12-17) (DRAFT)
- Cause: the layout-C parser (c00ff84 left it unchanged) put the trailing `Previous Status` (and, on 2019-12-16 08PM,
  `Previous Reason`) words into `reason` (e.g. "Left knee soreness Out"; interleaved with wrapped lines). `status` was
  correct. Parser now gives the Previous columns an ignored slot (nba/parse/availability.py); later layouts byte-identical
  (0 differences re-parsing 200 random post-2019-12-18 PDFs, old vs new; stored-record regression fixture passes).
- Fix (history DB only, `nba.duckdb` untouched; nba/ingest/history_reason_fix.py): re-parsed the 84 layout-C PDFs and
  UPDATEd only `reason` on the 9,120 stored rows in the window, matched by (player_id, as_of, game_id, status). Gate:
  rows 362,528 -> 362,528; fingerprint over every column except reason identical before/after (36bf2e19...); status counts
  identical; rows outside the window identical to the backup (0 differ); no reason in the window now ends with a status
  word or " -". Reason changed on all 9,120 rows (every row carried a Previous cell: 6,183 old reasons ended in "-", 809 in
  a status word, the other 2,128 had previous words interleaved mid-reason with wrapped lines or carried Previous Reason
  text). The brief's expectation (changed == ended in a status word) was too narrow; the changed set is exactly the set of
  rows whose old reason contained any Previous-column token.
- Not done on purpose: 262 parsed layout-C rows have no stored counterpart (names the early-2019 aliases resolve now);
  inserting them would change the row count. Left as a separate load decision.
- Backup: data/backups/history_player_availability_pre_reasonfix_20261011_133515.parquet. data_version unchanged (history DB
  is outside the manifest).
- 2026-10-11 14:14 CT (Devin: "APPLY 262"): the 262 layout-C injury-report rows now resolvable by the early-2019 aliases inserted into the history DB (2019-11-20..12-17; players 2403, 101123, 1628397, 1628424); rows 362,528 → 362,790; every PDF's stored rows now equal its parsed rows; no existing row changed (backup data/backups/history_player_availability_pre_insert262_20261011_141454.parquet). History reports are complete for 2019-20 from the first report of the season.
- 2026-10-11 15:11 CT: production OOF reference re-baselined on data_version 418510d401d3: `nba.eval.lower_tail_eval` collect →
  diagnose → collect_pi → candidates re-run on the current DB; reports/lower_tail/candidates.json 'off' now carries 2023 n 27,619
  pts integer CRPS 3.152830 (unchanged) and 2024 n 27,701 / 3.187553 (was 27,583 / 3.188411 before the restored neutral-site games).
  Old file backed up at data/backups/lower_tail_candidates_pre_rebaseline_20261011.json (the LOWER_TAIL ledger rows T129–T159
  keep the old numbers; the file is an untracked artifact). Every future gate's A0 reproduction check compares against this.

## 2026-10-11 UTC - Phase 2 close-out: F1 dedup view, F12 gate, F9 helper, data cards (data_version 418510d401d3, unchanged)
- DRAFT. No change to `nba.duckdb`, parquet or manifests in this entry; verification only (read-only): games 5,279; regular-season games
  per team 82 for 2022-25; possession pts == box pts 10,558/10,558; team-0/NULL `off_players` rows 0; stint key duplicates 0; players
  played >= 1 min with no stint row 1; `players_static` first_season/draft_status NULL 4/0; `player_availability` NULL game_id 167.
- `lineups.duckdb` (collector-owned): view `lineup_snapshots_dedup` added to the DDL (applied at the collector's next write tick;
  `lineup_snapshots_latest` is now `SELECT *` from it). Gate: duplicate groups 550 raw -> 0; mixed-status team-snapshots 57 -> 0;
  confirmed team-snapshots with 5 starters: all. Raw rows untouched.
- Details and per-finding before/after: docs/reviews/data_audit_2026-10-11.md section 10.

## 2026-10-11 UTC - F5 team_coach_games from Basketball-Reference, code and tests landed; load pending the fetch (data_version unchanged)
- DRAFT. No data written by this entry. `nba/data/coaches_br.py` (fetch 120 pages to `data/coaches/raw/`, write-once, 3.5 s apart, resumable; parse;
  `build-csv`) and `nba/data/coaches.py` (build + `--apply` load with backup and heavy.lock) replace the 2022-24 hand-copied draft. The network fetch was
  refused by the sandbox in this run, so the reference CSV, the table reload and the manifest snapshot are NOT done; the entry that records the reload
  (rows, gates, backup path) is appended by the run that does it. Table schema changes to the six columns in `nba/db/schema.sql` (the live table keeps
  its older eight columns until the reload drops and recreates it).
- Readers: `nba/facts/build.py` `coach_facts` reads only `game_id, team_id, coach_name, coach_id, known_at`, so it works on both layouts.
