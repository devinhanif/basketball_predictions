#!/bin/sh
# Launcher of the nightly 15-second Kalshi candle poller (python -m nba.kalshi.poll15 run), run by launchd
# every 10 minutes (ops/launchd/local.nba.kalshi-candles.plist). Most ticks do nothing and exit in a second:
#   no game today / too early / already done / window over  -> exit 0
#   a poller is already running (pid in its lock)            -> exit 0 (idempotent; never a second writer)
#   from first tip - 40 min, nothing running, not done       -> run the poller in the foreground
#                                                               (it waits for first tip - 30 min by itself
#                                                               and ends when every game market is closed,
#                                                               at the latest last tip + 4 h)
# Public, unauthenticated Kalshi market-data GETs only; it never opens nba.duckdb and takes NEITHER
# data/ops/lock NOR the heavy-fit lock: its own lock is data/ops/kalshi_candles_lock. No orders, no credentials.
# Display data only: nothing downstream (alerts, EV, JARVIS) reads it.
# Failures append to data/ops/ALERTS.md; the poller's own heartbeat (data/ops/heartbeat/kalshi_candles15.json,
# one beat per 15 s poll) and this launcher's (kalshi-candles-launch.json) are for the independent watchdog.
# Launched by launchd through uv (TCC): never run /bin/sh on this file directly.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
CAFFEINATE=${NBA_CAFFEINATE:-/usr/bin/caffeinate}
OPS=data/ops
LOCK=$OPS/kalshi_candles_lock
HB_DIR=$OPS/heartbeat
HB_JOB=kalshi-candles-launch
mkdir -p "$OPS" "$HB_DIR"
TODAY_ET=${NBA_TEST_DATE_ET:-$(TZ=America/New_York date +%Y-%m-%d)}
LOG=$OPS/kalshi_candles_$(date +%Y%m%d).log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() { log "ALERT: $*"; echo "- $(date '+%Y-%m-%d %H:%M') [kalshi-candles] $*" >> "$OPS/ALERTS.md"; }

HB_START=$(date +%Y-%m-%dT%H:%M:%S%z)
hb_write() {  # hb_write <end_ts|null> <rc|null> <detail>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "%s", "mode": "launch", "host": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s, "detail": "%s"}\n' \
    "$HB_JOB" "$(hostname -s)" "$HB_START" "$e" "$2" "$3" > "$HB_DIR/$HB_JOB.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.json.tmp" "$HB_DIR/$HB_JOB.json"
}

# one poller at a time: mkdir is atomic, the owner pid is inside. A dead owner is cleared at once; the
# lock of a live pid is never stolen.
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
  hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "poller already running"
  exit 0
fi
trap 'rc=$?; rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; exit $rc' EXIT
trap 'exit 143' INT TERM
hb_write null null "deciding"

PLAN=$("$UV" run --no-sync python -m nba.kalshi.poll15 plan --date "$TODAY_ET" 2>>"$LOG")
DECISION=${PLAN%% *}
case "$DECISION" in
  start) ;;
  unknown)  # schedule unreadable: a 15 s poller of an unknown slate cannot guess its window; say so and stop
    alert "plan unknown ($PLAN); no poll tonight unless the schedule is fixed"
    hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 1 "plan unknown"
    exit 0 ;;
  no_games|early|done|over)
    hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "$PLAN"
    exit 0 ;;
  *)
    alert "plan returned '$PLAN' (expected start|early|done|over|no_games|unknown), see $LOG"
    hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 1 "plan error"
    exit 0 ;;
esac

log "start poller for $TODAY_ET ($PLAN)"
hb_write null null "polling $TODAY_ET"
"$CAFFEINATE" -i "$UV" run --no-sync python -m nba.kalshi.poll15 run --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
log "poller rc=$rc"
if [ "$rc" -ne 0 ]; then
  alert "poller for $TODAY_ET exited rc=$rc, see $LOG; the next tick restarts it inside the window"
fi
hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" "$rc" "poller ended"
exit 0
