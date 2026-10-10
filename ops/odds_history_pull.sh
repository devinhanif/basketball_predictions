#!/bin/sh
# Sequential, detached chain for the-odds-api.com historical pull (read-only; raw prices only).
#   sh ops/odds_history_pull.sh            # start the chain (refuses if already running)
# Waits for any running `nba.odds history-pull` to finish, then runs, in this order, each step
# resumable (raw cache + state file; a rerun of a finished step costs 0 credits):
#   2024 props (resume check) -> 2023 props -> 2025 props -> games 2024 -> games 2023 -> games 2025
# The chain stops at the first non-zero exit (4 = credit cap / server floor / auth abort or refusal,
# 5 = internal error). Per-game event-match failures do not stop it; they are counted in the logs.
# Logs: data/odds/history_pull_<season>_<phase>.log ; chain log: data/odds/history_chain.log
# Status: uv run python -m nba.odds history-status
# The 2025 season is the frozen holdout: this chain pulls its raw prices only and joins nothing.
cd "$(dirname "$0")/.." || exit 1
mkdir -p data/odds
PIDFILE=data/odds/history_chain.pid
CHAIN_LOG=data/odds/history_chain.log

if [ "${1:-}" != "--run" ]; then
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "chain already running (pid $(cat "$PIDFILE"))"
        exit 0
    fi
    AWAKE=""
    command -v caffeinate >/dev/null 2>&1 && AWAKE="caffeinate -i"   # macOS only
    # shellcheck disable=SC2086
    nohup $AWAKE sh "$0" --run >> "$CHAIN_LOG" 2>&1 &
    echo $! > "$PIDFILE"
    echo "started odds history chain pid $!"
    exit 0
fi

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) chain: $*"; }

while pgrep -f "nba.odds history-pull" >/dev/null 2>&1; do
    log "waiting for a running history-pull to finish"
    sleep 30
done

for step in "2024 props" "2023 props" "2025 props" "2024 games" "2023 games" "2025 games"; do
    # shellcheck disable=SC2086
    set -- $step
    season=$1
    phase=$2
    log "start season=$season phase=$phase"
    uv run --env-file .env python -u -m nba.odds history-pull --season "$season" --phase "$phase" \
        >> "data/odds/history_pull_${season}_${phase}.log" 2>&1
    rc=$?
    log "end season=$season phase=$phase rc=$rc"
    if [ "$rc" -ne 0 ]; then
        log "STOP: rc=$rc (credit/auth abort, refusal, or internal error); see the step log"
        rm -f "$PIDFILE"
        exit "$rc"
    fi
done
log "chain complete"
rm -f "$PIDFILE"
