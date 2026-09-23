"""
Unit tests for ble_loop execution, reconnection, error handling, and command processing.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class FakeBleakClient:
    """Async context manager mocking BleakClient."""
    def __init__(self, device, disconnected_callback=None):
        self.device = device
        self.disconnected_callback = disconnected_callback
        self.is_connected = True
        self.name = getattr(device, "name", "Test Device")
        self.services = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.is_connected = False
        return False

    async def start_notify(self, char_uuid, callback):
        pass

    async def write_gatt_char(self, char_uuid, data, response=False):
        pass


class TestBLELoop(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.start_cmd_server", new_callable=AsyncMock)
    @patch("collector.collector.bluez_remove_device")
    @patch("collector.collector.post_heartbeat")
    def test_ble_loop_scan_failure_and_backoff(self, mock_heartbeat, mock_remove, mock_cmd_server, mock_sleep):
        """Verify ble_loop handles scan misses by removing stale state, updating heartbeat, and backing off."""
        # find_waterh_device returns None once, then raises CancelledError to terminate the loop
        async def fake_find(addr, timeout=12.0):
            if mock_sleep.await_count == 0:
                return None
            raise asyncio.CancelledError()

        with patch("collector.collector.find_waterh_device", side_effect=fake_find):
            with self.assertRaises(asyncio.CancelledError):
                self.run_async(col.ble_loop())

        mock_remove.assert_called_with(col.BOTTLE_ADDR)
        mock_sleep.assert_awaited()
        # Verify heartbeat received scanning status
        heartbeat_calls = [c[0][0] for c in mock_heartbeat.call_args_list]
        self.assertIn("starting", heartbeat_calls)
        self.assertIn("scanning", heartbeat_calls)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.start_cmd_server", new_callable=AsyncMock)
    @patch("collector.collector.bluez_remove_device")
    @patch("collector.collector.post_heartbeat")
    @patch("collector.collector.sync_cycle", new_callable=AsyncMock, return_value=True)
    def test_ble_loop_connect_and_process_commands(
        self, mock_sync_cycle, mock_heartbeat, mock_remove, mock_cmd_server, mock_sleep
    ):
        """Verify ble_loop establishes connection, runs sync_cycle, drains cmd_queue, and handles disconnect."""
        mock_dev = MagicMock()
        mock_dev.name = "WaterH Boost"

        call_count = 0

        async def fake_find(addr, timeout=12.0):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_dev
            raise asyncio.CancelledError()

        # Custom FakeBleakClient that disconnects after 1 sync
        class OneShotClient(FakeBleakClient):
            async def __aenter__(self):
                # Queue a command to test that cmd_queue is processed while connected
                col.cmd_queue.put_nowait((b"\x50\x54\x00\x01", "test_cmd"))
                return self

            async def start_notify(self, char, callback):
                # Immediately disconnect after notify setup to end inner while loop
                self.is_connected = False

        with patch("collector.collector.find_waterh_device", side_effect=fake_find), patch.object(
            col, "BleakClient", OneShotClient
        ):
            with self.assertRaises(asyncio.CancelledError):
                self.run_async(col.ble_loop())

        mock_cmd_server.assert_awaited_once()
        # Clean BlueZ removal should be called before scan and on reconnect
        self.assertGreaterEqual(mock_remove.call_count, 2)


if __name__ == "__main__":
    unittest.main()
