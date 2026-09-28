#!/usr/bin/env python3
"""
WaterH Bottle Command Sender
Sends commands and queries status from Home Assistant collector HTTP API using only Python standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_HOST = os.environ.get("HA_URL", "http://192.168.2.209:7700")


def send_http(url: str, method: str = "GET", data: dict | None = None, timeout: float = 8.0) -> dict | None:
    headers = {"User-Agent": "WaterH-CLI/1.0"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP Error {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"❌ Connection Error: {e.reason}", file=sys.stderr)
        print(f"   Make sure the WaterH add-on is running at {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"❌ Unexpected Error: {e}", file=sys.stderr)
        return None


def cmd_status(base_url: str):
    print("Fetching WaterH bottle status...")
    status = send_http(f"{base_url}/api/status")
    today = send_http(f"{base_url}/api/today")

    if not status or not today:
        return

    online_badge = "🟢 ONLINE" if status.get("online") else "🔴 OFFLINE"
    print("\n" + "=" * 50)
    print(f"  💧 WaterH Smart Bottle — {online_badge}")
    print("=" * 50)
    print(f"  MAC Address:  {status.get('bottle', 'Unknown')}")
    print(f"  State:        {status.get('state', 'Unknown')}")
    print(f"  Last Seen:    {status.get('last_seen', 'Never')}")
    print("-" * 50)
    total_ml = today.get("total_ml", 0)
    pct = today.get("goal_pct", 0)
    sips_count = today.get("sip_count", 0)
    temp = today.get("last_temp_c")
    temp_str = f"{temp}°C" if temp is not None else "—"

    bar_len = 25
    filled = int(bar_len * min(pct, 100) / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    print(f"  Today Intake: {total_ml} mL ({pct}%) [{bar}]")
    print(f"  Total Sips:   {sips_count}")
    if temp is not None:
        print(f"  Temperature:  {temp}°C")
    print("-" * 50)

    sips = today.get("sips", [])
    if sips:
        print("  Recent Sips Today:")
        for s in sips[:8]:
            ts = s.get("timestamp", "").split("T")[-1]
            ml = s.get("intake_ml", 0)
            t = s.get("temp_c")
            t_repr = f"({t}°C)" if t is not None else ""
            print(f"    • {ts}  +{ml:>3} mL  {t_repr}")
        if len(sips) > 8:
            print(f"    ... and {len(sips) - 8} earlier sips.")
    else:
        print("  No sips recorded yet today.")
    print("=" * 50 + "\n")


def cmd_flash(base_url: str):
    print(">> Queuing Flash LED command...")
    res = send_http(f"{base_url}/commands/flash", method="POST")
    if res and res.get("ok"):
        print("✔ Flash LED command successfully queued for delivery!")


def cmd_led(base_url: str, mode: str, color: str):
    print(f">> Queuing LED command: mode={mode}, color={color}...")
    res = send_http(f"{base_url}/commands/led", method="POST", data={"mode": mode, "color": color})
    if res and res.get("ok"):
        print(f"✔ LED set to mode '{mode}' ({color}) successfully queued!")


def cmd_intake(base_url: str, ml: int):
    print(f">> Queuing intake update: {ml} mL...")
    res = send_http(f"{base_url}/commands/intake", method="POST", data={"ml": ml})
    if res and res.get("ok"):
        print(f"✔ Intake display sync ({ml} mL) successfully queued!")


def cmd_goal(base_url: str, ml: int):
    print(f">> Queuing daily goal update: {ml} mL...")
    res = send_http(f"{base_url}/commands/goal", method="POST", data={"ml": ml})
    if res and res.get("ok"):
        print(f"✔ Daily goal ({ml} mL) successfully queued!")


def cmd_schedule(
    base_url: str,
    wake: str | None = None,
    sleep: str | None = None,
    interval: int | None = None,
    reminder_on: bool | None = None,
):
    if wake is None and sleep is None and interval is None and reminder_on is None:
        print("Fetching Active Day & Reminder schedule...")
        res = send_http(f"{base_url}/api/schedule")
        if not res:
            return
        print("\n" + "=" * 50)
        print("  ⏰ WaterH Active Day & Reminders")
        print("=" * 50)
        wake_t = res.get("wake_time", "08:00")
        sleep_t = res.get("sleep_time", "20:00")
        int_m = res.get("interval_min", 60)
        on = res.get("reminder_on", False)
        status_str = "🟢 ENABLED (Periodic)" if on else "⚪ DISABLED"
        tz = res.get("timezone", "UTC")
        local_t = res.get("local_time", "Unknown")

        print(f"  Reminders:     {status_str}")
        print(f"  Wake (Start):  {wake_t}")
        print(f"  Sleep (End):   {sleep_t}")
        print(f"  Interval:      Every {int_m} minutes")
        print(f"  Timezone:      {tz}")
        print(f"  Local Clock:   {local_t}")
        print("=" * 50 + "\n")
        return

    payload = {}
    if wake is not None:
        payload["wake"] = wake
    if sleep is not None:
        payload["sleep"] = sleep
    if interval is not None:
        payload["interval"] = interval
    if reminder_on is not None:
        payload["on"] = reminder_on

    print(f">> Updating schedule: {payload}...")
    res = send_http(f"{base_url}/commands/schedule", method="POST", data=payload)
    if res and res.get("ok"):
        sched = res.get("schedule", {})
        print("✔ Active Day & Reminder schedule successfully updated and queued!")
        print(f"  Wake: {sched.get('wake_time')} | Sleep: {sched.get('sleep_time')} | Interval: {sched.get('interval_min')}m | On: {sched.get('reminder_on')}")


def cmd_time(base_url: str):
    print(">> Queuing bottle clock synchronization...")
    res = send_http(f"{base_url}/commands/time", method="POST")
    if res and res.get("ok"):
        print(f"✔ Clock sync queued! (Bottle time will update to: {res.get('local_time')})")


def cmd_raw(base_url: str, hex_str: str):
    clean_hex = hex_str.replace(" ", "").strip()
    print(f">> Queuing raw hex command: {clean_hex}...")
    res = send_http(f"{base_url}/commands/raw", method="POST", data={"hex": clean_hex})
    if res and res.get("ok"):
        print(f"✔ Raw command '{clean_hex}' successfully queued!")


def interactive_menu(base_url: str):
    while True:
        print("\n" + "=" * 48)
        print("💧 WaterH Interactive Console")
        print("=" * 48)
        print(" 1. Check Status & Today's Sips")
        print(" 2. Flash Bottle LED")
        print(" 3. Set LED Mode & Color")
        print(" 4. Sync Intake Display (mL)")
        print(" 5. Set Daily Goal Target (mL)")
        print(" 6. View / Update Active Day & Reminders")
        print(" 7. Sync Bottle Clock to Local Time")
        print(" 8. Send Custom Raw Hex Bytes")
        print(" 0. Exit")
        print("=" * 48)

        choice = input("Select an option (0-8): ").strip()
        if choice == "1":
            cmd_status(base_url)
        elif choice == "2":
            cmd_flash(base_url)
        elif choice == "3":
            mode = input("Mode [default/breathe/calm/rainbow/warmth/christmas] (default: breathe): ").strip() or "breathe"
            color = input("Color [blue/green/red/yellow/purple/cyan/white] (default: blue): ").strip() or "blue"
            cmd_led(base_url, mode, color)
        elif choice == "4":
            ml_in = input("Enter intake in mL (e.g. 500): ").strip()
            if ml_in.isdigit():
                cmd_intake(base_url, int(ml_in))
            else:
                print("Invalid number!")
        elif choice == "5":
            ml_in = input("Enter daily goal in mL (e.g. 2500): ").strip()
            if ml_in.isdigit():
                cmd_goal(base_url, int(ml_in))
            else:
                print("Invalid number!")
        elif choice == "6":
            cmd_schedule(base_url)
            sub = input("Update schedule? (y/N): ").strip().lower()
            if sub == "y":
                wake = input("Wake time (HH:MM, e.g. 08:00): ").strip() or None
                sleep = input("Sleep time (HH:MM, e.g. 20:00): ").strip() or None
                interval_str = input("Interval in minutes (e.g. 60): ").strip()
                interval = int(interval_str) if interval_str.isdigit() else None
                on_str = input("Enable reminders? (y/n/leave blank): ").strip().lower()
                rem_on = True if on_str == "y" else (False if on_str == "n" else None)
                cmd_schedule(base_url, wake=wake, sleep=sleep, interval=interval, reminder_on=rem_on)
        elif choice == "7":
            cmd_time(base_url)
        elif choice == "8":
            raw_hex = input("Enter hex bytes (e.g. 50 54 00 03 02 1d 01): ").strip()
            if raw_hex:
                cmd_raw(base_url, raw_hex)
        elif choice in ("0", "q", "exit"):
            print("Goodbye!")
            break
        else:
            print("Invalid choice, please select 0-8.")


def main():
    parser = argparse.ArgumentParser(description="Send commands to WaterH bottle via Home Assistant add-on")
    parser.add_argument("--url", default=DEFAULT_HOST, help=f"Collector HTTP API base URL (default: {DEFAULT_HOST})")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # status
    subparsers.add_parser("status", help="Get bottle connection state and today's intake statistics")

    # flash
    subparsers.add_parser("flash", help="Flash bottle LED light once")

    # led
    led_p = subparsers.add_parser("led", help="Set LED lighting mode and color")
    led_p.add_argument("mode", nargs="?", default="breathe", choices=["default", "breathe", "calm", "rainbow", "warmth", "christmas"], help="LED mode")
    led_p.add_argument("color", nargs="?", default="blue", choices=["blue", "green", "red", "yellow", "purple", "cyan", "white"], help="LED color")

    # intake
    intake_p = subparsers.add_parser("intake", help="Sync intake amount on bottle OLED screen")
    intake_p.add_argument("ml", type=int, help="Volume in mL to display")

    # goal
    goal_p = subparsers.add_parser("goal", help="Set target daily hydration goal in mL")
    goal_p.add_argument("ml", type=int, help="Target volume in mL")

    # schedule
    sched_p = subparsers.add_parser("schedule", help="View or update active waking hours and reminder schedule")
    sched_p.add_argument("--wake", help="Wake time (start of active day), e.g. 08:00")
    sched_p.add_argument("--sleep", help="Sleep time (end of active day), e.g. 20:00")
    sched_p.add_argument("--interval", type=int, help="Reminder interval in minutes (e.g. 30, 45, 60)")
    on_group = sched_p.add_mutually_exclusive_group()
    on_group.add_argument("--on", action="store_true", default=None, help="Enable periodic reminders")
    on_group.add_argument("--off", action="store_true", default=None, help="Disable periodic reminders")

    # time
    subparsers.add_parser("time", help="Sync bottle clock to current local date and time")

    # raw
    raw_p = subparsers.add_parser("raw", help="Send arbitrary raw hex byte sequence")
    raw_p.add_argument("hex", nargs="+", help="Hex string (e.g. 50 54 00 03 02 1d 01)")

    # interactive
    subparsers.add_parser("interactive", help="Start interactive text menu")

    args = parser.parse_args()

    if not args.subcommand or args.subcommand == "interactive":
        interactive_menu(args.url)
        return

    if args.subcommand == "status":
        cmd_status(args.url)
    elif args.subcommand == "flash":
        cmd_flash(args.url)
    elif args.subcommand == "led":
        cmd_led(args.url, args.mode, args.color)
    elif args.subcommand == "intake":
        cmd_intake(args.url, args.ml)
    elif args.subcommand == "goal":
        cmd_goal(args.url, args.ml)
    elif args.subcommand == "schedule":
        reminder_on = True if args.on else (False if args.off else None)
        cmd_schedule(args.url, wake=args.wake, sleep=args.sleep, interval=args.interval, reminder_on=reminder_on)
    elif args.subcommand == "time":
        cmd_time(args.url)
    elif args.subcommand == "raw":
        cmd_raw(args.url, " ".join(args.hex))


if __name__ == "__main__":
    main()

