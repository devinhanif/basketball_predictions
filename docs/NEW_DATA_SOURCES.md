# New post-game data sources (nba_api backfill)

Scope: exactly the games in `games` (seasons 2022-2025, 5,269 games, 2022-10-18 to 2026-06-13).
Code: `nba/ingest/postgame.py`; tests: `tests/ingest/test_postgame.py`.

## As-of contract (read first)

Every per-game table here is **post-game data**, the same contract as `player_game_stats`: a feature
for game G may use these rows only from games strictly before G. Never join a game's own rows into
its own features. `team_coaches` is a season-end roster snapshot: it does not date mid-season
coaching changes, so it is not safe as-of an earlier date within the same season (use only
for prior seasons, or treat the current-season head coach as a known-ahead covariate only if verified).
`game_officials`: assignments are public before tip, but this endpoint returns them post hoc; treat as
post-game until a pre-tip source is added.

## Tables

| Table | Endpoint | Grain / key | Notes |
|---|---|---|---|
| `player_game_tracking` | BoxScorePlayerTrackV3 | (game_id, player_id) | speed, distance, touches, passes, secondary/FT assists, rebound chances, contested/uncontested FG, defended-at-rim |
| `player_game_hustle` | BoxScoreHustleV2 | (game_id, player_id) | deflections, contested shots 2/3, screen assists, box outs, loose balls, charges |
| `game_officials` | BoxScoreSummaryV3 (officials frame) | (game_id, official_id) | V2 returns empty officials for games on/after 2025-04-10, so V3 is used for all seasons |
| `player_game_matchups` | BoxScoreMatchupsV3 | (game_id, off_player_id, def_player_id) | `team_id` = offensive player's team; `def_team_id` derived from `games` at load |
| `shots` | ShotChartDetail (one call per team x season x {RS, PO, PI}) | (game_id, game_event_id) | `clock_seconds` = seconds left in period; `made` boolean |
| `team_coaches` | CommonTeamRoster (coaches frame) | (season, team_id, coach_id) | `is_assistant`: 1 head coach, 2 assistant, other codes for associate/etc. |

## How to run

```
# fetch phase: parquet cache under data/<source>/, per-source sidecar log, never write-locks nba.duckdb
uv run python -m nba.ingest postgame-fetch --source tracking     # hustle|officials|matchups|shots|coaches
# load phase: short write window on nba.duckdb (check `lsof nba.duckdb` first); prints coverage + shot/FGA reconciliation
uv run python -m nba.ingest postgame-load                        # or --source X (repeatable)
```

Resumable and idempotent: cached keys are never refetched; empty or malformed responses are retried
with exponential backoff (4 attempts) and then logged `failed` (never cached as done). Loading is
delete-then-insert per game, so re-running is safe.

## Coverage and known gaps

See the "Coverage" section appended below after the backfill.
Known gaps by design:
- Playoff / play-in shot calls are allowed to be empty (non-qualifying teams); regular-season shot calls
  are not. Games with box-score FGA but no shots are counted in the reconciliation output.
- Tracking/hustle/matchups depend on NBA's tracking coverage; any game that failed all retries stays
  `failed` in `data/<source>/ingest_log.duckdb` and is retried on the next run.
- Shot-chart attempts can differ slightly from box-score FGA (see reconciliation below).

## History DB (older seasons 2013-14 .. 2021-22)

Purpose: make a pre-registered training-window experiment (4 vs 7 vs 12 seasons) possible. Season ints
2013..2021 (the tracking era). **These seasons are deliberately NOT in `nba.duckdb`**: production models
refit daily on everything in that file, so adding seasons would silently change production. They live in a
separate DuckDB with the identical schema (`nba/db/schema.sql`):

| what | where |
|---|---|
| history DB | `data/history/nba_history.duckdb` |
| parquet caches | `data/history/<source>/` (games, boxscore, pbp, tracking, hustle, ...) |
| postgame sidecar logs | `data/history/<source>/ingest_log.duckdb` |
| queue log / status | `data/ops/ingest_queue.log`, `data/ops/ingest_queue_status.md` |

Caches are kept apart (not shared with `data/pbp/` etc.) because `nba/ingest/availability.py` builds its
injury-report name index from every parquet in `data/pbp/`; old-season players would change production name
resolution.

CLI: global options `--db-path`, `--data-dir`, or `--history` (= both history paths) retarget every
`python -m nba.ingest` command; defaults are unchanged (`nba.duckdb`, `data/`). Per-game season loops
(`boxscore`, `pbp`, `team-advanced`, `availability`) have a circuit breaker (3 consecutive failures ->
600 s cool-down; stop after 6 trips; rerun resumes). `postgame-fetch --newest-first` orders by season.
Possessions/stints: `python -m nba.parse.history [--season N]` (history DB by default; reconciliation:
possessions vs `FGA + 0.44 FTA + TOV - OREB`, parsed points vs final score, clean 5v5 rate). Note the
production parser itself measures mean |error| 1.9-2.2 (signed -1.3 to -1.7) on 2022-2025, so the literal
"<= 1" gate is reported but the history flag is "within the production envelope" (MAE <= 3.0).

### The queue (one stats.nba.com puller at a time, 2.5 s/request)

`sh ops/run_ingest_queue.sh` (nohup + caffeinate, no launchd) runs `nba/ingest/queue.py`:
handover from the legacy `run_postgame_backfill.sh` chain after hustle (stopped between sources) ->
current tracking/hustle top-up -> history games + box scores (2021 -> 2013) -> current officials, shots,
coaches, matchups (each `postgame-load`ed into `nba.duckdb` only when `lsof` shows no holder) ->
history play-by-play newest first, each season parsed under `data/ops/heavy.lock` -> history
tracking and hustle (+ load). Steps are idempotent; a restart skips finished steps. Failed steps get up to
two retry passes 30 min apart.

### Official injury-report PDFs for old seasons (probe, ak-static CDN, ranged GET)

Present: 2018-12-20 onward (checked 2018-12-20, 12-28, 2019-01-02/08/15, 2019-02-15, 2019-04-01,
2019-10-25, 2020-12-25, 2021-04-15, 2021-10-22 ... 2022-10-19). Absent: 2018-12-10 and earlier (checked
2017-12-01, 2018-04-01, 2018-10-20, 2018-11-10, 2018-12-01, 2018-12-10). Early years (2018-19 to
2020-21) publish two snapshots a day (`..._01PM`, `..._05PM`, hour-only filename); 2021-22 has `..._09AM`.
So history seasons 2018-19 (from Dec) .. 2021-22 could get a forward-looking availability feed via the
existing `official-injury-report` backfill; 2013-14 .. 2017-18 cannot (inactive-list only). Not bulk-pulled.

### Coverage (updated as steps finish)

See `data/ops/ingest_queue_status.md` for live state. Per-season/source coverage table: TBD (filled in
below as each step completes).
