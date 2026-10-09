#!/bin/sh
# Install (or reinstall) the daily pipeline launchd jobs. Times are the Mac's local time (US/Central).
#   local.nba.daily-pretip   at :20 and :50 of every hour 09-21 (pre-tip predictions + parlay shadow). :20/:50, not
#                            :00/:30, so the newest run before a :00/:30 tip starts >= 70 min ahead and is T-60 eligible
#   local.nba.daily-morning  08:00 (settle, report, post-game ingest, data snapshot/check, backup)
#   local.nba.lineups        every 5 min (T-30 lineup collector + shadow run-t30; idle outside game windows)
#   local.nba.watchdog       every 10 min: independent "did it run?" check of the jobs above + Kalshi (nba.ops.watchdog)
# Uninstall: ops/install_launchd.sh --uninstall
set -eu
ROOT=/Users/devin/Downloads/nba-prediction
# Launch through uv, not /bin/sh: macOS privacy protection (TCC) blocks launchd jobs whose
# executable is /bin/sh from reading ~/Downloads ("Operation not permitted", exit 126), while uv
# already has access (the Kalshi snapshot job runs the same way).
UV=/opt/homebrew/bin/uv
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/data/ops"

unload() { launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true; }

if [ "${1:-}" = "--uninstall" ]; then
  for l in local.nba.daily-pretip local.nba.daily-morning local.nba.lineups local.nba.watchdog; do unload $l; rm -f "$AGENTS/$l.plist"; done
  echo "uninstalled"; exit 0
fi

write_plist() {  # label mode schedule-xml [script]
  cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key>
  <array><string>$UV</string><string>run</string><string>--no-sync</string><string>sh</string><string>$ROOT/${4:-ops/nba_daily.sh}</string><string>$2</string></array>
  $3
  <key>StandardOutPath</key><string>$ROOT/data/ops/launchd_$2.out</string>
  <key>StandardErrorPath</key><string>$ROOT/data/ops/launchd_$2.err</string>
</dict>
</plist>
EOF
  unload "$1"
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$1.plist"
  echo "installed $1"
}

PRETIP="<array>"
h=9
while [ $h -le 21 ]; do
  for m in 20 50; do
    PRETIP="$PRETIP<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>$m</integer></dict>"
  done
  h=$((h + 1))
done
PRETIP="$PRETIP</array>"

write_plist local.nba.daily-pretip pretip "<key>StartCalendarInterval</key>$PRETIP"
write_plist local.nba.daily-morning morning "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"
write_plist local.nba.lineups lineups "<key>StartInterval</key><integer>300</integer>" ops/nba_lineups.sh
cat > "$AGENTS/local.nba.watchdog.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>local.nba.watchdog</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key>
  <array><string>$UV</string><string>run</string><string>--no-sync</string><string>python</string><string>-m</string><string>nba.ops.watchdog</string><string>check</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>StandardOutPath</key><string>$ROOT/data/ops/launchd_watchdog.out</string>
  <key>StandardErrorPath</key><string>$ROOT/data/ops/launchd_watchdog.err</string>
</dict>
</plist>
EOF
unload local.nba.watchdog
launchctl bootstrap "gui/$(id -u)" "$AGENTS/local.nba.watchdog.plist"
echo "installed local.nba.watchdog"
launchctl list | grep local.nba
