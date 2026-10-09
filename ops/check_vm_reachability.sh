#!/bin/sh
# Run on a candidate VM BEFORE moving the pipeline: can it reach every data source we depend on?
# Needs curl and uv (curl -LsSf https://astral.sh/uv/install.sh | sh). Read-only GETs, no credentials.
# stats.nba.com is tested through nba_api itself (it rejects requests without nba_api's exact
# browser headers). Run it when no other job is hammering stats.nba.com from the same IP: an
# active throttle also shows up as a timeout.        Usage: sh check_vm_reachability.sh
TMP=$(mktemp)
pass=0; fail=0
check() {  # name url
  out=$(curl -s -o "$TMP" -w "%{http_code} %{time_total}" --max-time 40 "$2")
  case "$out" in
    200*) echo "PASS  $1  (http ${out% *}, ${out#* }s, $(wc -c < "$TMP") bytes)"; pass=$((pass + 1)) ;;
    *)    echo "FAIL  $1  (http ${out% *}, ${out#* }s) <- $2"; fail=$((fail + 1)) ;;
  esac
}
nba_api_check() {  # name python-expr returning a row count
  if r=$(uv run --quiet --no-project --with nba_api python -c "
import time; t=time.time()
from nba_api.stats.endpoints import $2 as E
n=$3
print(f'{n} rows, {time.time()-t:.1f}s')" 2>&1); then
    echo "PASS  $1  ($r)"; pass=$((pass + 1))
  else
    echo "FAIL  $1  ($(echo "$r" | tail -n 1 | cut -c1-140))"; fail=$((fail + 1))
  fi
}
nba_api_check "stats.nba.com box score (nba_api)" boxscoretraditionalv3 \
  "len(E.BoxScoreTraditionalV3(game_id='0022400001', timeout=40).get_data_frames()[0])"
nba_api_check "stats.nba.com season schedule (nba_api)" scheduleleaguev2 \
  "len(E.ScheduleLeagueV2(season='2025-26', timeout=40).get_data_frames()[0])"
check "official injury report host" "https://official.nba.com/nba-injury-report-2025-26-season/"
check "Kalshi public market data" "https://api.elections.kalshi.com/trade-api/v2/markets?limit=1"
check "GitHub (repo clone)" "https://github.com"
check "Google Drive API (rclone)" "https://www.googleapis.com/discovery/v1/apis/drive/v3/rest"
rm -f "$TMP"
echo "----"
echo "$pass passed, $fail failed"
if [ "$fail" -eq 0 ]; then
  echo "VERDICT: this VM can run the pipeline."
else
  echo "VERDICT: see FAIL lines; if stats.nba.com fails here but works from home, cloud IPs are blocked."
fi
