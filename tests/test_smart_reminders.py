"""
Unit tests for the Smart Hydration Glow reminder system.
Verifies linear schedule pacing, inactivity thresholds, escalation ladder,
auto-off silencing, sip snoozing, and HTTP endpoints.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import unittest

from tests.base import IsolatedCollectorTestCase, MockWriter
import collector.collector as col


class TestSmartReminders(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        col.set_schedule_settings(wake_time="08:00", sleep_time="20:00", interval_min=60, reminder_on=False)
        col.GOAL_ML = 2000

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    async def _send_request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
        reader = asyncio.StreamReader()
        writer = MockWriter()
        req_lines = [f"{method} {path} HTTP/1.1", "Host: testserver"]
        body_bytes = b""
        if body is not None:
            body_bytes = json.dumps(body).encode("utf-8")
            req_lines.append("Content-Type: application/json")
            req_lines.append(f"Content-Length: {len(body_bytes)}")
        req_lines.append("\r\n")
        header_data = "\r\n".join(req_lines).encode("utf-8")
        reader.feed_data(header_data + body_bytes)
        reader.feed_eof()

        await col.handle_cmd_request(reader, writer)
        raw = bytes(writer.data).decode("utf-8", errors="replace")
        lines = raw.split("\r\n")
        status_line = lines[0]
        status_code = int(status_line.split(" ")[1])

        body_idx = raw.find("\r\n\r\n")
        body_str = raw[body_idx + 4:] if body_idx != -1 else ""
        data = json.loads(body_str) if body_str else {}
        return status_code, data, {}

    # --- Pacing & Active Hours Tests ---

    def test_calculate_expected_intake_linear_pace(self):
        """Verify linear progression of expected intake from wake to sleep."""
        sched = {"wake_hour": 8, "wake_minute": 0, "sleep_hour": 20, "sleep_minute": 0}
        today = datetime(2026, 9, 27)

        # Before wake
        dt_0700 = today.replace(hour=7, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_0700, 2000, sched), 0)

        # At wake
        dt_0800 = today.replace(hour=8, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_0800, 2000, sched), 0)

        # Midday (50% through 12h window)
        dt_1400 = today.replace(hour=14, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_1400, 2000, sched), 1000)

        # 75% through window (9h of 12h = 17:00)
        dt_1700 = today.replace(hour=17, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_1700, 2000, sched), 1500)

        # At sleep time
        dt_2000 = today.replace(hour=20, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_2000, 2000, sched), 2000)

        # After sleep
        dt_2200 = today.replace(hour=22, minute=0)
        self.assertEqual(col.calculate_expected_intake(dt_2200, 2000, sched), 2000)

    def test_is_in_active_window(self):
        """Verify is_in_active_window flags active waking hours."""
        sched = {"wake_hour": 8, "wake_minute": 0, "sleep_hour": 20, "sleep_minute": 0}
        today = datetime(2026, 9, 27)

        self.assertFalse(col.is_in_active_window(today.replace(hour=7, minute=59), sched))
        self.assertTrue(col.is_in_active_window(today.replace(hour=8, minute=0), sched))
        self.assertTrue(col.is_in_active_window(today.replace(hour=14, minute=30), sched))
        self.assertTrue(col.is_in_active_window(today.replace(hour=19, minute=59), sched))
        self.assertFalse(col.is_in_active_window(today.replace(hour=20, minute=0), sched))
        self.assertFalse(col.is_in_active_window(today.replace(hour=23, minute=0), sched))

    # --- Settings Persistence Tests ---

    def test_get_and_set_smart_reminder_settings(self):
        """Verify defaults and custom setting updates."""
        defaults = col.get_smart_reminder_settings()
        self.assertFalse(defaults["enabled"])
        self.assertEqual(defaults["sip_interval_min"], 40)
        self.assertEqual(defaults["gentle_mode"], "default")
        self.assertEqual(defaults["gentle_repeat_min"], 3)
        self.assertEqual(defaults["escalation_delay_min"], 15)
        self.assertEqual(defaults["escalated_mode"], "rainbow")
        self.assertEqual(defaults["snooze_min"], 10)
        self.assertEqual(defaults["auto_off_min"], 60)

        updated = col.set_smart_reminder_settings(
            enabled=True,
            sip_interval_min=35,
            behind_only=True,
            gentle_mode="breathe",
            gentle_repeat_min=2,
            escalation_delay_min=10,
            escalated_mode="calm",
            snooze_min=12,
            auto_off_min=90
        )
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["sip_interval_min"], 35)
        self.assertTrue(updated["behind_only"])
        self.assertEqual(updated["gentle_mode"], "breathe")
        self.assertEqual(updated["gentle_repeat_min"], 2)
        self.assertEqual(updated["escalation_delay_min"], 10)
        self.assertEqual(updated["escalated_mode"], "calm")
        self.assertEqual(updated["snooze_min"], 12)
        self.assertEqual(updated["auto_off_min"], 90)

    # --- State Machine & Escalation Tests ---

    def test_evaluation_when_disabled(self):
        """When disabled, state is 'disabled' and no command is queued."""
        now = datetime(2026, 9, 27, 14, 0)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=now)
        self.assertEqual(state, "disabled")
        self.assertIsNone(cmd)
        self.assertFalse(info["snoozed"])

    def test_evaluation_outside_active_hours(self):
        """When enabled but outside active waking window, state is 'outside_hours'."""
        col.set_smart_reminder_settings(enabled=True)
        night_time = datetime(2026, 9, 27, 22, 30)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=night_time)
        self.assertEqual(state, "outside_hours")
        self.assertIsNone(cmd)

    def test_evaluation_on_track_with_behind_only(self):
        """When behind_only is True and user is on schedule, reminders are suppressed."""
        col.set_smart_reminder_settings(enabled=True, behind_only=True, sip_interval_min=40)
        db = col.init_db()
        # 14:00 expects 1000ml (50% of 2000ml). Insert 1200ml at 13:15 (45 min idle > 40 min, but < 60 min auto-off)
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES ('2026-09-27T13:15:00', 1200)")
        db.commit()

        # At 14:00, user has 1200ml (ahead of 1000ml goal), 45m idle
        now = datetime(2026, 9, 27, 14, 0)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=now, db=db)
        self.assertEqual(state, "on_track")
        self.assertIsNone(cmd)
        self.assertFalse(info["behind_schedule"])
        db.close()

    def test_escalation_ladder_and_repeat_intervals(self):
        """
        Verify the full escalation ladder:
        - 0-39 min idle: idle, no command
        - 40 min idle: enters gentle, queues default glow
        - 41 min idle: gentle, not due for repeat yet
        - 43 min idle: gentle, repeats glow (3m interval)
        - 55 min idle (40+15): enters escalated, immediately queues rainbow glow
        - 58 min idle: escalated, repeats rainbow glow (3m interval)
        - 60 min idle: enters auto_off, completely silenced
        """
        col.set_smart_reminder_settings(
            enabled=True,
            sip_interval_min=40,
            escalation_delay_min=15,
            gentle_repeat_min=3,
            gentle_mode="default",
            escalated_mode="rainbow",
            auto_off_min=60,
        )
        db = col.init_db()
        # Sip at 10:00
        sip_time = datetime(2026, 9, 27, 10, 0)
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES (?, 100)", (sip_time.isoformat(),))
        db.commit()

        # Step 1: 10:25 (25 min idle < 40 min)
        t_25 = sip_time + timedelta(minutes=25)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_25, db=db)
        self.assertEqual(state, "idle")
        self.assertIsNone(cmd)
        self.assertEqual(info["idle_minutes"], 25.0)

        # Step 2: 10:40 (40 min idle -> triggers Stage 1 Gentle)
        t_40 = sip_time + timedelta(minutes=40)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_40, db=db)
        self.assertEqual(state, "gentle")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], col.cmd_set_led("default", "blue"))
        self.assertIn("gentle", cmd[1])

        # Step 3: 10:41 (41 min idle -> 1 min later, should NOT fire duplicate)
        t_41 = sip_time + timedelta(minutes=41)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_41, db=db)
        self.assertEqual(state, "gentle")
        self.assertIsNone(cmd)

        # Step 4: 10:43 (43 min idle -> 3 min later, should repeat gentle)
        t_43 = sip_time + timedelta(minutes=43)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_43, db=db)
        self.assertEqual(state, "gentle")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], col.cmd_set_led("default", "blue"))

        # Step 5: 10:55 (55 min idle -> 15 min after gentle start -> escalates to Rainbow immediately)
        t_55 = sip_time + timedelta(minutes=55)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_55, db=db)
        self.assertEqual(state, "escalated")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], col.cmd_set_led("rainbow", "blue"))
        self.assertIn("escalated", cmd[1])

        # Step 6: 10:58 (58 min idle -> 3 min repeat for rainbow)
        t_58 = sip_time + timedelta(minutes=58)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_58, db=db)
        self.assertEqual(state, "escalated")
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[0], col.cmd_set_led("rainbow", "blue"))

        # Step 7: 11:00 (60 min idle -> reaches auto-off limit)
        t_60 = sip_time + timedelta(minutes=60)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_60, db=db)
        self.assertEqual(state, "auto_off")
        self.assertIsNone(cmd)
        self.assertTrue(info["auto_off"])

        # Step 8: 11:20 (Still auto_off, remains silent)
        t_80 = sip_time + timedelta(minutes=80)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_80, db=db)
        self.assertEqual(state, "auto_off")
        self.assertIsNone(cmd)
        db.close()

    def test_sip_recording_resets_auto_off_and_snoozes(self):
        """Verify taking a sip clears auto-off and applies snooze."""
        col.set_smart_reminder_settings(enabled=True, snooze_min=10, sip_interval_min=40)
        col.smart_reminders_auto_off = True

        # Drink sip
        db = col.init_db()
        col.store_sips(db, [{"timestamp": "2026-09-27T11:30:00", "intake_ml": 250, "temp_c": 21.0, "tds": 50, "raw": "aabb"}])

        # Auto-off must be cleared
        self.assertFalse(col.smart_reminders_auto_off)
        self.assertIsNotNone(col.smart_reminders_snooze_until)

        # 5 minutes after sip (11:35): within 10-minute snooze window
        t_snooze = datetime(2026, 9, 27, 11, 35)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_snooze, db=db)
        self.assertEqual(state, "snoozed")
        self.assertIsNone(cmd)
        self.assertTrue(info["snoozed"])
        self.assertGreater(info["snooze_remaining_seconds"], 0)

        # 12 minutes after sip (11:42): snooze expired, idle time is 12m (< 40m) -> state is 'idle'
        t_after_snooze = datetime(2026, 9, 27, 11, 42)
        state, cmd, info = col.evaluate_smart_reminders(now_dt=t_after_snooze, db=db)
        self.assertEqual(state, "idle")
        self.assertIsNone(cmd)
        self.assertEqual(info["idle_minutes"], 12.0)
        db.close()

    # --- HTTP API Endpoints Tests ---

    def test_get_api_smart_reminders(self):
        """Verify GET /api/smart-reminders returns settings and status."""
        col.set_smart_reminder_settings(enabled=True, sip_interval_min=45, gentle_mode="breathe")
        status_code, data, _ = self.run_async(self._send_request("GET", "/api/smart-reminders"))
        self.assertEqual(status_code, 200)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["sip_interval_min"], 45)
        self.assertEqual(data["gentle_mode"], "breathe")
        self.assertEqual(data["wake_time"], "08:00")
        self.assertEqual(data["sleep_time"], "20:00")
        self.assertIn("state", data)
        self.assertIn("idle_minutes", data)

    def test_post_api_smart_reminders(self):
        """Verify POST /api/smart-reminders updates configuration."""
        payload = {
            "enabled": True,
            "sip_interval_min": 30,
            "behind_only": True,
            "gentle_mode": "calm",
            "gentle_repeat_min": 2,
            "escalation_delay_min": 12,
            "escalated_mode": "christmas",
            "snooze_min": 15,
            "auto_off_min": 75,
        }
        status_code, data, _ = self.run_async(self._send_request("POST", "/api/smart-reminders", body=payload))
        self.assertEqual(status_code, 200)
        self.assertTrue(data["enabled"])
        self.assertEqual(data["sip_interval_min"], 30)
        self.assertTrue(data["behind_only"])
        self.assertEqual(data["gentle_mode"], "calm")
        self.assertEqual(data["gentle_repeat_min"], 2)
        self.assertEqual(data["escalation_delay_min"], 12)
        self.assertEqual(data["escalated_mode"], "christmas")
        self.assertEqual(data["snooze_min"], 15)
        self.assertEqual(data["auto_off_min"], 75)

    def test_post_commands_smart_reminders_alias(self):
        """Verify POST /commands/smart-reminders alias works."""
        payload = {"enabled": True, "sip_interval_min": 50}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/smart-reminders", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["sip_interval_min"], 50)


if __name__ == "__main__":
    unittest.main()
