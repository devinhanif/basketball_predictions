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
   - Win prob: `MovEloBaseline` with `configs/mov_elo_tuned.yaml`, refit on every completed
     game with `game_date < slate date` (cheap, deterministic). On the first day of a new
     season the season-boundary carryover regression is applied explicitly, because `fit`
     only regresses between seasons it sees.
   - Props (pts/reb/ast/fg3m): `nba.props.forward.predict_slate` (model `props_routed_sim`,
     version `routed-v1`; `--props-model rolling` restores the old 82-game baseline, and a
     routed failure falls back to it loudly in the run summary). Everything is built from a
     scratch DB holding only `game_date < slate date`; rosters are players who played in
     their team's last 10 games minus injury-report outs. **Contract per player-stat**
     (all stored in the prediction JSON):
     - `mean`, `std`, `p_ge`, `q10/q50/q90`, `q_grid` (19 quantiles, tau 0.05..0.95): the
       distribution of the stat GIVEN the player plays (minutes > 0).
     - `p_play` (Platt-recalibrated minutes-model P(play); `p_play_raw` in the frame) and the
       unconditional mixture `mean_uncond = p_play * mean`, `p_ge_uncond = p_play * p_ge`
       (valid for thresholds >= 1; a DNP is a 0).
     - `routed_to` (`sim` | `season_avg`), `bucket` (Syntetos-Boylan class), `mean_sim`,
       `mean_season_avg`, `proj_minutes`.
     Routing: the sim for `insufficient_history` / `intermittent` / `erratic` players on
     the stats in `SIM_STATS`, otherwise the "season_avg" model. That model is a
     recency-weighted (half-life 10 games) average of PLAYED games.
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
7. **Report**: log loss / Brier (and log loss minus the naive p=0.5 reference) and prop
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

1. `nba.registry`: a promoted `rung0_mov_elo` version is still wanted so
   `resolve_production` returns a real version.
2. Done: `nba.props.forward.predict_slate`. 3. Done: `nba.ingest.games.refresh_season_games`.
4. Done: forward tables are in `nba/db/schema.sql`.
