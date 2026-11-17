#!/usr/bin/env bash
# The browser route walk, self-contained: starts the API on the committed fixtures (with the live fixtures) and a web dev
# server on spare ports, runs app/web/scripts/route-walk.mjs plain and with injected failures, then stops both servers.
#
#   app/scripts/walk.sh                 150 steps plain + 150 steps with --inject, at 390 px
#   app/scripts/walk.sh --widths        also 360, 768 and 1180 px (plain)
#
# Env: WALK_API_PORT (default 8090), WALK_WEB_PORT (default 5290). Needs Google Chrome. Exit code 1 on any violation.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${WALK_API_PORT:-8090}"
WEB_PORT="${WALK_WEB_PORT:-5290}"
widths=0
[ "${1:-}" = "--widths" ] && widths=1

die() { echo "walk.sh: $*" >&2; exit 2; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
port_busy "$API_PORT" && die "port $API_PORT is in use; set WALK_API_PORT"
port_busy "$WEB_PORT" && die "port $WEB_PORT is in use; set WALK_WEB_PORT"
[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] || die "Google Chrome is not installed"

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ph-walk.XXXXXX")"
API_PID=""; WEB_PID=""
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null || true); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  trap - EXIT INT TERM
  [ -z "$WEB_PID" ] || kill_tree "$WEB_PID"
  [ -z "$API_PID" ] || kill_tree "$API_PID"
  wait 2>/dev/null || true
  rm -f "$LOG_DIR/api.log" "$LOG_DIR/web.log"; rmdir "$LOG_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(cd "$APP_DIR/api" && exec env -u APP_EXPORT_DIR -u PH_PRIVATE "APP_LIVE_DIR=$APP_DIR/fixtures/live" \
  uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port "$API_PORT") >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!
(cd "$APP_DIR/web" && exec env "API_ORIGIN=http://127.0.0.1:$API_PORT" npx vite --host 127.0.0.1 --port "$WEB_PORT") >"$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!

ok=0
for _ in $(seq 1 60); do
  if node -e 'fetch(process.argv[1],{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(2))' \
    "http://127.0.0.1:$WEB_PORT/api/health" 2>/dev/null; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || { tail -n 20 "$LOG_DIR/api.log" "$LOG_DIR/web.log" >&2; die "the servers did not come up in 60 s"; }

base="http://127.0.0.1:$WEB_PORT/"
rc=0
cd "$APP_DIR/web"
node scripts/route-walk.mjs "$base" --seed 1 || rc=1
node scripts/route-walk.mjs "$base" --seed 2 --inject || rc=1
if [ "$widths" = 1 ]; then
  for w in 360 768 1180; do node scripts/route-walk.mjs "$base" --seed 3 --width "$w" || rc=1; done
fi
exit "$rc"
