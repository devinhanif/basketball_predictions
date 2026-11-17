#!/usr/bin/env bash
# Run the app locally with the betting switch OFF (the default): app/scripts/dev.sh without --betting, which removes any
# inherited VITE_PH_BETTING / PH_BETTING. Other dev.sh options pass through.
set -euo pipefail
for a in "$@"; do
  [ "$a" != "--betting" ] || { echo "betting-off.sh: --betting contradicts this script; use app/scripts/betting-on.sh" >&2; exit 2; }
done
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev.sh" "$@"
