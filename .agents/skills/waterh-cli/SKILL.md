---
name: waterh-cli
description: >-
  Control and inspect the WaterH Smart Water Bottle via the Home Assistant HTTP API and BLE bridge.
  Use this skill whenever the user asks to query bottle battery, temperature, or sip intake, flash
  the bottle LED, change LED colors/patterns, sync the OLED display or daily hydration goal, send
  custom/raw Bluetooth hex byte sequences, or test and debug WaterH bottle communication without
  rebuilding the Home Assistant add-on.
---

# WaterH Bottle CLI & Control Skill

This skill provides procedures for interacting with the physical WaterH Smart Water Bottle in real time using the Python standard library tool [`send_cmd.py`](file:///Users/jacek/Documents/apps/sylvexn/water/send_cmd.py).

The tool communicates with the WaterH collector add-on running on Home Assistant (`http://192.168.2.209:7700` or `http://homeassistant.local:7700`), which bridges the commands directly over Bluetooth Low Energy (BLE) to the bottle.

---

## When to Use This Skill

Activate and use this skill when:
- **Checking bottle status**: Reading current connection state, battery percentage, water temperature, today's total intake, or recent sip history.
- **Visual alerts & LED testing**: Flashing the bottle LED or setting LED animation modes (`default`, `breathe`, `rainbow`, `off`) and colors (`blue`, `green`, `red`, `yellow`, `purple`, `cyan`, `white`).
- **Syncing bottle display**: Setting or correcting the water volume displayed on the bottle's OLED screen (`intake <ml>`).
- **Goal management**: Updating the daily hydration target in milliliters (`goal <ml>`).
- **Protocol reverse engineering & raw testing**: Sending arbitrary hex bytes directly to the bottle (`raw <hex>`) to experiment with new commands without changing code or restarting containers.
- **Debugging & Verification**: Verifying that BLE write characteristics are responding and examining delivery logs.

---

## Command Reference

Run the tool using `python3 send_cmd.py <subcommand> [options]`.

| Subcommand | Arguments | Description | Example |
| :--- | :--- | :--- | :--- |
| `status` | *(none)* | Fetch online status, battery, temperature, progress bar, and today's sips | `python3 send_cmd.py status` |
| `flash` | *(none)* | Flash the bottle's circular LED light once | `python3 send_cmd.py flash` |
| `led` | `[mode] [color]` | Set LED animation mode and color | `python3 send_cmd.py led rainbow blue` |
| `intake` | `<ml>` | Sync water intake volume to bottle OLED display | `python3 send_cmd.py intake 500` |
| `goal` | `<ml>` | Set daily target goal in mL | `python3 send_cmd.py goal 2500` |
| `schedule` | `[--wake] [--sleep] [--interval] [--on/--off]` | View or update active waking hours and reminder schedule | `python3 send_cmd.py schedule --wake 08:00 --sleep 22:00 --interval 45 --on` |
| `time` | *(none)* | Sync bottle clock to local timezone and system time | `python3 send_cmd.py time` |
| `raw` | `<hex bytes>` | Send arbitrary hex bytes sequence over BLE | `python3 send_cmd.py raw 50 54 00 03 02 1d 01` |
| `interactive` | *(none)* | Launch interactive console menu | `python3 send_cmd.py interactive` |


---

## Detailed Workflows

### 1. Querying Live Bottle Telemetry & Intake
To inspect battery, temperature, and recent sip timestamps:
```bash
python3 send_cmd.py status
```
Expected output displays:
- Online/Offline status badge
- MAC address (`A4:C1:38:F3:63:2D`)
- Today's intake in mL and percentage toward target goal with ASCII progress bar
- Current water temperature in °C
- Log of recent sips with volume and temperature

### 2. Testing Bottle LED & Animations
To trigger visual feedback on the bottle:
```bash
# Flash once
python3 send_cmd.py flash

# Breathe blue
python3 send_cmd.py led breathe blue

# Rainbow mode
python3 send_cmd.py led rainbow

# Turn off LED
python3 send_cmd.py led off
```

### 3. Updating Bottle Display & Daily Target
To adjust the daily goal or sync a specific intake number on the bottle:
```bash
# Set 2.5L goal
python3 send_cmd.py goal 2500

# Set bottle OLED display to 750 mL
python3 send_cmd.py intake 750
```

### 4. Active Day Hours, Reminders & Timezone Sync
To view or adjust your daily wake/sleep window and hydration reminders:
```bash
# Check current schedule and bottle local clock
python3 send_cmd.py schedule

# Update wake and sleep hours with 45m reminder interval
python3 send_cmd.py schedule --wake 08:00 --sleep 22:00 --interval 45 --on

# Disable periodic reminders
python3 send_cmd.py schedule --off

# Sync current local system time to bottle clock
python3 send_cmd.py time
```

### 5. Sending Raw Hex Commands (Protocol Research)
To test newly discovered packet structures or raw byte sequences:
```bash
# Example: Send raw LED flash packet (50540003021d01)
python3 send_cmd.py raw 50 54 00 03 02 1d 01

# Example: Request bottle data / battery (47540001ff)
python3 send_cmd.py raw 47 54 00 01 ff
```


---

## Verifying Command Delivery

1. **Immediate Queue Acknowledgment**:
   `send_cmd.py` returns a JSON confirmation `{"ok": true, "queued": ...}` indicating the command was received by the add-on command queue.

2. **Verifying Physical Execution in Live Logs**:
   To inspect or stream add-on logs directly from Home Assistant:
   ```bash
   # Quick fetch (last 20 lines)
   python3 fetch_logs.py -n 20

   # Filter for errors or warnings
   python3 fetch_logs.py -n 50 --level ERROR
   python3 fetch_logs.py -n 50 --min-level WARNING

   # Follow live BLE logs in real-time
   python3 fetch_logs.py -f --tag BLE

   # Structured JSON output for automated agent parsing
   python3 fetch_logs.py -n 20 --json
   ```
   Look for lines formatted as:
   ```text
   [INFO] [BLE] >> cmd: raw ... (...)
   ```

> [!NOTE]
> If the bottle is asleep (motionless on a desk for several minutes), commands remain queued in memory. As soon as the bottle is picked up, moved, or takes a sip, the BLE link immediately wakes up, drains the queue, and writes the command to the bottle.

---

## Configuration & Environment Variables

If Home Assistant is reachable at a different IP or hostname, set the `HA_URL` environment variable:
```bash
export HA_URL="http://homeassistant.local:7700"
# or run inline:
HA_URL="http://192.168.2.209:7700" python3 send_cmd.py status
```
