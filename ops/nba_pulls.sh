#!/bin/sh
# One 2-minute tick of the pull orchestrator (python -m nba.pulls tick), run by launchd
# (ops/launchd/local.nba.pulls.plist, StartInterval 120; docs/DESIGN_PULL_ORCHESTRATOR.md).
# Serves open pull requests (JARVIS files them when an alert's Pinnacle reference is stale) with ONE per-event
# Pinnacle pull each, through the same client, ledger and known-at checks as the scheduled capture. Most ticks have
# no open request and exit in about a second without reading the key or touching the network.
# Read-only w.r.t. markets (GET only); no orders, no credentials in this file. The key is passed to python by
# `uv run --env-file .env` (THE_ODDS_API_KEY), never read or echoed here. OFF unless NBA_CLOSE_CAPTURE=1, in the
# environment or as a line in $ROOT/.env: the same gate as the scheduled capture in ops/nba_lineups.sh.
# Takes NEITHER data/ops/lock NOR data/ops/heavy.lock and never opens nba.duckdb. Own lock: data/ops/pulls_lock
# (owner pid inside; a dead owner is cleared at once, a live one is never stolen). Own heartbeat:
# data/ops/heartbeat/pulls.json (judged by nba.ops.watchdog). A non-zero tick appends a line to data/ops/ALERTS.md.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
OPS=data/ops
LOCK=$OPS/pulls_lock
HB_DIR=$OPS/heartbeat
HB_JOB=pulls
mkdir -p "$OPS" "$HB_DIR"
LOG=$OPS/pulls_$(date +%Y%m%d).log
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
alert() { log "ALERT: $*"; echo "- $(date '+%Y-%m-%d %H:%M') [pulls] $*" >> "$OPS/ALERTS.md"; }

HB_START=$(date +%Y-%m-%dT%H:%M:%S%z)
hb_write() {  # hb_write <end_ts|null> <rc|null> <detail>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "%s", "mode": "tick", "host": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s, "detail": "%s"}\n' \
    "$HB_JOB" "$(hostname -s)" "$HB_START" "$e" "$2" "$3" > "$HB_DIR/$HB_JOB.json.tmp" \
    && mv "$HB_DIR/$HB_JOB.json.tmp" "$HB_DIR/$HB_JOB.json"
}

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
  log "another tick holds $LOCK; skipped"
  exit 0   # the holder writes the heartbeat; a tick that overlaps the next is rare (a 2-minute job)
fi
trap 'rc=$?; rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; exit $rc' EXIT
trap 'exit 143' INT TERM

ON=${NBA_CLOSE_CAPTURE:-0}
if [ "$ON" != "1" ] && [ -f "$ROOT/.env" ] && grep -q '^NBA_CLOSE_CAPTURE=1' "$ROOT/.env"; then
  ON=1
fi
if [ "$ON" != "1" ]; then
  hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" 0 "disabled (NBA_CLOSE_CAPTURE != 1)"
  exit 0
fi

hb_write null null "ticking"
ENVF=""
[ -f "$ROOT/.env" ] && ENVF="--env-file .env"
# shellcheck disable=SC2086
NBA_CLOSE_CAPTURE=1 "$UV" run --no-sync $ENVF python -m nba.pulls tick >> "$LOG" 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  alert "pulls tick rc=$rc, see $LOG"
fi
hb_write "$(date +%Y-%m-%dT%H:%M:%S%z)" "$rc" "tick ended"
exit 0
