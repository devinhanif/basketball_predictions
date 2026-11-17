#!/usr/bin/env bash
# Bake the data and build the single-file page: app/web/dist-artifact/parlayhitters.html
#
#   app/scripts/artifact.sh                  bake from the committed fixtures (two 2024-25 nights)
#   app/scripts/artifact.sh --export [DIR]   bake from the nightly export (DIR, else $APP_EXPORT_DIR,
#                                            else <repo>/data/export/latest), read-only
#   app/scripts/artifact.sh --betting        build the page with the betting switch ON (VITE_PH_BETTING=1); without it
#                                            the variable is removed, so the page is built OFF whatever the shell says
#
# Never passes --money to the bake, so the page carries the shared desk cut only.
# Writes app/web/artifact-data/baked.json and app/web/dist-artifact/ (both git-ignored). Prints path and size.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$APP_DIR/.." && pwd)"
die() { echo "artifact.sh: $*" >&2; exit 1; }

use_export=0
export_dir=""
betting=0
while [ $# -gt 0 ]; do
  case "$1" in
    --export)
      use_export=1
      if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then export_dir="$2"; shift; fi
      ;;
    --betting) betting=1 ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option '$1' (try --help)" ;;
  esac
  shift
done

if [ "$use_export" = 1 ]; then
  export_dir="${export_dir:-${APP_EXPORT_DIR:-$REPO_DIR/data/export/latest}}"
  [ -f "$export_dir/manifest.json" ] || die "no manifest.json in '$export_dir'. Pass a directory the nightly export wrote: --export <dir>"
  export_dir="$(cd "$export_dir" && pwd)"
fi

command -v uv >/dev/null || die "uv is not installed"
command -v node >/dev/null || die "node is not installed"
if [ ! -d "$APP_DIR/api/.venv" ]; then echo "artifact.sh: uv sync"; (cd "$APP_DIR/api" && uv sync); fi
if [ ! -d "$APP_DIR/web/node_modules" ]; then echo "artifact.sh: npm ci"; (cd "$APP_DIR/web" && npm ci); fi

mkdir -p "$APP_DIR/web/artifact-data"
if [ "$use_export" = 1 ]; then
  echo "artifact.sh: baking from the export (read-only): $export_dir"
  (cd "$APP_DIR/api" && env "APP_EXPORT_DIR=$export_dir" PYTHONPATH=src \
    uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json)
else
  echo "artifact.sh: baking from the committed fixtures"
  (cd "$APP_DIR/api" && env -u APP_EXPORT_DIR PYTHONPATH=src \
    uv run python -m parlayhitters_api.bake_artifact --out ../web/artifact-data/baked.json)
fi

echo "artifact.sh: building the page"
if [ "$betting" = 1 ]; then
  echo "artifact.sh: the betting switch is ON for this page"
  (cd "$APP_DIR/web" && env VITE_PH_BETTING=1 npm run build:artifact)
else
  (cd "$APP_DIR/web" && env -u VITE_PH_BETTING npm run build:artifact)
fi

out="$APP_DIR/web/dist-artifact/parlayhitters.html"
[ -f "$out" ] || die "the build finished but $out is missing"
bytes="$(wc -c <"$out" | tr -d ' ')"
echo
echo "artifact: $out"
echo "size:     $bytes bytes"
echo "preview:  $APP_DIR/web/dist-artifact/preview.html (open it in a browser; for local viewing only)"
