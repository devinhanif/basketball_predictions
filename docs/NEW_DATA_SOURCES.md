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

## History DB (older seasons 2019-20 .. 2021-22)

Purpose: make a pre-registered training-window experiment (4 vs 7 vs 12 seasons) possible. Season ints
2019, 2020, 2021. **These seasons are deliberately NOT in `nba.duckdb`**: production models
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

### Scope decision (2026-10-09): history starts at 2019-20

Seasons 2013-14 .. 2018-19 were **removed from the queue** (maintainer decision): the training
history starts at 2019-20 because official injury-report PDFs (the forward-looking availability feed)
exist only from 2018-12-20, so every history season from 2019-20 has the same feature set as
production. Do not re-add earlier seasons without a new decision. 2019-20 and 2020-21 are
COVID-affected: see "COVID era tags" below.

### The queue (one stats.nba.com puller at a time, >= 2.5 s/request, ~600 req/h)

`sh ops/run_ingest_queue.sh` (nohup + caffeinate, no launchd) runs `nba/ingest/queue.py`. Order
(re-prioritised 2026-10-09; referee features are a priority):

1. handover (stop legacy chain), `cur:tracking`, `cur:hustle` (2022-2024; hustle was running)
2. `cur:officials` 2022-2024, then `load:officials` (only when `lsof` shows no `nba.duckdb` holder)
3. `hist:games` 2021, 2020, 2019 -> `hist:boxscore` 2021, 2020, 2019
4. `hist:pbp` + `hist:parse` 2021, 2020, 2019
5. `cur:officials:2025` + `load:officials:2025`, then `cur:shots`, `cur:coaches`, `cur:matchups` (each + load)
6. `hist:tracking`, `hist:hustle` (+ history-DB load; the history DB only holds 2019-2021)
7. LAST: `cur:tracking:2025`, `cur:hustle:2025` (frozen holdout season)

Every child yields per key to `data/ops/lock` and `data/ops/lineups_lock` (+300 s grace). Steps are
idempotent (cached keys never refetched); a restart skips finished steps, and killing the queue
(SIGTERM) terminates the child between keys. Restarted 2026-10-09 15:55Z mid `cur:hustle` with the
2,135 cached files intact. Failed steps get up to two retry passes 30 min apart.

### Official injury-report PDFs for history seasons (2019-20 .. 2021-22)

Probe (ak-static CDN, not stats.nba.com): PDFs exist from 2018-12-20 (absent 2018-12-10 and earlier;
checked 2017-12-01 .. 2018-12-10); always the hour-only filename
(`Injury-Report_2021-11-15_09AM.pdf`). Cadence: 2019-20 / 2020-21 three snapshots a day (01PM, 05PM,
08PM; bubble Aug 2020: 11AM, 02PM, 05PM); 2021-22 hourly 06AM-11PM.

`nba/ingest/history_injury.py` (CLI `python -m nba.ingest.history_injury fetch|load`):

* `fetch` is calendar-driven (no DB needed): per date it probes seed hours (11AM, 1PM, 2PM, 5PM, 8PM);
  only if 11AM+1PM+2PM all exist (hourly regime) does it probe the remaining hours. ~1 req / 2 s.
  Windows: 2019: 2019-10-20..2020-03-12 and 2020-07-20..2020-10-12 (stoppage skipped), 2020:
  2020-12-10..2021-07-23, 2021: 2021-10-10..2022-06-17 (707 dates). Cache `data/history/availability_official/`;
  one manifest per date in `data/history/availability_backfill/` makes it resumable (done dates are never
  re-probed); a transient error (non-404/403, timeout) leaves the date unmarked and retried on rerun; 8
  consecutive errors stop the run. Every snapshot is kept with `as_of` = its publish hour, so the
  real-tip gate is the same as production (`as_of <= tip`).
* `load` parses cached PDFs into the HISTORY DB `player_availability` (`source='nba_official_report'`,
  delete-then-insert per `as_of`) with a history-aware `NameResolver` (team-as-of from history box scores;
  unresolved names beyond `--max-unmatched-rate` fail loudly; unmatched names go to the resolver log). It
  REFUSES to run until the history DB has `player_game_stats` (box scores): PDFs stay cached until then.
  Run after `hist:boxscore:*` finish: `uv run python -m nba.ingest.history_injury load`.
  The 2019-21 PDFs parse with the existing production parser (verified on 2019-12-10 and 2021-02-03).
* Spot-audit after the fetch: probe a few dates exhaustively (all hours) and compare to the manifest
  to confirm the seed-hour regime detection missed nothing.

### COVID era tags (`game_era_flags`)

`nba/ingest/era_flags.py`, table `game_era_flags(game_id, season, covid_bubble, no_fans, limited_fans,
shortened_season, notes)` in `schema.sql`. Rule-based and conservative; written to the history DB and to
`data/history/game_era_flags.parquet` (history + current seasons, current ones are all-False; `nba.duckdb`
is only read). Command: `uv run python -m nba.ingest.era_flags` (after `hist:games`).

| season | rule |
|---|---|
| 2019-20 | `shortened_season` for all games (suspended 2020-03-11). Games on/after 2020-07-30 (Orlando restart): `covid_bubble` and `no_fans` (neutral site, no home court) |
| 2020-21 | `shortened_season` (72 games) and `limited_fans` for EVERY game, regular season and playoffs (attendance ranged from empty to partial capacity by arena/date; no per-arena calendar is encoded). `no_fans` is not asserted. TOR home games noted (played in Tampa) |
| others | all False |

Known unflagged edges: 2020-03-10/11 (some games played without fans), January 2022 capacity caps,
COVID-protocol postponements. Use `limited_fans OR no_fans OR covid_bubble` as one "reduced home advantage"
covariate or drop those games; do not treat `no_fans=False` in 2020-21 as "fans present".

### Forward referee assignments (`nba/ingest/referees.py`)

Read-only collector for `official.nba.com/referee-assignments` (posted ~9 am ET; NBA table only, the
WNBA table on the same page is ignored). Writes `data/refs/refs.duckdb` (separate from `nba.duckdb`):
`ref_snapshots` (fetched_at, page_date, sha256, raw HTML in `data/refs/raw/`) and `ref_assignments`
(one row per snapshot x game: away/home team + id, crew_chief / referee / umpire / alternate with jersey
and `official_id`). An identical page is not re-stored; a changed page adds a new snapshot, so use
`fetched_at < tip` as-of. Names map to `official_id` through `game_officials` history (read-only; cached
in `data/refs/official_ids.json`; same-name officials disambiguated by jersey). Unmapped officials are
kept with NULL id and the CLI exits 2; `python -m nba.ingest.referees remap` fills late ids once
`game_officials` is loaded (the table is empty in `nba.duckdb` until `load:officials`).

One-line addition for the lead (not applied; calls are cheap and deduped, so hourly is fine), in
`ops/nba_daily.sh`, `pretip)` branch right after the `predict` step:

    step refs "$UV" run python -m nba.ingest.referees collect

(rc 2 = some officials not yet mapped; harmless until `load:officials` has run, then it should stay 0.)

### Coverage (updated as steps finish)

See `data/ops/ingest_queue_status.md` for live state. Per-season/source coverage table: TBD (filled in
below as each step completes).
