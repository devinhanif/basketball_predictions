#!/bin/sh
# Launch the sequential history-ingest queue detached and awake (plain script; no launchd).
#   sh ops/run_ingest_queue.sh          # start (refuses if already running)
# Progress: data/ops/ingest_queue.log ; summary: data/ops/ingest_queue_status.md
cd "$(dirname "$0")/.." || exit 1
mkdir -p data/ops
if pgrep -f "nba.ingest.queue" >/dev/null; then echo "queue already running"; exit 0; fi
AWAKE=""
command -v caffeinate >/dev/null 2>&1 && AWAKE="caffeinate -i"   # macOS only
# shellcheck disable=SC2086
nohup $AWAKE .venv/bin/python -m nba.ingest.queue >> data/ops/ingest_queue.out 2>&1 &
echo "started queue pid $!"
