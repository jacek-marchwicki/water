# WaterH Smart Bottle Collector for Home Assistant

This add-on connects to your **WaterH Smart Water Bottle** over Bluetooth Low Energy (BLE), syncs hydration history, battery level, water temperature, and TDS (water quality), and exposes everything directly to Home Assistant using MQTT Auto-Discovery.

---

## 🚀 Features

- **Automatic Entities**:
  - `sensor.waterh_today_intake`: Total hydration intake today in mL (`total_increasing` sensor).
  - `sensor.waterh_battery`: Bottle battery level percentage (`battery`).
  - `sensor.waterh_water_temperature`: Current water temperature in °C (`temperature`).
  - `sensor.waterh_water_quality_tds`: Total Dissolved Solids in ppm.
  - `sensor.waterh_daily_goal`: Daily target water goal in mL.
  - `sensor.waterh_collector_status`: Real-time BLE connection state.
  - `button.waterh_flash_led`: Trigger bottle LED light effect on demand.
  - `select.waterh_led_mode`: Dropdown to change bottle LED lighting scheme (Default, Breathe, Calm, Rainbow, Warmth, Christmas).
  - `number.waterh_set_goal`: Update daily intake goal directly from Home Assistant.
  - `button.waterh_recalibrate_sensor`: Recalibrate internal water level sensor.

---

## 🛠️ Requirements

1. **Mosquitto MQTT Broker**: Install the official **Mosquitto broker** add-on in Home Assistant if not already running.
2. **Raspberry Pi Onboard Bluetooth**: Ensure your Raspberry Pi's Bluetooth adapter is enabled.

---

## 📱 Finding Your Bottle MAC Address

You can easily locate your WaterH bottle's Bluetooth MAC address:
1. **WaterH Mobile App**: Open the official **WaterH** app on your iOS or Android phone -> open device settings -> scroll all the way to the **bottom of the screen** to see your bottle's Bluetooth MAC address (e.g. `A4:C1:38:F3:63:2D`).
2. **nRF Connect App**: Open **nRF Connect for Mobile**, scan for nearby Bluetooth devices, and locate `WaterH-Boost` or `WaterH`.

---

## ⚙️ Configuration Options

| Option | Type | Default | Description |
|---|---|---|---|
| `bottle_address` | String | `A4:C1:38:F3:63:2D` | Bluetooth MAC Address of your WaterH bottle (found at bottom of screen in WaterH app). |
| `poll_interval` | Integer | `60` | Polling interval in seconds between sync cycles. |
| `mqtt_host` | String | `core-mosquitto` | Address of your MQTT broker (`core-mosquitto` for built-in HA broker). |
| `mqtt_port` | Integer | `1883` | MQTT port. |
| `mqtt_user` | String | `""` | MQTT username (auto-provisioned by Home Assistant if left blank). |
| `mqtt_password` | String | `""` | MQTT password. |

---

## 📋 Installation Steps

1. Copy the `waterh-collector` directory into your Home Assistant `/addons/` folder (or deploy via `./deploy_addon.sh`).
2. Go to **Settings -> Add-ons -> Add-on Store**.
3. Click the **⋮ (Menu)** in the top-right corner -> **Check for new add-ons**.
4. Scroll down to **Local Add-ons** and select **WaterH Smart Bottle Collector**.
5. Set your bottle's Bluetooth MAC address in the **Configuration** tab.
6. Click **Start**!
