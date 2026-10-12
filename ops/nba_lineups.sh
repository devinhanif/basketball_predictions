#!/bin/sh
# One 5-minute tick of the lineup collector + the T-30 and T-15 shadow runs. Run by launchd (StartInterval 300).
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
T15_LOCK=$OPS/lineups_t15_lock
NONE=$OPS/.not_held
HB_FINAL=0   # 1 once the poll heartbeat is final: a long T-30 wait must not overwrite newer ticks' heartbeats
T15_LOCK_HELD=$NONE
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
#   $T15_LOCK  the same for run-t15 (sibling arm);
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
trap 'rc=$?; release_lock "$LOCK_HELD"; release_lock "$T30_LOCK_HELD"; release_lock "$T15_LOCK_HELD"; [ "$HB_FINAL" = 1 ] || hb_end $rc' EXIT
trap 'exit 143' INT TERM

"$UV" run --no-sync python -m nba.lineups poll --date "$TODAY_ET" >> "$LOG" 2>&1
rc=$?
[ "$rc" -ne 0 ] && { HB_RC=$rc; log "poll rc=$rc"; echo "- $(date '+%Y-%m-%d %H:%M') [lineups] poll failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"; }
release_lock "$LOCK"; LOCK_HELD=$NONE   # polling is done: later ticks poll while a T-30 run waits/runs
hb_end 0; HB_FINAL=1

# Closing-line capture (the-odds-api.com, docs/CLOSING_LINE_CAPTURE.md): OFF unless NBA_CLOSE_CAPTURE=1,
# in the environment or as a line in $ROOT/.env (the same file that carries THE_ODDS_API_KEY; the key is
# passed to python by `uv run --env-file`, never read or echoed here). It lives in this 5-minute tick, not
# in a new job, because the tick already runs every 300 s and per-game timing is its business, and a new
# launchd job would be a scheduler install (the maintainer's). It runs BEFORE the T-30 block, whose bounded
# wait for nba.duckdb can last minutes and must not delay a close pull. Failures never touch the poll
# heartbeat (already final); they go to the log and ALERTS.md. rc 0 with no game in the window is the norm.
CLOSE_ON=${NBA_CLOSE_CAPTURE:-0}
if [ "$CLOSE_ON" != "1" ] && [ -f "$ROOT/.env" ] && grep -q '^NBA_CLOSE_CAPTURE=1' "$ROOT/.env"; then
  CLOSE_ON=1
fi
if [ "$CLOSE_ON" = "1" ]; then
  ENVF=""
  [ -f "$ROOT/.env" ] && ENVF="--env-file .env"
  KIND=all   # the whole configs/odds_live.yaml schedule, close first (it cannot wait)
  # shellcheck disable=SC2086
  NBA_CLOSE_CAPTURE=1 "$UV" run --no-sync $ENVF python -m nba.odds capture --kind "$KIND" --date "$TODAY_ET" >> "$LOG" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    log "odds capture $KIND rc=$rc"
    echo "- $(date '+%Y-%m-%d %H:%M') [lineups] odds capture $KIND rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
  fi
fi

# LLM shadow forecaster (docs/LLM_SHADOW.md): OFF unless NBA_LLM_SHADOW=1, in the environment or as a
# line in $ROOT/.env (which also carries ANTHROPIC_API_KEY; passed to python by `uv run --env-file`,
# never read or echoed here). With the flag unset NOTHING below runs and this script behaves exactly as
# before. It lives in this 5-minute tick (not a new job: a new launchd job is a scheduler install, the
# maintainer's) because only this cadence lands inside the request window [tip-70, tip-60]: the :20/:50
# pretip job sits 40 or 70 min before a :00/:30 tip. It runs AFTER the odds capture above (the T-60
# lines it needs are captured in the same tick) and BEFORE the T-30 block, bounded by max_wall_s so a
# slow provider cannot starve the T-30 tick. rc 0 = ok/nothing due; 4 = informational (key or monthly
# cap not set, budget, schema violation, post-tip refusal); 75 = nba.duckdb busy (next tick retries);
# anything else alerts. Failures never touch the poll heartbeat (already final).
LLM_ON=${NBA_LLM_SHADOW:-0}
if [ "$LLM_ON" != "1" ] && [ -f "$ROOT/.env" ] && grep -q '^NBA_LLM_SHADOW=1' "$ROOT/.env"; then
  LLM_ON=1
fi
if [ "$LLM_ON" = "1" ]; then
  ENVF=""
  [ -f "$ROOT/.env" ] && ENVF="--env-file .env"
  # shellcheck disable=SC2086
  NBA_LLM_SHADOW=1 "$UV" run --no-sync $ENVF python -m nba.shadow_llm run --date "$TODAY_ET" --provider anthropic >> "$LOG" 2>&1
  rc=$?
  log "llm_shadow rc=$rc"
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 4 ] && [ "$rc" -ne 75 ]; then
    echo "- $(date '+%Y-%m-%d %H:%M') [lineups] llm_shadow rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
  fi
fi

# ROSTER rule shared by both shadow arms (same as the pretip job)
ROSTER=recent
if [ "$TODAY_ET" \< "2026-11-04" ]; then ROSTER=official; fi

# T-30 shadow run only when some game is inside [tip-30, tip). Nothing in this block may exit the
# script: the T-15 block below must still run on this tick (fall-through, not early exit).
if "$UV" run --no-sync python -m nba.lineups due >> "$LOG" 2>&1; then
  if ! acquire_lock "$T30_LOCK" 30; then
    log "run-t30 already in progress (another tick); not starting a second"
  else
    T30_LOCK_HELD=$T30_LOCK
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
    release_lock "$T30_LOCK"; T30_LOCK_HELD=$NONE
  fi
fi

# T-15 sibling arm (docs/prereg/FORWARD_T15_2026_27.md): same model, cutoff tip-15 min, own lock
# (stale after 30 min), own skip heartbeat (lineups_t15), own table. Sequential after the T-30 block
# (8 GB box: never two fits at once from one tick). The wait for nba.duckdb is capped lower than
# T-30's (240 s) so the deadline stays reachable; a deferral (rc 75) is a skip record, and a game
# whose decision starts at/after tip is recorded run_after_tipoff, never dropped.
if "$UV" run --no-sync python -m nba.lineups due --lead-minutes 15 >> "$LOG" 2>&1; then
  if ! acquire_lock "$T15_LOCK" 30; then
    log "run-t15 already in progress (another tick); not starting a second"
  else
    T15_LOCK_HELD=$T15_LOCK
    NBA_DB_LOCK_WAIT_S=${NBA_T15_DB_LOCK_WAIT_S:-240} \
      "$UV" run --no-sync python -m nba.daily run-t15 --date "$TODAY_ET" --roster-source "$ROSTER" >> "$LOG" 2>&1
    rc=$?
    log "run-t15 rc=$rc"
    if [ "$rc" -eq 75 ]; then
      log "nba.duckdb still busy after the bounded wait; T-15 run deferred to the next tick"
      note_skip_job lineups_t15 "run-t15 deferred: nba.duckdb busy after bounded wait"
    elif [ "$rc" -ne 0 ]; then
      HB_RC=$rc; HB_START=$(date +%Y-%m-%dT%H:%M:%S%z); HB_FINAL=0
      echo "- $(date '+%Y-%m-%d %H:%M') [lineups] run-t15 failed rc=$rc, see $LOG" >> "$OPS/ALERTS.md"
    else
      rm -f "$HB_DIR/lineups_t15.skip.json"
    fi
    release_lock "$T15_LOCK"; T15_LOCK_HELD=$NONE
  fi
fi
exit 0
