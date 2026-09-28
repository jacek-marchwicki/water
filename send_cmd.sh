#!/bin/bash
# Send commands directly to WaterH bottle via Home Assistant collector HTTP API

HA_URL="${HA_URL:-http://192.168.2.209:7700}"

cmd="$1"
shift 2>/dev/null || true

case "$cmd" in
  flash)
    echo ">> Sending Flash LED command..."
    curl -4 -s -X POST "${HA_URL}/commands/flash"
    echo ""
    ;;
  led)
    mode="${1:-breathe}"
    color="${2:-blue}"
    echo ">> Setting LED mode=$mode, color=$color..."
    curl -4 -s -X POST "${HA_URL}/commands/led" \
      -H "Content-Type: application/json" \
      -d "{\"mode\": \"$mode\", \"color\": \"$color\"}"
    echo ""
    ;;
  intake)
    ml="${1:-250}"
    echo ">> Syncing intake to ${ml} mL..."
    curl -4 -s -X POST "${HA_URL}/commands/intake" \
      -H "Content-Type: application/json" \
      -d "{\"ml\": $ml}"
    echo ""
    ;;
  goal)
    ml="${1:-2500}"
    echo ">> Setting daily goal to ${ml} mL..."
    curl -4 -s -X POST "${HA_URL}/commands/goal" \
      -H "Content-Type: application/json" \
      -d "{\"ml\": $ml}"
    echo ""
    ;;
  raw)
    hex="$*"
    if [ -z "$hex" ]; then
      echo "Usage: ./send_cmd.sh raw <hex bytes>"
      echo "Example: ./send_cmd.sh raw 50 54 00 03 02 1d 01"
      exit 1
    fi
    echo ">> Sending raw hex: $hex..."
    curl -4 -s -X POST "${HA_URL}/commands/raw" \
      -H "Content-Type: application/json" \
      -d "{\"hex\": \"$hex\"}"
    echo ""
    ;;
  status)
    echo ">> Connection status:"
    curl -4 -s "${HA_URL}/api/status"
    echo ""
    echo ">> Today's intake:"
    curl -4 -s "${HA_URL}/api/today"
    echo ""
    ;;
  *)
    echo "💧 WaterH Bottle Command Sender"
    echo "Usage: ./send_cmd.sh <command> [args]"
    echo ""
    echo "Available commands:"
    echo "  ./send_cmd.sh flash                  - Flash bottle LED"
    echo "  ./send_cmd.sh led [mode] [color]     - Set LED (default, breathe, calm, rainbow, warmth, christmas; e.g. ./send_cmd.sh led rainbow blue)"
    echo "  ./send_cmd.sh intake <ml>            - Update intake display (e.g. ./send_cmd.sh intake 500)"
    echo "  ./send_cmd.sh goal <ml>              - Set daily goal (e.g. ./send_cmd.sh goal 2500)"
    echo "  ./send_cmd.sh raw <hex>              - Send arbitrary hex bytes (e.g. ./send_cmd.sh raw 50 54 00 03 02 1d 01)"
    echo "  ./send_cmd.sh status                 - Check bottle connection status and intake"
    ;;
esac
