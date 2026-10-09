# Live-path rehearsal (2026-10-09) for the 2026-10-20 opener

Goal: exercise, against the REAL network but a COPY of the database, the parts the offline replay
(docs/DRESS_REHEARSAL_2026-10-09.md, issues D and E) never touched: the live schedule, the injury-report
PDF path, the official rosters and `players_static` autofill, one simulated opening-night `nba.daily run`,
the lineups collector and `run-t30`. Read-only w.r.t. markets. The real `nba.duckdb` was never opened
(copied once while `lsof` showed no holder; `data/rehearsal/live_copy.duckdb` is the working copy).

Isolation used: DB copy; a scratch lineups store (`data/rehearsal/lineups/`); scratch roster cache
(`--roster-dir data/rehearsal/rosters`, so a rehearsal day never seeds the real per-date cache that the
real 10-20 run would then reuse) and model cache (`--model-cache`). While making live stats.nba.com calls the
hand-made lock `data/ops/lock` was held (queue yielded) and, for the model fit, `data/ops/heavy.lock`;
both released by a trap (`data/rehearsal/live.sh`). Calls made: ScheduleLeagueV2 x3, CommonTeamRoster x60
(two full 30-team passes), CommonPlayerInfo x110, lineups feed GET x4, plus about 600 ak-static PDF probes
(a different host, not the metered one). All at `--rate-limit-s 2.5`.

## Results per step

| # | Step | Result | Evidence |
|---|---|---|---|
| 1 | Live 2026-27 schedule | PASS | `ScheduleLeagueV2(2026-27)`: 1,274 rows in 2.9 s (001: 67, 002: 1,206, 006: 1); 1,200 kept. Columns match the parser. First kept date 2026-10-20. `slate_for_date(2026-10-20)` = 3 games: `0022600001` BOS@DET 15:00 ET (19:00Z), `0022600002` PHI@NYK 19:00 ET (23:00Z), `0022600003` OKC@SAS 21:30 ET (01:30Z on 10-21, still the 10-20 ET date). `live_tips_2026-27.parquet` holds 1,200 real tips. |
| 2 | Official injury report | PASS after 2 fixes | No preseason reports exist (162 probes 10-01..10-09 found none). Summer-League reports exist 07-03..07-19 but every row is `NOT YET SUBMITTED` (17 PDFs checked): parser returns 0 rows, no crash. Latest report with players: 2026-06-13 17:45 ET (finals; 2 rows). Live-path function `pull_latest_report` pulled it into the COPY (idempotent, 0 unmatched, 0 NULL player ids). Probing for 10-20 18:00Z found nothing in 96 probes ("no_report_found", 29 s at 0.3 s, ~4 min at 2.5 s). The live default name index was BROKEN (bug 1) and the resolved game_id was always NULL for upcoming games (bug 2). |
| 3 | Official rosters + autofill | PASS | 30/30 teams, 615 players (18-21 per team) in 73 s. 110 players lacked `players_static` (all `exp=R`, no box history); autofill (cap 120) pulled 110, loaded 110, failed 0 in 273 s. 53 are 2026 draftees with `draft_pick` (e.g. pick 1 = AJ Dybantsa, pick 2 Darryn Peterson, pick 3 Cameron Boozer), 51 undrafted (NULL draft fields, expected), 6 from earlier draft classes. 100 have position/height, 0 all-NULL rows. Still missing afterwards: 0. |
| 4 | Simulated opening night | PASS after 2 fixes | `nba.daily run --date 2026-10-20 --roster-source official --log-int-variant --now 2026-10-20T18:00` (full path, nothing skipped): slate 3, predicted 3, 1,112 rows: 3 `rung0_mov_elo`, 2 `rung0_injury_elo` (see below), 432 `props_context_residual` (108 players x 4 stats, 36 per game), 432 `props_recency_v1`, 243 `props_context_residual_int` (81 players x reb/ast/fg3m; pts excluded by design). Validator over every JSON: 0 NaN/inf, 0 non-monotone `p_ge` / `p_ge_uncond` / `q_grid`, 0 `q10<=q50<=q90` violations, p_play and p_home in range, all `made_at < tipoff`. Wall 322 s (about 75 s rosters + about 240 s of report probes + about 6 s models), peak RSS 871 MB. First run on a cold cache: ctxres fit 10 s on n=112,645 through 2026-06-13. |
| 5 | Lineups collector + `run-t30` | PASS after 2 fixes | One real poll of today's feed (scratch store): HTTP 200, 2 games, 48 rows, tips parsed from status text ("8:00 pm ET" -> 00:00Z). 10-20 schedule tips stored (3, source `schedule`). The 10-20 file is NOT published yet: the feed answers HTTP **302** to `/error/` (bug 3). `run-t30` dry on the copy: before T-30 untouched; at 18:35Z game 1 recorded `skipped / no_snapshot_before_t30`; reruns idempotent; 0 `t30` prediction rows. Before the fix the games were invisible (bug 4). |

### Step 4 detail (coverage, fallbacks, reasons)

* Roster source: "official rosters for 30/30 teams"; 0 rostered players lacked `players_static` after autofill.
* Coverage of the 6 slate teams: 125 official-roster players; 105 predicted (84%) + 3 recent-only players; 20 not
  predicted: 13 veterans with history and 7 rookies, all cut by the 18-per-team roster cap (lowest projected minutes).
* Routing: 324 context-residual rows (81 players), 108 `recency_fallback` rows (27 players with <5 prior played games, reason
  `fewer than 5 prior played games`); 22 predicted rookies have a mean projected 14.0 min (draft-slot prior applies).
* Win models: with NO report (the true state at 18:00Z) all 3 games are `rung0_mov_elo` primary with
  `fallback_reason = no_report_published_by_tipoff_minus_lead`. To exercise injury-Elo primary I inserted 3
  clearly labelled SYNTHETIC `out/questionable` rows into the COPY (reason "SYNTHETIC rehearsal row"; they exist
  nowhere else): `rung0_injury_elo` v2 became primary for the 2 games with rows (p_home 0.563 -> 0.652 and 0.735 -> 0.786, coefficient
  `coef_out` 0.306 refit on 5,143 signal games, d_out 1.22 / 0.93), the third game fell back with its reason, and `out_excluded=2`.
  Those two probabilities are mechanics, not a forecast.
* Season-boundary: the injury-Elo refit used games through 2026-06-13; MOV-Elo carryover regression applied
  (NYK over PHI at 0.73 is the post-regression rating, not checked against a market).

## Bugs found and fixed (all small, tested; no model behaviour changed)

1. **Live injury pull always failed.** `pull_latest_report` built `build_merged_name_index`, a last-name-keyed index
   from 5,269 cached play-by-play files, which raises `ValueError: ambiguous player names` ('green', 'smith', 'williams', ...). The pipeline
   catches it as `injury=failed`, so from the first report on opening night every game would have lost its injury input
   (props OUT list, injury-Elo) silently. Now the default is the team-aware `NameResolver` the historical backfill already uses
   (unmatched names go to `data/availability_backfill/unmatched.csv`; the 5% unmatched-rate fail-loud stays). Test:
   `test_pull_latest_report_default_uses_team_aware_resolver`.
2. **Injury-Elo could never fire live (the primary win model would have been MOV-Elo all season).** Report rows get their
   `game_id` by matching the `games` table, and `load_report_rows` joins `games`; an upcoming game has no `games` row until it is played (the fetch only returns
   played games). So rows were NULL-keyed and dropped, and every slate game fell back to MOV-Elo (paired delta exactly 0). Existing tests
   inserted the slate into `games`, which hid it. Fix: the schedule's `(date, home, away) -> game_id` is passed to the parser
   (`extra_games`), and `injury_report_rows` reads forward games straight from `player_availability`. Stored rows now carry the game_id, so they
   also join `games` and enter training once the game is played. Tests: `test_pipeline_live_slate_not_in_games_table_still_uses_injury_model`,
   `test_report_game_id_resolved_from_schedule_when_not_in_games`.
3. **Rookies on the injury report were silently dropped.** The bundled nba_api player list ends at id 1643141: 0 of the 53 2026 draftees and
   0 of 70 new ids are in it, and the resolver pool also needs box-score history, so a debutant listed OUT resolved to `no_candidate` (logged, not an error) while the
   official-roster path projects him as playing. Fix: the official roster frame now keeps `player_name`, the roster is loaded BEFORE the injury pull, and its names join
   the resolver pool (`extra_names`). Roster names resolving to their own id: 486 -> 596 of 615 (the rest need a team to disambiguate duplicates). Only active with
   `--roster-source official` (the ops script uses it until 2026-11-04; after that `recent`, by which time rookies have box scores). Test:
   `test_roster_names_feed_the_injury_resolver_for_debutants`.
4. **Lineups poll exited 1 on a day whose file is not published yet.** The feed answers a future date with HTTP 302 -> `/error/`, but only 404 was treated as benign, so
   `ops/nba_lineups.sh` would append an ALERT on every bootstrap tick of each off-day/morning. 302 is now benign (`poll_ok`). Test:
   `test_poll_exit_status_treats_unpublished_day_redirect_as_benign`.
5. **`run-t30` dropped games with no snapshot at all.** The T-30 slate came from `game_tips JOIN lineup_snapshots`, so a game whose file never appeared was neither
   logged nor recorded as skipped, and `due` never fired for it: the shadow arm's denominator silently lost exactly the games that failed. `games_with_tips(include_unseen=True)`
   (used by `schedule_from_lineups` and `due`) now returns them with team ids 0; `run_t30` records `skipped / no_snapshot_before_t30` before any team is needed. Test:
   `test_tip_known_game_with_no_snapshot_is_recorded_not_dropped`.
6. **`--roster-dir` CLI flag** (`nba.daily run`): lets a rehearsal keep the per-date roster cache out of `data/rosters/`. Without it a rehearsal using date 2026-10-20 would have
   written a cache the real 10-20 run reuses (stale rosters). Default unchanged.
7. **HEAD was syntactically broken** at the start of my commit: `nba/daily/pipeline.py` as committed in `f7a8c11` has `_additive_fields` spliced inside `_frame_preds`
   (`IndentationError`, line 434). The working tree (and every test run here) had the correct order; my pipeline commit contains the repair. If another branch or checkout was created from
   `f7a8c11`, it does not import `nba.daily`.

Commits: see "Commits" at the end (SHAs filled in).

## Remaining risks (not fixed)

1. **No real multi-game report with players has been parsed in this rehearsal.** Preseason reports do not exist; Summer-League ones are all placeholders. The PDF layout/headers
   of 06-13 and 07-19 are identical to the 1,673 historical reports already in the DB, and the live path reproduced the 06-13 load, but the first real full-slate PDF is on 10-19/10-20. Watch the
   first `pretip_20261020.log` for `injury=ok` and `unmatched.csv` growth.
2. **Probing cost.** With no report published, one run makes 96 probes, each through the shared limiter: about 240 s at `--rate-limit-s 2.5` (the ops value), holding `data/ops/lock` that long every hour of a morning.
   The probe host (ak-static.cms.nba.com) is not the metered one; giving probes their own 0.3 s limiter would cut this to 30 s. Not changed (timing only).
3. **Resolver tie-break window is 45 days**, so in October a full-name duplicate (two players with the same name) has no recent history on either team and stays unresolved; it counts toward the 5% limit,
   and above 5% the report load raises and the slate falls back (visible as `injury=failed:` in the summary). Expected rare.
4. **Rookies are covered in injury resolution only with `--roster-source official`** and only if CommonTeamRoster lists them. After 2026-11-04 (recent) a debutant first appears via box scores, which resolves him.
5. **Roster cap 18** leaves 13 of 125 veterans with history (and 7 rookies) unpredicted on opening night (slate teams). Same as the replay; not new.
6. **`run-t30` official-roster call** passes no roster dir and no rate limiter, and its autofill is unthrottled. Normally a pure cache hit on the date's pretip cache; a new unseen id would fire unthrottled CommonPlayerInfo calls (a handful, not 100).
7. **Schedule is not complete**: 1,200 of the 1,230 regular-season games are scheduled (80 per team, plus 6 Cup placeholder rows with TBD teams that the parser drops); the 30 are the post-group-stage Cup games and appear in the live
   schedule later (the pipeline re-reads it every run). The one `006` knockout row (2026-12-11) is dropped by design (dress-rehearsal issue C).
8. **Lineups feed semantics still unverified**: `Inactive` rows and the `timestamp` meaning need a regular-season day. The 302 behaviour for unpublished days is now recorded; how early the 10-20 file appears is unknown.
9. **Open from the dress rehearsal and not part of this scope**: issue A (market-deferral gate opens after one day of settled shadow rows), issue B (P(play) calibration in week 1), F (fg3m ties in q10/q50), and the Drive backup steps in `ops/nba_daily.sh`.
10. The real `nba.duckdb` has not been run with the fixed code; the first real run is the first live use of bugs 1-3's fixes. Mitigation: run the pretip job by hand on 10-19 (see checklist).

## Updated go / no-go for 2026-10-20

| Check | Status |
|---|---|
| Live schedule fetch, tips, 10-20 slate (3 games, first tip 15:00 ET) | GO (verified live) |
| Official rosters 30/30, debutant autofill with draft slots | GO (verified live; 110 filled, 53 draft slots) |
| `nba.daily run` end to end on a DB copy, all models, no NaN, monotone p_ge | GO |
| Injury-Elo primary actually fires live | GO only with this commit set (was NO-GO: bugs 1-2) |
| Injury PDF parse of a real multi-game, in-season report | UNVERIFIED until 10-19/10-20 (risk 1) |
| Rookies named on the report are not dropped | GO with `--roster-source official` (bug 3) |
| Lineups poll does not alert on an unpublished day; T-30 skips are recorded | GO (bugs 4-5) |
| `nba.duckdb` is safe to touch: HEAD imports | GO after this commit set (bug 7) |
| First live use of the fixes | Watch `data/ops/pretip_20261020.log` (09:30 CT job): `injury=ok`, `roster_source`, `rows=`, then `data/availability_backfill/unmatched.csv`; do NOT run a manual pre-dated `--date 2026-10-20` against the real DB before then (it would seed the per-date roster cache) |
| Backups / rclone Drive steps | Maintainer to confirm (unchanged) |

## Reproduce

```
data/rehearsal/live.sh uv run python -m nba.daily --db-path data/rehearsal/live_copy.duckdb run --date 2026-10-20 \
  --roster-source official --log-int-variant --now 2026-10-20T18:00 --rate-limit-s 2.5 \
  --roster-dir data/rehearsal/rosters --model-cache data/rehearsal/model_cache_live
uv run python -m nba.daily --db-path data/rehearsal/live_copy.duckdb run-t30 --date 2026-10-20 --now 2026-10-20T18:35 \
  --lineups-db data/rehearsal/lineups/lineups.duckdb --roster-source official
```
(`data/rehearsal/` is git-ignored; the synthetic availability rows exist only in `live_copy.duckdb`.)
