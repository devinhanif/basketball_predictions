#!/usr/bin/env bash
# One command to run the parlayhitters app locally: the read-only API plus the web dev server.
#
#   app/scripts/dev.sh                    API on the committed fixtures (two 2024-25 nights) + web, hot reload
#   app/scripts/dev.sh --export [DIR]     serve the nightly export read-only (DIR, else $APP_EXPORT_DIR,
#                                         else <repo>/data/export/latest); refuses if DIR has no manifest.json
#   app/scripts/dev.sh --lan              web server on 0.0.0.0 (a phone on the same Wi-Fi); the API stays on 127.0.0.1
#   app/scripts/dev.sh --private          set PH_PRIVATE=1 for the API (the private desk, local requests only)
#   app/scripts/dev.sh --betting          turn the betting switch ON: VITE_PH_BETTING=1 for the web server and
#                                         PH_BETTING=1 for the API (app/scripts/betting-on.sh does this)
#
# Env: API_PORT (default 8000), WEB_PORT (default 5173). Ctrl-C stops both servers.
# PH_PRIVATE is never set unless --private is given, and an inherited PH_PRIVATE is removed.
# Likewise VITE_PH_BETTING and PH_BETTING: never set unless --betting is given; inherited values are removed.
# Nothing here writes outside the logs in a temp directory; the export is opened read-only by the API.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$APP_DIR/.." && pwd)"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

use_export=0
export_dir=""
lan=0
private=0
betting=0

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
die() { echo "dev.sh: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --export)
      use_export=1
      if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then export_dir="$2"; shift; fi
      ;;
    --lan) lan=1 ;;
    --private) private=1 ;;
    --betting) betting=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" ;;
  esac
  shift
done

if [ "$use_export" = 1 ]; then
  export_dir="${export_dir:-${APP_EXPORT_DIR:-$REPO_DIR/data/export/latest}}"
  [ -f "$export_dir/manifest.json" ] || die "no manifest.json in '$export_dir'. Pass a directory the nightly export wrote: --export <dir>"
  export_dir="$(cd "$export_dir" && pwd)"
fi

case "$API_PORT$WEB_PORT" in
  *[!0-9]*|"") die "API_PORT and WEB_PORT must be plain port numbers (got API_PORT='$API_PORT', WEB_PORT='$WEB_PORT')" ;;
esac
[ "$API_PORT" != "$WEB_PORT" ] || die "API_PORT and WEB_PORT are both $API_PORT; choose two different ports"

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for pair in "API_PORT:$API_PORT" "WEB_PORT:$WEB_PORT"; do
  if port_busy "${pair#*:}"; then
    die "port ${pair#*:} is already in use (another copy of the app?). Choose another, for example: ${pair%%:*}=$((${pair#*:} + 30)) app/scripts/dev.sh"
  fi
done

command -v uv >/dev/null || die "uv is not installed (https://docs.astral.sh/uv/)"
command -v node >/dev/null || die "node is not installed (22.12 or newer)"

if [ ! -d "$APP_DIR/api/.venv" ]; then echo "dev.sh: installing the API environment (uv sync)"; (cd "$APP_DIR/api" && uv sync); fi
if [ ! -d "$APP_DIR/web/node_modules" ]; then echo "dev.sh: installing web packages (npm ci)"; (cd "$APP_DIR/web" && npm ci); fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ph-dev.XXXXXX")"
API_PID=""
WEB_PID=""

# Stop a process we started and everything it started (uv run and npm run each leave a child).
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}
cleanup() {
  trap - EXIT INT TERM
  echo
  echo "dev.sh: stopping"
  [ -z "$WEB_PID" ] || kill_tree "$WEB_PID"
  [ -z "$API_PID" ] || kill_tree "$API_PID"
  [ -z "$WEB_PID" ] || wait "$WEB_PID" 2>/dev/null || true
  [ -z "$API_PID" ] || wait "$API_PID" 2>/dev/null || true
  rm -f "$LOG_DIR/api.log" "$LOG_DIR/web.log"
  rmdir "$LOG_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# API: the environment for the child only. PH_PRIVATE is removed unless --private.
api_env=(env -u APP_EXPORT_DIR -u PH_PRIVATE -u PH_BETTING -u VITE_PH_BETTING)
[ "$use_export" = 1 ] && api_env=(env -u PH_PRIVATE -u PH_BETTING -u VITE_PH_BETTING "APP_EXPORT_DIR=$export_dir")
[ "$private" = 1 ] && api_env+=("PH_PRIVATE=1")
[ "$betting" = 1 ] && api_env+=("PH_BETTING=1")
# Web: the betting switch is build-time (read by src/features.ts); removed unless --betting.
web_env=(env -u VITE_PH_BETTING -u PH_BETTING "API_ORIGIN=http://127.0.0.1:$API_PORT")
[ "$betting" = 1 ] && web_env+=("VITE_PH_BETTING=1")

(cd "$APP_DIR/api" && exec "${api_env[@]}" uv run uvicorn parlayhitters_api.main:app --app-dir src \
  --host 127.0.0.1 --port "$API_PORT") >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!

web_script="dev"
[ "$lan" = 1 ] && web_script="dev:lan"
(cd "$APP_DIR/web" && exec "${web_env[@]}" npm run "$web_script" -- --port "$WEB_PORT") \
  >"$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!

# Wait for /api/health, asked through the web port so the proxy is proven too.
health() {
  node -e '
    fetch(process.argv[1], { signal: AbortSignal.timeout(2000) })
      .then(async (r) => { process.stdout.write(r.status + " " + (await r.text())); process.exit(r.ok ? 0 : 1); })
      .catch(() => process.exit(2));
  ' "http://127.0.0.1:$WEB_PORT/api/health"
}
body=""
ok=0
for _ in $(seq 1 90); do
  kill -0 "$API_PID" 2>/dev/null || { echo "dev.sh: the API exited; its log:" >&2; cat "$LOG_DIR/api.log" >&2; exit 1; }
  kill -0 "$WEB_PID" 2>/dev/null || { echo "dev.sh: the web server exited; its log:" >&2; cat "$LOG_DIR/web.log" >&2; exit 1; }
  if body="$(health 2>/dev/null)"; then ok=1; break; fi
  sleep 1
done
if [ "$ok" != 1 ]; then
  echo "dev.sh: /api/health did not answer in 90 s (last: ${body:-no answer}). API log:" >&2
  tail -n 20 "$LOG_DIR/api.log" >&2
  exit 1
fi

echo
echo "parlayhitters is up   ($body)"
echo "  open:   http://localhost:$WEB_PORT"
if [ "$lan" = 1 ]; then
  addr="$(ipconfig getifaddr en0 2>/dev/null || true)"
  [ -z "$addr" ] || echo "  phone:  http://$addr:$WEB_PORT   (same Wi-Fi; the API itself stays on 127.0.0.1)"
fi
if [ "$use_export" = 1 ]; then echo "  data:   the nightly export, read-only: $export_dir"; else echo "  data:   committed fixtures (two 2024-25 nights, a replay, not tonight)"; fi
if [ "$private" = 1 ]; then
  echo "  desk:   PH_PRIVATE=1 is SET (--private): the private desk answers local requests only"
else
  echo "  desk:   PH_PRIVATE is not set: only the shared desk cut is served"
fi
if [ "$betting" = 1 ]; then
  echo "  betting: switch ON (--betting): #/bet and /api/betting/status exist; analysis only, no orders are placed"
else
  echo "  betting: switch off (the default)"
fi
echo "  logs:   $LOG_DIR/api.log  $LOG_DIR/web.log"
echo "Ctrl-C stops both."

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do sleep 1; done
echo "dev.sh: a server exited. API log (last lines):" >&2
tail -n 15 "$LOG_DIR/api.log" >&2
echo "web log (last lines):" >&2
tail -n 15 "$LOG_DIR/web.log" >&2
exit 1
