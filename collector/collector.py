#!/usr/bin/env python3
"""
WaterH BLE Collector — full app-protocol implementation.

Replicates the official WaterH app's sync flow:
  1. Clean BlueZ state (remove stale connections)
  2. Connect (no pairing)
  3. Request bottle info
  4. Sync time + goal + reminder settings
  5. Request water logs (sip history)
  6. Ack received logs (clears them from bottle storage)
  7. Push new sips to remote API
"""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

try:
    import paho.mqtt.client as mqtt
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("waterh")

# --- Config ---
BOTTLE_ADDR = os.environ.get("WATERH_ADDR", "A4:C1:38:32:D7:DE")
NOTIFY_CHAR = "0000ffe4-0000-1000-8000-00805f9b34fb"
WRITE_CHAR = "0000ffe9-0000-1000-8000-00805f9b34fb"
POLL_INTERVAL = int(os.environ.get("WATERH_POLL_INTERVAL", "60"))
GOAL_ML = int(os.environ.get("WATERH_GOAL_ML", "2500"))
API_URL = os.environ.get("WATERH_API_URL", "https://water.syl.rest/api/ingest")
HEARTBEAT_URL = os.environ.get("WATERH_HEARTBEAT_URL", "https://water.syl.rest/api/heartbeat")
API_TOKEN = os.environ.get("WATERH_API_TOKEN", "")
DB_PATH = os.environ.get(
    "WATERH_DB_PATH",
    "/data/waterh.db" if Path("/data").exists() else str(Path(__file__).parent / "waterh.db")
)

CMD_PORT = int(os.environ.get("WATERH_CMD_PORT", "7700"))

# MQTT Settings
MQTT_HOST = os.environ.get("MQTT_HOST", "")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "homeassistant")

MAX_SCAN_FAILURES = 3
MAX_EMPTY_POLLS = 5
BACKOFF_BASE = 5
BACKOFF_CAP = 30



# --- Protocol commands ---

def cmd_bottle_data() -> bytes:
    return bytes.fromhex("47540001ff")


def cmd_sync_settings(goal_ml: int = GOAL_ML) -> bytes:
    now = datetime.now()
    goal_hex = f"{goal_ml:04x}"
    reminder_hex = "00080014003c"
    time_hex = (
        f"{(now.year - 2000):02x}"
        f"{now.month:02x}"
        f"{now.day:02x}"
        f"{now.hour:02x}"
        f"{now.minute:02x}"
        f"{now.second:02x}"
    )
    return bytes.fromhex(f"505400140305{goal_hex}0703{time_hex}0726{reminder_hex}")


def cmd_request_water_logs() -> bytes:
    return bytes.fromhex("4754000106")


def cmd_ack_water_logs(total_bytes: int) -> bytes:
    return bytes.fromhex(f"525000040306{total_bytes:04x}")


def cmd_sync_today_amount(ml: int) -> bytes:
    return bytes.fromhex(f"505400040304{ml:04x}")


def cmd_clear_offline() -> bytes:
    return bytes.fromhex("50540003021c05")


def cmd_flash_led() -> bytes:
    return bytes.fromhex("50540003021d01")


def cmd_set_led(mode: str, color: str) -> bytes:
    modes = {
        "default": "00", "breathe": "01", "calm": "02",
        "rainbow": "03", "warmth": "05", "christmas": "06",
    }
    colors = {
        "red": "ff0000", "yellow": "ffff00", "green": "00ff00",
        "cyan": "00ffff", "blue": "0000ff", "purple": "ff00ff",
        "white": "ffffff",
    }
    mode_hex = modes.get(mode, "00")
    # Accept named color or raw hex
    color_hex = colors.get(color, color if len(color) == 6 else "0000ff")
    return bytes.fromhex(f"5054000605fb{mode_hex}{color_hex}")


def cmd_set_reminder(on: bool, wake_h: int, wake_m: int, sleep_h: int, sleep_m: int, interval_min: int) -> bytes:
    type_byte = "01" if on else "00"
    return bytes.fromhex(
        f"505400080726{type_byte}"
        f"{wake_h:02x}{wake_m:02x}"
        f"{sleep_h:02x}{sleep_m:02x}"
        f"{interval_min:02x}"
    )


def cmd_set_goal(ml: int) -> bytes:
    return bytes.fromhex(f"505400040305{ml:04x}")


def cmd_recalibrate(full: bool) -> bytes:
    return bytes.fromhex("5054000302A101" if full else "5054000302A601")


# --- Home Assistant MQTT Integration ---

class HAConnection:
    def __init__(self, host: str, port: int, user: str, password: str, prefix: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.prefix = prefix
        self.client = None
        self.connected = False
        self.loop = None
        self.cmd_queue = None

    def start(self, loop: asyncio.AbstractEventLoop, cmd_queue: asyncio.Queue):
        if not HAS_MQTT:
            log.info("[MQTT] paho-mqtt package not installed, MQTT integration disabled")
            return

        # Auto-fetch MQTT credentials from Supervisor Services API if missing
        if not self.user or not self.password:
            token = get_supervisor_token()
            if token:
                svc_host, svc_port, svc_user, svc_pass = fetch_mqtt_service_credentials(token)
                if svc_user and svc_pass:
                    self.user = self.user or svc_user
                    self.password = self.password or svc_pass
                    if not self.host or self.host == "core-mosquitto":
                        self.host = svc_host
                    if svc_port:
                        self.port = svc_port

        if not self.host:
            log.info("[MQTT] MQTT_HOST not configured, MQTT integration disabled")
            return

        self.loop = loop
        self.cmd_queue = cmd_queue
        if hasattr(mqtt, "CallbackAPIVersion"):
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id="waterh_collector")
        else:
            self.client = mqtt.Client(client_id="waterh_collector")

        if self.user:
            self.client.username_pw_set(self.user, self.password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        try:
            self.client.connect_async(self.host, self.port, keepalive=60)
            self.client.loop_start()
            log.info(f"[MQTT] Connecting to broker at {self.host}:{self.port}...")
        except Exception as e:
            log.error(f"[MQTT] Failed to start MQTT client: {e}")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("[MQTT] Connected to MQTT broker successfully")
            self.connected = True
            self.publish_discovery()
            self.client.subscribe("waterh/cmd/#")
        else:
            reasons = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized (Check MQTT username & password in Add-on Configuration)",
            }
            reason_str = reasons.get(rc, f"Code {rc}")
            log.error(f"[MQTT] Connection to broker failed: {reason_str}")

    def _on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode(errors="ignore").strip()
        log.info(f"[MQTT] Received command on {topic}: {payload}")

        if topic == "waterh/cmd/flash":
            if self.cmd_queue and self.loop:
                self.loop.call_soon_threadsafe(
                    self.cmd_queue.put_nowait, (cmd_flash_led(), "flash (MQTT)")
                )
        elif topic == "waterh/cmd/led_mode":
            if self.cmd_queue and self.loop:
                self.loop.call_soon_threadsafe(
                    self.cmd_queue.put_nowait, (cmd_set_led(payload, "blue"), f"led {payload} (MQTT)")
                )
                self.publish_state("select/led_mode", payload)
        elif topic == "waterh/cmd/set_goal":
            try:
                ml = int(payload)
                if self.cmd_queue and self.loop:
                    self.loop.call_soon_threadsafe(
                        self.cmd_queue.put_nowait, (cmd_set_goal(ml), f"goal {ml}ml (MQTT)")
                    )
                    self.publish_state("sensor/daily_goal", ml)
            except ValueError:
                pass
        elif topic == "waterh/cmd/recalibrate":
            if self.cmd_queue and self.loop:
                self.loop.call_soon_threadsafe(
                    self.cmd_queue.put_nowait, (cmd_recalibrate(True), "recalibrate (MQTT)")
                )

    def publish_discovery(self):
        device_info = {
            "identifiers": ["waterh_bottle_boost"],
            "name": "WaterH Smart Bottle",
            "model": "Boost 24oz",
            "manufacturer": "WaterH",
        }

        discovery_configs = [
            ("sensor", "today_intake", {
                "name": "WaterH Today Intake",
                "unique_id": "waterh_today_intake",
                "state_topic": "waterh/sensor/today_intake/state",
                "unit_of_measurement": "mL",
                "device_class": "water",
                "state_class": "total_increasing",
                "icon": "mdi:cup-water",
                "device": device_info,
            }),
            ("sensor", "battery", {
                "name": "WaterH Battery",
                "unique_id": "waterh_battery",
                "state_topic": "waterh/sensor/battery/state",
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
                "device": device_info,
            }),
            ("sensor", "temperature", {
                "name": "WaterH Water Temperature",
                "unique_id": "waterh_temperature",
                "state_topic": "waterh/sensor/temperature/state",
                "unit_of_measurement": "°C",
                "device_class": "temperature",
                "state_class": "measurement",
                "device": device_info,
            }),
            ("sensor", "tds", {
                "name": "WaterH Water Quality (TDS)",
                "unique_id": "waterh_tds",
                "state_topic": "waterh/sensor/tds/state",
                "unit_of_measurement": "ppm",
                "icon": "mdi:water-check",
                "state_class": "measurement",
                "device": device_info,
            }),
            ("sensor", "daily_goal", {
                "name": "WaterH Daily Goal",
                "unique_id": "waterh_daily_goal",
                "state_topic": "waterh/sensor/daily_goal/state",
                "unit_of_measurement": "mL",
                "icon": "mdi:target-variant",
                "device": device_info,
            }),
            ("sensor", "status", {
                "name": "WaterH Collector Status",
                "unique_id": "waterh_status",
                "state_topic": "waterh/sensor/status/state",
                "icon": "mdi:bluetooth-connect",
                "device": device_info,
            }),
            ("button", "flash_led", {
                "name": "WaterH Flash LED",
                "unique_id": "waterh_flash_led",
                "command_topic": "waterh/cmd/flash",
                "icon": "mdi:led-on",
                "device": device_info,
            }),
            ("button", "recalibrate", {
                "name": "WaterH Recalibrate Sensor",
                "unique_id": "waterh_recalibrate",
                "command_topic": "waterh/cmd/recalibrate",
                "icon": "mdi:scale-balance",
                "device": device_info,
            }),
            ("select", "led_mode", {
                "name": "WaterH LED Mode",
                "unique_id": "waterh_led_mode",
                "command_topic": "waterh/cmd/led_mode",
                "state_topic": "waterh/select/led_mode/state",
                "options": ["default", "breathe", "calm", "rainbow", "warmth", "christmas"],
                "icon": "mdi:palette",
                "device": device_info,
            }),
            ("number", "set_goal", {
                "name": "WaterH Set Goal",
                "unique_id": "waterh_set_goal",
                "command_topic": "waterh/cmd/set_goal",
                "state_topic": "waterh/sensor/daily_goal/state",
                "min": 500,
                "max": 5000,
                "step": 50,
                "unit_of_measurement": "mL",
                "icon": "mdi:target",
                "device": device_info,
            }),
        ]

        for domain, object_id, config in discovery_configs:
            disc_topic = f"{self.prefix}/{domain}/waterh/{object_id}/config"
            self.client.publish(disc_topic, json.dumps(config), retain=True)

        # Publish baseline state values so HA entities immediately populate
        self.publish_state("sensor/daily_goal", GOAL_ML)
        self.publish_state("select/led_mode", "default")
        self.publish_state("sensor/status", "scanning")

    def publish_state(self, entity_subpath: str, value):
        if self.client:
            topic = f"waterh/{entity_subpath}/state"
            self.client.publish(topic, str(value), retain=True)


ha_conn: HAConnection | None = None


# --- Home Assistant Direct REST API & Services Integration ---

def get_supervisor_token() -> str:
    token = (
        os.environ.get("SUPERVISOR_TOKEN")
        or os.environ.get("HASSIO_TOKEN")
        or os.environ.get("HOMEASSISTANT_TOKEN")
        or ""
    )
    if token:
        return token

    # Check s6-overlay container environment files in HA base image
    for path_str in [
        "/var/run/s6/container_environment/SUPERVISOR_TOKEN",
        "/run/s6/container_environment/SUPERVISOR_TOKEN",
        "/var/run/s6/container_environment/HASSIO_TOKEN",
        "/run/s6/container_environment/HASSIO_TOKEN",
    ]:
        try:
            p = Path(path_str)
            if p.is_file():
                val = p.read_text().strip()
                if val:
                    log.info(f"[INIT] Loaded Supervisor token from {path_str}")
                    return val
        except Exception:
            pass

    return ""


def fetch_mqtt_service_credentials(token: str) -> tuple[str, int, str, str]:
    if not token:
        return ("", 0, "", "")

    url = "http://supervisor/services/mqtt"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") == "ok" and "data" in data:
                svc = data["data"]
                host = svc.get("host", "core-mosquitto")
                port = int(svc.get("port", 1883))
                user = svc.get("username", "")
                password = svc.get("password", "")
                log.info(f"[MQTT] Auto-retrieved Supervisor MQTT credentials (user: {user})")
                return (host, port, user, password)
    except Exception as e:
        log.warning(f"[MQTT] Could not fetch Supervisor MQTT service credentials: {e}")

    return ("", 0, "", "")


SUPERVISOR_TOKEN = get_supervisor_token()

class HARestAPI:
    def __init__(self, token: str):
        self.token = token
        self.base_urls = ["http://supervisor/core/api/states", "http://172.30.32.1/api/states"]

    def update_sensor(
        self,
        entity_name: str,
        state_value,
        unit: str = "",
        friendly_name: str = "",
        icon: str = "",
        device_class: str = "",
        state_class: str = ""
    ):
        if not self.token:
            return

        entity_id = f"sensor.waterh_{entity_name}"
        attributes = {}
        if friendly_name:
            attributes["friendly_name"] = friendly_name
        if unit:
            attributes["unit_of_measurement"] = unit
        if icon:
            attributes["icon"] = icon
        if device_class:
            attributes["device_class"] = device_class
        if state_class:
            attributes["state_class"] = state_class

        payload = json.dumps({
            "state": str(state_value),
            "attributes": attributes
        }).encode("utf-8")

        for base_url in self.base_urls:
            req = urllib.request.Request(
                f"{base_url}/{entity_id}",
                data=payload,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json"
                },
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    log.info(f"[HA-API] Direct API updated {entity_id} = {state_value}")
                    return
            except Exception as e:
                log.warning(f"[HA-API] Failed to update {entity_id} via {base_url}: {e}")

ha_api = HARestAPI(SUPERVISOR_TOKEN) if SUPERVISOR_TOKEN else None


# --- Command queue (shared between HTTP server and BLE loop) ---
# Initialized in ble_loop() once the event loop is running.

cmd_queue: asyncio.Queue[tuple[bytes, str]] | None = None


# --- Command HTTP server & Ingress Web UI ---

def send_json(writer: asyncio.StreamWriter, status: int, data: dict):
    body = json.dumps(data).encode()
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"\r\n".encode() + body
    )


def send_html(writer: asyncio.StreamWriter, status: int, html: str):
    body = html.encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status} OK\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Cache-Control: no-cache, no-store, must-revalidate\r\n"
        f"Pragma: no-cache\r\n"
        f"Expires: 0\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"\r\n".encode("utf-8") + body
    )


HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WaterH Smart Bottle Dashboard</title>
<style>
  :root { --bg: #0f172a; --card: #1e293b; --accent: #38bdf8; --text: #f8fafc; --text-muted: #94a3b8; --border: #334155; }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
  body { background: var(--bg); color: var(--text); padding: 20px; display: flex; justify-content: center; }
  .container { max-width: 800px; width: 100%; display: flex; flex-direction: column; gap: 20px; }
  header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
  h1 { font-size: 1.5rem; display: flex; align-items: center; gap: 8px; }
  .badge { background: #0284c7; color: #fff; font-size: 0.75rem; padding: 4px 10px; border-radius: 99px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 15px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; display: flex; flex-direction: column; gap: 8px; }
  .card-label { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .card-val { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
  .unit { font-size: 1rem; color: var(--text-muted); font-weight: 400; margin-left: 4px; }
  .ring-container { display: flex; flex-direction: column; align-items: center; padding: 20px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; }
  .ring-svg { width: 180px; height: 180px; transform: rotate(-90deg); }
  .ring-bg { fill: none; stroke: var(--border); stroke-width: 14; }
  .ring-fill { fill: none; stroke: var(--accent); stroke-width: 14; stroke-linecap: round; stroke-dasharray: 440; stroke-dashoffset: 440; transition: stroke-dashoffset 1s ease; }
  .ring-text { position: absolute; text-align: center; margin-top: 55px; }
  .controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; }
  button { background: #0284c7; color: white; border: none; padding: 12px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; transition: 0.2s; }
  button:hover { background: #0369a1; }
  select { background: #0f172a; color: white; border: 1px solid var(--border); padding: 10px; border-radius: 8px; width: 100%; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th, td { text-align: left; padding: 10px; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
  th { color: var(--text-muted); }
  .quick-buttons { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 15px; }
  .quick-btn { background: #0f172a; border: 1px solid #334155; color: #f8fafc; padding: 12px 10px; border-radius: 10px; font-size: 0.95rem; text-align: center; cursor: pointer; transition: all 0.2s; font-weight: 600; }
  .quick-btn:hover { background: #0284c7; border-color: #38bdf8; transform: translateY(-2px); }
  .subtext { font-size: 0.75rem; color: #94a3b8; font-weight: 400; display: block; margin-top: 2px; }
  .quick-btn:hover .subtext { color: #e0f2fe; }
  .slider-box { background: #0f172a; padding: 16px; border-radius: 10px; border: 1px solid #334155; }
  input[type=range] { width: 100%; accent-color: #38bdf8; cursor: pointer; height: 8px; border-radius: 4px; background: #334155; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>💧 WaterH Smart Bottle</h1>
    <span id="mac-badge" class="badge">A4:C1:38:F3:63:2D</span>
  </header>

  <div class="ring-container">
    <div style="position:relative; width:180px; height:180px;">
      <svg class="ring-svg" viewBox="0 0 160 160">
        <circle class="ring-bg" cx="80" cy="80" r="70"></circle>
        <circle id="ring" class="ring-fill" cx="80" cy="80" r="70"></circle>
      </svg>
      <div class="ring-text">
        <div id="ml-val" style="font-size: 2rem; font-weight: 700; color: #38bdf8;">0</div>
        <div style="font-size: 0.85rem; color: #94a3b8;">/ <span id="goal-val">2500</span> mL</div>
      </div>
    </div>
  </div>

  <div class="grid">
    <div class="card"><div class="card-label">Progress</div><div class="card-val"><span id="pct-val">0</span><span class="unit">%</span></div></div>
    <div class="card"><div class="card-label">Total Sips</div><div class="card-val"><span id="sips-val">0</span></div></div>
    <div class="card"><div class="card-label">Goal Target</div><div class="card-val"><span id="target-val">2500</span><span class="unit">mL</span></div></div>
  </div>

  <div class="card">
    <h3 style="font-size: 1.1rem; margin-bottom: 12px;">➕ Log Drink Manually</h3>
    
    <div style="font-size: 0.75rem; color: #94a3b8; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Quick Log</div>
    <div class="quick-buttons">
      <button class="quick-btn" onclick="logSip(120)">☕ Small Cup<span class="subtext">120 mL</span></button>
      <button class="quick-btn" onclick="logSip(200)">🥛 Large Cup<span class="subtext">200 mL</span></button>
      <button class="quick-btn" onclick="logSip(310)">🍵 Mug<span class="subtext">310 mL</span></button>
      <button class="quick-btn" onclick="logSip(700)">🚴 Bidon<span class="subtext">700 mL</span></button>
    </div>

    <div style="font-size: 0.75rem; color: #94a3b8; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Custom Amount</div>
    <div class="slider-box">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="color:#94a3b8; font-size:0.9rem;">Select Amount:</span>
        <span style="font-size:1.4rem; font-weight:700; color:#38bdf8;"><span id="slider-val">250</span> <span style="font-size:0.9rem; color:#94a3b8;">mL</span></span>
      </div>
      <input type="range" id="custom-slider" min="50" max="1000" step="10" value="250" oninput="updateSliderText(this.value)">
      <div style="display:flex; justify-content:space-between; font-size:0.75rem; color:#64748b; margin-top:6px;">
        <span>50 mL</span>
        <span>500 mL</span>
        <span>1000 mL</span>
      </div>
      <button style="margin-top: 14px; width: 100%; background: #0284c7;" onclick="logCustomSip()">💧 Log Custom Drink</button>
    </div>
  </div>

  <div class="card">
    <h3 style="font-size: 1.1rem; margin-bottom: 12px;">⚡ Interactive Controls</h3>
    <div class="controls">
      <button onclick="flashLED()">💡 Flash LED</button>
      <div style="display:flex; gap:8px;">
        <select id="led-select" onchange="setLED()">
          <option value="default">LED Mode: Default</option>
          <option value="breathe">LED Mode: Breathe</option>
          <option value="rainbow">LED Mode: Rainbow</option>
          <option value="off">LED Mode: Off</option>
        </select>
      </div>
    </div>
  </div>

  <div class="card">
    <h3 style="font-size: 1.1rem; margin-bottom: 12px;">📊 Today's Sip History</h3>
    <table>
      <thead><tr><th>Time</th><th>Amount</th><th>Temp</th><th>TDS</th></tr></thead>
      <tbody id="sip-rows"><tr><td colspan="4" style="color: #94a3b8;">Loading sips...</td></tr></tbody>
    </table>
  </div>
</div>

<script>
async function loadData() {
  try {
    const res = await fetch('./api/data');
    const data = await res.json();
    document.getElementById('ml-val').innerText = data.today_ml;
    document.getElementById('goal-val').innerText = data.goal_ml;
    document.getElementById('pct-val').innerText = data.pct;
    document.getElementById('sips-val').innerText = data.sips_count;
    document.getElementById('target-val').innerText = data.goal_ml;
    if (data.bottle) document.getElementById('mac-badge').innerText = data.bottle;
    
    const circ = 440;
    const offset = circ - (Math.min(data.pct, 100) / 100) * circ;
    document.getElementById('ring').style.strokeDashoffset = offset;

    const tbody = document.getElementById('sip-rows');
    if (data.sips && data.sips.length > 0) {
      tbody.innerHTML = data.sips.map(s => `
        <tr>
          <td>${new Date(s.timestamp).toLocaleTimeString()}</td>
          <td style="color:#38bdf8; font-weight:600;">+${s.intake_ml} mL</td>
          <td>${s.temp_c ? s.temp_c + ' °C' : '—'}</td>
          <td>${s.tds ? s.tds + ' ppm' : '—'}</td>
        </tr>
      `).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4" style="color: #94a3b8;">No sips logged today yet.</td></tr>';
    }
  } catch (e) {
    console.error("Fetch error", e);
  }
}

function updateSliderText(val) {
  document.getElementById('slider-val').innerText = val;
}

async function logSip(ml) {
  try {
    const res = await fetch('./commands/intake', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ml: ml })
    });
    const data = await res.json();
    if (data.ok) {
      loadData();
    }
  } catch (e) {
    console.error("Log sip error", e);
  }
}

async function logCustomSip() {
  const val = parseInt(document.getElementById('custom-slider').value);
  await logSip(val);
}

async function flashLED() {
  await fetch('./commands/flash', { method: 'POST' });
  alert("Flash command queued!");
}

async function setLED() {
  const mode = document.getElementById('led-select').value;
  await fetch('./commands/led', { method: 'POST', body: JSON.stringify({ mode: mode, color: 'blue' }) });
}

loadData();
setInterval(loadData, 5000);
</script>
</body>
</html>"""


async def handle_cmd_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Minimal HTTP handler for /commands endpoint."""
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        request_str = request_line.decode(errors="replace")
        # Read headers
        content_length = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":")[1].strip())

        body = b""
        if content_length > 0:
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=5)

        method = request_str.split(" ")[0] if request_str else ""
        path = request_str.split(" ")[1] if len(request_str.split(" ")) > 1 else "/"

        if method == "GET" and (path in ["/", "/index.html", "/ingress"] or path.startswith("/ingress")):
            send_html(writer, 200, HTML_DASHBOARD)

        elif method == "GET" and (path.startswith("/api/data") or path == "/api/data"):
            db = init_db()
            total_today = db.execute(
                "SELECT COALESCE(SUM(intake_ml), 0) FROM sips WHERE DATE(timestamp) = DATE('now')"
            ).fetchone()[0]
            rows = db.execute(
                "SELECT timestamp, intake_ml, temp_c, tds FROM sips ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()
            sips = [{"timestamp": r[0], "intake_ml": r[1], "temp_c": r[2], "tds": r[3]} for r in rows]
            resp = {
                "bottle": BOTTLE_ADDR,
                "today_ml": total_today,
                "goal_ml": GOAL_ML,
                "pct": round((total_today / GOAL_ML) * 100, 1) if GOAL_ML else 0,
                "sips_count": len(rows),
                "sips": sips,
            }
            send_json(writer, 200, resp)

        elif method == "GET" and path == "/commands":
            resp = {"commands": [
                "POST /commands/flash",
                "POST /commands/led    {mode, color}",
                "POST /commands/goal   {ml}",
                "POST /commands/intake {ml}",
                "POST /commands/reminder {on, wake, sleep, interval}",
                "POST /commands/calibrate {full}",
                "POST /commands/raw    {hex}",
            ]}
            send_json(writer, 200, resp)

        elif method == "POST" and path == "/commands/flash":
            cmd_queue.put_nowait((cmd_flash_led(), "flash"))
            send_json(writer, 200, {"ok": True, "queued": "flash"})

        elif method == "POST" and path == "/commands/led":
            data = json.loads(body) if body else {}
            mode = data.get("mode", "default")
            color = data.get("color", "blue")
            cmd_queue.put_nowait((cmd_set_led(mode, color), f"led {mode} {color}"))
            send_json(writer, 200, {"ok": True, "queued": f"led {mode} {color}"})

        elif method == "POST" and path == "/commands/goal":
            data = json.loads(body) if body else {}
            ml = int(data.get("ml", GOAL_ML))
            cmd_queue.put_nowait((cmd_set_goal(ml), f"goal {ml}ml"))
            send_json(writer, 200, {"ok": True, "queued": f"goal {ml}ml"})

        elif method == "POST" and path in ["/commands/intake", "/api/sips/manual"]:
            data = json.loads(body) if body else {}
            ml = int(data.get("ml", 0))
            if ml > 0:
                db = init_db()
                now_str = datetime.now().isoformat()
                db.execute(
                    "INSERT OR IGNORE INTO sips (timestamp, intake_ml, synced) VALUES (?, ?, 1)",
                    (now_str, ml)
                )
                db.commit()
                total_today = db.execute(
                    "SELECT COALESCE(SUM(intake_ml), 0) FROM sips WHERE DATE(timestamp) = DATE('now')"
                ).fetchone()[0]
                publish_ha_sensor("today_intake", total_today, unit="mL", friendly_name="WaterH Today Intake", icon="mdi:cup-water", device_class="water", state_class="total_increasing")
                if cmd_queue:
                    cmd_queue.put_nowait((cmd_sync_today_amount(total_today), f"intake {total_today}ml"))
                send_json(writer, 200, {"ok": True, "added_ml": ml, "today_total_ml": total_today})
            else:
                send_json(writer, 400, {"error": "invalid ml"})

        elif method == "POST" and path == "/commands/reminder":
            data = json.loads(body) if body else {}
            on = data.get("on", False)
            wake = data.get("wake", "08:00").split(":")
            slp = data.get("sleep", "20:00").split(":")
            interval = int(data.get("interval", 60))
            cmd = cmd_set_reminder(on, int(wake[0]), int(wake[1]), int(slp[0]), int(slp[1]), interval)
            label = f"reminder {'on' if on else 'off'}"
            cmd_queue.put_nowait((cmd, label))
            send_json(writer, 200, {"ok": True, "queued": label})

        elif method == "POST" and path == "/commands/calibrate":
            data = json.loads(body) if body else {}
            full = data.get("full", True)
            cmd_queue.put_nowait((cmd_recalibrate(full), f"calibrate {'full' if full else 'empty'}"))
            send_json(writer, 200, {"ok": True, "queued": f"calibrate {'full' if full else 'empty'}"})

        elif method == "POST" and path == "/commands/raw":
            data = json.loads(body) if body else {}
            hex_str = data.get("hex", "").replace(" ", "")
            if not hex_str:
                send_json(writer, 400, {"error": "missing hex"})
            else:
                cmd_queue.put_nowait((bytes.fromhex(hex_str), f"raw {hex_str}"))
                send_json(writer, 200, {"ok": True, "queued": f"raw {hex_str}"})

        else:
            send_json(writer, 404, {"error": "not found"})

    except Exception as e:
        log.error(f"[HTTP] Request error: {e}")
        try:
            send_json(writer, 500, {"error": str(e)})
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()


def send_json(writer: asyncio.StreamWriter, status: int, data: dict):
    body = json.dumps(data).encode()
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
    writer.write(
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"\r\n".encode() + body
    )


def publish_ha_sensor(
    entity_name: str,
    state_value,
    unit: str = "",
    friendly_name: str = "",
    icon: str = "",
    device_class: str = "",
    state_class: str = ""
):
    """Publish sensor state via MQTT if enabled; fallback to Direct REST API ONLY if MQTT is disabled."""
    if ha_conn:
        ha_conn.publish_state(f"sensor/{entity_name}", state_value)
    elif ha_api:
        ha_api.update_sensor(
            entity_name,
            state_value,
            unit=unit,
            friendly_name=friendly_name,
            icon=icon,
            device_class=device_class,
            state_class=state_class,
        )





async def start_cmd_server():
    server = await asyncio.start_server(handle_cmd_request, "0.0.0.0", CMD_PORT)
    log.info(f"[HTTP] Command server listening on :{CMD_PORT}")
    return server


# --- BlueZ cleanup (what Android does with gatt.close() + refreshDeviceCache) ---

def bluez_remove_device(addr: str):
    """Remove device from BlueZ to clear stale connections and GATT cache.
    This is the Linux equivalent of Android's gatt.close() + refreshDeviceCache().
    Without this, BlueZ can hold zombie connections that prevent the bottle
    from advertising."""
    log.info(f"[BLE] Clearing BlueZ state for {addr}")
    try:
        subprocess.run(
            ["bluetoothctl", "remove", addr],
            capture_output=True, timeout=5
        )
    except Exception:
        pass  # device might not exist in bluez, that's fine


def bluez_power_cycle():
    """Power cycle the Bluetooth adapter."""
    log.warning("[BLE] Power cycling Bluetooth adapter")
    try:
        subprocess.run(["bluetoothctl", "power", "off"], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(["bluetoothctl", "power", "on"], capture_output=True, timeout=5)
        time.sleep(2)
    except Exception as e:
        log.error(f"[BLE] Power cycle failed: {e}")


def bluez_full_reset(addr: str):
    """Full cleanup: remove device + power cycle. Use before reconnecting
    after a stale/zombie connection."""
    bluez_remove_device(addr)
    time.sleep(1)
    bluez_power_cycle()


# --- Database ---

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS sips (
            id INTEGER PRIMARY KEY,
            timestamp TEXT UNIQUE NOT NULL,
            intake_ml INTEGER NOT NULL,
            temp_c REAL,
            tds INTEGER,
            raw_hex TEXT,
            synced INTEGER DEFAULT 0
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS syncs (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            sip_count INTEGER,
            new_count INTEGER,
            acked_bytes INTEGER
        )
    """)
    db.commit()
    return db


def store_sips(db, sips):
    new_count = 0
    for sip in sips:
        try:
            db.execute(
                "INSERT INTO sips (timestamp, intake_ml, temp_c, tds, raw_hex) VALUES (?, ?, ?, ?, ?)",
                (sip["timestamp"], sip["intake_ml"], sip["temp_c"], sip["tds"], sip["raw"]),
            )
            new_count += 1
        except sqlite3.IntegrityError:
            pass
    db.commit()
    return new_count


def log_sync(db, sip_count, new_count, acked_bytes):
    db.execute(
        "INSERT INTO syncs (timestamp, sip_count, new_count, acked_bytes) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), sip_count, new_count, acked_bytes),
    )
    db.commit()


def get_unsynced(db):
    rows = db.execute(
        "SELECT id, timestamp, intake_ml, temp_c, tds, raw_hex FROM sips WHERE synced = 0"
    ).fetchall()
    return [
        {"id": r[0], "timestamp": r[1], "intake_ml": r[2], "temp_c": r[3], "tds": r[4], "raw_hex": r[5]}
        for r in rows
    ]


def mark_synced(db, ids):
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    db.execute(f"UPDATE sips SET synced = 1 WHERE id IN ({placeholders})", ids)
    db.commit()





# --- Heartbeat & HA Sensor Publishing ---

def post_heartbeat(state: str, detail: str = ""):
    status_val = f"{state}: {detail}" if detail else state
    publish_ha_sensor("status", status_val, friendly_name="WaterH Collector Status", icon="mdi:bluetooth-connect")
    if not API_TOKEN:
        return
    payload = json.dumps({
        "state": state, "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }).encode()
    req = urllib.request.Request(
        HEARTBEAT_URL, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_TOKEN}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


# --- Packet parsing ---

def parse_pt_packets(packets: list[bytes]) -> tuple[list[dict], int]:
    pt_payload = b""
    in_pt = False
    for pkt in packets:
        if len(pkt) >= 2 and pkt[0] == 0x50 and pkt[1] == 0x54:
            pt_payload = pkt[6:]
            in_pt = True
        elif in_pt and len(pkt) >= 2:
            pt_payload += pkt[2:]

    records = []
    record_size = 13
    for i in range(0, len(pt_payload) - record_size + 1, record_size):
        rec = pt_payload[i : i + record_size]
        year = 2000 + rec[0]
        month, day = rec[1], rec[2]
        hour, minute, second = rec[3], rec[4], rec[5]
        intake_ml = (rec[6] << 8) | rec[7]
        tds = (rec[8] << 8) | rec[9]
        temp_c = ((rec[10] << 8) | rec[11]) / 10.0
        try:
            ts = datetime(year, month, day, hour, minute, second).isoformat()
        except ValueError:
            ts = f"{year}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"
        records.append({
            "timestamp": ts, "intake_ml": intake_ml,
            "temp_c": temp_c, "tds": tds, "raw": rec.hex(" "),
        })
    return records, len(pt_payload)


# --- BLE helpers ---

async def find_waterh_device(target_addr: str, timeout: float = 12.0):
    """Find bottle by case-insensitive MAC address or device name."""
    try:
        device = await BleakScanner.find_device_by_address(target_addr, timeout=timeout)
        if device:
            return device
    except Exception:
        pass

    try:
        devices = await BleakScanner.discover(timeout=timeout)
        target_mac = target_addr.lower()
        for d in devices:
            if d.address.lower() == target_mac:
                return d
            if d.name and "waterh" in d.name.lower():
                log.info(f"[BLE] Discovered bottle by name: {d.name} ({d.address})")
                return d
    except Exception as e:
        log.error(f"[BLE] Discovery scan error: {e}")

    return None


def drain_queue(q: asyncio.Queue) -> list[bytes]:
    items = []
    while not q.empty():
        try:
            items.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


async def ble_write(client, cmd: bytes, label: str):
    log.info(f"[BLE] >> {label} ({cmd.hex(' ')})")
    await client.write_gatt_char(WRITE_CHAR, cmd, response=False)


async def ble_write_and_wait(client, cmd: bytes, label: str, queue: asyncio.Queue, wait: float = 2.0) -> list[bytes]:
    drain_queue(queue)
    await ble_write(client, cmd, label)
    await asyncio.sleep(wait)
    return drain_queue(queue)


# --- Sync cycle ---

async def sync_cycle(client, queue: asyncio.Queue, db) -> bool:
    # Step 1: Request bottle data
    pkts = await ble_write_and_wait(client, cmd_bottle_data(), "bottle-data", queue, wait=2.0)
    rp_pkts = [p for p in pkts if len(p) >= 2 and p[0] == 0x52 and p[1] == 0x50]
    if rp_pkts:
        rp = rp_pkts[0]
        if len(rp) > 31:
            log.info(f"[BLE] Battery: {rp[6]}%, charging: {rp[31]}")
            publish_ha_sensor("battery", rp[6], unit="%", friendly_name="WaterH Battery", device_class="battery")
    else:
        log.warning("[BLE] No bottle data response")

    # Step 2: Sync settings (time + goal + reminder)
    pkts = await ble_write_and_wait(client, cmd_sync_settings(), "sync-settings", queue, wait=2.0)
    rp_pkts = [p for p in pkts if len(p) >= 2 and p[0] == 0x52 and p[1] == 0x50]
    if rp_pkts:
        rp = rp_pkts[0]
        sync_ok = len(rp) > 10 and rp[10] == 0x00
        log.info(f"[BLE] Settings sync: {'ok' if sync_ok else 'check response'}")

    # Step 3: Sync today's amount to bottle display
    total_today = db.execute(
        "SELECT COALESCE(SUM(intake_ml), 0) FROM sips WHERE DATE(timestamp) = DATE('now')"
    ).fetchone()[0]
    await ble_write_and_wait(client, cmd_sync_today_amount(total_today), "sync-display", queue, wait=1.0)
    publish_ha_sensor("today_intake", total_today, unit="mL", friendly_name="WaterH Today Intake", icon="mdi:cup-water", device_class="water", state_class="total_increasing")
    publish_ha_sensor("daily_goal", GOAL_ML, unit="mL", friendly_name="WaterH Daily Goal", icon="mdi:target-variant")

    # Step 4: Request water logs
    pkts = await ble_write_and_wait(client, cmd_request_water_logs(), "request-logs", queue, wait=4.0)

    has_data = False
    for p in pkts:
        if len(p) >= 7 and p[0] == 0x52 and p[1] == 0x50 and p[5] == 0x06:
            has_data = p[6] == 0x01
            log.info(f"[BLE] Water logs: {'data found' if has_data else 'no data'}")

    if not has_data:
        log.info(f"[BLE] No new water logs, {total_today}ml today")
        log_sync(db, 0, 0, 0)
        return True

    # Step 5: Collect all PT packets
    all_packets = list(pkts)
    await asyncio.sleep(2.0)
    all_packets.extend(drain_queue(queue))

    sips, pt_bytes = parse_pt_packets(all_packets)
    log.info(f"[BLE] Received {len(sips)} sip records ({pt_bytes}B)")

    # Step 6: Store locally
    new_count = store_sips(db, sips)
    total_today = db.execute(
        "SELECT COALESCE(SUM(intake_ml), 0) FROM sips WHERE DATE(timestamp) = DATE('now')"
    ).fetchone()[0]
    log.info(f"[BLE] Stored {len(sips)} sips ({new_count} new), {total_today}ml today")
    publish_ha_sensor("today_intake", total_today, unit="mL", friendly_name="WaterH Today Intake", icon="mdi:cup-water", device_class="water", state_class="total_increasing")
    if sips:
        latest = sips[-1]
        publish_ha_sensor("temperature", latest["temp_c"], unit="°C", friendly_name="WaterH Water Temperature", device_class="temperature")
        publish_ha_sensor("tds", latest["tds"], unit="ppm", friendly_name="WaterH Water Quality (TDS)", icon="mdi:water-check")

    # Step 7: Ack + clear from bottle
    if sips:
        ack_bytes = len(sips) * 13
        await ble_write_and_wait(client, cmd_ack_water_logs(ack_bytes), "ack-logs", queue, wait=1.0)
        log.info(f"[BLE] Acked {ack_bytes}B ({len(sips)} records)")

    # Step 8: Update bottle display with new total
    await ble_write_and_wait(client, cmd_sync_today_amount(total_today), "sync-display", queue, wait=1.0)

    log_sync(db, len(sips), new_count, len(sips) * 13 if sips else 0)
    return True


# --- BLE main loop ---

async def ble_loop():
    global cmd_queue, ha_conn
    cmd_queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    ha_conn = HAConnection(
        host=MQTT_HOST,
        port=MQTT_PORT,
        user=MQTT_USER,
        password=MQTT_PASSWORD,
        prefix=MQTT_PREFIX,
    )
    ha_conn.start(loop, cmd_queue)

    db = init_db()
    log.info(f"[DB] Initialized at {DB_PATH}")

    total_today = db.execute(
        "SELECT COALESCE(SUM(intake_ml), 0) FROM sips WHERE DATE(timestamp) = DATE('now')"
    ).fetchone()[0]

    publish_ha_sensor("today_intake", total_today, unit="mL", friendly_name="WaterH Today Intake", icon="mdi:cup-water", device_class="water", state_class="total_increasing")
    publish_ha_sensor("daily_goal", GOAL_ML, unit="mL", friendly_name="WaterH Daily Goal", icon="mdi:target-variant")

    # Start command HTTP server
    await start_cmd_server()

    packet_queue: asyncio.Queue[bytes] = asyncio.Queue()
    scan_failures = 0
    backoff = BACKOFF_BASE

    post_heartbeat("starting")

    # Clean start: remove any stale BlueZ state from previous runs
    bluez_remove_device(BOTTLE_ADDR)

    while True:
        # --- Scan ---
        log.info(f"[BLE] Scanning for {BOTTLE_ADDR}...")
        post_heartbeat("scanning")

        device = await find_waterh_device(BOTTLE_ADDR, timeout=12.0)

        if not device:
            scan_failures += 1
            bluez_remove_device(BOTTLE_ADDR)
            log.warning(f"[BLE] Not found (attempt {scan_failures}), retry in {backoff}s")
            post_heartbeat("scanning", f"not found, retry {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_CAP)
            continue

        scan_failures = 0
        backoff = BACKOFF_BASE

        # --- Connect ---
        disconnected_event = asyncio.Event()

        def on_disconnect(client):
            log.warning("[BLE] Disconnected")
            disconnected_event.set()

        try:
            async with BleakClient(device, disconnected_callback=on_disconnect) as client:
                log.info(f"[BLE] Connected to {device.name}")
                post_heartbeat("connected")

                def on_notify(sender, data: bytearray):
                    packet_queue.put_nowait(bytes(data))

                await client.start_notify(NOTIFY_CHAR, on_notify)

                empty_cycles = 0
                while client.is_connected and not disconnected_event.is_set():
                    # Process any queued commands from the HTTP server
                    while not cmd_queue.empty():
                        try:
                            cmd, label = cmd_queue.get_nowait()
                            await ble_write(client, cmd, f"cmd: {label}")
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            log.error(f"[BLE] Command error: {e}")

                    try:
                        success = await sync_cycle(client, packet_queue, db)
                        if success:
                            empty_cycles = 0
                            post_heartbeat("connected", "sync ok")
                        else:
                            empty_cycles += 1
                    except Exception as e:
                        log.error(f"[BLE] Sync cycle error: {e}")
                        empty_cycles += 1

                    if empty_cycles >= MAX_EMPTY_POLLS:
                        log.warning(f"[BLE] {empty_cycles} failed cycles, forcing reconnect")
                        post_heartbeat("scanning", "stale connection")
                        break

                    backoff = BACKOFF_BASE

                    # While waiting for next poll, check for commands every second
                    for _ in range(POLL_INTERVAL):
                        if disconnected_event.is_set():
                            break
                        if not cmd_queue.empty():
                            while not cmd_queue.empty():
                                try:
                                    cmd, label = cmd_queue.get_nowait()
                                    await ble_write(client, cmd, f"cmd: {label}")
                                    await asyncio.sleep(0.5)
                                except Exception as e:
                                    log.error(f"[BLE] Command error: {e}")
                        await asyncio.sleep(1)

        except Exception as e:
            log.error(f"[BLE] Connection error: {e}")
            post_heartbeat("error", str(e))

        # Clean up BlueZ state before reconnecting — this is the critical step
        # that prevents zombie connections. Android does this in gatt.close().
        log.info(f"[BLE] Cleaning up BlueZ state before reconnect...")
        bluez_remove_device(BOTTLE_ADDR)

        log.info(f"[BLE] Reconnecting in {backoff}s...")
        post_heartbeat("scanning", "reconnecting")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, BACKOFF_CAP)


def main():
    log.info("[INIT] WaterH Collector (full protocol)")
    log.info(f"[INIT] Bottle: {BOTTLE_ADDR}")
    log.info(f"[INIT] Goal: {GOAL_ML}ml")
    log.info(f"[INIT] Poll interval: {POLL_INTERVAL}s")
    log.info(f"[INIT] API: {API_URL}")
    log.info(f"[INIT] Command server: :{CMD_PORT}")
    token_keys = [k for k in os.environ.keys() if any(x in k.upper() for x in ["TOKEN", "SUPERVISOR", "HASSIO"])]
    log.info(f"[INIT] Detected token env vars: {token_keys}")
    log.info(f"[INIT] HA Direct API: {'enabled' if SUPERVISOR_TOKEN else 'disabled (no token detected)'}")
    asyncio.run(ble_loop())


if __name__ == "__main__":
    main()
