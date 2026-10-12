#!/bin/sh
# Detached LLM shadow run (docs/LLM_SHADOW.md), started by ops/nba_lineups.sh in its own process group
# so a slow `claude -p` call never blocks the 5-minute lineups tick or its T-30 / T-15 blocks, and
# launchd's end-of-tick cleanup of the tick's process group does not kill it.
#   usage: sh ops/llm_shadow_bg.sh <ET date>      (NBA_ROOT / NBA_UV as in nba_lineups.sh)
# One run at a time: lock dir data/ops/llm_shadow_bg.lock (mkdir is atomic) holding `pid` (this
# script's pid) and `owner` (the tag below). A held lock is cleared ONLY when this mechanism created
# it (owner tag matches) AND its pid is dead; any other lock is left alone and this run exits 0.
# Output: data/ops/llm_shadow_YYYYMMDD.log. Heartbeat data/ops/heartbeat/llm_shadow.json at START and END.
# rc of the python step: 0 ok / nothing due; 4 informational; 75 nba.duckdb busy (next tick retries);
# anything else -> one line in data/ops/ALERTS.md. The flag (NBA_LLM_SHADOW) is checked by the caller.
set -u
ROOT=${NBA_ROOT:-/Users/devin/Downloads/nba-prediction}
cd "$ROOT" || exit 1
UV=${NBA_UV:-/opt/homebrew/bin/uv}
DAY=${1:?usage: llm_shadow_bg.sh <ET date>}
OPS=data/ops
LOCK=$OPS/llm_shadow_bg.lock
TAG=nba_llm_shadow_bg
LOG=$OPS/llm_shadow_$(date +%Y%m%d).log
HB_DIR=$OPS/heartbeat
mkdir -p "$OPS" "$HB_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

take_lock() {
  if mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"; echo "$TAG" > "$LOCK/owner"; return 0
  fi
  owner=$(cat "$LOCK/owner" 2>/dev/null || true)
  pid=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ "$owner" != "$TAG" ] || [ -z "$pid" ] || kill -0 "$pid" 2>/dev/null; then
    return 1   # not ours, still being created, or its owner is alive: never touched
  fi
  rm -f "$LOCK/pid" "$LOCK/owner"
  rmdir "$LOCK" 2>/dev/null || return 1
  mkdir "$LOCK" 2>/dev/null || return 1
  echo $$ > "$LOCK/pid"; echo "$TAG" > "$LOCK/owner"
  log "cleared stale $LOCK (owner $TAG, pid $pid dead)"
  return 0
}

if ! take_lock; then
  log "llm_shadow already running (lock $LOCK held); not starting a second"
  exit 0
fi
START=$(date +%Y-%m-%dT%H:%M:%S%z)
hb() {  # hb <end_ts|null> <rc|null>
  [ "$1" = null ] && e=null || e="\"$1\""
  printf '{"job": "llm_shadow", "mode": "background", "host": "%s", "start_ts": "%s", "end_ts": %s, "rc": %s}\n' \
    "$(hostname -s)" "$START" "$e" "$2" > "$HB_DIR/llm_shadow.json.tmp" && mv "$HB_DIR/llm_shadow.json.tmp" "$HB_DIR/llm_shadow.json"
}
RC=0
trap 'hb "$(date +%Y-%m-%dT%H:%M:%S%z)" "$RC"; rm -f "$LOCK/pid" "$LOCK/owner"; rmdir "$LOCK" 2>/dev/null' EXIT
trap 'RC=143; exit 143' INT TERM HUP
hb null null
log "llm_shadow start date=$DAY pid=$$"

ENVF=""
[ -f "$ROOT/.env" ] && ENVF="--env-file .env"
# shellcheck disable=SC2086
NBA_LLM_SHADOW=1 "$UV" run --no-sync $ENVF python -m nba.shadow_llm run --date "$DAY" >> "$LOG" 2>&1
RC=$?
log "llm_shadow rc=$RC"
if [ "$RC" -ne 0 ] && [ "$RC" -ne 4 ] && [ "$RC" -ne 75 ]; then
  echo "- $(date '+%Y-%m-%d %H:%M') [lineups] llm_shadow rc=$RC, see $LOG" >> "$OPS/ALERTS.md"
fi
exit 0
