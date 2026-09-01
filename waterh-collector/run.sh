#!/bin/bash
set -e

CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
    echo "[WaterH Add-on] Loading options from /data/options.json"
    export WATERH_ADDR=$(jq -r '.bottle_address // "A4:C1:38:32:D7:DE"' $CONFIG_PATH)
    export WATERH_POLL_INTERVAL=$(jq -r '.poll_interval // 60' $CONFIG_PATH)
    export WATERH_GOAL_ML=$(jq -r '.goal_ml // 2500' $CONFIG_PATH)
    export WATERH_DB_PATH="/data/waterh.db"
    export MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' $CONFIG_PATH)
    export MQTT_PORT=$(jq -r '.mqtt_port // 1883' $CONFIG_PATH)
    export MQTT_USER=$(jq -r '.mqtt_user // ""' $CONFIG_PATH)
    export MQTT_PASSWORD=$(jq -r '.mqtt_password // ""' $CONFIG_PATH)
    export MQTT_TOPIC_PREFIX=$(jq -r '.mqtt_topic_prefix // "homeassistant"' $CONFIG_PATH)


else
    echo "[WaterH Add-on] /data/options.json not found, using environment defaults"
fi

# Auto-detect MQTT credentials provided by Home Assistant Supervisor services
SERVICES_PATH=/data/services.json
if [ -f "$SERVICES_PATH" ] && jq -e '.mqtt' "$SERVICES_PATH" >/dev/null 2>&1; then
    echo "[WaterH Add-on] Auto-detecting MQTT credentials from Home Assistant Supervisor..."
    SVC_HOST=$(jq -r '.mqtt.host // "core-mosquitto"' "$SERVICES_PATH")
    SVC_PORT=$(jq -r '.mqtt.port // 1883' "$SERVICES_PATH")
    SVC_USER=$(jq -r '.mqtt.username // ""' "$SERVICES_PATH")
    SVC_PASS=$(jq -r '.mqtt.password // ""' "$SERVICES_PATH")

    if [ -z "$MQTT_USER" ]; then
        export MQTT_USER="$SVC_USER"
    fi
    if [ -z "$MQTT_PASSWORD" ]; then
        export MQTT_PASSWORD="$SVC_PASS"
    fi
    if [ "$MQTT_HOST" = "core-mosquitto" ] || [ -z "$MQTT_HOST" ]; then
        export MQTT_HOST="$SVC_HOST"
    fi
    if [ -z "$MQTT_PORT" ]; then
        export MQTT_PORT="$SVC_PORT"
    fi
fi

echo "[WaterH Add-on] Starting WaterH Collector for bottle: ${WATERH_ADDR}"
exec python3 /app/collector/collector.py
