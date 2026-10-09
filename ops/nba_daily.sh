#!/bin/sh
# Daily forward pipeline + data maintenance, run by launchd (see ops/install_launchd.sh).
#
#   ops/nba_daily.sh pretip    hourly on the half hour: write pre-tip predictions for today's
#                              ET slate (games already tipped are refused by the store, never
#                              backfilled), then the read-only parlay shadow evaluation.
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
hb_write null null
trap 'hb_end $?' EXIT

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() {
  log "ALERT: $*"
  echo "- $(date '+%Y-%m-%d %H:%M') [$MODE] $*" >> "$OPS/ALERTS.md"
  if command -v osascript >/dev/null 2>&1; then   # macOS only; the VM relies on ALERTS.md
    osascript -e "display notification \"$*\" with title \"NBA pipeline ($MODE)\"" >/dev/null 2>&1 || true
  fi
}
step() {  # step <name> <cmd...>; logs output + exit code, alerts on failure (exit 2 = tipped games refused, not a failure)
  name=$1; shift
  log "start $name"
  "$@" >> "$LOG" 2>&1
  rc=$?
  log "end $name rc=$rc"
  if [ "$rc" -ne 0 ] && ! { [ "$name" = predict ] && [ "$rc" -eq 2 ]; }; then
    HB_RC=$rc
    alert "$name failed (rc=$rc), see $LOG"
  fi
  return $rc
}

# one job at a time (mkdir is atomic); a stale lock older than 3 h is cleared
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
    rmdir "$LOCK" && mkdir "$LOCK" || exit 0
    log "cleared stale lock"
  else
    log "another job holds $LOCK; skipping"
    exit 0
  fi
fi
trap 'rc=$?; rmdir "$LOCK" 2>/dev/null; hb_end $rc' EXIT
trap 'exit 143' INT TERM

# DuckDB is single-writer: wait up to 15 min for any other writer (eval, backfill load)
i=0
while lsof nba.duckdb >/dev/null 2>&1; do
  i=$((i + 1))
  if [ $i -gt 30 ]; then alert "nba.duckdb held by another process for 15 min; skipped"; exit 1; fi
  sleep 30
done

case "$MODE" in
  pretip)
    # maintainer decisions 2026-10-09: official rosters for the first two weeks of 2026-27
    # (opening-week coverage 61% -> 96%), log the integer-support quantile variant live, and
    # SHADOW-log the pts lower-tail mixture (props_context_residual_lt; comparison only, never
    # primary; docs/LOWER_TAIL.md frozen live rule)
    ROSTER=recent
    if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi
    step predict "$UV" run python -m nba.daily run --date "$TODAY_ET" \
      --roster-source "$ROSTER" --log-int-variant --log-lower-tail-variant --rate-limit-s 2.5
    step market_capture "$UV" run python -m nba.markets capture --date "$TODAY_ET"
    step parlay_shadow "$UV" run python -m nba.parlay evaluate --date "$TODAY_ET"
    ;;
  morning)
    step settle "$UV" run python -m nba.daily settle
    # pre-registered look detection (docs/FORWARD_PREREG_2026_27.md section 8): writes an
    # immutable snapshot only when an arm-stat first reaches a look's date count; rc 0 otherwise
    step checkpoint "$UV" run python -m nba.daily checkpoint --look auto
    step report "$UV" run python -m nba.daily report
    step parlay_settle "$UV" run python -m nba.parlay evaluate --date "$TODAY_ET" --settle --no-log
    # post-game stats for newly completed games (per-game sources only; shots/coaches are
    # per team-season and cached once, so they are refreshed by hand). Skip while a backfill runs.
    if pgrep -f run_postgame_backfill.sh >/dev/null 2>&1 || pgrep -f "nba.ingest.queue" >/dev/null 2>&1; then
      log "backfill/ingest queue running (one stats.nba.com puller at a time); skipping post-game ingest"
    else
      for s in tracking hustle officials matchups; do
        step "fetch_$s" "$UV" run python -m nba.ingest --rate-limit-s 1.0 postgame-fetch --source "$s"
      done
      step postgame_load "$UV" run python -m nba.ingest postgame-load \
        --source tracking --source hustle --source officials --source matchups
    fi
    step data_snapshot "$UV" run python -m nba.datamanifest snapshot
    step data_diff "$UV" run python -m nba.datamanifest diff
    step data_check "$UV" run python -m nba.datamanifest check
    # rotating backup (no writer holds the file: we hold the job lock and checked lsof above)
    cp nba.duckdb "data/backups/nba_$(date +%Y%m%d).duckdb" && log "backup written"
    ls -1t data/backups/nba_*.duckdb 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | while read -r f; do
      rm -f "$f" && log "pruned old backup $f"
    done
    # off-machine copy (own Google Drive via the configured rclone remote): registry + latest backup
    latest=$(ls -1t data/backups/nba_*.duckdb 2>/dev/null | head -n 1)
    step backup_registry_drive rclone copy registry_store gdrive:nba_backups/registry_store
    [ -n "$latest" ] && step backup_db_drive rclone copyto "$latest" gdrive:nba_backups/nba_latest.duckdb
    [ -d data/lineups ] && step backup_lineups_drive rclone copy data/lineups gdrive:nba_backups/lineups
    du -sh nba.duckdb data/kalshi data/colab data/backups registry_store >> "$LOG" 2>&1
    ;;
  *)
    echo "usage: $0 pretip|morning" >&2
    exit 64
    ;;
esac
log "done"
