#!/bin/sh
# Launcher of the nightly live capture (python -m nba.live capture), run by launchd every 10 minutes
# (ops/launchd/local.nba.live-capture-start.plist). Most ticks do nothing and exit in about a second:
#   no game today / too early / already done / window over  -> exit 0
#   a capture is already running (pid in its lock)           -> exit 0 (idempotent; never a second writer)
#   from first tip - 150 min, nothing running, not done      -> run the capture in the foreground
#                                                               (it waits for first tip - 2 h by itself
#                                                               and ends at last tip + 4 h)
# The capture reads and writes public data only (ESPN, Kalshi), into write-once raw files under
# data/raw/live_<ET date>/. It never opens nba.duckdb, so it takes NEITHER data/ops/lock (daily jobs)
# NOR the heavy-fit lock: it has its own lock, data/ops/live_capture_lock. No orders, no credentials.
# Launched by launchd through uv (TCC); the capture itself runs under caffeinate -i.
# Failures append to data/ops/ALERTS.md; the capture's own heartbeat (data/ops/heartbeat/live_capture.json,
# one beat a minute) and this launcher's (live-capture-launch.json) are judged by nba.ops.watchdog.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
CAFFEINATE=${NBA_CAFFEINATE:-/usr/bin/caffeinate}
OPS=data/ops
LOCK=$OPS/live_capture_lock
HB_DIR=$OPS/heartbeat
HB_JOB=live-capture-launch
mkdir -p "$OPS" "$HB_DIR"
TODAY_ET=${NBA_TEST_DATE_ET:-$(TZ=America/New_York date +%Y-%m-%d)}
LOG=$OPS/live_capture_$(date +%Y%m%d).log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() { log "ALERT: $*"; echo "- $(date '+%Y-%m-%d %H:%M') [live-capture] $*" >> "$OPS/ALERTS.md"; }

HB_START=$(date +%Y-%m-%dT%H:%M:%S%z)
hb_write() {  # hb_write <end_ts|null> <rc|null> <detail>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "%s", "mode": "launch", "host": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s, "detail": "%s"}\n' \
    "$HB_JOB" "$(hostname -s)" "$HB_START" "$e" "$2" "$3" > "$HB_DIR/$HB_JOB.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.json.tmp" "$HB_DIR/$HB_JOB.json"
}

# one capture at a time: mkdir is atomic, the owner pid is inside. A dead owner (SIGKILL, power loss)
# is cleared at once; the lock of a live pid is never stolen (no age rule: a capture runs ~10 h).
acquire_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  owner=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then return 1; fi
  rm -f "$LOCK/pid"
  if rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"; log "cleared stale lock (owner pid ${owner:-none} is dead)"; return 0
  fi
  return 1
}

if ! acquire_lock; then
  hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "capture already running"
  exit 0
fi
trap 'rc=$?; rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; exit $rc' EXIT
trap 'exit 143' INT TERM
hb_write null null "deciding"

PLAN=$("$UV" run --no-sync python -m nba.ops.live_capture plan --date "$TODAY_ET" 2>>"$LOG")
DECISION=${PLAN%% *}
case "$DECISION" in
  start|unknown) ;;   # unknown = schedule unreadable: start anyway, the capture exits 0 on a no-game day
  no_games|early|done|over)
    hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "$PLAN"
    exit 0 ;;
  *)
    alert "plan returned '$PLAN' (expected start|early|done|over|no_games|unknown), see $LOG"
    hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 1 "plan error"
    exit 0 ;;
esac

log "start capture for $TODAY_ET ($PLAN)"
hb_write null null "capturing $TODAY_ET"
"$CAFFEINATE" -i "$UV" run --no-sync python -m nba.live capture --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
log "capture rc=$rc"
if [ "$rc" -ne 0 ]; then
  alert "capture for $TODAY_ET exited rc=$rc, see $LOG; the next tick restarts it inside the window"
fi
hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" "$rc" "capture ended"
exit 0
