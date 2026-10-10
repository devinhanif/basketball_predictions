# T-30 lineups collector and shadow prediction path

Status: BUILT 2026-10-09 (maintainer approved, SHADOW ONLY). Production stays at T-60
(`props_context_residual`). The T-30 rows are logged as `props_context_residual_t30`, a
comparison model; nothing reads them for parlays or promotion. Background and the offline
estimate of the value: `docs/LINEUPS_KNOWN.md`, `reports/lineups_known.md`, and the adversary review
`docs/reviews/redteam_lineups_known_2026-10-09.md` (open question: how many game-time decisions are
still open at the T-30 snapshot).

## Source (verified live 2026-10-09)

`https://stats.nba.com/js/data/leaders/00_daily_lineups_YYYYMMDD.json` (YYYYMMDD = Eastern game date).

* Static file on Akamai NetStorage, public, no credentials, one GET returns every game of the day
  (about 10 KB, 2 games on the day tested). It is NOT the metered stats API, but it shares the host,
  so the poller sends one GET per tick only inside game windows (see "Poll design").
* `cdn.nba.com/static/json/...` (scoreboard, boxscore, odds) returned **403 Access Denied** from this
  machine (with a browser User-Agent and with the nba_api headers), and the `staticData` paths 301-redirect to a 404.
  They are not used. If they open up later, `nba.lineups.source` is the only module to change.
* Shape: `{"games": [{gameId, gameStatus (1 not started, 2 live, 3 final), gameStatusText, homeTeam,
  awayTeam}]}`, each team `{teamId, teamAbbreviation, players: [...]}`, each player `{personId, teamId,
  firstName, lastName, playerName, lineupStatus, position, rosterStatus, timestamp}`.
* `lineupStatus`: `Expected` (projected five; the team lists only those five players) or `Confirmed`
  (official; the team lists its whole active roster, 18-20 players in the sample). `position` is non-empty
  only for the five starters (PG/SG/SF/PF/C); the parser takes `announced_starter = position != ""` and the
  T-30 path requires exactly five per team.
* `rosterStatus`: `Active` or `Inactive`. Only `Active` was seen in the two recorded blocks; whether the
  feed lists scratched players as `Inactive` (versus omitting them) is UNVERIFIED until a regular-season day
  with inactives is recorded. The T-30 path treats an `Inactive` row as out, and the
  open-decision measurement treats a player absent from a Confirmed list as `resolved_absent_from_confirmed_list`.
* Source timestamps: per-player `timestamp` is naive US-Eastern (08:43 ET on 2026-10-09 equals the HTTP
  `Last-Modified` of 12:4x GMT). It looks like the block's last refresh time (an `Expected` block's
  timestamp advanced between two polls about 5 minutes apart; the `Confirmed` block's stayed frozen at 08:43), NOT necessarily the moment of
  announcement. So both clocks are stored: our `fetched_at`, the feed's `source_ts` (converted to UTC), and the HTTP
  `Last-Modified`/`ETag`. The measurement of announcement lead time uses our first fetch that reads `Confirmed`
  (a lower bound on lead, resolution = poll interval) and reports the feed's own timestamp next to it.
* The file is overwritten in place (one file per day), so only our own snapshots preserve the history.
  Raw JSON is kept under `data/lineups/raw/<date>/<snapshot_id>.json` (git-ignored).
* Recorded fixture used by the tests: `tests/fixtures/lineups/daily_lineups_20261009_0851z.json`.

## Storage: `data/lineups/lineups.duckdb` (separate file, single writer = the poller)

* `snapshot_log` one row per poll (also non-200 and parse failures): `snapshot_id, fetched_at, game_date, url,
  http_status, last_modified, etag, sha256, raw_path, n_games, n_rows, note`.
* `lineup_snapshots` one row per snapshot x game x team x player: `fetched_at, game_id, game_status(_text),
  team_id, is_home, player_id, player_name, lineup_status, announced_starter, position, roster_status, source_ts`.
* `game_tips` `game_id -> tipoff` from the nba_api schedule (once per date) or, failing that, parsed from the
  feed's status text ("8:00 pm ET") while the game has not started. A schedule tip is never overwritten by a status-text tip.
* Decisions live in `nba.duckdb` (`forward_t30_decisions`, written by `run-t30`, which already holds that writer):
  `game_id, decided_at, tipoff, cutoff, snapshot_id, snapshot_fetched_at, outcome ('logged'|'skipped'), reason, n_rows`.

DuckDB allows one writer or several readers across processes, so `connect_lineups` retries a lock
collision (10 x 3 s). The poller never opens `nba.duckdb`.

## Poll design

`python -m nba.lineups poll` is one tick; launchd runs it every 5 minutes (below). A tick:

1. Learns tips (once per date: one nba_api season-schedule call, marker file `data/lineups/schedule_checked_<date>`;
   preseason games are not in the kept schedule, so they fall back to the feed's status text).
2. Fetches the day's file iff some game is inside `[tip-60 min, tip+5 min)`. Before any tip is known it does a
   bootstrap fetch at most every 30 minutes (this also learns the tips). Never faster than once per 4 minutes.
3. Stores every 200 response in full (also snapshots at/after tip-off: they show how status evolved, but the T-30
   reader never sees them). Garbage 200s (HTML) are logged as `parse_error`, not stored as data.

Cost: about 12 GETs per game-window hour in the busiest period, one shared file for all games, plus 1 schedule call/day.

## T-30 shadow run: `python -m nba.daily run-t30 --date D`

For each game of the Eastern date D (slate = the collector's own tip table, no network):

* not yet T-30 (`now < tip-30`): untouched (decided later);
* `now >= tip`: recorded `skipped: run_after_tipoff`, nothing written;
* otherwise the snapshot is the latest one **fetched strictly before `tip-30 min`** (`latest_snapshot_before`).
  Logged only if both teams read `Confirmed` and each has exactly 5 announced starters. Otherwise nothing is logged
  and the reason is stored: `no_snapshot_before_t30`, `lineup_not_confirmed:home=Expected,away=Confirmed`,
  `starters_not_5:home=4,away=5`, `team_<id>_missing_in_snapshot`, `no_model_rows`.
* features: the production T-60 feature rows (official report cutoff `min(now, tip-60)` unchanged) plus the five `t30_*` columns
  of `nba/props/lineup_features.py`, with tonight's five = the announced five; players listed `Inactive` are dropped
  from the roster; announced starters missing from the projected roster are added only so the five-man comparisons are right.
* model: LightGBM residual model fit on box-score starters (the proxy), cached per date in
  `data/models/context_residual_t30/<date>` (fingerprint includes `lineups_known`, so it never shares the T-60 cache).
  Only context-model rows are logged (no recency-fallback rows).
* rows: `model_name = props_context_residual_t30`, version `ctxres-v1-t30`, JSON includes `snapshot_id`,
  `snapshot_fetched_at`, `snapshot_age_min_before_tip`, both lineups' `source_ts`, `comparison_only: true`.
* `append_predictions` is the existing store: `made_at < tipoff` or the whole batch raises `LeakageError`.
* idempotent per game (`forward_t30_decisions` primary key): a second run changes nothing.
* `p_play`/`mean_uncond` still come from the T-60 minutes model (not lineup aware); settlement scores `mean`/`q_grid`
  conditional on playing, like every other prop row. DNP = `status='dnp'`, excluded from CRPS.

`python -m nba.lineups due` exits 0 iff some known game is in `[tip-30, tip)`; the shell wrapper uses it so a tick
only touches `nba.duckdb` when a decision is due. `python -m nba.lineups status` prints table counts.

## Report (existing `python -m nba.daily report`, no new command)

Settlement is unchanged (`settle_pending` scores every model_name). The forward report gains:

1. **Paired SHADOW section**: `props_context_residual_t30 minus props_context_residual`, per stat, on player-games scored by BOTH,
   CRPS delta with a date-clustered bootstrap CI and sample sizes; a CAUTION line under 300 pairs; decision counts by
   outcome/reason. The offline (upward-biased) expectation is about -1.0 to -1.4% CRPS on pts/reb/ast; fg3m was under its floor.
2. **Collector measurements** (reads `data/lineups/lineups.duckdb` read-only):
   * minutes before tip when each team first read `Confirmed` (our fetch; p10/p50/p90) and the same by the feed's own timestamp;
   * **open game-time decisions**: of the (game, player) pairs listed questionable/doubtful on the latest official report usable at
     T-60, how many were still unresolved at the latest snapshot strictly before T-30 (`open_lineup_unconfirmed` = the team's
     lineup still `Expected`, or `no_snapshot_before_t30`), versus `resolved_active`/`resolved_inactive`/
     `resolved_absent_from_confirmed_list`; with a Wilson 95% interval and the number of games;
   * announced five versus box-score starters on logged games (train/serve skew: announced starters who did not play; box starters not announced).

The adversary's estimate was that about 45% of the headline pts and ast gain is look-ahead if late decisions resolve after the
snapshot; the open-decision share is the number that tests it. Do not read either number with fewer than a few hundred
candidates; small n is printed next to every figure.

## Scheduling (NOT applied; for the maintainer to apply)

Do not touch `ops/nba_daily.sh` or `ops/install_launchd.sh`. Add one new script and one new LaunchAgent.

### 1. `ops/nba_lineups.sh` (new file, `chmod +x`)

```sh
#!/bin/sh
# One 5-minute tick of the lineup collector + the T-30 shadow run. Run by launchd (StartInterval 300).
# Read-only w.r.t. markets, no credentials. Writes data/lineups/lineups.duckdb (poller) and, only when a
# T-30 decision is due and nba.duckdb is free, forward_predictions via `nba.daily run-t30`.
set -u
ROOT=/Users/devin/Downloads/nba-prediction
cd "$ROOT" || exit 1
UV=/opt/homebrew/bin/uv
OPS=data/ops
LOCK=$OPS/lineups_lock
mkdir -p "$OPS"
LOG=$OPS/lineups_$(date +%Y%m%d).log
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)
HOUR_ET=$(TZ=America/New_York date +%H)
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# nothing tips between 03:00 and 09:00 ET
if [ "$HOUR_ET" -ge 3 ] && [ "$HOUR_ET" -lt 9 ]; then exit 0; fi

# own lock (NOT the nba_daily lock: this must not wait behind a long ingest); stale after 20 min
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then
    rmdir "$LOCK" && mkdir "$LOCK" || exit 0
    log "cleared stale lock"
  else
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

"$UV" run python -m nba.lineups poll --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
[ "$rc" -ne 0 ] && { log "poll rc=$rc"; echo "- $(date '+%Y-%m-%d %H:%M') [lineups] poll failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"; }

# T-30 shadow run only when some game is inside [tip-30, tip)
if "$UV" run python -m nba.lineups due >> "$LOG" 2>&1; then
  if lsof nba.duckdb >/dev/null 2>&1; then
    log "nba.duckdb busy; T-30 run deferred to the next tick"
  else
    ROSTER=recent
    if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi   # same rule as the pretip job
    "$UV" run python -m nba.daily run-t30 --date "$TODAY_ET" --roster-source "$ROSTER" >> "$LOG" 2>&1
    rc=$?
    log "run-t30 rc=$rc"
    [ "$rc" -ne 0 ] && echo "- $(date '+%Y-%m-%d %H:%M') [lineups] run-t30 failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
  fi
fi
exit 0
```

Notes: `poll` exits 1 only on a parse error or an unexpected HTTP status (404 is normal on a day with no file).
`run-t30` re-runs on every tick inside the window but is a no-op once a game is decided; the first run of the
day fits the T-30 model (about as long as the T-60 context fit, cached per date, holds the `nba.duckdb` writer for that time).
`ops/nba_daily.sh` already waits up to 15 minutes for the writer, so a collision delays the pretip job, it does not fail it.
With `--roster-source official` the first call per date reuses the cached `CommonTeamRoster` files the pretip job wrote.

### 2. LaunchAgent `~/Library/LaunchAgents/local.nba.lineups.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.nba.lineups</string>
  <key>WorkingDirectory</key><string>/Users/devin/Downloads/nba-prediction</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>/Users/devin/Downloads/nba-prediction/ops/nba_lineups.sh</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>/Users/devin/Downloads/nba-prediction/data/ops/launchd_lineups.out</string>
  <key>StandardErrorPath</key><string>/Users/devin/Downloads/nba-prediction/data/ops/launchd_lineups.err</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
</dict>
</plist>
```

```sh
chmod +x ops/nba_lineups.sh
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.nba.lineups.plist
# remove: launchctl bootout "gui/$(id -u)/local.nba.lineups"; rm ~/Library/LaunchAgents/local.nba.lineups.plist
```

The Mac clock is US/Central; every date in the script is taken in `America/New_York`, and all stored times are UTC.

### 3. Optional line for the existing `morning` case in `ops/nba_daily.sh`

The report (and so the paired section) already runs in `morning`. To back up the collected snapshots off-machine, add after the registry backup:

```sh
    step backup_lineups_drive rclone copy data/lineups gdrive:nba_backups/lineups
```

### Before opening night (regular season starts 2026-10-20)

1. Install the agent. Preseason days with games exercise the poller (`python -m nba.lineups status` shows counts).
2. After the first few regular-season days check `forward_t30_decisions` (how many `logged` versus skip reasons) and whether any
   `Inactive` rows appear (the unverified part of the feed).
3. No claim from the paired section before at least a few hundred paired player-games per stat; consider registering the
   decision rule in `docs/TEST_LEDGER.md` before looking at the forward numbers.

## Commands

```sh
uv run python -m nba.lineups poll --force --no-schedule     # one real GET now (e.g. a preseason day), writes data/lineups/
uv run python -m nba.lineups status
uv run python -m nba.lineups due
uv run python -m nba.daily run-t30 --date 2026-10-20        # normally run by ops/nba_lineups.sh
uv run python -m nba.daily report                           # now includes the T-30 sections
uv run pytest tests/lineups -q --no-cov
```

## Known limits

* `Confirmed` timing is measured, not assumed: if lineups routinely appear after T-30 most games will be skipped (that is the finding).
* Train/serve skew: trained on box-score starters, served on announced starters; the report counts mismatches.
* Late scratches after the snapshot are real noise in the shadow rows (they were absent from the offline proxy).
* The `Inactive` semantics and the `timestamp` semantics are inferred from two recorded blocks (see Source).
* The market reprices at T-30 on the same information; this measures information value against our T-60 model, not edge versus a T-30 price.
