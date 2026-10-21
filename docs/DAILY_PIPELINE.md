# Daily forward-prediction pipeline

Purpose: from the first 2026-27 tip (late Oct 2026) every game becomes a truly
out-of-sample test. `season=2025` is burned (see `HOLDOUT_ACCESS_LOG.md`); forward
predictions, written before tip-off and scored afterwards, are the only clean
holdout available. Read-only w.r.t. markets: no orders, no credentials.

## Commands (all `uv run`)

```
uv run python -m nba.daily run --date 2026-10-28      # ingest, injury report, predict, settle
uv run python -m nba.daily settle                     # score completed predictions only
uv run python -m nba.daily report                     # writes registry_store/reports/forward_report.md
# flags: --skip-ingest --skip-injury --no-props --db-path PATH --rate-limit-s 0.6
# comparison rows (default off): --log-int-variant, --log-lower-tail-variant
```

`run` exits 4 (informational) if any slate game had already tipped (those games get no prediction;
nothing is back-filled). `nba.duckdb` is single-writer: do not run while an eval holds it.

## Run steps

1. **Ingest** (`ingest_step.py`): refreshes `games` for the season string
   (`2026-27`) by calling the games fetcher directly. `pull_season_games` is NOT used
   because it is cached forever per season key and would freeze the season at its first
   pull. Then `pull_game_boxscore` (per-game cache, resumable) for completed games with no
   `player_game_stats` rows (cap 250/run).
   **Scheduled games (2026-10-21).** The finder returns only started or finished games, so
   `games` never held a game before tip. After the schedule fetch (step 2) and before the
   forecast, `insert_scheduled_games` writes TODAY's scorable ET slate (never preseason 001 or
   all-star 003) into `games` with NULL scores: insert-only (`ON CONFLICT DO NOTHING`, an existing
   finder row is never touched), count in the ingest dict as `scheduled_rows_inserted` and so in
   the run summary's `ingest:` line. The finder's later upsert fills the scores. `--skip-ingest`
   (the replay) inserts nothing: its `games` already holds every row, so the replay hash is
   unchanged. `python -m nba.daily ingest` (08:00 ET = 07:00 CDT, first step of the morning job,
   before settle) writes the same rows, from the cached schedule if the fetch fails, and does so
   even when the finder refresh fails (the run then returns degraded). A failed insert is a
   degraded run (exit 5) and never blocks the forecast. `scheduled_rows_inserted` counts rows
   actually inserted.
   **Stale placeholders.** A game postponed on the day keeps its NULL-score row, and the history
   frames (`games WHERE game_date < ?`) would count it as a played loss and shift rest days and
   travel. So, after a SUCCESSFUL finder refresh only, `drop_stale_scheduled_games` deletes
   `season = S AND home_pts IS NULL AND game_date < run_date` (the finder never writes NULL
   scores, so these are only our own placeholders) and reports `stale_scheduled_removed`. If the
   refresh fails nothing is deleted. The data manifest keeps `STALE_SCHEDULED` as the detector
   should the delete ever be skipped, and counts NULL-score rows separately (`scheduled_rows`),
   outside the `games` null rates and the coverage denominator. Readers grade on `pts > 0`, so a
   NULL row is never settled.
   **Coverage check** (`nba.ops.coverage`, `schedule`): it needs every slate game in `games` with
   home/away, so it should now pass before tip (it failed every game day before this step); the
   watchdog game-day probe's "games table lacks today's slate" line fires only if the step failed.
2. **Schedule** (`schedule.py`): nba_api `ScheduleLeagueV2` gives game ids and UTC tip
   times; the slate is games whose US-Eastern tip date equals `--date`.
3. **Injury report** (`injury.py`): probes 15-minute slots newest-first, strictly before
   `min(now, earliest tip-off)`, and pulls the first existing PDF through
   `pull_official_injury_report`. A feed failure is recorded in the run summary and never
   blocks the Elo forecast. Props exclude players marked `out` in the latest snapshot
   (<=36 h old).
4. **Models**: `resolve_production` calls `registry.load_model(name, 'production')`; if
   nothing is promoted the run records version `unregistered` (config fallback) rather than
   failing.
   - Win prob, primary: `rung0_injury_elo` (production alias; its `injury_elo.yaml` is read
     from the registered version dir, else `configs/injury_elo.yaml`). Computed by
     `nba.models.injury_elo.predict_games(con, as_of_ts, games, report_rows)`:
     frozen MOV-Elo logit + `b*(V_out_away - V_out_home)/10 + g*(V_doubt_away - V_doubt_home)/10`.
     **Leak-free coefficient choice:** `(b, g)` are refit from scratch on EVERY run by the same
     ridge logistic (lambda 5, MOV-Elo logit as offset, `min_signal_games=150` else 0) on
     completed games with `game_date <` the slate date only. No stored coefficients, so nothing
     stale or future can be carried (cost: one history feature build per run, seconds).
     Training features gate report snapshots on each game's REAL scheduled tip-off
     (`tip_source: real`, since 2026-10-09; see the changelog below), exactly as the
     walk-forward eval does; forward games use the live tip.
     **Real tip-off rule:** a report row counts for a game only if
     `as_of <= real tipoff (ET) - 60 min` and `as_of <= now`. A game with no usable report (or
     unknown tip) falls back to `rung0_mov_elo` with the reason stored as
     `prediction.fallback_reason`; an injury-model exception falls back for the whole slate and
     is recorded in the run summary. Games without a usable report are exactly MOV-Elo anyway
     (all injury features are 0).
   - Win prob, comparison: `MovEloBaseline` with `configs/mov_elo_tuned.yaml`, refit on every completed
     game with `game_date < slate date` (cheap, deterministic). On the first day of a new
     season the season-boundary carryover regression is applied explicitly, because `fit`
     only regresses between seasons it sees. BOTH models are stored in `forward_predictions`
     each day (`prediction.primary` marks the one to use; the injury row also stores
     `p_mov_elo`, `d_out`, `d_doubt`, the snapshot used and the coefficients).
   - Props (pts/reb/ast/fg3m), default `--props-model context`: **primary
     `props_context_residual` (`ctxres-v1`)** = `nba.props.context_residual` (LightGBM
     mean-residual + |residual| scale heads over the recency average, split-conformal
     quantiles; features include pre-tip injury-report vacated stats, minutes/role gaps,
     Elo margin, opponent, rest, CUSUM). It is fit on all PLAYED rows with
     `game_date < slate date` and cached in `data/models/context_residual/<date>/`
     (`models.pkl` + `meta.json` fingerprint of the training rows): a same-day re-run loads
     the cache, a new date (or newly ingested prior-day games) refits (~8 s fit, ~10 s per
     slate in the 2024-25 replay). The comparison **`props_recency_v1`**
     (recency-weighted played-games average, half-life 10) is logged for every player, like
     MOV-Elo is for win prob. Report rule: a game's OUT list is the latest official report
     stamped <= min(now, real tip-off - 60 min) and <= 36 h old; a game with no usable report
     has `has_report=0` (never "nobody out"). If the context model cannot run (too little
     history, error), the slate falls back to recency, written under both names, with
     `routed_to=recency_fallback` and the reason in `fallback_reason` and in the run summary.
     Players with < 5 prior played games get `recency_fallback` rows inside a normal slate.
     The only other mode is `--props-model rolling` (old 82-game baseline); the sim-routed
     mode and the registry-route branch were deleted on 2026-10-09 (edge 4 of the restructure).
     Rosters come from who played in each team's last 10 games minus outs; a rookie debut or
     an offseason move is invisible until a first box score. **Contract per player-stat**
     (all stored in the prediction JSON):
     - `mean`, `std`, `p_ge`, `q10/q50/q90`, `q_grid` (19 quantiles, tau 0.05..0.95): the
       distribution of the stat GIVEN the player plays (minutes > 0).
     - `p_play` (Platt-recalibrated minutes-model P(play); `p_play_raw` in the frame) and the
       unconditional mixture `mean_uncond = p_play * mean`, `p_ge_uncond = p_play * p_ge`
       (valid for thresholds >= 1; a DNP is a 0).
     - `routed_to` (`context_residual` | `recency_fallback` | `recency`), `bucket`
       (Syntetos-Boylan class), `mean_recency`, `proj_minutes`.
     How a prop market settles a DNP is not documented in the Kalshi notes (`result=scalar`
     is unexplained); until confirmed, the parlay tool must choose between the conditional
     and unconditional columns explicitly and treat DNP-sensitive legs as uncertain.
5. **Write** (`store.py`): append-only `forward_predictions`. Any `made_at >= tipoff`
   raises `LeakageError` and the whole batch is refused. Reruns add rows; settlement scores
   the latest prediction per key, all of which are pre-tip.
6. **Settle**: for completed games, appends `forward_scores`: log loss and Brier (win),
   props scored by empirical CRPS from the stored `q_grid` (2 x mean pinball over the
   grid; closed-form normal only for old rows without a grid). A player with no minutes is logged `dnp` and excluded
   from metrics (props are conditional on playing; the count is reported).
7. **Report** (win prob is shown per model, then a PAIRED injury-minus-MOV-Elo log loss/Brier
   delta on games scored by both, with n, dates, and the count of fallback-only games): log loss / Brier (and log loss minus the naive p=0.5 reference) and prop
   CRPS / bias, each with a percentile bootstrap that resamples **game dates** (2000 draws,
   seed 0), plus n and number of dates. Warns when n < 100 settled games.
   **Rollover trigger:** when >=20 completed `season=2026` games are in `games`, the report
   states the HOLDOUT_ACCESS_LOG rule-3 action (re-designate the holdout as 2026, fold 2025
   into tuning, never tune on 2026 before first confirmatory use). The pipeline never edits
   the log; do that by hand.

## Tables (created by `ensure_tables`, `CREATE TABLE IF NOT EXISTS`)

The same DDL is in `nba/db/schema.sql` (idempotent, safe to apply twice).

- `forward_predictions(run_id, made_at, game_id, tipoff, model_name, version, target,
  player_id, prediction JSON)`; `player_id = -1` for game-level targets (spec columns plus
  `player_id`, needed for props). Timestamps are naive UTC.
- `forward_scores(scored_at, game_id, game_date, season, model_name, version, target,
  player_id, made_at, y, pred, log_loss, brier, crps, status)`.

## Preseason and all-star games are skipped (2026-10-11)

`nba.daily.schedule.slate_for_date` is the one place a daily slate is built (daily run, T-30, lineups
tick, replay), and it drops game ids starting `001` (preseason) and `003` (all-star), so no forward
prediction, T-30 decision, injury probe or roster call is made for them. The parlay shadow
(`nba.parlay.slate.load_slate`) and the LLM shadow (`nba.shadow_llm.run.due_games`) apply the same
prefix test to stored rows. The jobs still run and heartbeat: on a date with only such games the run
prints `no regular-season games: N preseason skipped` (T-30: the same text in `notes`) and exits 0.

Existing rows on 001 games (136 `forward_predictions` rows and 10 `forward_t30_decisions` rows from
2026-10-10) are **unscorable rehearsal rows**: `games` never holds preseason games, so they can never
settle. They are kept, not deleted, and excluded from every scoring claim. Replay oracle: 2025 has no
001/003 rows in `games`, so the golden hash is unaffected.

## Season encoding

`games.season` is the start year: 2025 = 2025-26, so 2026-27 = **2026**
(`nba.ingest.games.season_to_int`). Pullers take any `YYYY-YY` string; they were not
modified. `nba.daily.season` maps a date to the string (Aug-Dec -> that year, else year-1).

## Scheduling on this Mac

`nba/daily/com.nba-prediction.daily.plist.template` is a user-level LaunchAgent template
(NOT installed): two runs a day (11:00, 16:30 local, assumed US-Eastern). A scheduled job
on your own laptop is not always-on infra; launchd runs a missed job on wake. Install and
removal commands are in the template header. Keep the Mac plugged in; the job is light
(one schedule call, one games call, a handful of box scores).

## Changelog

- **2026-10-09, real-tip training gate (maintainer-approved).** Both production models now
  build their TRAINING features by gating official-report snapshots on the scheduled tip-off
  minus 60 min (`tip_source="real"`, `nba.features.game_tipoff`) instead of the 19:00 ET
  proxy, which admitted post-tip snapshots for matinees. `rung0_injury_elo` v2 and
  `props_context_residual` v2 (tags `tip_source=real`) are `production`; v1 is `deprecated`.
  Tips come from `data/schedule/raw_<season>.parquet` (2022-23..2025-26, 100% of games in
  `games`) plus `live_tips_<season>.parquet`, which each real `run`/`predict` call rewrites from
  the live schedule it already fetches (no extra API call) so completed 2026-27 games keep
  their real tip. A game with no real tip falls back to the proxy and logs a WARNING
  ("real tip missing for N game(s)"); expect none. Rehearsals (`--schedule-from-db`) never
  write tips. The context model cache fingerprint now includes `tip_source`. Numbers: T091-T095
  reproduced; replacement entry in `docs/TEST_LEDGER.md`.

- **2026-10-09, pts lower-tail SHADOW (maintainer-approved).** `nba.daily run --log-lower-tail-variant`
  (`log_lower_tail_variant` on `run_daily`) additionally logs pts rows as `props_context_residual_lt`:
  the short-minutes mixture of docs/LOWER_TAIL.md with integer-support quantiles and the primary's mean.
  Comparison only, never primary; default off; enabled for the pretip step in `ops/nba_daily.sh`. It
  costs one extra pts-only fit per day (own cache dir `<model-cache>/<date>_lt`, about 1-3 s on the
  rehearsal copy) and a failure there is reported in the run summary without touching the primary
  rows. Primary, recency, Elo and injury-Elo stored predictions are byte-identical with the flag on or
  off (tests: `tests/daily/test_lower_tail_shadow.py`, `tests/props/test_lower_tail_shadow.py`). Settle
  scores the lt rows like any other model from the stored grid (already on integer support). The live
  comparison rule is frozen in docs/LOWER_TAIL.md and implemented in `nba/daily/lt_live.py`.

- **2026-10-09, live-path rehearsal fixes** (docs/LIVE_REHEARSAL_2026-10-09.md): the injury pull uses the
  team-aware name resolver (the pbp index raised on ambiguous names), report rows of not-yet-played games
  get their `game_id` from the schedule, forward report rows are read without joining `games`, and
  official-roster names let debutants resolve; lineups polls treat HTTP 302 (unpublished day) as benign and
  `run-t30` records games that have a tip but no snapshot.

## Caveats (honest)

- The nba_api `ScheduleLeagueV2` column names (`gameId`, `gameDateTimeUTC`,
  `homeTeam_teamId`, `awayTeam_teamId`) and the injury-report slot probing are written from
  the documented shapes and tested on fixtures only; do one supervised live run on a
  preseason/early day before trusting the schedule.
- Games with unknown/late tip-off are never predicted (cannot prove made_at < tipoff).
- A game ingested late in `games` (box score missing) stays unsettled until its box score
  arrives.
- Win-prob and props models are the existing baselines; early-season samples are tiny.
  Expect CIs spanning zero for weeks; report n with every number.

## Forward bias audit (2024-25 replay, 28 slates, as-of scratch DB)

Found: `player_game_stats` DNP rows have `minutes` NULL but stats of 0, so the old career
average (and the backtest baselines) silently averaged DNP games as zeros. Scored forward on
players who played that is a bench-concentrated negative bias. Fix: the mean/std use played
games only, recency-weighted. See the status report for the before/after table. Remaining
open items: the sim path still under-predicts bench rebounds and loses to the average on MAE
in the forward shape (projected, not actual, rosters); the minutes-model P(play) needed
recalibration; rookies and offseason moves are invisible until a first box score.

## Hooks (status)

1. `nba.registry`: `rung0_injury_elo` v1 is production; `resolve_production` returns its
   version. Both it and `rung0_mov_elo` fall back to config (status `unregistered`) if absent.
2. Done: `nba.props.forward.predict_slate`. 3. Done: `nba.ingest.games.refresh_season_games`.
4. Done: forward tables are in `nba/db/schema.sql`.

## Scheduled runs (launchd, optional)

`ops/nba_daily.sh` wraps the commands above. `ops/install_launchd.sh` installs two user launchd jobs
(Mac local time, US/Central). The maintainer runs the installer by hand; uninstall with
`ops/install_launchd.sh --uninstall`. INSTALLED on the maintainer's Mac 2026-10-09 (bridge until a VM passes `ops/check_vm_reachability.sh`).

| Job | When | Steps |
|---|---|---|
| `local.nba.daily-pretip` | at :20 and :50 of every hour, 09:20-21:50 (moved from hourly :30 on 2026-10-09, review M1) | `nba.daily run --date <today ET>` (exit 4 = some games already tipped, informational; exit 5 = degraded, see below; exit 2 is an argparse usage error and alerts), then `nba.ingest.referees collect`, then `nba.odds capture` (read-only sharp-line benchmark from theoddsapi.com into `odds_snapshots`; key from `.env` via `uv run --env-file`; exit 4 = feed unreachable or budget refused, informational; exit 5 alerts; added 2026-10-10, docs/prereg/ODDS_BENCHMARK.md), then `nba.markets capture` and the read-only `nba.parlay evaluate` shadow log |
| `local.nba.daily-morning` | 08:00 | `ingest` (games + box scores, before settle), `settle` (exit 0; 4 = a game past the 4 h guard is refused, tipped < 24 h ago, informational; 5 = a refusal 24 h or older, alerts), `report`, parlay `--settle`, post-game ingest of new games (tracking, hustle, officials, matchups; skipped while a backfill or the ingest queue runs), `datamanifest snapshot/diff/check`, `nba.duckdb` copy to `data/backups/` (last 7 kept), disk summary |

**Optional fact-store rebuild (drafted 2026-10-11; approved ON by Devin 2026-10-11, switch NOT yet in the job environment).** `ops/nba_daily.sh morning` has a step `facts` right after
`report`: `python -m nba.facts build --seasons 2022,2023,2024,2026 --schedule-seasons 2026 --out data/facts/facts.duckdb`.
It runs only when the job environment has `NBA_FACTS_NIGHTLY=1`; unset (the default) the branch is not entered and the script's
behaviour is unchanged. `--seasons` is an explicit list of seasons whose full facts load (replaces `--max-season`); 2025 stays
absent unless listed. The step reads `nba.duckdb` read-only and writes only `data/facts/facts.duckdb` (dropped and recreated;
seconds). Exit 0 and 4 are quiet; any other code (2 = source locked) alerts but never stops the later steps. Nothing in the
forecast path reads the fact store. Enabling it is the maintainer's: add `NBA_FACTS_NIGHTLY` to the launchd job's environment
by hand (`ops/install_launchd.sh`). Approved 2026-10-11 ("fix everything"); the steward does not touch schedulers, so the step
is ON only once `launchctl print gui/$(id -u)/local.nba.daily-morning` shows `NBA_FACTS_NIGHTLY => 1`. Manual run 2026-10-11 13:25 CT
exited 0 (10 predicates, 103,926 played_minutes, 87,612 listed_status, 5,171 home_team/away_team, 6,251 entities).

Guards: one job at a time (`data/ops/lock`; the owner pid is stored in it, a dead owner is cleared at once,
otherwise a lock is stale after 3 h). A run that finds the lock held exits 0 WITHOUT touching its
heartbeat and writes `data/ops/heartbeat/<job>.skip.json` (`status: skipped`, `consecutive_skips`); the
watchdog alerts at 3 consecutive pretip skips (1 for morning, 6 for lineups). `nba.duckdb` is guarded by
its own file lock: every `nba.daily` command waits with bounded backoff (`NBA_DB_LOCK_WAIT_S`, default 180 s,
240 s for the lineups tick) for a concurrent writer and exits 75 if it is still busy (lineups: deferred to
the next tick; pretip: alert). There is no `lsof` check-then-act any more except before the morning backup
(skipped with an alert if the file is busy or a `.wal` is left). Logs: `data/ops/<mode>_<YYYYMMDD>.log`. Failures append to
`data/ops/ALERTS.md` and post a macOS notification. Shots and coaches are per team-season and cached
once, so they are refreshed by hand. The Mac must be awake: a job missed during sleep runs once on wake.

`ops/check_vm_reachability.sh` checks that a candidate host (e.g. a cloud VM) can reach stats.nba.com
through nba_api, the injury-report host, Kalshi, GitHub and Drive before anything is moved there.

The VM move (bootstrap, cron equivalents, Mac-to-VM sync, shadow period, cutover) is in docs/VM_MIGRATION.md.

## Opening-week roster source

`nba.daily run --roster-source official` replaces the offseason-stale last-10-games roster with the
pre-tip `CommonTeamRoster` (cached per date) plus recent players, with the rookie minutes prior for
players with no history. Default is `recent`. The daily job and the lineups job use `official` through
2026-11-30 inclusive (US/Eastern; extended from 2026-11-03 by Devin, 2026-10-12), then `recent` automatically;
the switch lives only in `ops/roster_source.sh`, sourced by `ops/nba_daily.sh` and `ops/nba_lineups.sh` (the parlay steps use `$ROSTER`). Measurements and the recommendation are in
docs/OPENING_WEEK_ROSTERS.md.

## Timing: why :20 and :50 (review 2026-10-09, M1)

The T-60 comparator takes, per game, the latest primary row with `made_at <= tip - 60 min`. With runs at
:30 a `:00`/`:30` tip had no eligible run closer than T-90..T-120. Runs at :20 and :50 start at T-70 for
every `:00` and `:30` tip (about 5.5 min runtime). The watchdog (`nba.ops.watchdog.JOBS`) and
`ops/vm/install_cron.sh` use the same minutes. The injury-report rule (report stamped `<= tip - 60`) is unchanged.
Reinstall after any change: `sh ops/install_launchd.sh`.

## Degraded runs (review 2026-10-09, M2/M3, m5)

* `nba.daily run` exits 5 when the incremental ingest or the schedule fetch failed: ingest is skipped (features
  are strictly pre-slate) and the schedule falls back to the last good copy in `data/schedule_cache/`
  (written on every successful fetch; with no cached copy the error still aborts). The reasons are in
  `RunSummary.errors` and the run printout (`DEGRADED: ...`).
* Shadow arms (`_int`, `_lt`, recency comparison, `p_ge_full`) are isolated: a failure drops only that arm and is
  listed in `RunSummary.shadow_errors`; primary rows are never replaced by `recency_fallback` because of them.
* `made_at` is stamped at run start while rosters, report probes and box-score pulls happen later in the run.
  This is conservative for the injury report (bounded by `made_at`), but a roster fetched minutes after
  `made_at` is labelled with the earlier time. Accepted and documented (m5).

- **2026-10-09, M4 fix: serving uses the training report rule (maintainer-approved correctness alignment,
  no model selection).** `slate_report_outs` (primary props `report_out`, also T-30) used to take the
  league-wide newest snapshot `<= min(now, tip-60)`, so a game absent from that snapshot got `has_report=1`
  with nobody out. It now calls `nba.features.injury_report.serve_pretip_flagged`, a thin wrapper over the
  training function `latest_pretip_flagged`: per game, the latest snapshot among THAT game's own report rows
  with `as_of <= min(now, real tip - 60 min)` (and, serving-only guard, `<= 36 h` older than that cutoff);
  a game with no qualifying snapshot is absent (`has_report=0`). The win model (`predict_games`) already
  filtered per game_id on the real tip, so it is unchanged (its has_report now matches the props'). Tests:
  `tests/daily/test_report_serving_rule.py` (early game never sees a snapshot after its tip-60; absent game is
  not "nobody out"; late game sees the newest qualifying snapshot; identical to the old function when every
  snapshot covers every game). Backtest check on `data/rehearsal/live_copy.duckdb` (read-only), real tips,
  2024-10-30 / 11-10 / 11-20 / 12-25 (35 games), 654 (game, pretip run) pairs at :20/:50 ET:
  has_report differs old vs new in 38 pairs (13 of 35 games at some run), always old=1 / new=0; OUT sets
  restricted to the game's teams never differ where both have a report (0 of 616); only the league-wide raw
  set shrinks. Agreement with the training rule evaluated at the same instant: new 654/654, old 616/654; at
  the last run before tip: new 35/35, old 35/35 (the effect is confined to earlier runs). Stored primary
  props at the run with the most flips (10:50 ET): mean changed in 401/1088, 488/1112, 669/796 rows
  (10-30, 11-10, 11-20), mean |delta| of changed rows 0.17-0.25, max 3.6 (q90 max 6.2); 12-25 had no
  flips and 0 changes. Injury-Elo probabilities are unchanged by construction (code path untouched). Script:
  scratchpad `m4_counts.py` / `m4_props.py` (not committed).

## Pretip vs T-30 overlap (hardening review 2026-10-09, M-B)

Pretip (`:20`/`:50`) and the T-30 tick (`nba.daily run-t30`, inside the 5-minute lineups job) both
write `nba.duckdb`, and for a slate with both `:00` and `:30` tips they overlap by construction.
DuckDB is single-writer, so whichever opens second waits (`connect_with_retry`, bounded; the
wait is now logged as `nba.duckdb lock: waited N s ...`) and exits 75 if the holder outlasts it.

Measured 2026-10-09 (this Mac, scratch copy of the DB, live HTTP reads, nothing written to the
real files):

| Run | Wall (holds nba.duckdb) | Notes |
|---|---|---|
| `nba.daily run` as pretip does it (official rosters, int + lt arms), cold caches | **339 s** | ~240 s injury-report probing (96 probes at 2.5 s, no report in window), ~75 s official rosters, rest models |
| same, warm (roster, probe-miss and model caches present) | **16.5 s** | the typical `:50` run after a `:20` run |
| `run-t30`, fixture DB | 2.4-4.8 s | tests/lineups/test_t30.py (tiny fixture; no network) |
| `run-t30`, real DB (estimate, NOT measured) | ~1-2 min | cold ctxres fit ~10 s + up to 30 roster calls at 2.5 s + start-up |

Design (so neither job starves):

- `ops/nba_lineups.sh` now takes the poll lock only for the poll and releases it before `run-t30`;
  `run-t30` runs under its own `lineups_t30_lock`. Lineup polling therefore continues every
  5 minutes while a T-30 run waits or runs (a second tick that finds the T-30 lock held still polls
  and does not start a second run). The poll heartbeat is final at that point, so a long wait never
  overwrites newer ticks' heartbeats.
- Wait budgets: `run-t30` waits up to **600 s** (> the 339 s cold pretip + margin; was 240 s);
  pretip steps wait up to **300 s** (> the estimated T-30 hold of 1-2 min x2; was 180 s).
  Override with `NBA_DB_LOCK_WAIT_S`.
- Visibility of an exit-75: pretip rc 75 goes through `step`, which sets the heartbeat rc (the
  watchdog's `daily-pretip:rc` alert) and writes `ALERTS.md`. A `run-t30` rc 75 now writes a
  `lineups_t30.skip.json` skip record (consecutive count, cleared by the next successful T-30
  run); the watchdog alerts at 2 in a row (`SKIP_ONLY` in `nba/ops/watchdog.py`).
- A lost `:50` pretip still leaves the `:20` run as the T-60 comparator (up to 100 min stale),
  recorded as such; with the budgets above that now needs a holder longer than 5-10 minutes.
- The injury-probe loop stops at the first report found, so the cold probing cost only recurs when
  the newest slots have no report yet; expired (>2 h) cached misses are re-probed (M-A).
- Read-only openers (`daily checkpoint`, `parlay evaluate`/`--settle`) use the same bounded
  lock retry; `markets capture` waits up to ~10 min (was 30 s).

Accepted residual risk (m-5/M5): a background queue load that opened `nba.duckdb` before its
game-window gate can still hold the file past any of these waits. Operational rule: stop the
queue before 2026-10-20.

## Injury-report probe cache (hardening review 2026-10-09, M-A)

`probe_report_status` is three-state: `present`, `absent` (HTTP 403/404, or a 200/206 that is not
a PDF) and `unknown` (timeout, connection error, 5xx, 429). Only a definite `absent` for BOTH
filename variants of a slot that is at least 30 minutes old is cached
(`data/injury_probe_cache/<ET date>.json`, slot -> time recorded, written atomically). Unknown
never caches and also drops an older cached miss for that slot. A cached miss is re-probed once it
is 2 hours old (`MISS_TTL_HOURS`) in case the report posted late. Legacy list-format cache files
are ignored (re-probed once).

## Other hardening (review minors)

- Schedule cache: a cached schedule older than 36 h (`MAX_SCHEDULE_CACHE_AGE`, file mtime = last
  good fetch) is refused; the original fetch error is raised (rc nonzero, alert).
- Shadow-arm failures (`RunSummary.shadow_errors`) append one `[daily-run]` line to
  `data/ops/ALERTS.md`; rc stays 0 because primary rows are unaffected.

## Forward-prediction storage: write-on-change (replay finding, 2026-10-09)

The 2025-26 systems replay (`data/rehearsal/replay2025/summary.json`: 210 dates, 877 runs) stored
1,403,829 `forward_predictions` rows, 1.49 GB of JSON (work DB 1.05 GB), because each run re-wrote the
whole slate. Live pretip runs every 30 min (about 26 runs a day), so the same table would grow
roughly 6x faster (about 9 GB of JSON a season).

**Rule** (`nba.daily.store.append_predictions_ex(..., dedup=True)`, used by `run_daily` and on by
default; `run_daily(dedup=False)` restores the old behaviour):

* The `LeakageError` tip-off check runs first, on the whole batch, exactly as before.
* A row's content hash is `sha256(tipoff, sorted-key prediction JSON)`. `made_at`/`run_id` are not
  part of it. Two slate-level diagnostics that change every run without changing the forecast
  (`n_games_with_report`, `n_slate_games`; read by no scorer) are excluded from the hash but still
  stored on every row that is written.
* Rows are grouped by `(game_id, player_id, target)` (all arms/models of one forecast). A group is
  skipped only if EVERY member equals the latest stored row for its key
  `(model_name, version, target, game_id, player_id)` and that row's `made_at <= this made_at`.
  Otherwise the whole group is written. Writing groups together keeps the arms of a forecast on one
  `run_id`, so the checkpoint's `same_run` pairing (`_int`, `_lt` arms) is unchanged.
* Nothing is updated or deleted; append-only still holds.

**Why selection is unchanged.** `settle_eligible_pending` / `checkpoint` pick, per key, the row with
the greatest `made_at <= tipoff - lead` (T-30 arm: also the snapshot cutoff). A skipped row is by
construction content-identical (including `tipoff`) to the latest stored row before it, so the
latest stored row at any cutoff has exactly the content the full log would have had there. Only
`run_id`/`made_at` of the eligible row (earlier run, same content) and the two diagnostic counters
can differ. `tests/daily/test_store_dedup.py` runs a seven-run synthetic day with and without dedup
and asserts identical `forward_scores_elig` (all columns except run_id/made_at/scored_at), identical
`forward_scores`, and identical `paired_deltas(same_run=True)` with 0 run mismatches.

**Run summary.** Each run reports `rows` (written) and `deduped` (skipped) in the CLI line and
`RunSummary.n_rows_written` / `n_rows_deduped` (replay: `rows_deduped` in the per-run record). The
T-30 arm is not deduped (one decision per game, snapshot-dependent content).

**Estimate** (measured on the replay's own table, applying the same change rule): 410,368 of
1,403,829 rows (29%) and 436 MB of 1,493 MB JSON (29%) are new or changed; group-level writing costs
only 2% more than per-key. That is about 1.2 writes per key per day against 4.2 runs. Live per-season
estimate: about 1.2 to 3 writes per key per day, i.e. roughly 0.4 to 1.1 GB of JSON (about 0.3 to 0.8
GB in DuckDB) instead of about 9 GB; the high end assumes injury/lineup information changes about
twice as often live as in the replay. A run that finds nothing new writes 0 rows. Measure the real
figure from `deduped` in the daily logs after a week. `p_ge_full` is already a compact integer-count
form (`{"n","c"}`, cut at the 99.9% tail); recompressing it would change stored output, so it is left.

**Consumer note.** `nba.markets capture` records market state per stored row, so it now captures at
change times rather than at every run; the as-of rule is unaffected.

## Kalshi DB safety (replay finding, 2026-10-09)

During the replay a process held a write lock on `data/kalshi/kalshi.duckdb`, so the 15-minute
snapshot failed ("Could not set lock", 13:22 CDT). Cause: `python -m nba.kalshi` opens
`--db` (default: the live file) WRITABLE for every command, including `backfill-historical`
(`data/rehearsal/kalshi_candles_backfill.log` is one at 13:22), `cutoff`, `markets`, `candles` and
even `alias-candidates` (which only writes JSON files). `nba.daily.replay_season`, `nba.parlay`
(evaluate, `--settle`, analyze, assistant) and `nba.markets` were already READ_ONLY (or used the
copied working file).

Fixes: `nba.kalshi` now refuses `backfill-historical`, `cutoff`, `markets`, `candles` when `--db`
resolves to the live file (pass a separate `--db`, then merge); `alias-candidates` opens no DB; only
`snapshot` may write the live file; `make_hidden_kalshi` refuses the live path as its working copy.
`tests/kalshi/test_db_safety.py` checks the guard, that consumer openers are read-only (writable
open of a 0444 file fails, theirs succeeds), and a static scan that every `ATTACH` in `nba/` is
`READ_ONLY`.

## LLM shadow forecaster (flag-gated, off by default)

A step in the 5-minute lineups tick (`ops/nba_lineups.sh`), after the odds capture and before the T-30 block, launches `ops/llm_shadow_bg.sh` DETACHED (own process group, nohup) only when `NBA_LLM_SHADOW=1`; it runs `python -m nba.shadow_llm run` in the background so a 35-60 s `claude -p` call per game never delays the T-30 / T-15 blocks or the next tick. One run at a time: lock `data/ops/llm_shadow_bg.lock` (pid and owner tag inside; a stale lock is cleared only when this mechanism created it and its pid is dead); log `data/ops/llm_shadow_YYYYMMDD.log`; heartbeat `data/ops/heartbeat/llm_shadow.json` (START/END; not yet in the watchdog's job list); rc not in {0, 4, 75} -> one `[lineups] llm_shadow rc=` line in `ALERTS.md`. It reads the pretip run's T-60 rows and writes `llm_shadow` rows pre-tip. The lineups agent needs `HOME`, `USER`, `LOGNAME`, `NBA_CLAUDE_BIN` in its plist `EnvironmentVariables` (`ops/install_launchd.sh`). See `docs/LLM_SHADOW.md`.

## launchd calendar timezone (found 2026-10-11)

launchd evaluates `StartCalendarInterval` in the zone it captured at its own start, not the current
one. This Mac's launchd (up 99 days) fires on **Eastern** time while the wall clock is Central:
plist `Hour 9..21` ran at 08:20..20:50 CDT, the morning job (`Hour 8`) at 07:00 CDT. So the last
pretip start of a day is 20:50 CDT (21:50 ET) and the watchdog's Central slots (21:20, 21:50 CDT)
raised two false "daily-pretip stale" alerts a night plus one "daily-morning stale" (expected 08:00
CDT). Nothing was missed. The watchdog now judges slots in `schedule_tz()` (env `NBA_SCHEDULE_TZ`,
else the one-line untracked file `data/ops/schedule_tz`, else Central; the VM's cron really is
Central). The Mac has `data/ops/schedule_tz = America/New_York`. After a reboot or relogin launchd
re-reads the zone and the plists then mean Central (pretip until 21:50 CDT, morning 08:00 CDT):
delete that file then. Whether to keep Eastern or reinstall for Central is the maintainer's call.

## P(play) shadow: recent-role model R (flag-gated; ON in the pretip job since 2026-10-12)

`python -m nba.daily run ... --log-pplay-shadow` (run_daily `with_pplay_shadow=True`; default off, so
replays and the oracle are unaffected). After every primary row is written and settled, the run logs the
holdout-confirmed recent-role P(play) model (docs/prereg/RECENT_ROLE_HOLDOUT.md, frozen 302bc693, ledger
T458) next to production's `p_play`. Own tables; `forward_predictions`, `forward_scores`, the props
outputs and `nba.daily.canonical_dump` are untouched (a real 2025-01-15 slate run on a DB copy, flag on vs
off: canonical hash identical, 2246 predictions, 2110 scores).

* Coverage: every rostered not-OUT player, not only those production logs. Production's own
  `predict_slate_context` is called a second time with `min_p_play=0`, `min_proj_minutes=-1` and no roster
  cap (same report rule, same cached models), so dropped players carry production's conditional
  `q_grid` too. `in_prod_log` / `prod_drop_reason` (`p_play_raw<min_p_play`, `proj_minutes<min`,
  `roster_cap`) say who production drops.
* `forward_pplay_shadow` (append-only, write-on-change, one row per run x game x player): `run_id, made_at
  (known-at), game_id, tipoff, player_id, team_id, model_version (pplay_r_v1), p_play_prod, p_play_r (NULL
  when R has no row), r_reason (out_on_report | n_played_prior<5 | no_model | no_features), in_prod_log,
  prod_drop_reason, proj_minutes, props_cond, train_through, n_train, features_through, feature_set_sha
  (ordered feature names + learner params + lightgbm version), features_sha (this row's 44 feature values),
  t60_report_as_of, div_rival, hist_rival`. The last two are the F10A option-1 rivalry flags (frozen pair
  list `nba/daily/pplay_shadow/rivalry_pairs.csv`, 80 rows = docs/research/rivalries_2026-10-09.md Tier 1 + 2;
  as-of by calendar year; logged only, no model reads them; revisit at 300+ forward division non-play events).
* `props_cond` is JSON `{stat: {"model": "context_residual" | "recency_fallback", "mean": float,
  "q_grid": [19 floats]}}` for pts / reb / ast / fg3m, conditional on playing, quantiles at taus 0.05, 0.10
  .. 0.95 (`nba.props.forward.QUANTILE_TAUS`), the continuous (non-integer-support) grid of the primary
  rows. A player with fewer than 5 prior played games is a `recency_fallback` row and gets no `p_play_r`.
* Model: LightGBM binary, `configs/context_residual.yaml` params, seed 0, 44 features (C + production's 13
  recent-minutes/role columns + 5 recent-role columns), refit each run on every eligible played / DNP row
  dated before the slate date; features vendored in `nba/daily/pplay_shadow/` (no research import), built
  by one code path for training and tonight's rows (tonight enters as NULL-minutes placeholders). Parity
  with the research frame on seasons <= 2024: all 44 features identical on 80,570 history rows and 235
  slate rows (as of 2025-02-01), except `is_national_tv`, which the schedule feed does not carry for an
  unplayed game (live value False; effect on p_play_r: mean abs 0.0008, max 0.035 on that slate).
* Settle: `python -m nba.daily settle` (and the end of every `run`) writes `forward_pplay_scores`: the
  latest shadow row with `made_at <= tipoff - 60 min`, `y = minutes > 0`, log loss of both columns;
  players absent from a final box are `not_in_box`, unscored. `build_report` prints a P(play) shadow section
  (paired R minus production, date-clustered CI) once rows exist.
* A shadow failure is recorded in `shadow_errors` (-> ALERTS.md) and cannot change a primary row.
* Enabled 2026-10-12: `ops/nba_daily.sh pretip` passes `--log-pplay-shadow` (oracle 89889f6d identical with
  the shadow code, e105caf). Cost inside the pretip lock, measured 2026-10-12 on a DB copy, real 2025-01-15
  slate (11 games, 3,304 prediction rows, `recent` rosters, int + lt arms): warm run 20 s without the flag,
  30 s with it (+10 s: the second `predict_slate_context` call, the LightGBM refit on 77,422 rows at 5.3 s,
  and the shadow settle); cold first run 39 s without it. Against the 339 s cold / 16.5 s warm pretip
  measurements above this adds seconds, far inside the 30-minute :20/:50 cadence and the 300 s / 600 s
  lock-wait budgets (one n = 1 timing per cell; real opening-night slates are 5-15 games).
* Comparators (2026-10-12, adversary review of RECENT_ROLE_HOLDOUT): `p_play_c` (C, the WORKLOAD feature set)
  and `p_play_cp` (C' = C + production's 13 recent-minutes/role columns) are fitted with the same daily refit and
  learner as R on the same rows (feature lists `C_FEATURES` / `CP_FEATURES`, pinned to
  `research.eval.recent_role_eval.arm_columns()` by a test; NULL exactly where `p_play_r` is NULL).
  `forward_pplay_scores` gains `p_play_c, p_play_cp, ll_c, ll_cp`; the report prints R minus C and R minus C'
  (date-clustered). Measured on the same 2025-01-15 slate (n = 1 each): warm pretip run 20 s without the shadow,
  30 s with R only, 42 s with R + C + C'; canonical hash of `forward_predictions`/`forward_scores` identical flag on
  vs off (6b3edfe9...).
* Parity check (`nba.daily.pplay_shadow.settle.parity_check`, printed by the report): over the trailing 7 days of
  settled games (>= 200 player-games), if live R minus production log loss is above -0.05 the report prints a
  `PARITY WARNING` line (likely a feature parity fault, e.g. a stale last-night box at T-60). On the one historical
  slate above the replay gives -0.048, i.e. the check would fire on that day alone: the threshold is the
  adversary's, a week pools many days, and nothing acts on it automatically.
* Not done: promotion (Devin decides after the live log).

## FULL shadow: RECENT_ROLE_FULL conditional grids (flag-gated; ON in the pretip job since 2026-10-12)

`python -m nba.daily run ... --log-full-shadow` (run_daily `with_full_shadow=True`; default off, so replays and
the oracle are unaffected). Devin approved the shadow (DECISIONS b348af7, "shadow the combined model yes"); the
rule is docs/prereg/RECENT_ROLE_FULL.md (frozen 73f3f4b0) and the shadow builds exactly its FULL arm: production
`context_residual` stat heads refit WITH `wl_min_last, wl_mean_min_l3, wl_min_3d, wl_min_7d, wl_mean_min_l5` and
the lower-tail short-minutes classifier `pi` refit on `COMMON_FEATURES + those five`, read out through the
production mixture (`nba.props.lower_tail`, stored `w_q` construction) for ALL four stats, then `ceil(q - 0.5)`.
Code: `nba/daily/full_shadow/` (`model.py` the refit and the same-date cache, `run.py`, `store.py`, `settle.py`);
the five columns come from `nba.daily.pplay_shadow.features.load_min_features` (no research import). After every
primary row is written and settled, in its own try/except (a failure lands in `shadow_errors`, never in a
primary row). The wide-roster production frame (`predict_slate_context` with its thresholds relaxed) is computed
once per run and shared with the P(play) shadow (`pipeline._wide_roster_frame`).

* `forward_full_shadow` (append-only, write-on-change, one row per run x game x player x stat; refused when
  `made_at >= tipoff`): `run_id, made_at, game_id, tipoff, player_id, team_id, stat, model_version (full_v1),
  q_grid` (FULL conditional-on-playing integer quantiles at taus 0.05..0.95, 19), `mean_full, pi_full` (FULL
  P(short minutes | plays)), `q_grid_prod, mean_prod, p_play_prod` (production's continuous conditional grid,
  mean and hurdle P(play) of the same wide frame and run), `in_prod_log, train_through, n_train,
  features_through, feature_set_sha` (ordered head and pi feature names, learner params, lightgbm),
  `features_sha` (this row's head + pi feature values). Coverage: every player the context model forecasts in the
  wide frame (rostered, not OUT, >= 5 prior played games); `recency_fallback` players get no row.
* Refit: daily on played rows dated before the slate date, `ContextResidualConfig()` (production), seed 0, one
  `pi` classifier shared by the four stats. Same-date cache `<model cache>/<date>_full/` (models.pkl + meta.json,
  fingerprint = row count, newest training date, config, recipe sha), exactly production's pattern: the first
  run of a date refits, the later :20/:50 runs reuse it. About 4 MB a day.
* Equivalence proofs: with the five columns removed from both feature lists the model is production's
  `lower_tail='mixture'` model bit for bit (unit test on fixtures; on the real 2025-01-15 slate 271 pts rows
  equal the live `props_context_residual_lt` grids, 0 differ; the wide frame's conditional grids equal the logged
  production grids for 1,100 of 1,100 rows).
* Settle (`python -m nba.daily settle` and the end of every `run`; idempotent) writes `forward_full_scores` with
  the same eligibility as the other shadows (latest row with `made_at <= tipoff - 60 min`): integer-support
  conditional CRPS `crps_prod` / `crps_full` on played rows (the FORWARD_PREREG pairing, 2 x mean pinball of the
  `ceil(q - 0.5)` grids), and the JOINT unconditional forecast for every player in a final box (listed DNP scores
  as 0): `crps_joint_prod` = production `p_play` x production grid, `crps_joint_full` = `p_play_r` (latest eligible
  `forward_pplay_shadow` row) x FULL grid, `crps_joint_rprod` = `p_play_r` x production grid (isolates R from
  FULL). The joint forecast is `(1 - p) at 0 + p/19 on each of the 19 stored quantiles` and the score is its exact
  CRPS, so joint levels are not comparable to conditional levels, only within a column pair. NULL `p_play_r`
  (no R row) leaves the two R columns NULL.
* `build_report` prints a "FULL shadow" section once rows exist (paired FULL minus production per stat, conditional
  and joint, date-clustered 95% CI). Descriptive: the binding claims are Stage 1/2 of the prereg and the FORWARD_PREREG
  looks; nothing here promotes anything.
* Cost, measured 2026-10-12 on a DB copy, real 2025-01-15 slate (11 games, 300 context players, 1,200 shadow
  rows, `recent` rosters, int + lt arms, P(play) shadow on): pretip run 29 s cold / 20 s warm without the FULL
  shadow, 47 s cold / 17-21 s warm with it. The added cost is the first run of a date: 15.4 s (feature build
  2.3 s, refit of four heads + `pi` 12.4 s); every later run of that date reuses the cache and adds about 2 s.
  Far inside the 30-minute :20/:50 cadence and the lock-wait budgets. Canonical hash of `forward_predictions` /
  `forward_scores` identical flag on vs off (d3d2d86a..., 3,440 predictions). n = 1 slate each.
* Enabled 2026-10-12: `ops/nba_daily.sh pretip` passes `--log-full-shadow`. Replay oracle (20 dates of season 2025, `data/rehearsal/oracle_fullshadow.duckdb`, a new file): sha256 89889f6d IDENTICAL to `oracle_pplay.duckdb` (46,658 predictions, 44,562 scores); the replay does not pass the shadow flags, so this proves the shadow code is inert when off.
* Not done: promotion (Devin decides after the live log); the FULL shadow does not enter the T-30 / T-15 arms.

## Run metadata (2026-10-12, N15)

`run_daily` records `git_head` and a tracked-file `git_dirty` flag (plus the count of modified tracked files)
in the `RunSummary` (`nba/daily/runmeta.py`) and `nba.daily run` prints `code: git=<12 hex> dirty=<bool>
(tracked files modified: N)` into the job log. launchd executes the working tree, so a dirty run is
visible after the fact. Nothing is written into `forward_predictions` or any scored table (the replay oracle
cannot see it). Untracked files do not count.

## Sibling T-15 arm (docs/prereg/FORWARD_T15_2026_27.md, frozen 5c0dec6d; implemented 2026-10-12)

`python -m nba.daily run-t15 --date D --roster-source ...` is `run-t30`'s body (`nba.daily.t30.run_t30`
parametrised with `lead_minutes`, `model_name`, `version`, `decisions_table`; defaults are the T-30 values and
the T-30 path is byte-identical, `tests/daily/test_t15_arm.py::test_t30_path_identical_with_explicit_default_kwargs`)
with the cutoff at tip minus 15 min: arm `props_context_residual_t15`, version `ctxres-v1-t15`, table
`forward_t15_decisions`, same model cache and features. Reason strings are T-30's, plus `no_snapshot_before_cutoff`
(T-30 keeps `no_snapshot_before_t30`) and `run_after_tipoff` (counted as skipped).
* `python -m nba.lineups due --lead-minutes N` (default 30): exit 0 iff a game is in [tip-N, tip).
* `ops/nba_lineups.sh`: after the T-30 block (which now falls through, no early exit, when its lock is held),
  `due --lead-minutes 15` then `run-t15` under `data/ops/lineups_t15_lock` (stale after 30 min), `NBA_DB_LOCK_WAIT_S`
  capped at 240 s (`NBA_T15_DB_LOCK_WAIT_S` overrides), same ROSTER rule. Sequential after T-30 on a tick, never
  parallel from one tick. rc 75 writes `lineups_t15.skip.json` (the watchdog alerts at 2 consecutive, `SKIP_ONLY`).
  No new launchd job; the existing 5-minute tick lands inside [tip-15, tip).
* `settle`: `forward_scores_elig` eligibility for `_t15` rows is `made_at < tip` and `snapshot_fetched_at <
  tip - 15 min` (`lead_min` = 15).
* `checkpoint`: keys `t15_pts..t15_fg3m` in their own family of 4 with their own BH (`bh_adjusted_t15`); L14
  monitoring, L30 kill-only (rules b, c, d implemented; a leak guard failure is flagged AUDIT for a human), efficacy
  looks L60/L120/END; `t15_share` from `forward_t15_decisions`; descriptive `t15_vs_t30` head-to-head. The 9-key
  T-30 family is untouched. `report`: a descriptive T-15 section; lineup analysis prints the before-T-15 share.
* Not implemented (not in the code-change list): the `live_book_odds` t15 market comparator.

## Production's continuous grid (2026-10-15)

Right after the model stamps, `run_daily` writes the 199 equal-mass quantile values behind every
`props_context_residual` primary row (the `q199` that `p_ge` and `p_ge_full` are cut from) to the NEW table
`forward_prop_grid199(run_id, made_at, game_id, tipoff, player_id, stat, model, q199 DOUBLE[], q199_sha)`
(`nba/daily/grid199.py`). It exists so the app export can compute the CAL-rung map's median and spread
exactly (`docs/contracts/rung_cal_export_v1.md`); `forward_predictions` keeps only the integer survival.

- Own table, read by neither the scorers nor `canonical_dump`: the replay hash is blind to it (test).
- Write-on-change per (game, player, stat) on `q199_sha`; a grid whose game has tipped is refused.
- Own try/except (`grid199.persist` never raises): a failure sets `grid_status = "failed: ..."` and a
  shadow-error line; no primary row and no exit code changes. Printed as `prop grid199: ...`.
- Cost: about 0.4 s per run for a 14-game, 1,212-row slate (measured on the preseason rehearsal copy).
- Recency-routed rows and any run before this table existed have no grid; the export then leaves `rung_cal`
  null with a counted reason.

`line_cal` (2026-10-21, additive): the app export also carries, per prop row, the frozen LINE_CALIBRATION P(over) (T493, the parlay
producer's `p_cal`) at the DraftKings main line, beside the raw P(over), plus `line_cal_null_reason`. It has no raw-P(over) >= 0.5 support
restriction, so unlike `rung_cal` it covers the main line itself. Display only, built read-only from the production row, NULL with a counted
reason when an input is missing; it changes no scoring, forward row or replay-closure code. Contract: `docs/contracts/line_cal_export_v1.md`.

## Model stamps (2026-10-13)

Every `run_daily` that writes prediction rows also writes one row per model name of that run to the NEW
table `forward_model_stamps` (`nba/daily/stamps.py`), right after `append_predictions_ex`. Join to
`forward_predictions` on `run_id` + `model_name`. The table has no `game_id`, is created lazily by the
stamp writer, and is read by neither the scorers nor `canonical_dump`, so the replay oracle cannot see it.

`forward_model_stamps(run_id, model_name, version, made_at, weights_sha256, weights_scope, data_version,
fit_through DATE, n_train, git_sha, git_dirty, stamped_at)`.

- `weights_sha256`: sha256 of a canonical serialization of the fitted state (sorted keys, arrays as dtype +
  shape + bytes, LightGBM boosters as their text dump; never pickle bytes). MOV-Elo: ratings after fit +
  params. Injury-Elo: ridge coefficients, train counts and Elo params (its Elo ratings are refit inside
  `predict_games` and are not exposed, stated in `weights_scope`). Context props (and the `_int` / `_lt`
  arms, which share the models): the per-stat model dict read back from `models.pkl` in the cache dir
  (heads, conformal arrays, lower-tail state, config). Recency and the rolling baseline have no fitted
  weights: the digest covers name + version and the scope says so. If the context model did not fit
  (recency fallback) the digest is the literal `unavailable`.
- `data_version`: `nba.datamanifest.manifest.latest_data_version()` at stamp time (the newest manifest the
  nightly manifest job wrote, not a recompute). `git_sha` / `git_dirty`: as in Run metadata.
- Never blocks the run: collection and insert sit in their own try/except; `RunSummary.stamp_status` is
  printed (`model stamps: ok: N rows`) and a failure also adds a `shadow_errors` line (ALERTS.md).
- Not stamped: the P(play) and FULL shadow models (their own tables and fits).
- Rows deduplicated by write-on-change still get a stamp for their run: the stamp says which fit that run
  used, `forward_predictions` keeps the first run that wrote the row.

## AVG shadow: 0.5 pmf(SEQc) + 0.5 pmf(FULL) (AVG_SHADOW_LIVE_2; flag `--log-avg-shadow`, ON in the pretip job since 2026-10-14; built 2026-10-13)

Rule: `docs/prereg/AVG_SHADOW_LIVE_2.md` (frozen 2026-10-14 11:11 CDT, sha256 9afbfeb6, commit 6e86f1a; supersedes AVG_SHADOW_LIVE 6e57fc11, which changed only G1/P3 after its P3 failed on the 19-atom G1 reading). Code: `nba/daily/avg_shadow/`, reader `nba/eval/avg_shadow_live.py`,
rebuild job `research/colab/jobs/avg_shadow_rebuild/`. **ON in `ops/nba_daily.sh` since 2026-10-14 (Devin's go).** The bundle `b8d2b27b` (month_block 2026-10, fit_through 2026-06-13) was installed to
`data/avg_shadow/bundles/2026-10` at 2026-10-14 16:16 CDT, after Devin deleted `data/avg_shadow/gate_work` (the build review's blocker: the 2025 gate rows); the source copy stays in
`data/avg_shadow/incoming/2026-10`. A slate with no regular-season game records `skipped` without touching `consecutive_bad`; a month with no valid bundle serves `stale_model = 1`
(November needs its own bundle before the 1st; the watchdog alerts past the 3rd). Not yet recorded in `forward_avg_meta`: `meta bundle`, the five proofs and the counting start (UNSET; never
earlier than the FULL shadow's, 2026-10-20). Counting start in `forward_avg_meta` is UNSET: `store.set_counting_start` refuses until P1a, P1b, P2, P3, P4 are recorded
as `pass` (`python -m nba.daily.avg_shadow.meta proof ...`).

* **Step** (`pipeline._avg_shadow`, after the FULL shadow, own try/except): per run, regular-season games (`game_id` prefix `002`) that have not tipped; reads
  production's latest A0 rows (pts: `props_context_residual_lt`, the others `props_context_residual`) and the latest `forward_full_shadow` grid already in the
  store (`made_at <=` now); builds tonight's sequence arrays with the live exporter (`sequences.py`, a copy of T478's `research/features/player_sequences.py`
  pinned to it by proof P2: bit-identical arrays, planted-future test, real tips, reports `as_of <= tip - 60`, era channels 0); runs the 3-seed E2 transformer on
  CPU **in a child process** (`infer.py`; torch and lightgbm hang when loaded in one process on macOS) with a 240 s timeout; learned gate (T442 coefficients
  from the bundle) -> SEQc; AVG = 0.5 pmf(SEQc) + 0.5 pmf(FULL) on the 199-point integer grid (`grids.py`), stored as 19 integer quantiles plus the 199 values.
  Serving rule: only rows with `n_prior >= 3` and a FULL and an A0 grid; every other row (preseason 001, playoffs 004, play-in 005, Cup final, debuts) is served
  FULL: no AVG row is written. The writer refuses `made_at >= tipoff_utc`; rows are write-on-change. Nothing the pretip run waits on.
* **Tables** (`store.py`, all new, none in the canonical dump): `forward_avg_shadow`, `forward_avg_scores` (settle, latest row with `made_at <= tip - 60`),
  `forward_avg_heartbeat` (one row per run: ok / skipped / failed, rc, rows, games seen), `forward_avg_meta` (key/value; the steward's writes).
* **Weights**: `data/avg_shadow/bundles/<YYYY-MM>/` = `seed0-2.pt`, `vocab.json`, `gate.json`, `manifest.json` (sha256s, `model_id`, `fit_through`, `month_block`,
  gate id and fit). `python -m nba.daily.avg_shadow.bundle install <dir>` verifies and stamps `installed_at` (the maintainer's act, before the first game of the
  month; weights are never overwritten). No bundle installed: status `skipped`. A bundle for another month serves with `stale_model = 1` (valid iff
  `month_block` = game month, `installed_at <= made_at`, `fit_through` before the 1st).
* **Heartbeat / watchdog**: `data/ops/heartbeat/avg_shadow.json` at START and END of every run (`consecutive_bad` counts failed runs and runs skipped for want of
  weights; a slate with nothing to serve leaves it alone). `nba.ops.watchdog.SHADOW_JOBS` alerts at 3 consecutive bad runs and at once for a missed monthly install
  past the 3rd. Silent while the flag is off (no file). pmset preflight: `preflight.py` appends a `shadow_errors` (ALERTS) warning when `pmset -g custom` shows
  AC `sleep` other than 0; it never blocks.
* **Reader** (`python -m nba.eval.avg_shadow_live`): intention to treat (a missing, late or failed AVG row is the FULL grid, delta 0, never dropped), missingness
  table, integrity gates, primary + guards G1-G5 (G1 is read on the stored 199-point `avg_pmf199` with the randomised PIT, seed 0; an ITT triple without an AVG row reads `to_integer_support(ext19(FULL q19))`; the 19-atom reading is printed beside it, descriptive; every CRPS contrast and G2-G5 stay on the 19 atoms), BH over the seven, kill-only interim; the one shared bootstrap is
  `nba.eval.full_shadow_live.cluster_boot` (imported). `look_due` fixes the calendar (interim Sun 2026-11-15, final the first Sunday on or after 2027-01-10 with
  pts triples >= 6,900, one second look); `read_look` raises `LookNotDue` before touching a score on any other day. The briefing (`nba.daily report`) prints counts
  and gates only (`display.py`).
* **Replay**: `python -m nba.daily.replay_season ... --avg-shadow-root <bundle root>` turns the flag on for the replay (proof P4 only; the heartbeat file goes
  to `<out-dir>/avg_heartbeat/`, never `data/ops`).
* **Pre-count proofs, state at build (2026-10-13)** (`reports/prereg_avg_shadow_live/`; the scripts are in the rebuild job directory):
  * **P2 PASS** (`p2.json`; current data copy, seasons <= 2024 only): the live exporter vs T478's exporter on all 84,052 rows of 2022-24, every array bit-identical (ctx on
    the 31 pre-era columns, the 3 era columns of T478 are all zero); planted-future identical on 4 dates; the live placeholder slate (games strictly before D + a placeholder
    row per player who played, real tips, reports `as_of <= tip - 60`) identical to T478's rows on 4 dates. First real-data replay also found and fixed an int32/int64 merge
    key in the placeholder rows (regression test added).
  * **P3 PASS under AVG_SHADOW_LIVE_2** (2026-10-14, `reports/prereg_avg_shadow_live_2/p3.json`, 61 s, selection-side files only): pts AVG minus FULL live 19-tau -0.01624 vs offline 199-point -0.01610 (diff 0.00014 <= 0.0005, n 25,767), reb / ast / fg3m same sign; G1 on the 199-point pmf built exactly as the shadow builds it: q10 0.1129, q20 0.2151, 80% coverage 0.790, bias -0.086 (limits 0.12 / 0.22 / [0.75, 0.85] / 0.5: pass); (f) AVG minus AVG_A0 -0.00847 (floor -0.003: pass). The 19-atom G1 (q10 0.1257, q20 0.2272) is printed, descriptive. Count stats on the same form: reb 0.1040 / 0.2066, ast 0.1077 / 0.2057, fg3m 0.1039 / 0.1989 (all pass).
  * *Superseded:* **P3 FAIL (G1 rehearsal, AVG_SHADOW_LIVE)** (`reports/prereg_avg_shadow_live/p3.json`): the 19-quantile arithmetic reproduces the 199-point contrast (pts AVG minus FULL -0.01624 vs -0.01610, diff 0.00014 <= 0.0005,
    n 25,767; reb / ast / fg3m same sign), but G1 on the 19-atom pmf with the randomised PIT misses on 2024 pts (q10 0.126 vs 0.10 +- 0.02, q20 0.228 vs 0.20 +- 0.02):
    FULL and A0 show the same shift on 19 atoms (q10 0.128 / 0.129), the 199-point form passes (0.114 / 0.217). Counting does not start; a repair is a new rule id.
  * **P4 hash PASS** (20 dates of season 2025, source copy of nba.duckdb at 13:59, `oracle_avg_{1,2,3}`): sha256 89889f6d (46,658 predictions, 44,562 scores) IDENTICAL
    with the flag on and no weights (96 runs, all `skipped`), with fixture random-init weights (model_id f325012d..., 96 runs `ok`, 15,021 sequence rows inferred, step
    wall time mean 2.9 s, max 6.3 s per run against the 60 s budget, no shadow error; no FULL shadow runs in the replay, so no AVG row is written there) and with the flag
    off. The replay copies are deleted by the maintainer (command in the hand-back); no AVG row was read.
  * **P1a / P1b**: scripts written (`p1a_check.py`, `p1b_check.py`, mechanics checked on T478's own run-1 file: P1a reproduces -0.016101 exactly), not run: they need the Colab rebuild.
* **Rebuild order (maintainer)**: `export_inputs.py hist` and `cur` -> `make colab-push JOB=avg_shadow_rebuild` (the notebook and the builder refuse any rule text whose sha256 does not start with the pinned 9afbfeb6) -> run the notebook (T4, budget 45 min, ceiling 60) ->
  `pull` -> `p1a_check.py`, `p1b_check.py` -> assemble the aligned 2023-25 rows and `fit_gate.py` -> `assemble_bundle.py` -> `bundle install` -> `meta bundle`, `meta proof` ->
  `meta counting-start` (refused until all five proofs pass). Not run: the gate fit needs the 2025 A0 members of T478's held-out path.

## COMBO_STOCKS shadow: blk, stl and the combo props (COMBO_STOCKS_SHADOW; flag `--log-combo-shadow`, ON in the pretip job since 2026-10-14)

Rule: `docs/prereg/COMBO_STOCKS_SHADOW.md` (FROZEN 2026-10-14 15:08 CDT, sha256 b9b38cbe; config `configs/combo_stocks_shadow.yaml` sha256 8932ccec). Code: `nba/daily/combo_shadow/`
(`config.py`, `store.py`, `run.py`, `settle.py`, `display.py`, `meta.py`), reader `nba/eval/combo_shadow_live.py`, step `pipeline._combo_shadow`. Flag-gated, ON in the pretip job
(Devin's go, 2026-10-14). Counting start in `forward_combo_meta` is UNSET: `store.set_counting_start` refuses until P1, P2, P3, P4 are recorded as `pass`
(`python -m nba.daily.combo_shadow.meta proof ...`).

* **Step** (after the AVG shadow, own try/except, own heartbeat; a failure is a `shadow_errors` ALERTS line and changes no primary row): per run, regular-season games (`002`) that
  have not tipped. (a) Production's feature frame with the slate placeholders plus the stock columns (`nba.props.stock_features`); blk and stl come from their own read-only
  query (`_history_frames` does not select them and is not edited). (b) `NBCountModel` (Poisson mean + NB dispersion in 4 bins) for blk and stl on played rows dated before the
  slate date, same-date cache `<model cache>/<date>_combo/`. (c) The latest production rows for pts (`props_context_residual_lt`), reb, ast, fg3m rebuilt to 199-point grids from the stored
  `p_ge_full` (n = 199). (d) `joint_grids` with the frozen R and 10,000 frozen base normals (one call: every combo is a sum of the SAME simulated games, so PRA >= PR, PA, RA draw by
  draw) and the same sampler with R = I (INDEP). (e) RECENT from the player's last 40 played games (half-life 10). (f) rows, write-on-change, refusing `made_at >= tipoff_utc`.
  Serving rule: `n_prior >= 5`; a player without an on-time context row for each of the four marginals is not servable (counted in the run info, never imputed). DIRECT and RECENT_CAL
  (descriptive, droppable per the rule) are not built; their columns are NULL. The writer REFUSES to run on any config or base-normal sha other than the frozen ones.
* **Tables** (`store.py`, all new, none read by the canonical dump, so the replay oracle cannot move through this writer): `forward_combo_shadow`, `forward_combo_scores` (settle: the
  latest row with `made_at <= tip - 60`, exact integer CRPS of the 199-point grid for S, RECENT, INDEP), `forward_combo_heartbeat`, `forward_combo_meta`.
* **Settle**: `nba.daily settle` (morning) and every pretip run call `settle_combo_shadow` in their own try; idempotent; a player absent from a final box is `not_in_box`.
* **Heartbeat / watchdog**: `data/ops/heartbeat/combo_shadow.json` at START and END of every run (`consecutive_bad` counts failed runs; a slate with nothing to serve leaves it alone).
  `nba.ops.watchdog.SHADOW_JOBS` alerts at 3 consecutive failed runs and when a run started but never ended (30 min). Silent while the flag is off (no file).
* **Reader** (`python -m nba.eval.combo_shadow_live`): rows are the on-time shadow rows of played regular-season players with `n_prior >= 5`; S minus RECENT on the exact integer CRPS, both
  clusterings (game and date, `full_shadow_live.cluster_boot` / `one_sided` imported), BH over the seven targets, G1 and the slice guard from `nba.props.combo_g1`, the later-half condition,
  COPULA minus INDEP for PRA, PR, PA, RA, integrity gates, servability (flag above 5% not servable), coverage (95% overall, 90% starter and bench, kill below 80%) and the missingness
  table at every look. `look_due` fixes the calendar (interim Sun 2026-11-15 when every target has >= 1,500 rows, kill-only; final the first Sunday on or after 2027-01-10 at >= 3,000 rows
  per target; one second look); `read_look` raises `LookNotDue` before touching a score on any other day. The briefing prints counts and gates only (`display.py`).
* **Wall-time budget (review S1, 2026-10-14)**: `run_combo_shadow` records `skipped` / detail `budget`, writes nothing and leaves `consecutive_bad` alone when the earliest
  upcoming tip is within 35 minutes of the run's stamp at the start (the T-30 tick needs the database), or when the step passes 420 s. The 420 s check is cooperative: between the
  frame build, the NB fit, each batch of 96 rows in the sampler and RECENT (a LightGBM fit is never killed; a fit already running finishes). A budget skip is a missing row that G-COV counts.
  A regular-season slate with no production rows (a broken join) is now `skipped` but counts as bad (`consecutive_bad` + 1) so the watchdog sees it.
* **Readers and the counting start (review S2)**: the combo, AVG and (when `forward_full_meta` exists) FULL readers drop rows of games before `counting_start` in their own meta
  table; with no counting start nothing is scored (`CountingNotStarted`, a `LookNotDue`) and the daily display says so. FULL has no meta table of its own (its rule counts from the
  first regular-season game), so an absent `forward_full_meta` means no extra filter.
* **Replay (proof P3)**: `... replay_season ... --combo-shadow` turns the writer on for the replay (heartbeat to `<out-dir>/combo_heartbeat/`, never `data/ops`). Each replayed date's
  `models.combo_shadow` entry in the replay state shows the status and `written`.
* **Proofs, state at build (2026-10-14)**: **P1 PASS** (`tests/props/test_combo_p1.py` and `tests/daily/test_combo_shadow.py::test_p1_reference_is_reproduced_through_the_writers_code_path`:
  the committed 200-row reference reproduced bit for bit in two chunkings through `run.joint_from_cdfs`). **P2 PASS** (`tests/props/test_stock_features.py` plus the writer-level planted-future test:
  a later game, tonight's own leaked box and a post-run report move no grid; editing a prior game does). **P3 NOT RUN**: needs the HOLDOUT_ACCESS_LOG row first (drafted in the rule, the
  archivist's), then the maintainer's replay of 20 dates of season 2025 with `--combo-shadow` (about 30 min), hash 89889f6d with `rows_written > 0`, copy deleted unread.
  **P4 (cost and storage)**: not measured on real data; fixture figures in the build hand-back.

## App export job (its own launchd job, 2026-10-14)

`python -m nba.ops.app_export` runs `python -m nba.export build --date <today ET> --out data/export/latest` after the morning job. It is NOT a step of `ops/nba_daily.sh`: an export failure
can never change a morning or pretip exit code, and a slow Drive upload in the morning job (today: 98 minutes, rc 1) cannot delay it past its own slots.

* **Schedule**: `ops/launchd/local.nba.app-export.plist`, two slots a day at 12:35 and 14:35 on the schedule clock (the same clock as the other jobs, `data/ops/schedule_tz`), at :35 so they fall
  between the pretip runs. **Not installed by anything in the repo.** Install: `cp ops/launchd/local.nba.app-export.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.nba.app-export.plist && launchctl kickstart -k gui/$(id -u)/local.nba.app-export`
  (the kickstart writes the first heartbeat at once, otherwise the watchdog alerts `app-export:stale` until the first slot has run; it runs the gate, so it may record a skip);
  remove: `launchctl bootout gui/$(id -u)/local.nba.app-export`.
* **Gate** (DECISIONS 2026-10-14 20:31, A1): the `daily-morning` heartbeat must have an END written today (Eastern date) AND every DATA step of that run must have succeeded. `ops/nba_daily.sh`
  writes `data/ops/heartbeat/daily-morning.steps`: a first line `start_ts <the heartbeat's start_ts>` (ties the file to this run) then one `<step> <rc> <ok|failed>` line per step (same
  ok/failed rule as the heartbeat rc). A failed `backup_*_drive` step does NOT block the export (the data is fine; today's first run skipped on exactly that). Any other failed step does, and the gate
  fails closed: a missing steps file, one from another run, an unreadable line, or a run cut short (any of settle, checkpoint, report, parlay_settle, data_snapshot, data_diff, data_check not recorded ok)
  skips. A skip is recorded with its reason (heartbeat `status: skipped`, the log `data/ops/app_export_YYYYMMDD.log`, and `app-export.skip.json` with a consecutive count); it exits 0 and the watchdog
  alerts after two in a row. The morning job's own rc is unchanged (still the worst step), so the watchdog still sees a failed Drive upload. A morning run started before this change wrote no steps
  file, so its export skips (fail closed) until the next morning run.
* **Atomic**: built in `data/export/.latest.stage.<pid>` and swapped into `data/export/latest` only after the build exited 0; a failed build leaves the previous export in place. A locked
  `nba.duckdb` (exit 2) is retried 4 times a minute apart. A second slot that finds today's export already built after the morning job's end does nothing.
* **Heartbeat / watchdog**: `data/ops/heartbeat/app-export.json` at START and END (`status`, `reason`, `rc`). `JOBS["app-export"]` in `nba.ops.watchdog` is `optional`: silent until the job has left a
  heartbeat or its label is loaded, so merging before the maintainer installs it raises no false alert; once loaded, a missed slot, a non-zero rc or two skips alert like any job.

## Watchdog drill (2026-10-14)

`python -m nba.ops.watchdog drill [--hb-dir data/ops/heartbeat]` copies the heartbeat directory to a temporary directory (the source is only read), writes a healthy baseline for every job, injects
(1) a missed pretip (the newest pretip start is one slot older than the slot that is due) and (2) a failed morning job (rc 1), runs the real `check` on the copy (collecting notifier instead of osascript,
`launchctl` text injected), and prints what was raised; exit 0 only when both alerts fire. Result on the live copy at 17:17 ET 2026-10-14: baseline 0 alerts; raised `daily-pretip:stale`
("last start 16:20, expected by 16:50") and `daily-morning:rc` ("last run exited rc=1"), both written to the temporary ALERTS.md and sent to the notifier; the live heartbeat directory unchanged (tests/ops/test_watchdog_drill.py
checks byte equality of the source and four clock positions, including overnight and just after a slot).

## Watchdog game-day probe (2026-10-20)

Incident: on opening night 2026-10-20 the Mac slept on battery, the morning job started late (07:07 CDT, ended 12:24 CDT, rc 2) and nothing told anyone. `nba.ops.watchdog.evaluate_game_day`
(called from `check`, every 10 minutes) runs from the first check at or after 09:00 ET on a day whose schedule (`data/schedule_cache/schedule_<season>.json`, plus `data/schedule/live_tips_<season>.parquet`)
holds a regular-season (`002...`) game on that ET date; non-game days and earlier checks are silent. Four checks, one alert each per ET game day (key `gameday:<YYYY-MM-DD>:<check>`, never re-alerted
every 6 h): `battery` (`pmset -g batt` says "Battery Power"), `caffeinate` (`pgrep -x caffeinate` finds nothing), `slate` (today's game_ids missing from `games`, read-only `nba.duckdb`, 20 s bounded wait; a
held lock defers to the next check and is not an alert), `morning` (the `daily-morning` heartbeat has no start today, or a start with no end). A check that cannot compute alerts as `gameday:<date>:probe-<check>`.
Known: the `slate` check fails on every game day until the schedule fetch writes scheduled games into `games` (LeagueGameFinder returns only started games); that fetch is Devin's decision.
Devin's side: plug in and run `caffeinate -dimsu` before 09:00 ET. Tests: `tests/ops/test_watchdog_gameday.py`.
## The live loop refuses, it does not report (2026-10-21, plan item A)

Opening night (2026-10-20) showed that the loop reported problems after the fact. Four refusals:

1. **A winner row needs a box score.** `settle` / `settle_eligible_pending` grade `win_prob_home` only when
   the game has >= 10 `player_game_stats` rows with `minutes > 0` (`MIN_PLAYED_ROWS`), on top of the 4 h
   `SETTLE_AFTER_TIP_HOURS` guard and `home_pts > 0`. Points alone are not a result (PHI@NYK sat at 63-62).
2. **The morning job ingests first.** `python -m nba.daily ingest [--date D]` runs `incremental_ingest` for the
   season of D (default today ET); exit 0, or 5 (`RC_DEGRADED`) when the games fetch fails. `ops/nba_daily.sh
   morning` runs `step ingest` immediately before `step settle`; a failed ingest alerts and settle still runs.
3. **Withdrawals.** `forward_withdrawals` (append-only; `withdrawn_at, run_id, game_id, model_name, target,
   player_id, official_team_id, reason`). After a run with `--roster-source official` writes its rows, each
   forecast key of the slate whose player the fetched official rosters place on a team that is neither home
   nor away is withdrawn (once per key). A player on no fetched roster is unknown and is not withdrawn. Both
   `settle` skips withdrawn keys (no `forward_scores` row); `forward_scores_elig` is untouched (the frozen
   rule writes the mover as `dnp` there); `nba.export.reader` and
   `nba.parlay.top_source` do not show them. `forward_predictions` is never edited. A withdrawal is
   permanent for its key. `recent` runs (all replays) never withdraw. The failure guard is
   `pipeline._withdraw_misplaced` (error -> `summary.errors`, rc 5, settle unaffected).
4. **Teams for a game absent from `games`.** The games feed returns only started games (OKC@SAS never
   appeared before tip). `top_source` and the export take home/away from the schedule cache
   (`data/schedule_cache/schedule_<season>.json`), then the win row's stamped teams. The live-tips parquet
   holds tip times only, so it cannot supply teams.

### The box must agree with the game (review M2, M3)

Two paths stayed open after item A: a box pulled while the game was in progress is cached per game and never
re-pulled (`missing_boxscore_game_ids` selects games with no rows at all), and the games feed can return a
partial score after the box is final (OKC@SAS 44-65 sixteen minutes after the scoreboard said Final).

* **M2.** `settle_pending` and `settle_eligible_pending` grade any target (winner or prop) only when, for each
  team, `sum(coalesce(pts, 0))` over its `player_game_stats` rows (`team_id = games.home_team / away_team`;
  a DNP row's NULL counts as 0) equals `games.home_pts` / `away_pts` (`_BOX_AGREES_SQL`), on top of the
  4 h guard, the >= 10 played rows for a winner and a game with box rows at all. A game with no box stays
  pending as before. Census: team sums equal the score in 3,958 of 3,958 games 2022-24, 11,512 of 11,516 for
  2013-21 and 3 of 3 for 2026. The gate changes WHEN a row is scored, never WHAT (`FORWARD_PREREG_2026_27.md`
  is not edited).
* **M3.** A refusal is visible. `settle_pending` returns `refused_no_box`, `refused_thin_box` (winner under 10
  played rows) and `refused_box_mismatch` (distinct games each, evaluated in that order) plus `refused_stale`
  (tipped >= 24 h before now); `nba.daily settle` prints each refused game and exits `RC_INFORMATIONAL` (4)
  when every refused game tipped < 24 h ago, `RC_DEGRADED` (5) when any tipped 24 h or more ago, else 0
  (the shadow settlers still run). `ops/nba_daily.sh step()` treats `settle` rc 4 as informational; rc 5 alerts.
  A refused key is never written and stays pending for the next settle. `tests/daily/test_box_consistency.py`.

Not closed: the shadow settlers (pplay, full, avg, combo) do not read `forward_withdrawals`, and they have
neither the 4 h guard nor the box-agrees gate: they still grade a game with any box rows, including a box
pulled in progress during the pretip `run` (follow-up F2, the shadow owners' change).

**Runbook: `refused <gid> box_mismatch` (rc 4 on day 1, rc 5 from day 2).** Nothing re-selects the game
automatically (follow-up F1a, not built): `missing_boxscore_game_ids` picks only games with no box rows, and
`fetch_cached` never re-fetches a (source, key) with an `ingest_log` 'done' row, so the pretip run's in-progress
box stays cached. `nba.daily ingest` therefore does NOT repair it. By hand, the maintainer, with no other
writer on `nba.duckdb`:
1. Compare the score with the box: `SELECT home_pts, away_pts FROM games WHERE game_id = '<gid>'` against
   `SELECT team_id, sum(coalesce(pts,0)), count(*) FILTER (WHERE minutes > 0) FROM player_game_stats WHERE
   game_id = '<gid>' GROUP BY 1`. Also check the games score is final (a partial games-feed score is repaired
   by the next `ingest`, which refreshes `games`).
2. If the box is short: move the cached raw aside (`data/boxscore/<gid>.parquet`, keep it, raw is
   immutable) and delete the log row: `DELETE FROM ingest_log WHERE source = 'boxscore' AND key = '<gid>'`.
3. Re-pull: `uv run python -c "from nba.db.connect import connect; from nba.ingest.boxscores import
   pull_game_boxscore; c = connect(); pull_game_boxscore(c, '<gid>')"`. Its upsert is delete-then-insert per
   game, so the rows are replaced. The next `settle` grades the keys from the same eligible predictions.
4. If the fresh official box still disagrees with the official score, add a DECISIONS row naming the game; its
   keys stay unscored (the game leaves the population on both sides; the checkpoint does not list it yet,
   follow-up F1).
