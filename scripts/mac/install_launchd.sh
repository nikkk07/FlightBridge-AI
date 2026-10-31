#!/bin/bash
# Installs the Mac runner as a LaunchAgent (starts at login, restarts if it stops). Undo: ./uninstall_launchd.sh
set -e
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/in.weoneaviation.flightbridge.plist"
[ -x "$REPO/.venv/bin/python" ] || { echo "Create the venv first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$REPO/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>in.weoneaviation.flightbridge</string>
  <key>ProgramArguments</key><array><string>$REPO/scripts/mac/run_worker.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$REPO/logs/worker.log</string>
  <key>StandardErrorPath</key><string>$REPO/logs/worker.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "FlightBridge Mac runner installed. Log: $REPO/logs/worker.log"
