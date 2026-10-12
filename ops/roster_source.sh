# Sourced by ops/nba_daily.sh and ops/nba_lineups.sh: the ONE place the roster-source switch lives.
# --roster-source official through ROSTER_OFFICIAL_THROUGH (inclusive, US/Eastern date), then recent.
# Extended from 2026-11-03 to 2026-11-30 (Devin, DECISIONS 2026-10-12).
# Expects TODAY_ET (YYYY-MM-DD); sets ROSTER.
ROSTER_OFFICIAL_THROUGH=2026-11-30
ROSTER=recent
if [ "$TODAY_ET" = "$ROSTER_OFFICIAL_THROUGH" ] || [ "$TODAY_ET" \< "$ROSTER_OFFICIAL_THROUGH" ]; then ROSTER=official; fi
