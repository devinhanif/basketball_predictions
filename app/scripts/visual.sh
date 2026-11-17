#!/usr/bin/env bash
# The visual checks, self-contained: starts the API on the committed fixtures (with the live fixtures) and a web dev
# server on spare ports, runs app/web/scripts/visual-check.mjs, then stops both servers.
#
#   app/scripts/visual.sh                      the full matrix, switch off (structural checks gate; pixels vs a baseline
#                                              are reported when one exists, never gating)
#   app/scripts/visual.sh --betting            the same with the betting switch ON (PH_BETTING=1 for the API,
#                                              VITE_PH_BETTING=1 for the web server) plus the betting screens
#   app/scripts/visual.sh --quick              a representative third of the matrix
#   app/scripts/visual.sh --update             also write the pixel baseline (app/web/.visual/<off|on>/baseline/)
#   app/scripts/visual.sh --strict             a capture whose pixels differ from the baseline above the threshold fails
#   app/scripts/visual.sh --no-pixels          structural checks only (what check.sh --visual runs)
#   app/scripts/visual.sh --only leg,game      only these screens
#
# Every option except --betting passes through to visual-check.mjs (see its header for --threshold and --tolerance).
# Env: VISUAL_API_PORT (default 8142), VISUAL_WEB_PORT (default 5282). Needs Google Chrome. Exit code 1 on any gating
# finding; screenshots and report.json go to app/web/.visual/ (git-ignored).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${VISUAL_API_PORT:-8142}"
WEB_PORT="${VISUAL_WEB_PORT:-5282}"
betting=0
pass=()
for a in "$@"; do
  case "$a" in
    --betting) betting=1; pass+=("--betting") ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) pass+=("$a") ;;
  esac
done

die() { echo "visual.sh: $*" >&2; exit 2; }
port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
port_busy "$API_PORT" && die "port $API_PORT is in use; set VISUAL_API_PORT"
port_busy "$WEB_PORT" && die "port $WEB_PORT is in use; set VISUAL_WEB_PORT"
[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ] || die "Google Chrome is not installed"
[ -d "$APP_DIR/web/node_modules" ] || die "run npm ci in app/web first"
[ -d "$APP_DIR/api/.venv" ] || die "run uv sync in app/api first"

# The switch: set for these two servers only when --betting is given; an inherited value is always removed otherwise.
if [ "$betting" = 1 ]; then
  sw_api=("PH_BETTING=1"); sw_web=("VITE_PH_BETTING=1")
  echo "visual.sh: the betting switch is ON for this run"
else
  sw_api=(); sw_web=()
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ph-visual.XXXXXX")"
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

(cd "$APP_DIR/api" && exec env -u APP_EXPORT_DIR -u PH_PRIVATE -u PH_BETTING "APP_LIVE_DIR=$APP_DIR/fixtures/live" ${sw_api[@]+"${sw_api[@]}"} \
  uv run uvicorn parlayhitters_api.main:app --app-dir src --host 127.0.0.1 --port "$API_PORT") >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!
(cd "$APP_DIR/web" && exec env -u VITE_PH_BETTING "API_ORIGIN=http://127.0.0.1:$API_PORT" ${sw_web[@]+"${sw_web[@]}"} \
  npx vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort) >"$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!

ok=0
for _ in $(seq 1 60); do
  if node -e 'fetch(process.argv[1],{signal:AbortSignal.timeout(2000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(2))' \
    "http://127.0.0.1:$WEB_PORT/api/health" 2>/dev/null; then ok=1; break; fi
  sleep 1
done
[ "$ok" = 1 ] || { tail -n 20 "$LOG_DIR/api.log" "$LOG_DIR/web.log" >&2; die "the servers did not come up in 60 s"; }

cd "$APP_DIR/web"
rc=0
node scripts/visual-check.mjs "http://127.0.0.1:$WEB_PORT/" ${pass[@]+"${pass[@]}"} || rc=$?
exit "$rc"
