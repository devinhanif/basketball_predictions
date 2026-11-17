#!/usr/bin/env bash
# Every local check for app/api and app/web, in order, with a pass/fail table and times at the end.
#
#   app/scripts/check.sh              everything except the browser walk
#   app/scripts/check.sh --fast       pytest without the three slow files (test_live, test_links, test_last10_links)
#   app/scripts/check.sh --bake       bake the artifact data from the fixtures first (else the artifact step is
#                                     skipped when app/web/artifact-data/baked.json is absent)
#   app/scripts/check.sh --browser    also run the browser route walk (app/scripts/walk.sh: own servers on spare ports, needs Chrome)
#   app/scripts/check.sh --betting    run every step with the betting switch ON (VITE_PH_BETTING=1 for the web build and
#                                     tests, PH_BETTING=1 for the API). Without it both are removed from the environment,
#                                     so an inherited value never turns the default (OFF) check into an ON one.
#
# The "flag parity" step always builds both states itself (app/web/scripts/flag-parity.mjs, temp dirs only).
#
# Output of each step streams unpiped. Exit code is non-zero if any step failed. A failure does not stop later steps.
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fast=0; bake=0; browser=0; betting=0
for a in "$@"; do
  case "$a" in
    --fast) fast=1 ;;
    --bake) bake=1 ;;
    --browser) browser=1 ;;
    --betting) betting=1 ;;
    -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "check.sh: unknown option '$a' (try --help)" >&2; exit 2 ;;
  esac
done

if [ "$betting" = 1 ]; then
  export VITE_PH_BETTING=1 PH_BETTING=1
  echo "check.sh: the betting switch is ON for this run (VITE_PH_BETTING=1, PH_BETTING=1)"
else
  unset VITE_PH_BETTING PH_BETTING
fi

command -v uv >/dev/null || { echo "check.sh: uv is not installed" >&2; exit 2; }
command -v node >/dev/null || { echo "check.sh: node is not installed" >&2; exit 2; }
[ -d "$APP_DIR/api/.venv" ] || { echo "check.sh: uv sync"; (cd "$APP_DIR/api" && uv sync) || exit 2; }
[ -d "$APP_DIR/web/node_modules" ] || { echo "check.sh: npm ci"; (cd "$APP_DIR/web" && npm ci) || exit 2; }

NAMES=(); RESULTS=(); TIMES=(); failed=0

# run <name> <dir> <command...>
run() {
  local name="$1" dir="$2"; shift 2
  echo
  echo "=== $name  ($*)"
  local t0 t1 rc=0
  t0="$(date +%s)"
  (cd "$APP_DIR/$dir" && "$@") || rc=$?
  t1="$(date +%s)"
  NAMES+=("$name"); TIMES+=("$((t1 - t0))")
  if [ "$rc" = 0 ]; then RESULTS+=("pass"); else RESULTS+=("FAIL"); failed=1; fi
}
skip() { NAMES+=("$1"); RESULTS+=("skip"); TIMES+=("-"); echo; echo "=== $1  SKIPPED: $2"; NOTES+=("$1: $2"); }
NOTES=()

run "api ruff"            api uv run ruff check .
run "api ruff format"     api uv run ruff format --check .
run "api mypy"            api uv run mypy
if [ "$fast" = 1 ]; then
  run "api pytest (fast)" api uv run pytest --ignore=tests/test_live.py --ignore=tests/test_links.py --ignore=tests/test_last10_links.py
else
  run "api pytest"        api uv run pytest
fi
run "fixtures ruff"       api uv run ruff check ../fixtures/build_fixtures.py --config ../fixtures/ruff.toml

run "web eslint"          web npm run lint
run "web tsc"             web npm run typecheck
run "web vitest"          web npm test
run "web build"           web npm run build

if [ "$bake" = 1 ]; then
  run "artifact (bake+build)" . "$APP_DIR/scripts/artifact.sh"
elif [ -f "$APP_DIR/web/artifact-data/baked.json" ]; then
  run "artifact build"    web npm run build:artifact
else
  skip "artifact build" "app/web/artifact-data/baked.json is absent; run with --bake (fixtures) or app/scripts/artifact.sh --export"
fi

run "flag parity"         web node scripts/flag-parity.mjs

if [ "$browser" = 1 ]; then
  if [ -f "$APP_DIR/web/scripts/route-walk.mjs" ]; then
    run "browser route walk" . "$APP_DIR/scripts/walk.sh"
  else
    skip "browser route walk" "app/web/scripts/route-walk.mjs does not exist yet"
  fi
fi

echo
echo "================ check summary ================"
if [ "$betting" = 1 ]; then echo "betting switch: ON"; else echo "betting switch: off"; fi
printf '%-26s %-6s %s\n' "step" "result" "seconds"
total=0
for i in "${!NAMES[@]}"; do
  printf '%-26s %-6s %s\n' "${NAMES[$i]}" "${RESULTS[$i]}" "${TIMES[$i]}"
  [ "${TIMES[$i]}" = "-" ] || total=$((total + TIMES[i]))
done
printf '%-26s %-6s %s\n' "total" "" "$total"
for n in "${NOTES[@]:-}"; do [ -z "$n" ] || echo "note: $n"; done
if [ "$failed" = 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; fi
exit "$failed"
