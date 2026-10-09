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
ROOT=/Users/devin/Downloads/nba-prediction
cd "$ROOT" || exit 1
UV=/opt/homebrew/bin/uv
MODE=${1:-}
OPS=data/ops
LOCK=$OPS/lock
KEEP_BACKUPS=7
mkdir -p "$OPS" data/backups
LOG=$OPS/${MODE}_$(date +%Y%m%d).log
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() {
  log "ALERT: $*"
  echo "- $(date '+%Y-%m-%d %H:%M') [$MODE] $*" >> "$OPS/ALERTS.md"
  osascript -e "display notification \"$*\" with title \"NBA pipeline ($MODE)\"" >/dev/null 2>&1 || true
}
step() {  # step <name> <cmd...>; logs output + exit code, alerts on failure (exit 2 = tipped games refused, not a failure)
  name=$1; shift
  log "start $name"
  "$@" >> "$LOG" 2>&1
  rc=$?
  log "end $name rc=$rc"
  if [ "$rc" -ne 0 ] && ! { [ "$name" = predict ] && [ "$rc" -eq 2 ]; }; then
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
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

# DuckDB is single-writer: wait up to 15 min for any other writer (eval, backfill load)
i=0
while lsof nba.duckdb >/dev/null 2>&1; do
  i=$((i + 1))
  if [ $i -gt 30 ]; then alert "nba.duckdb held by another process for 15 min; skipped"; exit 1; fi
  sleep 30
done

case "$MODE" in
  pretip)
    step predict "$UV" run python -m nba.daily run --date "$TODAY_ET"
    step parlay_shadow "$UV" run python -m nba.parlay evaluate --date "$TODAY_ET"
    ;;
  morning)
    step settle "$UV" run python -m nba.daily settle
    step report "$UV" run python -m nba.daily report
    step parlay_settle "$UV" run python -m nba.parlay evaluate --date "$TODAY_ET" --settle --no-log
    # post-game stats for newly completed games (per-game sources only; shots/coaches are
    # per team-season and cached once, so they are refreshed by hand). Skip while a backfill runs.
    if pgrep -f run_postgame_backfill.sh >/dev/null 2>&1; then
      log "postgame backfill running; skipping post-game ingest"
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
    du -sh nba.duckdb data/kalshi data/colab data/backups registry_store >> "$LOG" 2>&1
    ;;
  *)
    echo "usage: $0 pretip|morning" >&2
    exit 64
    ;;
esac
log "done"
