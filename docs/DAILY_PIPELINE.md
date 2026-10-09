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

`run` exits 2 if any slate game had already tipped (those games get no prediction;
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
     Other modes: `--props-model routed` (sim routing per `SIM_STATS`, currently empty so it
     is recency only) and `--props-model rolling` (old 82-game baseline).
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
