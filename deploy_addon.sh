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

# Ensure collector.py is copied into waterh-collector/collector/
echo "--> Syncing latest collector code into add-on directory..."
mkdir -p "$SCRIPT_DIR/waterh-collector/collector"
cp "$SCRIPT_DIR/collector/collector.py" "$SCRIPT_DIR/waterh-collector/collector/collector.py"

# Check if rsync is available on BOTH local and remote machines
if command -v rsync >/dev/null 2>&1 && ssh -o ConnectTimeout=5 -p "${HA_PORT}" "${HA_HOST}" "command -v rsync" >/dev/null 2>&1; then
    echo "--> Copying add-on files via rsync..."
    rsync -avz -e "ssh -p ${HA_PORT}" --delete \
        "$SCRIPT_DIR/waterh-collector/" \
        "${HA_HOST}:${DEST_DIR}/"
else
    echo "--> Copying add-on files via tar stream over SSH..."
    ssh -p "${HA_PORT}" "${HA_HOST}" "mkdir -p '${DEST_DIR}'"
    tar -czf - -C "$SCRIPT_DIR/waterh-collector" . | ssh -p "${HA_PORT}" "${HA_HOST}" "tar -xzf - -C '${DEST_DIR}'"
fi

echo "------------------------------------------"
echo "✔ Copy completed successfully!"
echo "------------------------------------------"
echo "Next steps in Home Assistant:"
echo " 1. Go to Settings -> Add-ons -> Add-on Store"
echo " 2. Click top-right menu (⋮) -> 'Check for new add-ons'"
echo " 3. Find 'WaterH Smart Bottle Collector' under Local Add-ons"
echo " 4. Configure your bottle MAC address & click Start!"
echo "=========================================="
