# Data audit, 2026-10-11 (Phase 1, read-only)

Trigger: Devin's approval of a comprehensive data clean-up (docs/DECISIONS.md, 2026-10-11 ~00:05 CT). This is the audit that approval
asked for first. **Nothing was written to `nba.duckdb` or to any parquet/manifest.** Every query ran with `read_only=True` under
`data/ops/heavy.lock`. Scripts: `data/scratch/data_audit_2026-10-11/` (README there). The only code executed outside SQL was an
in-memory monkeypatch of `_NameBook` to prototype the stint fix (section 7a); no repo file under `nba/` was changed.

**Holdout statement.** Seasons <= 2025 were READ for row counts, null rates, key duplicates and source-vs-source reconciliation (a count is
not an evaluation). No model row or outcome of season 2025 was scored or compared against a forecast anywhere in this audit. No season 2026
games exist in `games`; 2026 appears only in the forward tables and the live schedule.

Season convention used throughout: `season = 2022` is 2022-23, so 2025 is 2025-26. Game-type prefixes in `game_id`: 002 regular, 004 playoffs,
005 play-in (`games` keeps only these). Core scope: `nba.duckdb` (games 5,269; player_game_stats 138,409; possessions 1,047,292; stints 298,394).

## 0. Findings, ranked by what reads them

Severity: LIVE-PATH = read by the production daily path or a live decision (`nba/daily`, `nba/props`, `nba/models`, `nba/features`, `nba/lineups`);
RESEARCH = read only by `nba/eval`, `nba/facts`/`nba/explain` or `research/`; COSMETIC = read by nothing or documentation only.
"Mine" marks what the data owner can change; everything else goes through the main session.

| # | Sev | Finding | Size | Read by | Proposed fix | Gate (before -> after) |
|---|---|---|---|---|---|---|
| F1 | LIVE-PATH | `lineup_snapshots` lists some players twice in one snapshot (Expected + Confirmed, or Confirmed twice with conflicting `announced_starter`). The T-30 reader takes the set of statuses, sees "Confirmed/Expected" and skips the game. | 240 duplicate (snapshot, game, team, player) groups; 48 of 406 team-snapshots; 20 of 39 snapshots. 4 of the 6 recorded `forward_t30_decisions` were skipped for exactly this reason. | `nba/daily/t30.py` (reader, not mine), `nba/facts/build.py` | Reader or a view keeps one row per (snapshot, game, team, player): latest `source_ts`, ties to Confirmed. Raw snapshots stay append-only. Mine: the view + test; main session: the `t30.py` change. | duplicate groups 240 -> 0; offline replay of the 4 mixed-status decisions resolves to confirmed (no new log rows); starters per confirmed team still 5 |
| F2 | LIVE-PATH | 10 neutral-site regular-season games (5 in 2024, 5 in 2025: Mexico City x2, Paris x2, Berlin, London, 4 Cup semifinals) are in the schedule as Final but absent from `games` and so from every table keyed on it. | 10 games; 20 team-slots (teams show 79-81 games); 336 `player_availability` rows with NULL `game_id`; rest-day / back-to-back for the next game of those teams is wrong | `nba/models/injury_elo.py`, `nba/features/game_context.py`, `nba/daily/*` all read `games`; injury report rows are invisible to the injury model | Probable cause (to confirm with a fixture): `_normalize_games_frame` splits home/away on "@", which neutral games break. Insert games from the schedule parquet (home, away, date, score all present and agree 100% elsewhere); fetch box + PBP (network, maintainer command); re-resolve the 336 report rows; reparse possessions/stints for the 10 games. | regular-season games per team = 82 for 2022-25 (now 15 teams off); games 5,269 -> 5,279; availability NULL `game_id` 336 -> 0; poss pts == box pts for the 10 new games |
| F3 | LIVE-PATH (low) | Live 2026-27 tip file has 1,200 of 1,230 regular-season games (ids 1201-1230 absent; 80 games per team instead of 82). Those games fall back to the 19:00 ET proxy tip when they appear. | 30 games | `nba/features/game_tipoff.py`, `nba/daily/pipeline.py` | Reason unknown (not yet published, or deliberately held back). Re-pull when the NBA publishes; do not guess. Maintainer question below. | count 1,200 -> 1,230 and per-team 82, or a recorded reason |
| F4 | RESEARCH | `stints` has no row for 1,106 played player-games (0.98%; 458 games), almost all under 2 minutes. Cause found: the substitution-in name cannot be resolved for a player who never acts in that game's play-by-play, so the sub is skipped as "unresolved" and the player never enters the tracker. | 435 (<1 min), 480 (1-2), 157 (2-5), 34 (5+) | `nba/eval/f12*`, `research/minutes/*`, `research/eval/f8*, f11*` | Seed the per-game name book with a season-wide player_id -> name map restricted to the game's box roster. Prototype on 989 games: unresolved 224 -> 27, missing <2 min rows 167 -> 0, within-1-min 98.66% -> 99.76%. | see section 7a |
| F5 | RESEARCH | `team_coaches` names the wrong head coach in 14 of 87 reference team-seasons (3 more missing), and cannot represent mid-season changes (9 team-seasons changed coach in the reference). 11 of the 14 name the NEXT season's coach (summer hires), so the facts store stamps a future fact as known on 1 Oct. | 14/87 wrong, 3 missing, 9 changes | `nba/facts/build.py` (confidence 0.5), `research/minutes/hazard.py` | Not fixable by changing the fetch (see 7b). Replace with a reviewed game-by-game table. | see section 7b |
| F6 | RESEARCH | Possession parser treats a free throw as missed when the player's name starts with "Miss" (`_is_miss` = `startswith("MISS")`): a player named Missi has every made free throw read as a miss, so the trip stays open waiting for a rebound and its points are not counted. Verified: the 79 affected games are exactly the games with a Missi free throw (79 of 79; 0 other games contain a "Miss"-prefixed non-miss free throw). | 79 New Orleans team-games (2024: 49, 2025: 30); possession points short by 1-8; only team 1610612740 | `research/*` possession features; `nba/props/forward.py` copies possessions to a scratch DB | Anchor on the miss marker ("MISS " + space) and reparse affected games. Code is in `nba/parse` (mine). | poss pts == box pts for all team-games (now 98.1% in 2024, 98.9% in 2025, 100% in 2022-23) |
| F7 | RESEARCH | 2 spurious possession rows with `off_team = 0` (a team turnover) in games 0022200323 and 0042300303, NULL `off_players`; they are the only team-games under 80 possessions (1 each, team id 0). | 2 rows | any `GROUP BY off_team` | Attribute to the real team from the PBP event or drop and re-index; reparse the 2 games. | rows with team 0 or NULL `off_players`: 2 -> 0; team-games < 80: 2 -> 0 |
| F8 | RESEARCH | `stints` has no unique natural key: 24.9% of rows are zero-duration (chained substitutions at one clock reading) and 3 rows are exact duplicates; `(game, team, period, start_clock)` repeats 57,233 times. | 74,386 zero-duration; 3 exact duplicates | all stint readers (sums are unaffected) | Add `stint_idx` per (game, team, period) when the stints are rebuilt in F4; keep zero-duration rows (needed for ordering) but drop the 3 exact duplicates. | key unique; minute sums unchanged |
| F9 | RESEARCH | `player_game_hustle` row existence depends on tonight's minutes: a row exists for 14% of players under 1 minute, 32% at 1-2, 65% at 2-5, 88% at 5-10, 98% at 10-20 (and for 453 players with 10+ minutes none). A LEFT JOIN turns this into NULLs that are NULL iff minutes are low: the leak shape that bit us twice. | 79,263 rows (2022-24 only) | `research/features/tracking_features.py` | Document in a data card; any consumer must treat "no row" as unknown, never as zero, and must not use it for the current game. No data change. | data card committed; consumer test: a planted low-minute player gets NaN not 0 |
| F10 | RESEARCH | `players_static`: 208 of 891 rows have no `first_season`. 204 are legacy rows loaded before the column existed (cached with the 8-column schema, also no `draft_status`); 4 are undrafted players where the feed gave no FROM_YEAR. | 208 | `nba/eval/f9b_young_pick.py` (has its own fallback: draft_year, then first loaded game) | Re-pull the 204 (cached, resumable, network), derive first_season for the 4 from `player_game_stats`. | first_season NULL 208 -> <= 4 (documented); draft_status NULL 204 -> 0 |
| F11 | RESEARCH | 30 empty (zero-column) cached parquet files in `data/players_static/`, keyed 12737..12766: the last five digits of the 30 team ids, i.e. a team id was passed as a player id and the empty response was cached as done. | 30 files | none today | Delete is not allowed to me; move to `data/quarantine/` with a note (main session approves), and make the puller raise on an empty frame. | zero-column parquet files in the cache dirs: 30 -> 0 |
| F12 | RESEARCH | `parse/reconcile.py` and BEST_PRACTICES gate possessions vs `FGA + 0.44 FTA + TOV - OREB` at MAE <= 1 per team-game; the stored data has MAE 2.0-2.25 and a bias of -1.6 to -2.0 in every season. Already known (possession_order review: 2.007); not new, but the written gate is not met. | all team-games | docs, tests | Decide: the gate is mis-specified (team turnovers and the 0.44 constant are not in the player box) or the parser is wrong. Evidence says mis-specified: possession points equal box points in 98.1-100% of team-games. Propose re-stating the gate as points-reconciliation plus the formula as a diagnostic. | maintainer decision |
| F13 | COSMETIC | Empty tables with no writer in the daily path: `game_era_flags`, `kalshi_markets`, `kalshi_prices`, `paper_trades`, `player_game_matchups`, `player_rates`, `shots`, `team_context`, `team_game_advanced`, `forward_scores`, `forward_scores_elig`. `forward_predictions` (136 rows) are all preseason game ids (001...) which `games` excludes, so they can never be scored. | 11 tables | none | Leave. Record in the data cards. | none |
| F14 | COSMETIC | `games.national_tv` is NULL for 77.5% of regular-season games (about 952 per season): a negative ("not national") and an unknown are the same value. The consumer treats NULL as False. | 3,807 rows | `nba/features/game_context.py` | None needed while consumer semantics hold; note in the data card. | none |
| F15 | COSMETIC | 2025-26 has no `game_officials`, `player_game_hustle`, `player_game_tracking` rows and no `game_context_features` rows (post-game sources were pulled for 2022-24 only). | 1,316 games each | research only | Pull when a consumer needs it; not a defect. | none |

Maintainer questions (headless: no decision taken):
1. F3: is 1,200 the published 2026-27 regular season, or should the 30 missing ids be re-pulled once the NBA publishes? Safe default: leave and re-check weekly.
2. F5: the reference coach table is hand-built from Basketball-Reference for 2022-24 and unverified beyond "each team-season sums to 82". 2025-26 has no reference. Safe default: build 2022-24 now, mark 2025 as `source=team_coaches (unverified)` until Devin supplies or approves a source.
3. F12: keep or restate the possession-formula gate (see above). Safe default: keep the points-reconciliation gate as the hard gate, report the formula as a diagnostic.

## 1. Row counts per season

Seasons from `game_id` digits 4-5 (checked equal to `games.season` for all 5,269 games).

| table | 2022 | 2023 | 2024 | 2025 | 2026 | game_id NULL | total |
|---|---|---|---|---|---|---|---|
| games | 1,320 | 1,318 | 1,315 | 1,316 | 0 | 0 | 5,269 |
| player_game_stats | 34,024 | 34,815 | 34,946 | 34,624 | 0 | 0 | 138,409 |
| player_game_stats_played | 27,737 | 28,197 | 28,118 | 28,593 | 0 | 0 | 112,645 |
| possessions | 263,458 | 260,450 | 261,131 | 262,253 | 0 | 0 | 1,047,292 |
| stints | 71,886 | 72,126 | 74,553 | 79,829 | 0 | 0 | 298,394 |
| game_officials | 3,999 | 4,016 | 4,016 | 0 | 0 | 0 | 12,031 |
| player_availability | 26,757 | 29,378 | 31,395 | 30,372 | 0 | 336 | 118,238 |
| player_game_hustle | 26,332 | 26,473 | 26,458 | 0 | 0 | 0 | 79,263 |
| player_game_tracking | 34,024 | 34,815 | 34,946 | 0 | 0 | 0 | 103,785 |
| prop_predictions | 272,192 | 278,520 | 279,568 | 276,992 | 0 | 0 | 1,107,272 |
| forward_predictions | 0 | 0 | 0 | 0 | 136 | 0 | 136 |
| forward_scores | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| forward_scores_elig | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| forward_t30_decisions | 0 | 0 | 0 | 0 | 4 | 0 | 4 |
| team_coaches (by season col) | 257 | 238 | 263 | 283 | 0 | 0 | 1,041 |
| players_static (no season) | 0 | 0 | 0 | 0 | 0 | 0 | 891 |

Reading: the core tables line up 1:1 with `games` in every season; post-game sources stop at 2024, `forward_*` hold 2026 preseason rows only, and `forward_scores` is empty because nothing scoreable has finished.

Other stores (not season-keyed):

| store | rows | note |
|---|---|---|
| `data/lineups/lineups.duckdb` `lineup_snapshots` (live collector; counts as of the audit run) | 4,972 (2026-10-09 and 10-10) | 39 snapshots, 406 team-snapshots, 9 preseason games; 240 duplicate groups (F1) |
| `lineups.duckdb` `snapshot_log` | 45 | 39 HTTP 200, 6 HTTP 302 (not modified; `raw_path`/`etag`/`last_modified` NULL by design for those) |
| `lineups.duckdb` `game_tips` | 8 | all preseason ids, source `status_text` |
| `data/schedule/raw_2022-23 .. raw_2025-26.parquet` | 1,394 / 1,396 / 1,400 / 1,400 | all game types (001 preseason, 002, 003 all-star, 004, 005, 006 Cup final); 5,590 unique ids, 0 duplicates |
| `data/schedule/game_context_features.parquet` | 3,958 | 2022-24 only; 5 rows are the 2024 neutral games absent from `games` |
| `data/schedule/live_tips_2026-27.parquet` | 1,200 | all regular season, ids 1-1200 (F3) |

Reading: the schedule is the more complete record of what was played; `games` is missing exactly its 10 neutral-site games.

## 2. Primary-key duplicates

| table | key | rows | dup keys | excess rows | rows with NULL in key |
|---|---|---|---|---|---|
| games | (game_id) | 5,269 | 0 | 0 | 0 |
| player_game_stats | (game_id, player_id) | 138,409 | 0 | 0 | 0 |
| possessions | (game_id, poss_idx) | 1,047,292 | 0 | 0 | 0 |
| stints | (game_id, team_id, period, start_clock) | 298,394 | 57,233 | 74,370 | 0 |
| game_officials | (game_id, official_id) | 12,031 | 0 | 0 | 0 |
| player_availability | (player_id, as_of, game_id, source) | 118,238 | 0 | 0 | 336 |
| player_game_hustle | (game_id, player_id) | 79,263 | 0 | 0 | 0 |
| player_game_tracking | (game_id, player_id) | 103,785 | 0 | 0 | 0 |
| team_coaches | (season, team_id, coach_id) | 1,041 | 0 | 0 | 0 |
| players_static | (player_id) | 891 | 0 | 0 | 0 |
| forward_predictions | (run_id, game_id, model_name, target, player_id) | 136 | 0 | 0 | 0 |
| prop_predictions | (run_id, game_id, player_id, stat) | 1,107,272 | 0 | 0 | 0 |

player_availability full-row duplicates (excess rows): 118237

Reading: every table is unique on its key except `stints`, which has no real key (zero-duration chained substitutions repeat the start clock; F8). `player_availability` has no duplicate rows. The lineup snapshots have no declared key; on (snapshot_id, game_id, team_id, player_id) they have 240 duplicate groups (F1).

## 3. Null rates

### 3a. Overall (only columns with any NULL)

| table | column | null rate | meaning |
|---|---|---|---|
| player_game_stats | minutes | 18.6% (25,746 rows) | DNP, by design: NULL minutes and zero stats; no stat column has a NULL |
| games | national_tv | 72.6% overall (77.5% of regular season) | "not nationally televised", indistinguishable from unknown (F14) |
| possessions | shooter_id, shot_zone | 21.5% | structural: 88.4% of FT trips and 89.9% of turnovers carry no shooter; 0% on FG outcomes |
| possessions | assister_id | 75.9% | structural: no assister on misses, free-throw trips or turnovers; unassisted makes (detail below) |
| possessions | off_players | 2 rows | the 2 spurious team-0 rows (F7) |
| player_availability | game_id | 336 rows (0.28%) | unresolved game: all in the 10 neutral-site games (F2) |
| players_static | position, height_in | 0.45% | 4 rows |
| players_static | weight_lb | 1.0% | |
| players_static | draft_year, draft_pick | 32.6%, 33.2% | undrafted (matches `draft_status`); intended |
| players_static | first_season, draft_status | 23.3%, 22.9% | 204 legacy rows + 4 (F10) |
| players_static | college_stats | 100% | never populated |
| snapshot_log | last_modified, etag, raw_path | 13.6% | HTTP 302 rows, by design |

Reading: no unexpected NULL appears in the core tables. All other columns of all audited tables (including `stints`, `game_officials`, `team_coaches`, hustle, tracking, `forward_*`, lineup snapshots, schedule parquet, `game_context_features`) have 0 NULLs.

Assister detail: assister NULL rate is 100% on `FGA_miss`, 51.3% on `FGM2` (48.7% assisted), 23.0% on `FGM3` (77.0% assisted), 100% on `FT_trip`, 99.98% on `TOV`.

### 3b. By realised-minutes bucket (where a player row exists)

`player_game_stats` rows by own minutes; this is the table where a NULL could depend on tonight's minutes. Only `minutes` has any NULL and it is NULL exactly for DNPs (and 0 for 18 rows listed with 0 minutes). Cell = NULL rate of `minutes` in that bucket:

| bucket | n | minutes |
|---|---|---|
| DNP (NULL min) | 25,746 | 1 |
|  zero min | 18 | 0 |
| <1 | 1,102 | 0 |
| 1-2 | 2,189 | 0 |
| 2-5 | 6,566 | 0 |
| 5-10 | 8,271 | 0 |
| 10-20 | 26,132 | 0 |
| 20+ | 68,385 | 0 |

constant-zero-null columns not shown: 19

The asymmetry scan (played = minutes >= 5 vs not, any column of player_game_stats, hustle and tracking, threshold 0.01 on the NULL rate): **no column flagged** other than `minutes` itself (structural, the definition of DNP). `hustle` and `tracking` carry no NULL values at all.

The leak-shaped pattern here is not a NULL but row *existence*. Share of `player_game_stats` rows (2022-24) that have a row in each side table, by that row's own minutes:

| bucket (2022-24) | n | has hustle row | has tracking row | has availability row |
|---|---|---|---|---|
| DNP (NULL min) | 19,718 | 0 | 1 | 0.186 |
|  zero min | 15 | 0.067 | 1 | 0.133 |
| <1 | 858 | 0.144 | 1 | 0.034 |
| 1-2 | 1,684 | 0.319 | 1 | 0.072 |
| 2-5 | 4,852 | 0.647 | 1 | 0.053 |
| 5-10 | 6,206 | 0.88 | 1 | 0.058 |
| 10-20 | 19,441 | 0.982 | 1 | 0.064 |
| 20+ | 51,011 | 0.998 | 1 | 0.089 |

Reading: `player_game_tracking` has a row for every box row (DNPs included, with a comment); `player_game_hustle` is only present for players who played, with presence rising from 14% under one minute to 99.8% at 20+ (F9); report rows (`player_availability`) are pre-tip and so legitimately concentrated on DNP (18.6%) but they are present for 6-9% of players who played 10+ minutes.

`stints` presence by box minutes is in section 5b (the same shape: missing exactly for the shortest appearances, F4).

## 4. Coverage versus `games`

| table (games with rows / games with a result) | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|
| player_game_stats | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 1,316/1,316 (100.0%) |
| possessions | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 1,316/1,316 (100.0%) |
| stints | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 1,316/1,316 (100.0%) |
| game_officials | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 0/1,316 (0.0%) |
| player_availability | 1,318/1,320 (99.8%) | 1,317/1,318 (99.9%) | 1,314/1,315 (99.9%) | 1,313/1,316 (99.8%) |
| player_game_hustle | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 0/1,316 (0.0%) |
| player_game_tracking | 1,320/1,320 (100.0%) | 1,318/1,318 (100.0%) | 1,315/1,315 (100.0%) | 0/1,316 (0.0%) |
| games with result / games | 1,320/1,320 | 1,318/1,318 | 1,315/1,315 | 1,316/1,316 |

Orphan game_ids (in table, not in games):

| table | orphan game_ids |
|---|---|
| player_game_stats | 0 |
| possessions | 0 |
| stints | 0 |
| game_officials | 0 |
| player_availability | 0 |
| player_game_hustle | 0 |
| player_game_tracking | 0 |

By game type (all rows present for 002 regular / 004 playoffs / 005 play-in for the core tables; officials, hustle and tracking are present for every 2022-24 game and absent for all 2025 games): every `games` row has box score, possessions and stints. `games` has a result for 5,269 of 5,269.

Regular-season games per team: every 2022 and 2023 team has 82. In 2024, 8 teams have 80-81 and in 2025, 7 have 79-81 (10 team-slots each year): the 10 neutral-site games (F2): 0022400147, 0022400621, 0022400633, 0022401229, 0022401230 and 0022500147, 0022500578, 0022500602, 0022501229, 0022501230. All ten are Final in `data/schedule/raw_*` (isNeutral = true), none has an `ingest_log` row.

Injury reports: 5,262 of 5,269 games have at least one report row. The 7 without one: 6 playoff games (0042200221/2, 0042400211, 0042500302/3/4) and one regular-season game (0022300065). 5,260 have a snapshot at least 60 minutes before the real tip.

Reading: coverage is complete except the neutral games and the intentionally unfetched 2025 post-game sources.

## 5. Reconciliation where two sources overlap

### 5a. Possession points and rebounds vs box score (per team-game)

| season | team-games | poss pts == box pts | poss pts == games pts | box pts == games pts | mean(poss-box) pts | MAE pts | max abs pts diff | n_oreb == box OREB | MAE OREB | n_dreb == box DREB | MAE DREB | mean poss/team-game |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022 | 2,640 | 1 | 1 | 1 | 0 | 0 | 0 | 0.996 | 0.004 | 0.998 | 0.002 | 99.794 |
| 2023 | 2,636 | 1 | 1 | 1 | 0 | 0 | 0 | 0.996 | 0.004 | 0.997 | 0.003 | 98.805 |
| 2024 | 2,630 | 0.981 | 0.981 | 1 | -0.048 | 0.048 | 8 | 0.998 | 0.002 | 1 | 0.0003802 | 99.289 |
| 2025 | 2,632 | 0.989 | 0.989 | 1 | -0.022 | 0.022 | 5 | 0.995 | 0.005 | 0.997 | 0.003 | 99.64 |

Reading: possession points equal box points in 100% of 2022-23 team-games and 98.1% / 98.9% in 2024 / 2025. All 79 misses are one team (New Orleans, 1610612740) and always short (F6). Box points equal the final score in 100% of team-games. Offensive and defensive rebound counts agree in 99.5-100%; the mean absolute error is 0.0004-0.005 per team-game.

Possession counts vs the box estimate `FGA + 0.44 FTA + TOV - OREB`:

| season | team-games | mean(poss - formula) | MAE | q01 / q99 | min poss | max poss | <80 | > 130 |
|---|---|---|---|---|---|---|---|---|
| 2022 | 2,640 | -1.647 | 2.013 | -6.5 / 2.5 | 85 | 132 | 0 | 1 |
| 2023 | 2,636 | -1.606 | 1.974 | -6.6 / 2.2 | 82 | 128 | 0 | 0 |
| 2024 | 2,630 | -1.662 | 2.029 | -6.7 / 2.5 | 85 | 127 | 0 | 0 |
| 2025 | 2,632 | -1.96 | 2.252 | -7.3 / 2.2 | 82 | 126 | 0 | 0 |

Reading: counts are plausible (mean about 99 per team-game, min 82, max 132) but sit 1.6-2.0 below the formula with MAE 2.0-2.25, above the written gate of 1 (F12). The 132 is the 175-176 double-overtime game (real). Team-games under 80: **2, both are spurious team-0 rows with 1 possession** (F7), not short real games.

### 5b. Stint minutes vs box minutes per player-game

Stint minutes = sum of (start_clock - end_clock) / 60 over the stints containing the player. Played player-games (box minutes > 0):

| season | player-games | with box min>0 | no stint row (of played) | |diff|<=1 | mean diff | q01 | q05 | q25 | q50 | q75 | q95 | q99 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2022 | 34,024 | 27,737 | 0.008 | 0.991 | -0.0002385 | -0.01 | -0.01 | -0 | 0 | 0 | 0.01 | 0.01 |
| 2023 | 34,815 | 28,197 | 0.012 | 0.987 | -0.0002467 | -0.48 | -0.01 | -0 | 0 | 0 | 0.01 | 0.48 |
| 2024 | 34,946 | 28,118 | 0.01 | 0.986 | -0.0002271 | -0.35 | -0.01 | -0 | 0 | 0 | 0.01 | 0.43 |
| 2025 | 34,624 | 28,593 | 0.01 | 0.984 | -0.0006493 | -0.52 | -0.01 | -0 | 0 | 0 | 0.01 | 0.69 |

Difference by box-minute bucket (all 4 seasons; "no stint row" is the share of the bucket with no stint row at all):

| box-minute bucket | player-games | no stint row | |diff|<=1 min | mean (stint-box) | median | p05 | p95 | MAE | share with stint>0 & box 0 |
|---|---|---|---|---|---|---|---|---|---|
| none/zero | 25,764 | 1 | 1 | 3.881e-07 | 0 | 0 | 0 | 3.881e-07 | 7.763e-05 |
| <1 | 1,102 | 0.395 | 0.998 | -0.224 | -0.005 | -0.917 | 0.005 | 0.265 | 0 |
| 1-2 | 2,189 | 0.219 | 0.785 | -0.302 | -1.589e-08 | -1.65 | 5.563e-08 | 0.317 | 0 |
| 2-5 | 6,566 | 0.024 | 0.976 | -0.066 | 0 | -0.003 | 1.907e-07 | 0.068 | 0 |
| 5-10 | 8,271 | 0.003 | 0.996 | -0.021 | 0 | -0.005 | 0.003 | 0.028 | 0 |
| 10-20 | 26,132 | 0.0002679 | 0.997 | 0.004 | 0 | -0.007 | 0.005 | 0.018 | 0 |
| 20+ | 68,385 | 0 | 0.989 | 0.02 | 0 | -0.007 | 0.005 | 0.032 | 0 |

Missing rows in detail, played player-games with no stint row at all:

| season | played player-games | no stint row | <1 min | 1-2 min | 2-5 min | 5+ min | games affected |
|---|---|---|---|---|---|---|---|
| 2022 | 27,737 | 220 | 100 | 81 | 34 | 5 | 88 |
| 2023 | 28,197 | 327 | 144 | 147 | 33 | 3 | 126 |
| 2024 | 28,118 | 269 | 101 | 130 | 33 | 5 | 112 |
| 2025 | 28,593 | 290 | 90 | 122 | 57 | 21 | 132 |
| all | 112,645 | 1,106 | 435 | 480 | 157 | 34 | 458 |

Further checks: every team's stints tile every period exactly (42,758 team-periods, 0 off by more than 0.5 s: 720 s regulation, 300 s overtime); team-game stint minutes vs team box minutes differ by 0.0045 min on average (exactly 1 team-game differs by more than 1 minute, by 11.6); no DNP player has a stint row (25,746 DNP rows, 0 with stints); 2 of the 18 rows listed with 0.0 minutes have stint time.

Reading: durations are right (98.4-99.1% within one minute, median error 0); the shape of what is missing is the problem: a stint row is absent for 39.5% of players under 1 minute and 21.9% at 1-2 minutes, falling to 0.03% at 10-20 and 0 at 20+. Section 7a gives the cause.

### 5c. Schedule vs `games`

| check | result |
|---|---|
| schedule rows for kept game types (002/004/005) not in `games` | 10 (the neutral games, F2) |
| `games` ids not in schedule | 0 |
| home/away team ids differ | 0 of 5,269 |
| home/away score differs | 0 of 5,269 |
| `games.game_date` != schedule date, or != date of the ET tip | 0 and 0 |
| schedule game not Final | 0 of 5,269 |
| UTC tip - ET tip is 4 h (EDT) or 5 h (EST) and agrees with America/New_York for every row | 5,590 of 5,590 (1,817 EDT, 3,452 EST among the 5,269) |
| tip missing or placeholder midnight | 0 and 0 |
| `game_context_features.tip_utc` vs schedule tip | 0 differ of 3,953 matched |
| tips: hour of day (ET) | 12:00-23:00; 63% start at 19:00-20:59 |

Reading: schedule and `games` agree exactly on the games they share. `games` itself carries a date only; the real tip lives only in the schedule files, as designed.

### 5d. Injury report vs games and tips

| check | result |
|---|---|
| games with at least one report row / games with a result | 5,262 / 5,269 (99.9%); per season 99.8-99.9% |
| rows per game with a report | min 1, median 21, max 69 |
| distinct snapshots per game | min 1, median 3, max 4 |
| report snapshot times | 82.2% at :00, 17.8% at :45 (the two scrape slots) |
| lead of report before real tip (minutes): q01 / q05 / median / q95 | -165 / 60 / 435 / 1,590 |
| rows with as_of AFTER the real tip | 3,543 of 117,902 (3.0%): present, stamped, to be excluded by the >= 60 minute gate |
| rows with lead < 60 minutes | 4,669 (4.0%) |
| `pulled_at` != `as_of` | 0 |
| reports with NULL `game_id` | 336, all neutral-site games (F2) |

Reading: every report row is stamped with when it was knowable, as required; consumers must (and do, via the real-tip gate) drop the 4.0% inside 60 minutes of tip.

### 5e. Other overlaps

| check | result |
|---|---|
| tracking minutes vs box minutes | identical for 103,785 of 103,785 rows |
| hustle minutes and points vs box | identical for 79,263 of 79,263 rows; team ids agree |
| hustle/tracking rows with no box row | 0 / 0 |
| `player_game_stats_played` vs `player_game_stats WHERE minutes > 0` | identical, 112,645 rows |
| starters per team-game | exactly 5 in all 10,538 team-games |
| players per team-game | 10 to 16, median 13 |
| a player on two teams in one game | 0 |
| officials per game | 3 (3,781 games) or 4 (172 games); 83 officials, no id with two names |
| raw cache: zero-row parquet files | none in boxscore, pbp, officials, hustle, tracking, coaches, national-tv, games; 30 zero-column files in players_static (F11) |

## 6. Out-of-range values

| table | check | rows | share | note |
|---|---|---|---|---|
| player_game_stats | minutes > 60 (all) | 0 | 0.0000% |  |
| player_game_stats | minutes > 65 (>1 OT) | 0 | 0.0000% |  |
| player_game_stats | minutes > 48 & game regulation-only? (see below) | 0 | 0.0000% |  |
| player_game_stats | minutes < 0 | 0 | 0.0000% |  |
| player_game_stats | pts < 0 | 0 | 0.0000% |  |
| player_game_stats | reb < 0 | 0 | 0.0000% |  |
| player_game_stats | ast < 0 | 0 | 0.0000% |  |
| player_game_stats | fg3m < 0 | 0 | 0.0000% |  |
| player_game_stats | stl < 0 | 0 | 0.0000% |  |
| player_game_stats | blk < 0 | 0 | 0.0000% |  |
| player_game_stats | tov < 0 | 0 | 0.0000% |  |
| player_game_stats | fgm < 0 | 0 | 0.0000% |  |
| player_game_stats | fga < 0 | 0 | 0.0000% |  |
| player_game_stats | fg3a < 0 | 0 | 0.0000% |  |
| player_game_stats | ftm < 0 | 0 | 0.0000% |  |
| player_game_stats | fta < 0 | 0 | 0.0000% |  |
| player_game_stats | oreb < 0 | 0 | 0.0000% |  |
| player_game_stats | dreb < 0 | 0 | 0.0000% |  |
| player_game_stats | pf < 0 | 0 | 0.0000% |  |
| player_game_stats | fgm > fga | 0 | 0.0000% |  |
| player_game_stats | fg3m > fg3a | 0 | 0.0000% |  |
| player_game_stats | fg3m > fgm | 0 | 0.0000% |  |
| player_game_stats | ftm > fta | 0 | 0.0000% |  |
| player_game_stats | pts != 2*fgm + fg3m + ftm | 0 | 0.0000% |  |
| player_game_stats | reb != oreb + dreb | 0 | 0.0000% |  |
| player_game_stats | pf > 6 (fouled-out cap, 7+ impossible) | 0 | 0.0000% |  |
| player_game_stats | pts > 0 but minutes NULL (DNP with stats) | 0 | 0.0000% |  |
| player_game_stats | minutes = 0 (not NULL) | 18 | 0.0130% |  |
| player_game_stats | minutes > 0 and all box stats 0 and fga=0 | 2,263 | 1.6350% |  |
| player_game_stats | starter IS NULL | 0 | 0.0000% |  |
| player_game_stats | starter true but minutes NULL/0 | 0 | 0.0000% |  |
| possessions | clock_start > 720 (P1-4) | 0 | 0.0000% |  |
| possessions | clock_start > 300 (OT) | 0 | 0.0000% |  |
| possessions | clock_end < 0 | 0 | 0.0000% |  |
| possessions | clock_end > clock_start | 0 | 0.0000% |  |
| possessions | clock_start < 0 | 0 | 0.0000% |  |
| possessions | period < 1 or > 8 | 0 | 0.0000% |  |
| possessions | pts < 0 or > 6 | 0 | 0.0000% |  |
| possessions | fta<0 or n_oreb<0 or n_dreb<0 | 0 | 0.0000% |  |
| possessions | n_dreb > 1 | 0 | 0.0000% |  |
| possessions | off_team = def_team or team 0 | 2 | 0.0002% |  |
| possessions | oreb != (n_oreb>0) | 0 | 0.0000% |  |
| possessions | score_diff abs > 60 | 22 | 0.0021% |  |
| possessions | shooter NULL on FGA/FGM outcome | 0 | 0.0000% |  |
| possessions | shot_zone NULL on FG outcome | 0 | 0.0000% |  |
| possessions | outcome FGM2/3 with pts=0 | 0 | 0.0000% |  |
| stints | start_clock > 720 (P1-4) | 0 | 0.0000% |  |
| stints | start_clock > 300 (OT) | 0 | 0.0000% |  |
| stints | end_clock < 0 | 0 | 0.0000% |  |
| stints | end_clock > start_clock | 0 | 0.0000% |  |
| stints | period > 8 | 0 | 0.0000% |  |
| stints | duplicate player in players | 0 | 0.0000% |  |
| stints | zero-duration (start==end) | 74,386 | 24.9288% | expected for chained substitutions; see s2 |
| games | home_pts or away_pts < 70 or > 200 | 2 | 0.0380% | 83-pt team game is real; flagged only |
| games | home_team = away_team | 0 | 0.0000% |  |
| games | tie score | 0 | 0.0000% |  |
| games | game_date outside season window | 0 | 0.0000% |  |
| games | national_tv NULL & regular season | 3,807 | 72.2528% | national_tv NULL means not nationally televised or unknown (undetermined) |
| possessions | poss_idx gaps (max+1 != count) in any game | 0 games | 0% | |

Maximum minutes in a player-game: 55.1. Team-game box minutes equal 240 + 25 x overtimes within 0.07 minute in all 10,538 team-games (max 290.0 = double overtime; no game goes past 2 overtimes). Reading: nothing is out of range. The 175 and 176-point scores are one double-overtime game (0022200902); 83 points for a team is real and is flagged, not clipped. 22 possessions with |score_diff| > 60 are blowouts, not errors. 2,263 rows with minutes > 0 and every box stat 0 are short garbage-time appearances (1.6%).

## 7. The four named items

### 7a. `stints` missing very short appearances (confirmed, cause found)

Size: 1,106 of 112,645 played player-games (0.98%) in 458 of 5,269 games (8.7%), 220 / 327 / 269 / 290 per season 2022-25. By box minutes: 39.5% of players under 1 minute, 21.9% at 1-2, 2.4% at 2-5, 0.3% at 5-10, 0.03% at 10-20, 0% at 20+. Your figures (~40% and ~21%) are right.

Cause (`nba/parse/lineups.py`, `_NameBook`, called by `nba/parse/rebuild_lineups.py:compute_all`). The tracker is driven by `SUB: <in> FOR <out>` events. The *out* player is exact (player_id on the event) but the *in* player is a surname string. `_NameBook` learns surname -> id only from players who appear *by name in that game's play-by-play* (they act, or are subbed out), then falls back to "roster elimination" (box players who never appear by name) and accepts it only when exactly one candidate is left. A garbage-time player who subs in and never acts (no shot, rebound, foul, turnover; never subbed out because the period or game ends) is in the box roster but unnamed in the PBP. With two or more such players, or when the pool is not exactly one, the sub is skipped and logged in `LineupReport.unresolved`; the player never enters the tracked five and no stint row is ever written for him. The outgoing player stays on the floor in the tracker, so his time is over-credited by the same seconds. Not the cause: a duration threshold (none exists in the code), substitutions after the last event (handled; the period close writes the final stint), end-of-game desync (7 desyncs vs 224 unresolved in the sample).

Evidence (989 games, every 4th game of 2022-24, tracker replayed from the cached PBP): 167 played player-games with box 0-2 minutes had no stint row. All 167 never act in the PBP, were never the out side of a sub, and had no name in that game's PBP; **166 of 167 (99.4%)** have a surname equal to an unresolved in-name for the same team-game; **167 of 167** have a name elsewhere in the season's PBP. 73 of 74 affected team-games had at least as many unresolved in-names as missing players.

Prototype fix (in memory only; not applied anywhere): add, for each box-roster player absent from the game's PBP, a name taken from any other game's PBP, before building the name book.

| same 989 games | unresolved subs | no stint row, <1 min | no stint row, 1-2 min | within 1 min of box | desyncs |
|---|---|---|---|---|---|
| current code | 224 | 82 of 205 | 85 of 420 | 98.66% | 7 |
| with season-wide names | 27 | 0 of 205 | 0 of 420 | 99.76% | 7 |

Fix plan: change `_NameBook` (mine, `nba/parse`), unit-test with a fixture game where a non-acting bench player subs in (planted: he must appear), rebuild `stints` for all 5,269 games with the existing `rebuild_lineups` entrypoint (the module snapshots the pre-fix stints and possession lineups to parquet first), add `stint_idx` (F8) in the same rebuild. Gate (before -> after, all seasons <= 2025 rebuilt by the same code, none evaluated):
- played player-games with no stint row, box >= 1 min: 1,106-435 = 671 -> <= 40 (0.04%); under 1 minute is excluded from the hard gate because a player under a few seconds can still lack a tracked entry;
- share within 1 minute of box: 98.4-99.1% by season -> not lower in any season, and >= 99.5% overall;
- unresolved subs in `reports/lineups_rebuild/summary.json` terms: 1,177 -> <= 150;
- team-period tiling exact for all 42,758 team-periods (unchanged); possessions `poss_idx` and all non-lineup columns unchanged (hash);
- no DNP player gets a stint.
Also touched: `possessions.off_players/def_players` are rewritten by the same rebuild, so the possession lineups change for the affected possessions only.

### 7b. `team_coaches` (confirmed; the claim "shifted one season" needs one refinement)

Reference: `data/scratch/coach_allocation_2026-10-10.py` (Basketball-Reference, 2022-24, 90 team-seasons, each sums to 82). DB has exactly one Head Coach row per team-season it covers: 114 of 120 team-seasons (2022-25 x 30 teams); the 6 without one are 2023 DET and LAL, 2024 NYK, 2025 CHI, DEN and ORL. Compared against the reference's coach with the most games:

| result | team-seasons |
|---|---|
| compared | 87 (3 reference team-seasons have no DB head-coach row: 2023 LAL, 2023 DET, 2024 NYK) |
| DB coach differs from the reference's main coach | **14** |
| of which DB coach is the reference's NEXT-season main coach | 11 |
| of which DB coach coached part of that season (mid-season replacement) | 2 (2024 DEN: Adelman, 2024 MEM: Iisalo) |
| other | 1 (2024 PHX: Ott, hired June 2025) |
| team-seasons with a coach change in the reference | 9 (2022: ATL, BKN; 2023: BKN, MIL, WAS; 2024: DEN, MEM, SAC, SAS); the DB shows one coach for each |

The 14: 2022 ATL (Snyder; McMillan coached 59 of 82), HOU (Udoka for Silas), MIL (Griffin for Budenholzer), PHI (Nurse for Rivers), PHX (Vogel for Williams), TOR (Rajakovic for Nurse), DET (M. Williams for Casey); 2023 MIL (Rivers for Griffin), BKN (Fernandez for Vaughn), PHX (Budenholzer for Vogel), WAS (Keefe for Unseld); 2024 DEN (D. Adelman for Malone), PHX (Ott for Budenholzer), MEM (Iisalo for Jenkins).

Cause: the fetch is correct as written (`nba/ingest/postgame.py` `_fetch_coaches` calls `CommonTeamRoster(team_id, season_str)`, the right endpoint and a per-season parameter; 120 team-season parquet files, 1,041 rows, fetched 2026-10-08/10). The endpoint returns the coaching staff as the NBA site currently records it for that season label, which is the staff after the following offseason's hires and after in-season changes (head coach = whoever was in the chair last, not whoever coached the games). So it is "last occupant, including summer hires", not a clean one-season shift: 11 of 14 are next-season hires, 2 are interim/late replacements, and the other 73 are right only because nothing changed. A different season parameter cannot fix it; the endpoint has no game-level or date-effective dimension. The table's `coach_type = 'Head Coach'` has exactly one row per team-season, so mid-season changes are unrepresentable.

Known-at consequence: `nba/facts/build.py` stamps the head coach as known on 1 October of the season. For the 11 next-season-hire rows that is a fact from the future (e.g. Griffin as 2022-23 Milwaukee coach, hired June 2023). The facts layer is not on the prediction path (confidence 0.5 and the value text says so), hence RESEARCH, but it is a known-at falsehood.

Fix plan (mine): new table `team_coach_games(game_id, team_id, coach_name, coach_id, source, known_at)` built from a reviewed, committed CSV (`data/coaches/head_coach_games_reference.csv`: season, team, coach, first and last regular-season game number, source URL, date reviewed), where a coach change takes effect on the first game he coached and `known_at` = that game's real tip (a change is public by the next tip; a conservative bound). Playoff games take the coach of the team's last regular-season game. `team_coaches` stays (raw fetch, with a data-card note) and `facts/build.py` should switch to the new table (main session). Depends on F2 (game numbers shift when the 10 neutral games are added: assign by the date of each change, not only by game count). Gate: every (season, team) has coaches summing to exactly its regular-season games; every game has exactly 2 coach rows; reference agreement 100% for 2022-24 by construction and a test that re-derives the 14 disagreements; 2025 rows marked `unverified` until Devin approves a source; planted-future test: a coach change dated after a game's tip never appears for that game.

### 7c. `players_static` first_season (explained)

208 of 891 NULL first_season (23.3%). 204 are legacy rows loaded on 2026-10-07 from cached parquet with the original 8-column schema (before commit b41f23e added `first_season` and `draft_status`); they also lack `draft_status` and almost all are 2015-class and older draftees (ids 2738 to 1643007, `draft_year`/`draft_pick` present). 4 are undrafted players for whom the feed had no FROM_YEAR. 692 of the 1,038 cached files carry the new schema. Every player who appears in `player_game_stats` has a row (0 missing). Compared with the first season in our own box scores (2022 onward) the field is meaningful only for debutants. Fix (F10): re-pull the 204 (resumable, network, ~6 minutes at the rate limiter), derive the 4 from `player_game_stats`. 6 non-null rows have `first_season < draft_year` (international players); left as is and documented.

### 7d. Team-games under 80 possessions (explained)

Two. Both are one-possession rows for `off_team = 0` (a team turnover the tokenizer could not attribute): game 0022200323 (2022-11-30, SAC-IND, clock 84 s, period 4) and game 0042300303 (2024-05-25). The real team-games in those games have 114/115 and 93/95 possessions and reconcile to box points. They are not short games (F7). The only team-game over 130 is a real 132-possession double-overtime game.

## 8. Method and limits

- Source of every number: queries in `data/scratch/data_audit_2026-10-11/` against `nba.duckdb` (read-only), `data/lineups/lineups.duckdb` (read-only), `data/schedule/*.parquet`, `data/coaches/*.parquet`, `data/players_static/*.parquet`, PBP parquet cache for the stint-cause replay.
- Not audited: `data/facts/facts.duckdb`, the odds DB, `data/kalshi/*`, `prop_predictions` content (log table; only keys and counts), `experiments`. The decision names "the odds DB"; it was outside the table list given for this audit, so it is queued for Phase 2 or a follow-up on request.
- The coach reference is hand-built and unverified beyond the 82-game sum; I could not check Basketball-Reference from here.
- F2's cause (home/away split on "@") is a hypothesis from reading `_normalize_games_frame`; the raw LeagueGameFinder frames are not cached, so it needs a fixture test or a one-off network look.
- `make data-snapshot` / `data-check` were not run (they write the committed `docs/data_manifests/latest.json`); Phase 2 takes the snapshot before and after each table fix.

## 9. Phase 2 order (proposal; nothing runs until approved)

One table per commit, backup under `data/backups/` first, `heavy.lock`, `make data-snapshot` before and after, one DATA_CHANGELOG entry per manifest run, a DECISIONS row where a module or a rule changes, planted-future test and a data card per new table.

1. F2 games + availability (first, because coach assignment and rest-day features depend on it). Needs the maintainer to run the two network pulls.
2. F6/F7 possession parse fix and reparse of the 79 + 2 games (no network; PBP cached).
3. F4/F8 stint rebuild with the new name book and `stint_idx` (no network; compute ~ the 2026-10-09 rebuild time, run chunked via the existing entrypoint).
4. F5 `team_coach_games` (new table; `team_coaches` untouched).
5. F10/F11 players_static re-pull and cache quarantine (network).
6. F1 lineup view (mine) plus the `t30.py` reader change (main session).
Replay-oracle golden hash: re-baselined once after steps 1-4 (data changed, code did not, recorded as such).
