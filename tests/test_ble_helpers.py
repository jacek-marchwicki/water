"""
Unit tests for BLE communication helpers, GATT characteristic resolution, and device discovery.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestBLEHelpers(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def test_drain_queue(self):
        """Verify drain_queue extracts all items and leaves the queue empty."""
        q = asyncio.Queue()
        self.assertEqual(col.drain_queue(q), [])

        q.put_nowait(b"\x01")
        q.put_nowait(b"\x02")
        q.put_nowait(b"\x03")

        items = col.drain_queue(q)
        self.assertEqual(items, [b"\x01", b"\x02", b"\x03"])
        self.assertTrue(q.empty())

    def test_resolve_gatt_characteristics_finds_uuid(self):
        """Verify resolve_gatt_characteristics identifies write and notify UUIDs from GATT service tree."""
        mock_char_notify = MagicMock()
        mock_char_notify.uuid = "0000ffe4-0000-1000-8000-00805f9b34fb"
        mock_char_notify.properties = ["notify"]

        mock_char_write = MagicMock()
        mock_char_write.uuid = "0000ffe9-0000-1000-8000-00805f9b34fb"
        mock_char_write.properties = ["write-without-response"]

        mock_service = MagicMock()
        mock_service.characteristics = [mock_char_notify, mock_char_write]

        mock_client = MagicMock()
        mock_client.services = [mock_service]

        self.run_async(col.resolve_gatt_characteristics(mock_client))

        self.assertEqual(col.WRITE_CHAR, "0000ffe9-0000-1000-8000-00805f9b34fb")
        self.assertEqual(col.NOTIFY_CHAR, "0000ffe4-0000-1000-8000-00805f9b34fb")

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_resolve_gatt_characteristics_immediate_discovery(self, mock_sleep):
        """Verify resolve_gatt_characteristics resolves immediately without sleeping when services exist."""
        mock_char_notify = MagicMock()
        mock_char_notify.uuid = "0000ffe4-0000-1000-8000-00805f9b34fb"
        mock_char_notify.properties = ["notify"]

        mock_char_write = MagicMock()
        mock_char_write.uuid = "0000ffe9-0000-1000-8000-00805f9b34fb"
        mock_char_write.properties = ["write-without-response"]

        mock_service = MagicMock()
        mock_service.characteristics = [mock_char_notify, mock_char_write]

        mock_client = MagicMock()
        mock_client.services = [mock_service]

        self.run_async(col.resolve_gatt_characteristics(mock_client))

        self.assertEqual(col.WRITE_CHAR, "0000ffe9-0000-1000-8000-00805f9b34fb")
        self.assertEqual(col.NOTIFY_CHAR, "0000ffe4-0000-1000-8000-00805f9b34fb")
        mock_sleep.assert_not_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_resolve_gatt_characteristics_retries_and_succeeds(self, mock_sleep):
        """Verify resolve_gatt_characteristics retries multiple times when services are delayed, then succeeds."""
        mock_char_notify = MagicMock()
        mock_char_notify.uuid = "0000ffe4-0000-1000-8000-00805f9b34fb"
        mock_char_notify.properties = ["notify"]

        mock_char_write = MagicMock()
        mock_char_write.uuid = "0000ffe9-0000-1000-8000-00805f9b34fb"
        mock_char_write.properties = ["write-without-response"]

        mock_service = MagicMock()
        mock_service.characteristics = [mock_char_notify, mock_char_write]

        mock_client = MagicMock()
        mock_client.services = []

        call_count = 0
        async def fake_get_services():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                mock_client.services = [mock_service]
                return [mock_service]
            return []

        mock_client.get_services = AsyncMock(side_effect=fake_get_services)

        col.WRITE_CHAR = "old_write"
        col.NOTIFY_CHAR = "old_notify"

        self.run_async(col.resolve_gatt_characteristics(mock_client, timeout=5.0, poll_interval=0.5))

        self.assertEqual(col.WRITE_CHAR, "0000ffe9-0000-1000-8000-00805f9b34fb")
        self.assertEqual(col.NOTIFY_CHAR, "0000ffe4-0000-1000-8000-00805f9b34fb")
        self.assertEqual(mock_sleep.await_count, 2)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_resolve_gatt_characteristics_timeout_fallback(self, mock_sleep):
        """Verify resolve_gatt_characteristics retries up to timeout (5s) before falling back to defaults."""
        mock_client = MagicMock()
        mock_client.services = []
        mock_client.get_services = AsyncMock(return_value=[])

        col.WRITE_CHAR = "default_write"
        col.NOTIFY_CHAR = "default_notify"

        self.run_async(col.resolve_gatt_characteristics(mock_client, timeout=5.0, poll_interval=0.5))

        self.assertEqual(col.WRITE_CHAR, "default_write")
        self.assertEqual(col.NOTIFY_CHAR, "default_notify")
        self.assertEqual(mock_sleep.await_count, 10)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_resolve_gatt_characteristics_handles_none_services(self, mock_sleep):
        """Verify resolve_gatt_characteristics handles None services without throwing TypeError."""
        mock_char_notify = MagicMock()
        mock_char_notify.uuid = "0000ffe4-0000-1000-8000-00805f9b34fb"
        mock_char_notify.properties = ["notify"]

        mock_char_write = MagicMock()
        mock_char_write.uuid = "0000ffe9-0000-1000-8000-00805f9b34fb"
        mock_char_write.properties = ["write-without-response"]

        mock_service = MagicMock()
        mock_service.characteristics = [mock_char_notify, mock_char_write]

        mock_client = MagicMock()
        mock_client.services = None

        async def fake_get_services():
            mock_client.services = [mock_service]
            return [mock_service]

        mock_client.get_services = AsyncMock(side_effect=fake_get_services)

        col.WRITE_CHAR = "old_write"
        col.NOTIFY_CHAR = "old_notify"

        self.run_async(col.resolve_gatt_characteristics(mock_client, timeout=5.0, poll_interval=0.5))

        self.assertEqual(col.WRITE_CHAR, "0000ffe9-0000-1000-8000-00805f9b34fb")
        self.assertEqual(col.NOTIFY_CHAR, "0000ffe4-0000-1000-8000-00805f9b34fb")

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_resolve_gatt_characteristics_handles_exception(self, mock_sleep):
        """Verify resolve_gatt_characteristics gracefully handles errors without raising."""
        mock_client = MagicMock()
        mock_client.services = MagicMock(side_effect=AttributeError("no services"))

        col.WRITE_CHAR = "default_write"
        col.NOTIFY_CHAR = "default_notify"
        self.run_async(col.resolve_gatt_characteristics(mock_client))

        self.assertEqual(col.WRITE_CHAR, "default_write")
        self.assertEqual(col.NOTIFY_CHAR, "default_notify")

    def test_ble_write_direct_success(self):
        """Verify ble_write writes to primary WRITE_CHAR."""
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()

        self.run_async(col.ble_write(mock_client, b"\x01\x02", "test-cmd"))
        mock_client.write_gatt_char.assert_awaited_once_with(col.WRITE_CHAR, b"\x01\x02", response=False)

    def test_ble_write_fallback_success(self):
        """Verify ble_write tries fallback characteristic when primary write fails."""
        mock_client = MagicMock()

        fallback_char = MagicMock()
        fallback_char.uuid = "fallback-write-uuid"
        fallback_char.properties = ["write"]

        mock_service = MagicMock()
        mock_service.characteristics = [fallback_char]
        mock_client.services = [mock_service]

        async def fake_write(char_uuid, data, response=False):
            if char_uuid == col.WRITE_CHAR:
                raise Exception("Primary char write failed")
            return None

        mock_client.write_gatt_char = AsyncMock(side_effect=fake_write)

        self.run_async(col.ble_write(mock_client, b"\x05", "fallback-cmd"))
        self.assertEqual(mock_client.write_gatt_char.await_count, 2)
        mock_client.write_gatt_char.assert_awaited_with("fallback-write-uuid", b"\x05", response=False)
        self.assertEqual(col.WRITE_CHAR, "fallback-write-uuid")

    def test_ble_write_failure_raises(self):
        """Verify ble_write raises exception if primary and all fallbacks fail."""
        mock_client = MagicMock()
        mock_client.services = []
        mock_client.write_gatt_char = AsyncMock(side_effect=RuntimeError("GATT write completely broken"))

        with self.assertRaises(RuntimeError):
            self.run_async(col.ble_write(mock_client, b"\x09", "broken-cmd"))

    @patch("asyncio.sleep", new_callable=AsyncMock)
    def test_ble_write_and_wait(self, mock_sleep):
        """Verify ble_write_and_wait drains queue, writes, sleeps, and returns subsequent items."""
        mock_client = MagicMock()
        mock_client.write_gatt_char = AsyncMock()

        q = asyncio.Queue()
        q.put_nowait(b"stale_packet")

        async def simulate_response(*args, **kwargs):
            # Put response in queue during the sleep wait
            q.put_nowait(b"response_packet")

        mock_sleep.side_effect = simulate_response

        res = self.run_async(col.ble_write_and_wait(mock_client, b"\x01", "label", q, wait=1.0))
        self.assertEqual(res, [b"response_packet"])
        mock_sleep.assert_awaited_once_with(1.0)

    def test_find_waterh_device_by_address(self):
        """Verify find_waterh_device succeeds when address search finds device."""
        mock_device = MagicMock()
        mock_device.address = "A4:C1:38:32:D7:DE"

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_address = AsyncMock(return_value=mock_device)

        with patch.object(col, "BleakScanner", mock_scanner, create=True):
            device = self.run_async(col.find_waterh_device("A4:C1:38:32:D7:DE"))
            self.assertEqual(device, mock_device)

    def test_find_waterh_device_fallback_discover_address(self):
        """Verify find_waterh_device falls back to discover() matching MAC address case-insensitively."""
        mock_device = MagicMock()
        mock_device.address = "a4:c1:38:32:d7:de"
        mock_device.name = "Unknown"

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_address = AsyncMock(return_value=None)
        mock_scanner.discover = AsyncMock(return_value=[mock_device])

        with patch.object(col, "BleakScanner", mock_scanner, create=True):
            device = self.run_async(col.find_waterh_device("A4:C1:38:32:D7:DE"))
            self.assertEqual(device, mock_device)

    def test_find_waterh_device_fallback_discover_by_name(self):
        """Verify find_waterh_device discovers device by 'waterh' name match."""
        mock_device = MagicMock()
        mock_device.address = "11:22:33:44:55:66"
        mock_device.name = "WaterH Boost 24oz"

        mock_scanner = MagicMock()
        mock_scanner.find_device_by_address = AsyncMock(return_value=None)
        mock_scanner.discover = AsyncMock(return_value=[mock_device])

        with patch.object(col, "BleakScanner", mock_scanner, create=True):
            device = self.run_async(col.find_waterh_device("A4:C1:38:32:D7:DE"))
            self.assertEqual(device, mock_device)

    def test_find_waterh_device_not_found(self):
        """Verify find_waterh_device returns None when device is not found."""
        mock_scanner = MagicMock()
        mock_scanner.find_device_by_address = AsyncMock(return_value=None)
        mock_scanner.discover = AsyncMock(return_value=[])

        with patch.object(col, "BleakScanner", mock_scanner, create=True):
            device = self.run_async(col.find_waterh_device("A4:C1:38:32:D7:DE"))
            self.assertIsNone(device)


if __name__ == "__main__":
    unittest.main()
