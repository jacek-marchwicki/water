#!/bin/bash
set -e

CONFIG_PATH=/data/options.json

if [ -f "$CONFIG_PATH" ]; then
    echo "[WaterH Add-on] Loading options from /data/options.json"
    export WATERH_ADDR=$(jq -r '.bottle_address // "A4:C1:38:32:D7:DE"' $CONFIG_PATH)
    export WATERH_POLL_INTERVAL=$(jq -r '.poll_interval // 60' $CONFIG_PATH)
    export WATERH_GOAL_ML=$(jq -r '.goal_ml // 2500' $CONFIG_PATH)
    export MQTT_HOST=$(jq -r '.mqtt_host // "core-mosquitto"' $CONFIG_PATH)
    export MQTT_PORT=$(jq -r '.mqtt_port // 1883' $CONFIG_PATH)
    export MQTT_USER=$(jq -r '.mqtt_user // ""' $CONFIG_PATH)
    export MQTT_PASSWORD=$(jq -r '.mqtt_password // ""' $CONFIG_PATH)
    export MQTT_TOPIC_PREFIX=$(jq -r '.mqtt_topic_prefix // "homeassistant"' $CONFIG_PATH)

    ENABLE_API_PUSH=$(jq -r '.enable_api_push // false' $CONFIG_PATH)
    if [ "$ENABLE_API_PUSH" = "true" ]; then
        export WATERH_API_URL=$(jq -r '.api_url // ""' $CONFIG_PATH)
        export WATERH_API_TOKEN=$(jq -r '.api_token // ""' $CONFIG_PATH)
    else
        export WATERH_API_URL=""
        export WATERH_API_TOKEN=""
    fi
else
    echo "[WaterH Add-on] /data/options.json not found, using environment defaults"
fi

echo "[WaterH Add-on] Starting WaterH Collector for bottle: ${WATERH_ADDR}"
exec python3 /app/collector/collector.py
