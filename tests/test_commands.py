"""
Unit tests for WaterH BLE protocol command builders.
Compatible with both pytest and python -m unittest.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from tests.base import IsolatedCollectorTestCase
from collector.collector import (
    cmd_bottle_data,
    cmd_sync_settings,
    cmd_request_water_logs,
    cmd_ack_water_logs,
    cmd_sync_today_amount,
    cmd_clear_offline,
    cmd_flash_led,
    cmd_set_led,
    cmd_set_reminder,
    cmd_set_goal,
    cmd_recalibrate,
)


class TestProtocolCommands(IsolatedCollectorTestCase):
    def test_cmd_bottle_data(self):
        """Verify command requesting bottle hardware, firmware, battery info."""
        cmd = cmd_bottle_data()
        self.assertEqual(cmd, bytes.fromhex("47540001ff"))
        self.assertTrue(cmd.startswith(b"GT"))  # 0x47 0x54

    def test_cmd_sync_settings_default_goal(self):
        """Verify sync settings with default goal and fixed time."""
        fixed_time = datetime(2026, 9, 23, 14, 30, 45)
        with patch("collector.collector.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            cmd = cmd_sync_settings()

        # Goal = 1800 ml -> 0x0708
        # Year = 2026 - 2000 = 26 -> 0x1a
        # Month = 9 -> 0x09, Day = 23 -> 0x17, Hour = 14 -> 0x0e, Min = 30 -> 0x1e, Sec = 45 -> 0x2d
        # Reminder hex = 00080014003c
        expected_hex = "505400140305070807031a09170e1e2d072600080014003c"
        self.assertEqual(cmd, bytes.fromhex(expected_hex))
        self.assertTrue(cmd.startswith(b"PT"))  # 0x50 0x54

    def test_cmd_sync_settings_custom_goal(self):
        """Verify sync settings with custom goal."""
        fixed_time = datetime(2025, 1, 5, 8, 0, 0)
        with patch("collector.collector.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_time
            cmd = cmd_sync_settings(goal_ml=2500)

        # 2500 ml -> 0x09c4
        # Year 25 -> 0x19, Month 1 -> 0x01, Day 5 -> 0x05, 08:00:00 -> 0x08 0x00 0x00
        expected_hex = "50540014030509c40703190105080000072600080014003c"
        self.assertEqual(cmd, bytes.fromhex(expected_hex))

    def test_cmd_request_water_logs(self):
        """Verify request water logs command."""
        cmd = cmd_request_water_logs()
        self.assertEqual(cmd, bytes.fromhex("4754000106"))

    def test_cmd_ack_water_logs(self):
        """Verify ack water logs command encodes byte length as 4 hex chars."""
        cases = [
            (0, "5250000403060000"),
            (13, "525000040306000d"),
            (26, "525000040306001a"),
            (130, "5250000403060082"),
            (1300, "5250000403060514"),
        ]
        for total_bytes, expected_hex in cases:
            with self.subTest(total_bytes=total_bytes):
                cmd = cmd_ack_water_logs(total_bytes)
                self.assertEqual(cmd, bytes.fromhex(expected_hex))
                self.assertTrue(cmd.startswith(b"RP"))  # 0x52 0x50

    def test_cmd_sync_today_amount(self):
        """Verify sync today's intake amount to bottle display."""
        cases = [
            (0, "5054000403040000"),
            (500, "50540004030401f4"),
            (1800, "5054000403040708"),
            (3000, "5054000403040bb8"),
        ]
        for ml, expected_hex in cases:
            with self.subTest(ml=ml):
                cmd = cmd_sync_today_amount(ml)
                self.assertEqual(cmd, bytes.fromhex(expected_hex))

    def test_cmd_clear_offline(self):
        """Verify nuclear clear offline data command."""
        cmd = cmd_clear_offline()
        self.assertEqual(cmd, bytes.fromhex("50540003021c05"))

    def test_cmd_flash_led(self):
        """Verify flash LED pulse command."""
        cmd = cmd_flash_led()
        self.assertEqual(cmd, bytes.fromhex("50540003021d01"))

    def test_cmd_set_led(self):
        """Verify set LED mode and color command encoding."""
        cases = [
            ("default", "blue", "5054000605fb000000ff"),
            ("breathe", "red", "5054000605fb01ff0000"),
            ("calm", "yellow", "5054000605fb02ffff00"),
            ("rainbow", "green", "5054000605fb0300ff00"),
            ("warmth", "cyan", "5054000605fb0500ffff"),
            ("christmas", "purple", "5054000605fb06ff00ff"),
            ("default", "white", "5054000605fb00ffffff"),
            # Custom 6-character hex color
            ("breathe", "123456", "5054000605fb01123456"),
            # Unknown mode falls back to "00"
            ("unknown_mode", "blue", "5054000605fb000000ff"),
            # Unknown color name falls back to "0000ff"
            ("calm", "nonexistent_color", "5054000605fb020000ff"),
        ]
        for mode, color, expected_hex in cases:
            with self.subTest(mode=mode, color=color):
                cmd = cmd_set_led(mode, color)
                self.assertEqual(cmd, bytes.fromhex(expected_hex))

    def test_cmd_set_reminder_on(self):
        """Verify setting active reminder with wake, sleep, and interval."""
        cmd = cmd_set_reminder(True, 8, 30, 21, 45, 60)
        expected_hex = "50540008072601081e152d3c"
        self.assertEqual(cmd, bytes.fromhex(expected_hex))

    def test_cmd_set_reminder_off(self):
        """Verify setting disabled reminder."""
        cmd = cmd_set_reminder(False, 9, 0, 22, 0, 30)
        expected_hex = "50540008072600090016001e"
        self.assertEqual(cmd, bytes.fromhex(expected_hex))

    def test_cmd_set_goal(self):
        """Verify goal setting command encoding."""
        cmd = cmd_set_goal(2000)
        self.assertEqual(cmd, bytes.fromhex("50540004030507d0"))

    def test_cmd_recalibrate_full(self):
        """Verify water sensor recalibration for full bottle."""
        cmd = cmd_recalibrate(full=True)
        self.assertEqual(cmd, bytes.fromhex("5054000302A101"))

    def test_cmd_recalibrate_empty(self):
        """Verify water sensor recalibration for empty bottle."""
        cmd = cmd_recalibrate(full=False)
        self.assertEqual(cmd, bytes.fromhex("5054000302A601"))

    def test_send_cmd_led_mode_choices(self):
        """Verify send_cmd.py parser supports all 6 official LED modes."""
        import send_cmd
        expected_modes = ["default", "breathe", "calm", "rainbow", "warmth", "christmas"]
        # Parse arguments for led subcommand
        for mode in expected_modes:
            with patch("sys.argv", ["send_cmd.py", "led", mode, "blue"]):
                # Should not raise SystemExit
                parser = None
                # Test by extracting the parser directly from send_cmd
                import argparse
                # We can check that send_cmd.py's choices contain all expected modes
                # Or run its arg parsing logic
        # Let's inspect the subparser choices directly:
        with patch("sys.argv", ["send_cmd.py", "--help"]):
            # Inspect send_cmd module directly
            import sys
            import io
            from contextlib import redirect_stdout, redirect_stderr
            # Parse 'led' command with each mode
            for mode in expected_modes:
                with patch("sys.argv", ["send_cmd.py", "led", mode]):
                    # Simulate calling send_cmd with each mode
                    # Using send_cmd's parser
                    with patch("send_cmd.cmd_led"):
                        with redirect_stdout(io.StringIO()):
                            send_cmd.main()


if __name__ == "__main__":
    unittest.main()
