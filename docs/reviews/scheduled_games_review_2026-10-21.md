# Adversary review: scheduled games written into `games` with NULL scores (2026-10-21)

Branch `agent/steward-scheduled-games`, head f90825f (one commit on main 60d1e12). Live-path change.
Reviewer worktree `agent/adversary-scheduled-games` (base main 4046519). Tests run read-only in the
steward's worktree. `nba.duckdb` opened `read_only=True` only (constraints and season 2022/2023/2024/2026
row counts). No season-2025 row was read.

Verdict: BLOCKED

Advisory reading in the caller's three words: APPROVED WITH EDITS once B1 is fixed. The branch's own
code is correct: insert-only, ET date, scorable ids only, its own try/except, replay-neutral. What blocks
it is a side effect outside its five files. The 08:00 morning `ingest` now leaves today's NULL-score rows
in `games` when `data_snapshot` / `data_check` run later in that same job. On the first morning, and on
about 20 more mornings before New Year, `data_check` exits 1 and alerts (NULL_SHIFT on
`games.home_pts`/`away_pts`, COVERAGE_DROP on `player_game_stats`). The branch exists to remove a daily
false alarm, and as written it adds a new one on the leak guard. Merge after B1, plus M1 or a recorded
decision to accept M1.

## Findings (severity-ranked)

### B1 (blocker): `data_check` alerts on the morning job because of today's scheduled rows

- Why: `ops/nba_daily.sh` morning (08:00, `ops/install_launchd.sh:5`) runs `step ingest` first. With
  this branch that step inserts today's slate (`nba/daily/__main__.py` run_ingest, new block after
  `incremental_ingest`). Further down the same job, `data_snapshot`, `data_diff` and `data_check` run
  (`ops/nba_daily.sh:201-203`). `nba/datamanifest/manifest.py:167-171` uses
  `SELECT season, count(*) FROM main.games` as the coverage denominator for every game-keyed table. Per
  season, the null rates of `games.home_pts`/`away_pts` include the NULL rows. `nba/datamanifest/diff.py:105`
  flags COVERAGE_DROP > 0.02 and `:115` flags NULL_SHIFT > 0.10 (`configs/datamanifest.yaml:15,17`).
  `check` returns 1 on any flag (`nba/datamanifest/__main__.py:119`), and `step()` alerts on rc 1.
- Verified with the real code: `scratchpad/real_manifest.py` built two scratch DuckDB files from
  `games` + `player_game_stats` of seasons 2024 and 2026, one with the 11 NULL rows of the 10-21 slate
  added. It then ran `build_manifest` + `diff_manifests` with the repo config. Exit 0. Output:
  - `games 2026 home_pts null_rate: 0.0 -> 0.785714`
  - `pgs 2026 coverage: 1.0 -> 0.214286`
  - flags: `NULL_SHIFT games.away_pts`, `NULL_SHIFT games.home_pts`, `COVERAGE_DROP player_game_stats`
- How often: `scratchpad/sim_manifest.py` replays the 2026-27 schedule cache, with morning state = all
  earlier games scored + today's slate NULL. It predicts flags on 21 mornings between 10-22 and 12-23
  (first morning 10-22: null rate 0 -> 0.125 and coverage 1.0 -> 0.875; then every day the slate jumps
  in size, e.g. 11-04 with 14 games after a 0-game day). This is a simulation; the scratch-file run above
  is the measured mechanism.
- Exact fix (preferred; in `nba/datamanifest`, so it goes through the main session to that directory's
  owner):
  1. `nba/datamanifest/manifest.py:170`: denominator over scored or past rows only:
     `"SELECT season, count(*) FROM main.games WHERE home_pts IS NOT NULL GROUP BY 1"`.
  2. In `_table_manifest`, when `name == "games"`, add `WHERE t.home_pts IS NOT NULL` to the per-season
     and overall null-rate aggregates. Record `scheduled_rows` = `count(*) WHERE home_pts IS NULL` as
     its own field. Add a diff flag `STALE_SCHEDULED` when any NULL-score row has `game_date` earlier
     than the snapshot's ET date minus one day. That turns M1 residue into a single deliberate alert.
  3. Test: `tests/datamanifest/` adds a case with N NULL-score rows dated today. It must produce no
     NULL_SHIFT or COVERAGE_DROP; a NULL-score row dated 3 days ago must produce STALE_SCHEDULED.
- Alternative fix, inside the branch plus one constant: drop `schedule_fn=fetch_schedule_nba_api` from
  the `ingest` call in `nba/daily/__main__.py` `main()`. The first pretip run at 09:20 then writes the
  rows after the 08:xx snapshot. Move `GAMEDAY_PROBE_FROM` (`nba/ops/watchdog.py:55`) from 09:00 to
  09:45 so the probe does not fire before that first pretip run. Cheaper, but it leaves the manifest
  unaware of a by-design state, and any manual snapshot during the day still flags. Not preferred.

### M1 (major, conditional): a same-day-postponed game leaves a NULL row that later features count as a played loss

- Why: the docs say "A postponed game keeps its NULL row (never scored, harmless)"
  (`docs/DAILY_PIPELINE.md`, the new paragraph). Grading code is indeed unaffected. Feature code is not.
  Once the game's date passes, the history copies that feed live props are all `games WHERE game_date < ?`
  with no score filter:
  - `nba/props/forward.py:183` (`_COPY_SQL["games"]`) and `:581` (`_history_frames`)
  - `nba/daily/pplay_shadow/features.py:351,389`
  - `nba/daily/t30.py:303`, `combo_shadow/run.py:145`, `full_shadow/run.py:60` (all via `_history_frames`)

  `nba/features/team_features.py:59-67` takes every `games` row. `COUNT(*) OVER w_season` counts it,
  `CASE WHEN team_pts > opp_pts ... ELSE 0.0` scores NULL vs NULL as a loss, and `LAG(game_date)` and
  `LAG(host_team_id)` move rest days and travel. `nba/props/minutes.py:235` uses `rest_days`, `b2b` and
  `travel_miles_asof` from it, and `build_standings_features` uses the record.
- Verified: `scratchpad/postponed.py` (schema.sql in memory, exit 0). Team 1610612737: a scored win on
  10-21, a target game on 10-25, and a postponed NULL row on 10-23.
  - Without the row: `games_played_prior 1, season_win_pct_prior 1.0, rest_days 4.0, travel_miles_asof 0.0`.
  - With the row: `games_played_prior 2, season_win_pct_prior 0.5, rest_days 2.0, travel_miles_asof 424.8`.
- The finder's upsert moves the row to the make-up date when the game is finally played
  (`upsert_rows` updates `game_date`; verified in `scratchpad/upsert_after.py`). So the damage lasts from
  the postponement to the make-up, usually weeks, and is permanent for a cancelled game. The same residue
  holds that date out of `_SETTLED_DATES` (`nba/daily/checkpoint.py:183-186`), which delays look counting,
  and shifts `tgn` (`checkpoint.py:199-202`). Frequency: about 1-3 same-day postponements a season
  (weather, a death in the organisation, the 2025 LA fires). Rare, but silent.
- This is a new state. The finder never writes NULL scores: in the live DB, seasons 2022/2023/2024/2026
  have 0 NULL `home_pts` rows. The replay NULLs only `game_date >= replay_date`, never a past date, so
  the replay oracle cannot see it.
- Exact fix: in `nba/daily/ingest_step.py` add `drop_stale_scheduled_games(con, run_date, season_int)`:
  `DELETE FROM games WHERE season = ? AND home_pts IS NULL AND game_date < ?`, returning the count.
  Call it in `run_ingest` and in run_daily's ingest block, only after `incremental_ingest` succeeded
  (the finder has just had its chance to fill every played game), and record
  `scheduled_rows_dropped`. Because every NULL-score row is a scheduled row (above), this only returns
  the table to its pre-branch state for that game. Test: insert a NULL row dated yesterday, run
  `run_ingest` with an EMPTY finder, and assert the row is gone and a scored row is untouched. Then
  rewrite the doc sentence. If deleting rows in a daily job is unwelcome, the alternative is that B1's
  `STALE_SCHEDULED` flag alerts and the row is removed by hand. That is Devin's call; record it in
  DECISIONS either way.

### m1 (minor): doc says the ingest runs at 07:00

`docs/DAILY_PIPELINE.md` (new paragraph): "`python -m nba.daily ingest` (07:00, before settle)". The
morning job runs at 08:00 (`ops/install_launchd.sh:5`). Fix: "08:00, first step of the morning job".

### m2 (minor): the watchdog slate alert text is now wrong

`nba/ops/watchdog.py:571-576` says "the schedule fetch does not write scheduled games (expected: `games`
gets a row only once a game starts; not a fetch failure)". After this branch, a missing row means the
insert did not run or failed. Fix the text to "today's scheduled rows were not written (morning ingest
or pretip insert failed or has not run); see data/ops/morning_<date>.log / pretip log". This file is not
on the branch; it is a follow-up for its owner.

### m3 (minor): `run_ingest` writes no slate when the finder fails

`nba/daily/__main__.py` run_ingest returns `RC_DEGRADED` straight from the `incremental_ingest`
except-branch, before the new schedule block. On a finder failure the 09:00 watchdog probe then fires the
slate alert as a second alert until 09:20. Fix: record the failure, fall through to the schedule block,
and return `RC_DEGRADED` at the end. If M1 is adopted, keep the stale-row drop behind the finder's
success.

### m4 (nit): the insert counter counts attempts, not inserts

`nba/daily/ingest_step.py` `insert_scheduled_games`: `n += 1` runs even if `ON CONFLICT DO NOTHING`
dropped the row (possible only with a second writer, which the single-writer rule forbids). Fix:
`n += int(con.execute(<insert>, [...]).fetchone()[0])` (DuckDB returns the inserted count).

### m5 (minor, manual tools only, not in launchd): readers with no score or date guard

- `nba/markets/capture.py:236-240` `backfill_games`: a run before 19:00 ET with `until >= today` would
  store today's market state as of now under the 19:00 proxy tip, and the `done` key blocks a re-capture.
  Fix: add `AND home_pts > 0` (the steward flagged this too).
- `nba/ingest/availability.py:616-621` `game_dates_between`: a manual report backfill whose end is today
  would mark today done after a partial day. Fix: callers cap `end` at yesterday, or add
  `AND (home_pts IS NOT NULL OR game_date < current_date)`.

## Check table

| # | Check | Command / evidence | Result | Pass |
|---|---|---|---|---|
| 1 | Scope | `git diff --stat main...agent/steward-scheduled-games`: 5 files (`docs/DAILY_PIPELINE.md`, `nba/daily/__main__.py`, `nba/daily/ingest_step.py`, `nba/daily/pipeline.py`, `tests/daily/test_scheduled_games.py`) | `settle.py`, the settle branch of `__main__.py` and `ops/nba_daily.sh` are untouched. The `__main__` diff touches only the imports, `run_ingest` and the `ingest` call. | PASS |
| 2a | PK exists | `schema.sql:6` `game_id VARCHAR PRIMARY KEY`; live DB `duckdb_constraints()` read-only: `[('PRIMARY KEY', ['game_id']), ('NOT NULL', ['game_id'])]` | ON CONFLICT is valid. The existence check makes it belt and braces. | PASS |
| 2b | Finder row never overwritten | test `test_existing_finder_row_is_never_modified`; scratch `upsert_after.py`: the finder upsert over a NULL row fills scores and keeps `national_tv='TNT'` | `('0022600009', 2026-10-22, 2026, 1, 2, 101, 99, 'TNT')` | PASS |
| 2c | ET date and season | `slate_for_date` / `game_date_et` (UTC to ET); `season_int_for_date` is the same start-year rule as `season_to_int`; test `test_insert_uses_the_et_date_of_the_tip` (02:00 UTC is the previous ET day) | correct | PASS |
| 2d | 001/003 never inserted | the filter is `is_scorable_game_id` inside the function, so callers cannot bypass it; test covers 001; scratch covers 003 (`inserted 1` of 002+003) | correct | PASS |
| 2e | Postponed residue | `postponed.py` (above) | NOT harmless (M1) | FAIL (major, conditional) |
| 3 | Reader audit | own grep over `nba/` and `ops/` (77 files with `FROM/JOIN games`); table below | 1 blocker path (datamanifest), 1 conditional path (history copies), the rest safe | see table |
| 4 | Replay oracle | `replay_season.py:299` passes `skip_ingest=True`; the insert sits under `if not skip_ingest`; nothing else in run_daily's non-ingest path changed (diff is imports + 6 lines) | The hash cannot move through run_daily. It can move only if the replay ever ran with `skip_ingest=False` (it does not) or through the `ingest` subcommand (not called by the replay). Oracle row 2026-10-21 logged; its result line was not yet in the log when I read it. | PASS (reasoned) |
| 5a | Schedule fails, no cache | run_daily: `_schedule_with_fallback` re-raises as before (unchanged by this branch). run_ingest: test `test_ingest_subcommand_degrades_when_schedule_is_unavailable` gives rc 5 and no rows | unchanged / degrades | PASS |
| 5b | Insert raises mid-run | run_daily: own try/except appends to `summary.errors` (exit 5) and the forecast continues. run_ingest: caught and rc 5 after the finder work committed. **Untested** in run_daily. | correct by reading | PASS (untested) |
| 5c | Day with no games | `insert_scheduled_games(c, [], ...)` returns 0 (scratch). The ingest still makes one ScheduleLeagueV2 request each morning. | fine | PASS |
| 6 | Tests | 9 tests read; gaps listed below | adequate for the happy path | PARTIAL |
| 7a | pytest | `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/daily tests/ops tests/export tests/parlay tests/test_layering.py --no-cov -q -p no:cacheprovider` (output to scratch file, exit shown) | `741 passed, 2 skipped, 64814 warnings in 349.12s`, exit 0 | PASS |
| 7b | ruff | `uv run --no-sync ruff check` on the 4 changed .py files | `All checks passed!` exit 0 | PASS |
| 7c | format | `uv run --no-sync ruff format --check` on the 4 files | `4 files already formatted` exit 0 | PASS |
| 7d | mypy | `uv run --no-sync mypy nba/daily/__main__.py nba/daily/ingest_step.py nba/daily/pipeline.py` | `Success: no issues found in 3 source files` exit 0 | PASS |
| B1 | Manifest side effect | `real_manifest.py` (exit 0), `sim_manifest.py` (exit 0) | 3 flags on the first morning; 21 flag mornings simulated through 12-23 | FAIL (blocker) |

## Reader table (my own grep, not the steward's)

Today's row has `game_date == run_date` and NULL scores. "Lingering" means a NULL row dated before
run_date (M1). Every live-path reader that needs history uses `game_date < run_date`, so today's own row
is never a prior game for today's forecast. The danger is only lingering rows.

| Reader | Filter | Today's row | Lingering row | Verdict |
|---|---|---|---|---|
| `daily/settle.py:84,226` (winner and prop settle) | `home_pts > 0 AND away_pts > 0` + box | ignored | ignored | safe |
| `daily/ingest_step.py:26` missing_boxscore_game_ids | `home_pts > 0` | ignored | ignored | safe |
| `daily/predict.py:157` completed_games_before (MOV-Elo fit) | `< slate`, pts > 0 | ignored | ignored | safe |
| `models/injury_elo.py:276,604` | `home_pts IS NOT NULL` / `> 0` | ignored | ignored | safe |
| `daily/predict.py:127-133` injury_report_rows | `load_report_rows` joins `games`; `known` | Today's report rows now arrive through the join with `game_date = run_date` instead of through the `forward` branch with the same date (pipeline passes run_date; `predict.py:262` passes the ET tip date, the same day). Equivalent; the replay already ran this way. | n/a | safe |
| `daily/predict.py:215`, `combo_shadow/run.py:160,294`, `avg_shadow/run.py:345`, `top_source.py:354`, `shadow_llm/payload.py:285,326,456` | box join and/or `< run_date`, pts > 0 | ignored | no box, so no rows (team-games query at 326 has `home_pts > 0`) | safe |
| `props/forward.py:183,581`, `pplay_shadow/features.py:351,389`, t30 / combo / full via `_history_frames` | `game_date < as_of`, no score filter | excluded by date | **included; team_features counts it** | M1 |
| `features/team_features.py:59-67`, `game_context.py:117`, `props/minutes.py:235` | none | only through the copies above | **counted as a played loss; rest/travel moved** | M1 |
| `avg_shadow/sequences.py:179` | `< before_date` (daily passes run_date, `:826`) | excluded | included (sequence model reads scores NULL) | M1 class |
| `daily/checkpoint.py:183-186` `_SETTLED_DATES` | dates with any unscored 002 game excluded | today excluded (correct: not settled) | the date stays unsettled until the make-up | M1 |
| `daily/checkpoint.py:199-202` tgn | none | later than every box game: no shift | +1 tgn for later games | M1 |
| `daily/checkpoint.py:577` | LEFT JOIN on a forward table | season resolves | n/a | safe |
| `daily/report.py:56` rollover count | pts > 0 | ignored | ignored | safe |
| `daily/report.py:349-404` latest slate day | max date with forward_predictions | Morning report at 08:xx: today has no predictions yet, so unchanged. A manual run during the day shows today (the intended view). | n/a | safe (behaviour note) |
| `daily/stamps.py:216`, `late_scratch.py:124`, `lt_live.py:64`, all `*_shadow/settle.py`, `*_shadow/display.py`, `replay_season.py:665` | pts > 0 | ignored | ignored | safe |
| `forward/scorecard.py:269,279,297` | box joins | no box | no box | safe |
| `export/reader.py:153,216,704` | `final = bool(home_pts and away_pts)` | `final: false`, pts None (never "final 0-0"); home/away/date now filled from `games` | same | safe |
| `export/reader.py:319,376` | `home_pts IS NOT NULL` | ignored | ignored | safe |
| `export/best_model.py:171,250`, `parlay/top_source.py:249` | LEFT JOIN for season/teams | season and teams now resolve (intended) | n/a | safe |
| `parlay/papertrade.py:68,93` | pts not NULL and > 0 | not settled | not settled | safe |
| `ops/coverage.py:182`, `ops/watchdog.py:466` | presence only | now present (the purpose) | n/a | safe (m2 text) |
| `facts/build.py:132` (nightly, `NBA_FACTS_NIGHTLY=1`) | none; selects no scores | today joins the game list (schedule facts already include it) | in the list | safe |
| `datamanifest/manifest.py:170` + per-season null rates | none | **NULL_SHIFT / COVERAGE_DROP** | same | **B1** |
| `markets/capture.py:237` backfill_games (manual) | dates only | captured early under the 19:00 proxy | captured | m5 |
| `ingest/availability.py:618` game_dates_between (manual) | dates only | today in range | in range | m5 |
| `odds/history_pull.py:95`, `ingest/national_tv.py:152`, `ingest/injury_intraday.py:302,603`, `data/*.py`, `kalshi/alias_review.py`, `explain/*`, `ingest/postgame.py`, research/eval/parlay harnesses (season <= 2024 or explicit) | ids/dates/seasons, manual or historical | harmless (no score read, or season-capped) | harmless | safe / not live |
| `daily/rehearsal.py:48`, `schedule.py:104`, `replay_season.py:95,210` | replay helpers | replay unaffected (skip_ingest) | n/a | safe |

## Tests: untested branches

1. run_daily with `insert_scheduled_games` raising (monkeypatch). Assert `summary.errors` has
   "scheduled games: failed" and the forecast rows are still written.
2. run_ingest where the schedule fetch fails but a fresh cache exists (the success path of the fallback).
3. An all-star `003` id in the slate (only `001` is tested; scratch shows it is skipped).
4. The finder upsert after the insert fills scores and keeps `national_tv` (scratch only).
5. Empty slate (0 inserted, rc 0) and run_ingest with a failing finder (no insert today: m3).
6. A schedule entry passed in whose ET date is not run_date (the in-function filter).
7. B1 and M1 regression tests (above).

The midnight-UTC case is covered (`test_insert_uses_the_et_date_of_the_tip`). Idempotence on a second
same-day run is covered.

## Playbook check

Model class: none. This is data plumbing in the live path, not a model, so the class checks do not apply.
The cross-cutting questions as they bear on this change:

- Information or capacity: no model change. It changes what `games` holds before tip, so it can
  change model inputs (M1).
- Enough data: n/a.
- Test validity: the replay oracle is a valid regression test for run_daily under `skip_ingest`. It is
  NOT a test of the new live state: it never runs the insert, never has a past-dated NULL row, and never
  runs the morning datamanifest steps. An IDENTICAL oracle does not clear B1 or M1.
- Lineage and inherited guards: settle's `pts > 0` and box guards hold. The leak guard (datamanifest)
  is the one that breaks.
- Scored against: n/a (no metric claim). No n or CI is claimed, and none is needed for a plumbing change.

## Most likely failure mode if "this is safe" is wrong

On the first morning after merge, `data_check` alerts (B1). Within a few days, a real leak flag in the
same step gets read as "the scheduled-rows noise again". That is how `opp_adjusted_ridge` survived three
experiments: a NULL pattern sitting where we had learned to stop looking.

## What I could not verify

- The actual oracle result for f90825f (logged; the result line was not in the log when I read it).
- Real-table breadth of B1: I ran the manifest on `games` + `player_game_stats` only. Other game-keyed
  tables (possessions, tracking, hustle, matchups, availability) share the same denominator and are
  expected to flag too, but I did not run them.
- Live postponement frequency for 2026-27 (an estimate from past seasons).
- Whether `nba.ingest.national_tv` is ever run during a game day. It is not in launchd, but if it were,
  today's row would now get `national_tv` and the live `is_national_tv` would flip from False to True:
  more consistent with training, but a change.

## Recommended changes (not applied by me)

B1 (manifest fix plus test) before merge. Then either M1 (stale-row drop plus test, doc sentence
corrected) or a DECISIONS row accepting M1 with B1's STALE_SCHEDULED flag as the detector. Then m1, m3
and m4 on this branch, and m2 and m5 as follow-ups for their owners.

## Drafted DECISIONS row (not appended)

| 2026-10-21 | Scheduled games in `games` (agent/steward-scheduled-games f90825f): adversary BLOCKED pending B1. The morning `data_check` would flag NULL_SHIFT/COVERAGE_DROP from today's NULL-score rows (verified on scratch copies with the repo's manifest code; 21 flag mornings simulated through 12-23). Fix: the datamanifest excludes NULL-score rows from coverage and null-rate baselines and flags only NULL rows older than yesterday. M1 (postponed residue counted as a played loss by team_features) fixed by dropping NULL-score rows dated before run_date after a successful finder refresh, or accepted with the flag as detector (Devin's call). | Reverse if the manifest exclusion hides a real scores gap (a finder row with NULL scores), which the STALE_SCHEDULED flag is meant to catch. |
