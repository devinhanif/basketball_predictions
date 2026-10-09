#!/bin/sh
# Install (or reinstall) the daily pipeline launchd jobs. Times are the Mac's local time (US/Central).
#   local.nba.daily-pretip   every hour at :30 from 09:30 to 21:30 (pre-tip predictions + parlay shadow)
#   local.nba.daily-morning  08:00 (settle, report, post-game ingest, data snapshot/check, backup)
# Uninstall: ops/install_launchd.sh --uninstall
set -eu
ROOT=/Users/devin/Downloads/nba-prediction
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$ROOT/data/ops"

unload() { launchctl bootout "gui/$(id -u)/$1" 2>/dev/null || true; }

if [ "${1:-}" = "--uninstall" ]; then
  for l in local.nba.daily-pretip local.nba.daily-morning; do unload $l; rm -f "$AGENTS/$l.plist"; done
  echo "uninstalled"; exit 0
fi

write_plist() {  # label mode calendar-xml
  cat > "$AGENTS/$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$1</string>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$ROOT/ops/nba_daily.sh</string><string>$2</string></array>
  <key>StartCalendarInterval</key>
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
  PRETIP="$PRETIP<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>30</integer></dict>"
  h=$((h + 1))
done
PRETIP="$PRETIP</array>"

write_plist local.nba.daily-pretip pretip "$PRETIP"
write_plist local.nba.daily-morning morning "<dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>"
launchctl list | grep local.nba
