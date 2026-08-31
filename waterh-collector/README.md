# WaterH Smart Bottle Collector (Home Assistant Add-on)

This add-on connects to your WaterH Smart Water Bottle over BLE and exposes sensors and controls in Home Assistant via MQTT Auto-Discovery.

## Quick Deployment via SSH

From the repo root, run:

```bash
./deploy_addon.sh
```

If your Home Assistant SSH port or host differs:

```bash
HA_HOST="root@192.168.1.100" HA_PORT=22222 ./deploy_addon.sh
```
