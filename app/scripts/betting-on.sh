#!/usr/bin/env bash
# Run the app locally with the betting switch ON: app/scripts/dev.sh --betting, which sets VITE_PH_BETTING=1 for the web
# dev server and PH_BETTING=1 for the API (for those two processes only). Other dev.sh options pass through, for
# example: app/scripts/betting-on.sh --export
# Analysis only: nothing in the betting module places an order, holds a credential or stores anything off this browser.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/dev.sh" --betting "$@"
