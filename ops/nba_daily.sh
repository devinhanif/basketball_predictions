#!/bin/sh
# Daily forward pipeline + data maintenance, run by launchd (see ops/install_launchd.sh).
#
#   ops/nba_daily.sh pretip    twice an hour at :20 and :50: write pre-tip predictions for today's
#                              ET slate (games already tipped are refused by the store, never
#                              backfilled), then the read-only parlay shadow evaluation. :20/:50 so
#                              that for any :00 or :30 tip the newest run starts >= 70 min before it
#                              and is eligible for the T-60 comparator (made_at <= tip - 60 min).
#   ops/nba_daily.sh morning   once a day: settle + report, post-game ingest for new games,
#                              data snapshot/leak check, rotating DuckDB backup, disk summary.
#
# Read-only w.r.t. markets: no order endpoints, no credentials. Nothing here deletes data
# except this script's own backups beyond KEEP_BACKUPS. Failures append to data/ops/ALERTS.md
# and raise a macOS notification.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}   # override on the VM (see ops/vm/)
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
RCLONE=${NBA_RCLONE:-/opt/homebrew/bin/rclone}   # launchd has no Homebrew on PATH: bare `rclone` exited 127 on 2026-10-10
MODE=${1:-}
OPS=data/ops
LOCK=$OPS/lock
KEEP_BACKUPS=7
# the morning backup's own name (nba_YYYYMMDD.duckdb, written below). Not nba_*: that also matches the
# history DB backups (nba_history_pre_*.duckdb), which would be pruned here and, if the morning copy
# failed, uploaded to Drive as nba_latest.
BACKUP_GLOB='data/backups/nba_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9].duckdb'
mkdir -p "$OPS" data/backups
LOG=$OPS/${MODE}_$(date +%Y%m%d).log
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)
YESTERDAY_ET=$(TZ=America/New_York date -v-1d +%Y-%m-%d)  # last night's slate (morning: coverage, scorecard)

case "$MODE" in pretip|morning) ;; *) echo "usage: $0 pretip|morning" >&2; exit 64 ;; esac

# heartbeat for the independent watchdog (nba.ops.watchdog): written at START and END, so a job
# that never launches (or dies) is visible from outside even though it cannot alert itself.
HB_DIR=$OPS/heartbeat
HB_JOB=daily-$MODE
HB_RC=0
HB_START=$(date +%Y-%m-%dT%H:%M:%S%z)
mkdir -p "$HB_DIR"
hb_write() {  # hb_write <end_ts|null> <rc|null>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "%s", "mode": "%s", "host": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s}\n' \
    "$HB_JOB" "${MODE:-}" "$(hostname -s)" "$HB_START" "$e" "$2" > "$HB_DIR/$HB_JOB.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.json.tmp" "$HB_DIR/$HB_JOB.json"
}
hb_end() {  # hb_end <exit status>
  rc=$1; [ "$rc" -eq 0 ] && rc=$HB_RC
  hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" "$rc"
}

# per-step status for readers outside the job (nba.ops.app_export): one line "<step> <rc> <ok|failed>" per step,
# under a first line "start_ts <HB_START>" that ties the file to this run's heartbeat. Truncated at each start.
STEPS_FILE=$HB_DIR/$HB_JOB.steps
step_record() {  # step_record <name> <rc> <ok|failed>
  printf '%s %s %s\n' "$1" "$2" "$3" >> "$STEPS_FILE"
}

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() {
  log "ALERT: $*"
  echo "- $(date '+%Y-%m-%d %H:%M') [$MODE] $*" >> "$OPS/ALERTS.md"
  if command -v osascript >/dev/null 2>&1; then   # macOS only; the VM relies on ALERTS.md
    osascript -e "display notification \"$*\" with title \"NBA pipeline ($MODE)\"" >/dev/null 2>&1 || true
  fi
}
note_skip() {  # note_skip <reason>: separate skip heartbeat; the real heartbeat is NOT touched
  n=1
  if [ -f "$HB_DIR/$HB_JOB.skip.json" ]; then
    p=$(sed -n 's/.*"consecutive_skips": \([0-9][0-9]*\).*/\1/p' "$HB_DIR/$HB_JOB.skip.json")
    [ -n "$p" ] && n=$((p + 1))
  fi
  printf '{"job": "%s", "status": "skipped", "host": "%s", "ts": "%s", "consecutive_skips": %s, "reason": "%s"}\n' \
    "$HB_JOB" "$(hostname -s)" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$n" "$1" > "$HB_DIR/$HB_JOB.skip.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.skip.json.tmp" "$HB_DIR/$HB_JOB.skip.json"
  log "skipped ($n consecutive): $1"
}
step() {  # step <name> <cmd...>; logs output + exit code, alerts on failure (exit 4 = informational, not a failure)
  name=$1; shift
  log "start $name"
  "$@" >> "$LOG" 2>&1
  rc=$?
  log "end $name rc=$rc"
  # exit 4 is informational for: predict (some games already tipped) and refs (officials not yet
  # mapped to ids until game_officials is loaded). 2 is argparse's usage-error code and must alert.
  # predict exits 5 when it degraded (ingest/schedule failed, cached data used): that alerts too.
  # odds_capture exits 4 when the feed is unreachable or the request budget refuses: informational
  # (the benchmark is never a dependency of the forecast); its exit 5 (internal error) still alerts.
  # facts (opt-in, NBA_FACTS_NIGHTLY=1) follows the same 0/4 rule; its exit 2 (source locked) alerts.
  # coverage (pretip, every half hour) exits 3 when something is missing or stale: informational
  # there, so one bad slate does not alert 26 times; the morning coverage_yesterday step alerts on 3.
  failed=ok
  if [ "$rc" -ne 0 ] && ! { { [ "$name" = predict ] || [ "$name" = refs ] || [ "$name" = odds_capture ] || [ "$name" = facts ]; } && [ "$rc" -eq 4 ]; } \
      && ! { [ "$name" = coverage ] && [ "$rc" -eq 3 ]; }; then
    HB_RC=$rc
    failed=failed
    alert "$name failed (rc=$rc), see $LOG"
  fi
  step_record "$name" "$rc" "$failed"
  return $rc
}

# one job at a time (mkdir is atomic). The owner pid is stored in the lock dir: a dead owner
# (SIGKILL, power loss) is cleared at once; a live-looking lock older than 3 h is cleared too.
acquire_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  owner=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then why="owner pid $owner is dead"
  elif [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then why="older than 3 h"
  else return 1; fi
  rm -f "$LOCK/pid"
  if rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"; log "cleared stale lock ($why)"; return 0
  fi
  return 1
}
if ! acquire_lock; then
  note_skip "another job holds $LOCK"   # exit 0, but the watchdog alerts on consecutive skips
  exit 0
fi
rm -f "$HB_DIR/$HB_JOB.skip.json"
hb_write null null
printf 'start_ts %s\n' "$HB_START" > "$STEPS_FILE"
trap 'rc=$?; rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; hb_end $rc' EXIT
trap 'exit 143' INT TERM

# DuckDB is single-writer. The python entry points wait (bounded, NBA_DB_LOCK_WAIT_S) for a
# concurrent writer when they open the file, so no check-then-act lsof gate is needed here.
# Wait budget 300 s: the T-30 tick (lineups job) holds the file for ~1-2 min (fit + <=30 roster
# calls at 2.5 s); the reverse wait is 600 s in nba_lineups.sh (> the 339 s cold pretip run).
# Measured durations and the rationale: docs/DAILY_PIPELINE.md "Pretip vs T-30 overlap".
export NBA_DB_LOCK_WAIT_S=${NBA_DB_LOCK_WAIT_S:-300}

case "$MODE" in
  pretip)
    # maintainer decisions 2026-10-09: official rosters for the first two weeks of 2026-27
    # (opening-week coverage 61% -> 96%), log the integer-support quantile variant live, and
    # SHADOW-log the pts lower-tail mixture (props_context_residual_lt; comparison only, never
    # primary; docs/LOWER_TAIL.md frozen live rule), and SHADOW-log the recent-role P(play) model R
    # (--log-pplay-shadow, 2026-10-12: own tables, runs after every primary row is written; oracle
    # 89889f6d identical with the shadow code; its second predict call runs inside the pretip lock)
    # and SHADOW-log RECENT_ROLE_FULL (--log-full-shadow, 2026-10-12: props heads + lower-tail pi refit with
    # the five recent-role columns; own tables forward_full_shadow / forward_full_scores; same contract)
    # and SHADOW-log AVG_SHADOW_LIVE_2 (--log-avg-shadow, Devin 2026-10-14: logs "skipped" until the weights
    # bundle is installed by hand; own tables forward_avg_*, heartbeat avg_shadow.json; counting never starts
    # before 2026-10-20) and COMBO_STOCKS_SHADOW (--log-combo-shadow, Devin 2026-10-14: blk / stl NB heads and
    # the six-stat copula joint for PRA, PR, PA, RA, blk+stl; own tables forward_combo_*, heartbeat
    # combo_shadow.json; each in its own try/except after every primary row is written)
    . "$(dirname "$0")/roster_source.sh"   # sets ROSTER (official through 2026-11-30)
    step predict "$UV" run --no-sync python -m nba.daily run --date "$TODAY_ET" \
      --roster-source "$ROSTER" --log-int-variant --log-lower-tail-variant --log-pplay-shadow --log-full-shadow \
      --log-avg-shadow --log-combo-shadow --rate-limit-s 2.5
    step refs "$UV" run --no-sync python -m nba.ingest.referees collect
    # live sharp-line capture (nba.odds capture, theoddsapi.com) was removed on 2026-10-10 when Devin
    # cancelled that subscription; the adapter stays in nba/odds, re-add this step with a valid key in .env.
    step market_capture "$UV" run --no-sync python -m nba.markets capture --date "$TODAY_ET"
    step parlay_shadow "$UV" run --no-sync python -m nba.parlay evaluate --date "$TODAY_ET" \
      --roster-source "$ROSTER"
    step props_analysis "$UV" run --no-sync python -m nba.parlay analyze --date "$TODAY_ET" \
      --roster-source "$ROSTER"
    # Data coverage of today's slate (nba/ops/coverage.py): schedule, rosters, injury report, lines
    # paired, forecasts before tip, job gaps, early scores. Read-only; exit 3 is informational here.
    step coverage "$UV" run --no-sync python -m nba.ops.coverage --date "$TODAY_ET"
    ;;
  morning)
    # 2026-10-20 (opening night): the morning settle ran before any ingest, so a game left at its
    # half-time score by the last pretip run (PHI@NYK 63-62) would have been graded on it. Refresh
    # games and box scores first; settle then refuses any game without a box score. A failed ingest
    # alerts (rc 5) and settle still runs.
    step ingest "$UV" run --no-sync python -m nba.daily ingest --rate-limit-s 1.0
    step settle "$UV" run --no-sync python -m nba.daily settle
    # pre-registered look detection (docs/FORWARD_PREREG_2026_27.md section 8): writes an
    # immutable snapshot only when an arm-stat first reaches a look's date count; rc 0 otherwise
    step checkpoint "$UV" run --no-sync python -m nba.daily checkpoint --look auto
    step report "$UV" run --no-sync python -m nba.daily report
    # Last night, in the record: the coverage check alerts on any FAIL (exit 3); the live scorecard
    # (nba/forward/scorecard.py) rebuilds the season-to-date report after the settle above.
    step coverage_yesterday "$UV" run --no-sync python -m nba.ops.coverage --date "$YESTERDAY_ET"
    step scorecard "$UV" run --no-sync python -m nba.forward.scorecard --date "$YESTERDAY_ET" \
      --since 2026-10-20 --out-dir reports/forward_scorecard
    # OFF by default (launchd behaviour unchanged until the maintainer sets NBA_FACTS_NIGHTLY=1 in
    # the job environment). Rebuilds the derived fact store (docs/FACTS_STORE.md); reads nba.duckdb
    # read-only, writes only data/facts/facts.duckdb, and never blocks the steps below (rc is logged).
    if [ "${NBA_FACTS_NIGHTLY:-}" = 1 ]; then
      step facts "$UV" run --no-sync python -m nba.facts build --seasons 2022,2023,2024,2026 \
        --schedule-seasons 2026 --out data/facts/facts.duckdb
    fi
    step parlay_settle "$UV" run --no-sync python -m nba.parlay evaluate --date "$TODAY_ET" --settle --no-log
    # post-game stats for newly completed games (per-game sources only; shots/coaches are
    # per team-season and cached once, so they are refreshed by hand). Skip while a backfill runs.
    if pgrep -f run_postgame_backfill.sh >/dev/null 2>&1 || pgrep -f "nba.ingest.queue" >/dev/null 2>&1; then
      log "backfill/ingest queue running (one stats.nba.com puller at a time); skipping post-game ingest"
    else
      for s in tracking hustle officials matchups; do
        step "fetch_$s" "$UV" run --no-sync python -m nba.ingest --rate-limit-s 1.0 postgame-fetch --source "$s"
      done
      step postgame_load "$UV" run --no-sync python -m nba.ingest postgame-load \
        --source tracking --source hustle --source officials --source matchups
    fi
    step data_snapshot "$UV" run --no-sync python -m nba.datamanifest snapshot
    step data_diff "$UV" run --no-sync python -m nba.datamanifest diff
    step data_check "$UV" run --no-sync python -m nba.datamanifest check
    # rotating backup: needs a quiet file (no other writer, no leftover WAL), else skipped + alert
    i=0
    while lsof nba.duckdb >/dev/null 2>&1 && [ $i -le 30 ]; do i=$((i + 1)); sleep 30; done
    if lsof nba.duckdb >/dev/null 2>&1; then
      alert "nba.duckdb held by another process for 15 min; backup skipped"
    elif [ -e nba.duckdb.wal ]; then
      alert "nba.duckdb.wal present; backup skipped (a plain cp would miss committed rows)"
    else
      cp nba.duckdb "data/backups/nba_$(date +%Y%m%d).duckdb" && log "backup written"
    fi
    ls -1t $BACKUP_GLOB 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r f; do
      rm -f "$f" && log "pruned old backup $f"
    done
    # off-machine copy (own Google Drive via the configured rclone remote): registry + latest backup
    latest=$(ls -1t $BACKUP_GLOB 2>/dev/null | head -n 1)
    step backup_registry_drive "$RCLONE" copy registry_store gdrive:nba_backups/registry_store
    [ -n "$latest" ] && step backup_db_drive "$RCLONE" copyto "$latest" gdrive:nba_backups/nba_latest.duckdb
    [ -d data/lineups ] && step backup_lineups_drive "$RCLONE" copy data/lineups gdrive:nba_backups/lineups
    # checkpoint snapshots are the immutable record of pre-registered looks: copy, never sync/delete
    [ -d data/checkpoints ] && step backup_checkpoints_drive "$RCLONE" copy --immutable data/checkpoints gdrive:nba_backups/checkpoints
    du -sh nba.duckdb data/kalshi data/colab data/backups registry_store >> "$LOG" 2>&1
    ;;
  *)
    echo "usage: $0 pretip|morning" >&2
    exit 64
    ;;
esac
log "done"
