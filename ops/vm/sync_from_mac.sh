#!/bin/sh
# Run ON THE MAC: copy the state the daily pipeline needs to the VM over SSH (rsync, no --delete).
#
#   NBA_VM_HOST=ubuntu@<vm-ip> sh ops/vm/sync_from_mac.sh [--dry-run] [--no-db]
#
# Env: NBA_VM_HOST (required, user@host), NBA_VM_DIR (remote repo dir, relative to the remote home;
#      default basketball_predictions), NBA_SSH_KEY (optional private key path), NBA_ROOT (default: this repo).
#
# Copied:   nba.duckdb, registry_store/, data/{kalshi,lineups,schedule,players_static} and the parquet caches the
#           daily run reads (injury/availability, box scores, post-game sources, stack/rapm/winprob, manifests, parlay).
# NOT copied: data/colab, data/history, data/backups, data/ops (logs/locks), data/rehearsal, data/reviews,
#           data/backfill_db, .venv, anything under .git.
# Safety: refuses while any local process holds nba.duckdb / kalshi.duckdb / lineups.duckdb (single-writer
#         files; retry in a minute, the kalshi job holds its file only briefly). Nothing is deleted on the VM.
# WARNING: --no-db is what you want for every re-sync once the VM has its OWN database (shadow period):
#          without it the VM's nba.duckdb is overwritten by the Mac's copy.
set -eu
ROOT=${NBA_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}
DRY=""; WITH_DB=1
for a in "$@"; do
  case "$a" in
    --dry-run) DRY="-n" ;;
    --no-db) WITH_DB=0 ;;
    *) echo "usage: NBA_VM_HOST=user@host $0 [--dry-run] [--no-db]" >&2; exit 64 ;;
  esac
done
: "${NBA_VM_HOST:?set NBA_VM_HOST=ubuntu@<vm-ip>}"
REMOTE_DIR=${NBA_VM_DIR:-basketball_predictions}
cd "$ROOT"

SSH="ssh -o StrictHostKeyChecking=accept-new"
[ -n "${NBA_SSH_KEY:-}" ] && SSH="$SSH -i $NBA_SSH_KEY"

# relative paths (from the repo root) to copy
PATHS="registry_store data/kalshi data/lineups data/schedule data/players_static
data/availability_official data/availability_backfill data/injury_elo data/injury_elo_v2 data/injury_elo_realtip
data/boxscore data/games data/coaches data/shots data/tracking data/hustle data/officials data/matchups data/national-tv
data/pbp data/rapm data/stack data/winprob_family data/manifests data/parlay data/models"
[ "$WITH_DB" -eq 1 ] && PATHS="nba.duckdb $PATHS"

echo "== sizes (source side)"
LIST=""
for p in $PATHS; do
  if [ -e "$p" ]; then du -sh "$p"; LIST="$LIST $p"; else echo "  (absent, skipped) $p"; fi
done

echo "== writer check"
for f in nba.duckdb data/kalshi/kalshi.duckdb data/lineups/lineups.duckdb; do
  if [ -e "$f" ] && lsof "$f" >/dev/null 2>&1; then
    echo "STOP: $f is held by a running process; DuckDB files are copied only with no writer. Retry shortly." >&2
    lsof "$f" 2>/dev/null | head -n 5 >&2
    exit 1
  fi
done
echo "no writers on the duckdb files"

echo "== rsync -> $NBA_VM_HOST:$REMOTE_DIR ${DRY:+(dry run)}"
$SSH "$NBA_VM_HOST" "mkdir -p '$REMOTE_DIR'"
# -R keeps the relative layout; the excludes guard against stray large dirs and macOS cruft
# shellcheck disable=SC2086
rsync -azR --partial $DRY -e "$SSH" \
  --exclude '.DS_Store' --exclude 'data/colab' --exclude 'data/history' --exclude 'data/backups' \
  --exclude '*.duckdb.wal.tmp' \
  $LIST "$NBA_VM_HOST:$REMOTE_DIR/"
echo "== done. On the VM: ls $REMOTE_DIR/nba.duckdb && sh ops/vm/install_cron.sh"
