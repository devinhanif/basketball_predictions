#!/bin/sh
# One 5-minute tick of the lineup collector + the T-30 shadow run. Run by launchd (StartInterval 300).
# Read-only w.r.t. markets, no credentials. Writes data/lineups/lineups.duckdb (poller) and, only when a
# T-30 decision is due, forward_predictions via `nba.daily run-t30` (which waits, bounded, for
# any other nba.duckdb writer and exits 75 if it is still busy: deferred to the next tick).
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}   # override on the VM (see ops/vm/)
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
OPS=data/ops
LOCK=$OPS/lineups_lock
mkdir -p "$OPS"
LOG=$OPS/lineups_$(date +%Y%m%d).log
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)

# heartbeat for the independent watchdog (nba.ops.watchdog): written at START and END, so a job
# that never launches (or dies) is visible from outside even though it cannot alert itself.
HB_DIR=$OPS/heartbeat
MODE=lineups
HB_JOB=lineups
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
HOUR_ET=$(TZ=America/New_York date +%H)
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# nothing tips between 03:00 and 09:00 ET: an idle tick still proves the job is alive
if [ "$HOUR_ET" -ge 3 ] && [ "$HOUR_ET" -lt 9 ]; then
  hb_write null null
  trap 'hb_end $?' EXIT
  exit 0
fi

# own lock (NOT the nba_daily lock: this must not wait behind a long ingest). The owner pid is
# stored in the lock dir: a dead owner is cleared at once; a live-looking lock older than 20 min too.
acquire_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  owner=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then why="owner pid $owner is dead"
  elif [ -n "$(find "$LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then why="older than 20 min"
  else return 1; fi
  rm -f "$LOCK/pid"
  if rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"; log "cleared stale lock ($why)"; return 0
  fi
  return 1
}
if ! acquire_lock; then
  note_skip "another tick holds $LOCK"   # exit 0, but the watchdog alerts on consecutive skips
  exit 0
fi
rm -f "$HB_DIR/$HB_JOB.skip.json"
hb_write null null
trap 'rc=$?; rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; hb_end $rc' EXIT
trap 'exit 143' INT TERM

"$UV" run --no-sync python -m nba.lineups poll --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
[ "$rc" -ne 0 ] && { HB_RC=$rc; log "poll rc=$rc"; echo "- $(date '+%Y-%m-%d %H:%M') [lineups] poll failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"; }

# T-30 shadow run only when some game is inside [tip-30, tip)
if "$UV" run --no-sync python -m nba.lineups due >> "$LOG" 2>&1; then
  ROSTER=recent
  if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi   # same rule as the pretip job
  NBA_DB_LOCK_WAIT_S=${NBA_DB_LOCK_WAIT_S:-240} \
    "$UV" run --no-sync python -m nba.daily run-t30 --date "$TODAY_ET" --roster-source "$ROSTER" >> "$LOG" 2>&1
  rc=$?
  log "run-t30 rc=$rc"
  if [ "$rc" -eq 75 ]; then
    log "nba.duckdb still busy after the bounded wait; T-30 run deferred to the next tick"
  elif [ "$rc" -ne 0 ]; then
    HB_RC=$rc
    echo "- $(date '+%Y-%m-%d %H:%M') [lineups] run-t30 failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
  fi
fi
exit 0
