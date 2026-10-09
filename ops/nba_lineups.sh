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
T30_LOCK=$OPS/lineups_t30_lock
NONE=$OPS/.not_held
HB_FINAL=0   # 1 once the poll heartbeat is final: a long T-30 wait must not overwrite newer ticks' heartbeats
T30_LOCK_HELD=$NONE   # trap targets: set to the real lock only while held (never release a lock another tick now owns)
LOCK_HELD=$NONE
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
note_skip() { note_skip_job "$HB_JOB" "$1"; }
note_skip_job() {  # note_skip_job <job> <reason>: separate skip heartbeat; the real heartbeat is NOT touched
  sj=$1; n=1
  if [ -f "$HB_DIR/$sj.skip.json" ]; then
    p=$(sed -n 's/.*"consecutive_skips": \([0-9][0-9]*\).*/\1/p' "$HB_DIR/$sj.skip.json")
    [ -n "$p" ] && n=$((p + 1))
  fi
  printf '{"job": "%s", "status": "skipped", "host": "%s", "ts": "%s", "consecutive_skips": %s, "reason": "%s"}\n' \
    "$sj" "$(hostname -s)" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$n" "$2" > "$HB_DIR/$sj.skip.json.tmp" \
    && mv "$HB_DIR/$sj.skip.json.tmp" "$HB_DIR/$sj.skip.json"
  log "skipped $sj ($n consecutive): $2"
}
HOUR_ET=${NBA_TEST_HOUR_ET:-$(TZ=America/New_York date +%H)}
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# nothing tips between 03:00 and 09:00 ET: an idle tick still proves the job is alive
if [ "$HOUR_ET" -ge 3 ] && [ "$HOUR_ET" -lt 9 ]; then
  hb_write null null
  trap 'hb_end $?' EXIT
  exit 0
fi

# Two locks, so a long T-30 run never pauses lineup polling:
#   $LOCK      held only for the poll itself (a few seconds); the next tick can always poll;
#   $T30_LOCK  held for the whole run-t30 (which may wait for nba.duckdb behind a pretip run).
# Neither is the nba_daily lock (this must not wait behind a long ingest). The owner pid is stored
# in the lock dir: a dead owner is cleared at once; a live-looking lock older than <stale_min> too.
acquire_lock() {  # acquire_lock <dir> <stale_min>
  if mkdir "$1" 2>/dev/null; then echo $$ > "$1/pid"; return 0; fi
  owner=$(cat "$1/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then why="owner pid $owner is dead"
  elif [ -n "$(find "$1" -maxdepth 0 -mmin +"$2" 2>/dev/null)" ]; then why="older than $2 min"
  else return 1; fi
  rm -f "$1/pid"
  if rmdir "$1" 2>/dev/null && mkdir "$1" 2>/dev/null; then
    echo $$ > "$1/pid"; log "cleared stale lock $1 ($why)"; return 0
  fi
  return 1
}
release_lock() { rm -f "$1/pid"; rmdir "$1" 2>/dev/null; }
if ! acquire_lock "$LOCK" 20; then
  note_skip "another tick holds $LOCK"   # exit 0, but the watchdog alerts on consecutive skips
  exit 0
fi
LOCK_HELD=$LOCK
rm -f "$HB_DIR/$HB_JOB.skip.json"
hb_write null null
trap 'rc=$?; release_lock "$LOCK_HELD"; release_lock "$T30_LOCK_HELD"; [ "$HB_FINAL" = 1 ] || hb_end $rc' EXIT
trap 'exit 143' INT TERM

"$UV" run --no-sync python -m nba.lineups poll --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
[ "$rc" -ne 0 ] && { HB_RC=$rc; log "poll rc=$rc"; echo "- $(date '+%Y-%m-%d %H:%M') [lineups] poll failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"; }
release_lock "$LOCK"; LOCK_HELD=$NONE   # polling is done: later ticks poll while a T-30 run waits/runs
hb_end 0; HB_FINAL=1

# T-30 shadow run only when some game is inside [tip-30, tip)
if "$UV" run --no-sync python -m nba.lineups due >> "$LOG" 2>&1; then
  if ! acquire_lock "$T30_LOCK" 30; then
    log "run-t30 already in progress (another tick); not starting a second"
    exit 0
  fi
  T30_LOCK_HELD=$T30_LOCK
  ROSTER=recent
  if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi   # same rule as the pretip job
  # wait for a running pretip `daily run` (measured 4-6 min cold, docs/DAILY_PIPELINE.md) + margin
  NBA_DB_LOCK_WAIT_S=${NBA_DB_LOCK_WAIT_S:-600} \
    "$UV" run --no-sync python -m nba.daily run-t30 --date "$TODAY_ET" --roster-source "$ROSTER" >> "$LOG" 2>&1
  rc=$?
  log "run-t30 rc=$rc"
  if [ "$rc" -eq 75 ]; then
    log "nba.duckdb still busy after the bounded wait; T-30 run deferred to the next tick"
    note_skip_job lineups_t30 "run-t30 deferred: nba.duckdb busy after bounded wait"   # watchdog reads it
  elif [ "$rc" -ne 0 ]; then
    HB_RC=$rc; HB_START=$(date +%Y-%m-%dT%H:%M:%S%z); HB_FINAL=0   # surface the failure in the heartbeat
    echo "- $(date '+%Y-%m-%d %H:%M') [lineups] run-t30 failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
  else
    rm -f "$HB_DIR/lineups_t30.skip.json"
  fi
fi
exit 0
