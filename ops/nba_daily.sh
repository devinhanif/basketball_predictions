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
mkdir -p "$OPS" data/backups
LOG=$OPS/${MODE}_$(date +%Y%m%d).log
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)

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
  if [ "$rc" -ne 0 ] && ! { { [ "$name" = predict ] || [ "$name" = refs ] || [ "$name" = odds_capture ]; } && [ "$rc" -eq 4 ]; }; then
    HB_RC=$rc
    alert "$name failed (rc=$rc), see $LOG"
  fi
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
    # primary; docs/LOWER_TAIL.md frozen live rule)
    ROSTER=recent
    if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi
    step predict "$UV" run --no-sync python -m nba.daily run --date "$TODAY_ET" \
      --roster-source "$ROSTER" --log-int-variant --log-lower-tail-variant --rate-limit-s 2.5
    step refs "$UV" run --no-sync python -m nba.ingest.referees collect
    # live sharp-line capture (nba.odds capture, theoddsapi.com) was removed on 2026-10-10 when Devin
    # cancelled that subscription; the adapter stays in nba/odds, re-add this step with a valid key in .env.
    step market_capture "$UV" run --no-sync python -m nba.markets capture --date "$TODAY_ET"
    step parlay_shadow "$UV" run --no-sync python -m nba.parlay evaluate --date "$TODAY_ET"
    step props_analysis "$UV" run --no-sync python -m nba.parlay analyze --date "$TODAY_ET"
    ;;
  morning)
    step settle "$UV" run --no-sync python -m nba.daily settle
    # pre-registered look detection (docs/FORWARD_PREREG_2026_27.md section 8): writes an
    # immutable snapshot only when an arm-stat first reaches a look's date count; rc 0 otherwise
    step checkpoint "$UV" run --no-sync python -m nba.daily checkpoint --look auto
    step report "$UV" run --no-sync python -m nba.daily report
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
    ls -1t data/backups/nba_*.duckdb 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r f; do
      rm -f "$f" && log "pruned old backup $f"
    done
    # off-machine copy (own Google Drive via the configured rclone remote): registry + latest backup
    latest=$(ls -1t data/backups/nba_*.duckdb 2>/dev/null | head -n 1)
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
