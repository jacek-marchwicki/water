#!/bin/bash
# Deploy WaterH Add-on to Home Assistant via SSH
set -e

HA_HOST="${HA_HOST:-root@homeassistant.local}"
HA_PORT="${HA_PORT:-22}"
DEST_DIR="${DEST_DIR:-/addons/waterh-collector}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo " WaterH Add-on Deployment to Home Assistant"
echo "=========================================="
echo " Target Host: ${HA_HOST}"
echo " SSH Port:    ${HA_PORT}"
echo " Destination: ${DEST_DIR}"
echo "------------------------------------------"

# Sync single source of truth collector.py into waterh-collector package
echo "--> Syncing single-source-of-truth collector/collector.py..."
mkdir -p "$SCRIPT_DIR/waterh-collector/collector"
cp "$SCRIPT_DIR/collector/collector.py" "$SCRIPT_DIR/waterh-collector/collector/collector.py"

echo "--> Copying add-on files via tar stream over SSH..."
ssh -p "${HA_PORT}" "${HA_HOST}" "mkdir -p '${DEST_DIR}'"
tar -czf - -C "$SCRIPT_DIR/waterh-collector" . | ssh -p "${HA_PORT}" "${HA_HOST}" "tar -xzf - -C '${DEST_DIR}'"

echo "------------------------------------------"
echo "✔ Copy completed successfully!"
echo "------------------------------------------"
echo "Next steps in Home Assistant:"
echo " 1. Go to Settings -> Add-ons -> Add-on Store"
echo " 2. Click top-right menu (⋮) -> 'Check for new add-ons'"
echo " 3. Find 'WaterH Smart Bottle Collector' under Local Add-ons"
echo " 4. Configure your bottle MAC address & click Start!"
echo "=========================================="
