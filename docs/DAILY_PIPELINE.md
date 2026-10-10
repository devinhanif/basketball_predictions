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
| `local.nba.daily-pretip` | at :20 and :50 of every hour, 09:20-21:50 (moved from hourly :30 on 2026-10-09, review M1) | `nba.daily run --date <today ET>` (exit 4 = some games already tipped, informational; exit 5 = degraded, see below; exit 2 is an argparse usage error and alerts), then the read-only `nba.parlay evaluate` shadow log |
| `local.nba.daily-morning` | 08:00 | `settle`, `report`, parlay `--settle`, post-game ingest of new games (tracking, hustle, officials, matchups; skipped while a backfill or the ingest queue runs), `datamanifest snapshot/diff/check`, `nba.duckdb` copy to `data/backups/` (last 7 kept), disk summary |

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
players with no history. Default is `recent`. Measurements and the recommendation are in
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
  with nobody out. It now calls `nba.sim.usage_redistribution.serve_pretip_flagged`, a thin wrapper over the
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
