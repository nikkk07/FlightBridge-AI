#!/bin/bash
PLIST="$HOME/Library/LaunchAgents/in.weoneaviation.flightbridge.plist"
launchctl unload "$PLIST" 2>/dev/null; rm -f "$PLIST"; echo "Mac runner removed."
