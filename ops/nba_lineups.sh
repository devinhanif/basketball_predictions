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
