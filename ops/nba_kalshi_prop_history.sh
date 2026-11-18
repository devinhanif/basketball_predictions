#!/bin/sh
# Launcher of the Kalshi prop price history build (python -m nba.kalshi.prop_history_job run), run by launchd at
# :02, :17, :32 and :47 every hour (ops/launchd/local.nba.kalshi-prop-history.plist). Display data only: nothing
# downstream (alerts, EV, JARVIS, paper trades) reads it. docs/DAILY_PIPELINE.md, "Kalshi prop price history job".
#   heavy.lock or the daily lock held                    -> skip (exit 0; <job>.skip.json counts consecutive skips)
#   another build still running (pid + start in its lock) -> skip, never a second writer
#   no capture for the slate yet / night already final    -> the Python run is a no-op, exit 0 in about a second
#   capture present                                       -> read only the pages not read before, rewrite
#                                                            data/export/kalshi_prop_history/latest/; after last
#                                                            tip + 4 h 10 min write <slate>/ once and drop the state
# Reads the live capture's and the snapshot's raw pages in place (write-once files; never written, never locked).
# Sends no request, opens no database (neither nba.duckdb nor kalshi.duckdb), takes neither data/ops/lock nor
# heavy.lock (it only looks whether they are held). Own lock: data/ops/kalshi_prop_history_lock. No orders, no
# credentials. Runs under nice. Heartbeat data/ops/heartbeat/kalshi-prop-history.json (start, end, rc, detail).
# Launched by launchd through uv (TCC): never run /bin/sh on this file directly.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
OPS=data/ops
LOCK=$OPS/kalshi_prop_history_lock
HEAVY=$OPS/heavy.lock
DAILY=$OPS/lock
HB_DIR=$OPS/heartbeat
HB_JOB=kalshi-prop-history
mkdir -p "$OPS" "$HB_DIR"
# slate date = the ET date of (now - 6 h): a game still running after midnight ET belongs to the day it started
SLATE=${NBA_TEST_DATE_ET:-$(TZ=America/New_York date -v-6H +%Y-%m-%d)}
LOG=$OPS/kalshi_prop_history_$(date +%Y%m%d).log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() { log "ALERT: $*"; echo "- $(date '+%Y-%m-%d %H:%M') [kalshi-prop-history] $*" >> "$OPS/ALERTS.md"; }

HB_START=$(date +%Y-%m-%dT%H:%M:%S%z)
hb_write() {  # hb_write <end_ts|null> <rc|null> <detail>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "%s", "mode": "build", "host": "%s", "slate": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s, "detail": "%s"}\n' \
    "$HB_JOB" "$(hostname -s)" "$SLATE" "$HB_START" "$e" "$2" "$3" > "$HB_DIR/$HB_JOB.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.json.tmp" "$HB_DIR/$HB_JOB.json"
}
note_skip() {  # separate skip record; the run heartbeat is NOT touched (a skip never looks like a run)
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

# yield to the heavy jobs: a fit or rebuild holding heavy.lock, a daily run holding the daily lock (memory on an 8 GB Mac)
if [ -d "$HEAVY" ]; then note_skip "heavy.lock held"; exit 0; fi
if [ -d "$DAILY" ]; then note_skip "daily lock held"; exit 0; fi

# one build at a time: mkdir is atomic. The owner is its pid AND its process start time (ps lstart): a recycled
# pid of some other live process has a different start time, so its lock is stale. A live owner is never stolen.
start_of() { ps -o lstart= -p "$1" 2>/dev/null | sed 's/^ *//; s/ *$//'; }
write_owner() { echo $$ > "$LOCK/pid"; start_of $$ > "$LOCK/start"; }
acquire_lock() {
  if mkdir "$LOCK" 2>/dev/null; then write_owner; return 0; fi
  owner=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
    [ -s "$LOCK/start" ] || return 1   # no start time recorded yet (mid-write): a live pid keeps the lock
    [ "$(start_of "$owner")" = "$(cat "$LOCK/start" 2>/dev/null || true)" ] && return 1
  fi
  rm -f "$LOCK/pid" "$LOCK/start"
  if rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null; then
    write_owner; log "cleared stale lock (owner pid ${owner:-none} is dead or was recycled)"; return 0
  fi
  return 1
}
if ! acquire_lock; then note_skip "another build holds $LOCK"; exit 0; fi
trap 'rc=$?; rm -f "$LOCK/pid" "$LOCK/start"; rmdir "$LOCK" 2>/dev/null; exit $rc' EXIT
trap 'exit 143' INT TERM
rm -f "$HB_DIR/$HB_JOB.skip.json"
hb_write null null "building $SLATE"

OUT=$(nice -n 10 "$UV" run --no-sync python -m nba.kalshi.prop_history_job run --date "$SLATE" 2>>"$LOG")
rc=$?
[ -n "$OUT" ] && log "$OUT"
DECISION=$(printf '%s\n' "$OUT" | tail -n 1 | sed -n 's/.*"decision": "\([a-z_]*\)".*/\1/p')
if [ "$rc" -ne 0 ]; then
  alert "build for $SLATE exited rc=$rc, see $LOG; the next slot retries"
  hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" "$rc" "build failed"
  exit 0
fi
hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "${DECISION:-unknown}"
exit 0
