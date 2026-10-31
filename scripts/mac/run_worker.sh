#!/bin/bash
# FlightBridge worker on the Mac: full cycle every 15 min, "Run now" requests within a minute while the Mac is on.
# Reads secrets from ../../.env (gitignored). Sending stays off unless .env says otherwise AND the kill switch is released.
cd "$(dirname "$0")/../.." || exit 1
set -a; [ -f .env ] && . ./.env; set +a
export FB_RUNNER="mac"
exec ./.venv/bin/python -m flightbridge.cli worker --interval 900 --poll 60
