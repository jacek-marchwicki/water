"""
Unit tests for BlueZ Bluetooth state cleanup and power cycle helpers.
"""
from __future__ import annotations

import subprocess
import unittest
from unittest.mock import call, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestBlueZ(IsolatedCollectorTestCase):
    @patch("subprocess.run")
    def test_bluez_remove_device_success(self, mock_run):
        """Verify bluez_remove_device calls bluetoothctl remove with expected arguments."""
        col.bluez_remove_device("A4:C1:38:32:D7:DE")
        mock_run.assert_called_once_with(
            ["bluetoothctl", "remove", "A4:C1:38:32:D7:DE"],
            capture_output=True,
            timeout=5,
        )

    @patch("subprocess.run", side_effect=FileNotFoundError("bluetoothctl not found"))
    def test_bluez_remove_device_exception_swallowed(self, mock_run):
        """Verify bluez_remove_device does not raise exception when bluetoothctl fails or is missing."""
        try:
            col.bluez_remove_device("A4:C1:38:32:D7:DE")
        except Exception as e:
            self.fail(f"bluez_remove_device unexpectedly raised {e}")

    @patch("time.sleep")
    @patch("subprocess.run")
    def test_bluez_power_cycle_success(self, mock_run, mock_sleep):
        """Verify bluez_power_cycle runs power off, sleeps, power on, and sleeps."""
        col.bluez_power_cycle()

        expected_calls = [
            call(["bluetoothctl", "power", "off"], capture_output=True, timeout=5),
            call(["bluetoothctl", "power", "on"], capture_output=True, timeout=5),
        ]
        mock_run.assert_has_calls(expected_calls)
        self.assertEqual(mock_sleep.call_count, 2)
        mock_sleep.assert_has_calls([call(1), call(2)])

    @patch("time.sleep")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="power off", timeout=5))
    def test_bluez_power_cycle_handles_timeout(self, mock_run, mock_sleep):
        """Verify bluez_power_cycle handles subprocess errors without raising."""
        try:
            col.bluez_power_cycle()
        except Exception as e:
            self.fail(f"bluez_power_cycle unexpectedly raised {e}")

    @patch("collector.collector.bluez_power_cycle")
    @patch("time.sleep")
    @patch("collector.collector.bluez_remove_device")
    def test_bluez_full_reset(self, mock_remove, mock_sleep, mock_power):
        """Verify bluez_full_reset orchestrates remove, sleep, and power cycle."""
        col.bluez_full_reset("AA:BB:CC:DD:EE:FF")
        mock_remove.assert_called_once_with("AA:BB:CC:DD:EE:FF")
        mock_sleep.assert_called_once_with(1)
        mock_power.assert_called_once()


if __name__ == "__main__":
    unittest.main()
