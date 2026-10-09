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
