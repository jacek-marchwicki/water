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

# Sync single source of truth collector.py and frontend directory
echo "--> Syncing single-source-of-truth collector.py & frontend directory..."
mkdir -p "$SCRIPT_DIR/waterh-collector/collector"
cp "$SCRIPT_DIR/collector/collector.py" "$SCRIPT_DIR/waterh-collector/collector/collector.py"
rm -rf "$SCRIPT_DIR/waterh-collector/frontend"
cp -r "$SCRIPT_DIR/frontend" "$SCRIPT_DIR/waterh-collector/frontend"

echo "--> Copying add-on files via tar stream over SSH..."
ssh -p "${HA_PORT}" "${HA_HOST}" "mkdir -p '${DEST_DIR}' /local_apps/waterh-collector"
tar -czf - -C "$SCRIPT_DIR/waterh-collector" . | ssh -p "${HA_PORT}" "${HA_HOST}" "tar -xzf - -C '${DEST_DIR}' && cp -r '${DEST_DIR}/.' /local_apps/waterh-collector/"

echo "------------------------------------------"
echo "✔ Copy completed successfully!"
echo "------------------------------------------"

echo "--> Reloading Supervisor app store..."
ssh -p "${HA_PORT}" "${HA_HOST}" "ha store reload || true"

echo "--> Checking WaterH Collector app status..."
INFO_JSON=$(ssh -p "${HA_PORT}" "${HA_HOST}" "ha apps info local_waterh_collector --raw-json 2>/dev/null || ha addons info local_waterh_collector --raw-json 2>/dev/null || true")

if echo "$INFO_JSON" | grep -q '"update_available":true'; then
    echo "--> Update detected! Upgrading WaterH Collector..."
    ssh -p "${HA_PORT}" "${HA_HOST}" "ha apps update local_waterh_collector 2>/dev/null || ha addons update local_waterh_collector"
else
    echo "--> Rebuilding WaterH Collector with latest code..."
    ssh -p "${HA_PORT}" "${HA_HOST}" "ha apps rebuild local_waterh_collector 2>/dev/null || ha addons rebuild local_waterh_collector"
fi

echo "------------------------------------------"
echo "✔ App update and restart complete!"
echo "=========================================="
