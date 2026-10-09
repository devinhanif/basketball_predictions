#!/bin/sh
# Install the daily-pipeline cron jobs on the VM (equivalent of ops/install_launchd.sh + the kalshi plist).
# Run ON the VM as the ubuntu user after bootstrap. The VM timezone must be America/Chicago (bootstrap sets it),
# so these are the same local times as the Mac's launchd jobs:
#   pretip   at :20 and :50 of hours 09-21          -> ops/nba_daily.sh pretip
#   morning  08:00                                  -> ops/nba_daily.sh morning
#   lineups  every 5 min                            -> ops/nba_lineups.sh (idles itself outside game windows)
#   kalshi   every 15 min (+ once at boot)          -> python -m nba.kalshi snapshot (flock: never overlaps itself)
# Optional (--with-queue): @reboot restart of the history-ingest queue (ops/run_ingest_queue.sh). Off by default:
# the queue is a one-off backfill, only restart it if it was interrupted on purpose.
#
#   sh ops/vm/install_cron.sh [--with-queue]    install / replace (idempotent; only the marked block is touched)
#   sh ops/vm/install_cron.sh --print           show the block, install nothing
#   sh ops/vm/install_cron.sh --uninstall       remove the block (e.g. to fall back to the Mac)
#
# Env: NBA_ROOT (default: the repo this script lives in), NBA_UV (default ~/.local/bin/uv).
set -eu
ROOT=${NBA_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}
UV=${NBA_UV:-$HOME/.local/bin/uv}
BEGIN="# BEGIN nba-pipeline (managed by ops/vm/install_cron.sh)"
END="# END nba-pipeline"
MODE=install; QUEUE=0
for a in "$@"; do
  case "$a" in
    --print) MODE=print ;;
    --uninstall) MODE=uninstall ;;
    --with-queue) QUEUE=1 ;;
    *) echo "usage: $0 [--with-queue|--print|--uninstall]" >&2; exit 64 ;;
  esac
done

block() {
  echo "$BEGIN"
  echo "SHELL=/bin/sh"
  echo "PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
  echo "NBA_ROOT=$ROOT"
  echo "NBA_UV=$UV"
  echo "20,50 9-21 * * * cd $ROOT && sh ops/nba_daily.sh pretip >> data/ops/cron_pretip.out 2>&1"
  echo "0 8 * * * cd $ROOT && sh ops/nba_daily.sh morning >> data/ops/cron_morning.out 2>&1"
  echo "*/5 * * * * cd $ROOT && sh ops/nba_lineups.sh >> data/ops/cron_lineups.out 2>&1"
  echo "*/15 * * * * cd $ROOT && flock -n data/ops/kalshi.flock $UV run python -m nba.kalshi snapshot >> data/kalshi/snapshot.log 2>> data/kalshi/snapshot.err"
  echo "@reboot sleep 60 && cd $ROOT && flock -n data/ops/kalshi.flock $UV run python -m nba.kalshi snapshot >> data/kalshi/snapshot.log 2>> data/kalshi/snapshot.err"
  if [ "$QUEUE" -eq 1 ]; then
    echo "@reboot sleep 90 && cd $ROOT && sh ops/run_ingest_queue.sh >> data/ops/cron_queue.out 2>&1"
  fi
  echo "$END"
}

if [ "$MODE" = print ]; then block; exit 0; fi

command -v crontab >/dev/null 2>&1 || { echo "crontab not found (apt install cron)" >&2; exit 1; }
CUR=$(mktemp)
crontab -l > "$CUR" 2>/dev/null || true
# strip any existing managed block
NEW=$(mktemp)
awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1;next} $0==e{skip=0;next} !skip' "$CUR" > "$NEW"

if [ "$MODE" = install ]; then
  if [ "$(date +%Z)" != CST ] && [ "$(date +%Z)" != CDT ]; then
    echo "WARNING: VM timezone is $(date +%Z), not US/Central. The schedule assumes America/Chicago:" >&2
    echo "         sudo timedatectl set-timezone America/Chicago" >&2
  fi
  mkdir -p "$ROOT/data/ops" "$ROOT/data/kalshi"
  block >> "$NEW"
fi
crontab "$NEW"
rm -f "$CUR" "$NEW"
if [ "$MODE" = install ]; then echo "installed:"; crontab -l | sed -n "/^$BEGIN\$/,/^$END\$/p" | grep -v '^#'; else echo "removed nba-pipeline cron block"; fi
