"""
Unit tests for the end-to-end BLE synchronization cycle.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestSyncCycle(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        self.packet_queue = asyncio.Queue()
        self.mock_client = MagicMock()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_no_water_logs(self, mock_publish, mock_sleep):
        """Verify sync_cycle when bottle reports no new sip logs."""
        db = col.init_db()

        # Build RP bottle data packet (>31 bytes)
        # byte 0-1: 52 50 ('RP')
        # byte 6: 88 (88% battery)
        # byte 31: 1 (charging)
        rp_bottle_data = bytearray(b"\x52\x50\x00\x27\x00\x00\x58" + b"\x00" * 24 + b"\x01" + b"\x00" * 5)

        # Settings ack: byte 10 = 0x00
        rp_settings_ack = bytearray(b"\x52\x50\x00\x0f\x00\x00\x00\x00\x00\x00\x00\x00")

        # Water log status: byte 5 = 0x06, byte 6 = 0x00 (no data)
        rp_no_logs = bytes.fromhex("52500004030600")

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            if label == "bottle-data":
                return [bytes(rp_bottle_data)]
            elif label == "sync-settings":
                return [bytes(rp_settings_ack)]
            elif label == "request-logs":
                return [rp_no_logs]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            success = self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertTrue(success)

        # Check battery published
        mock_publish.assert_any_call("battery", 88, unit="%", friendly_name="WaterH Battery", device_class="battery")

        # Verify syncs table recorded empty sync
        sync_row = db.execute("SELECT sip_count, new_count, acked_bytes FROM syncs").fetchone()
        self.assertEqual(sync_row, (0, 0, 0))
        db.close()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_with_water_logs(self, mock_publish, mock_sleep):
        """Verify sync_cycle downloading sips, persisting to DB, acknowledging, and syncing display."""
        db = col.init_db()

        rp_bottle_data = bytearray(b"\x52\x50\x00\x27\x00\x00\x4b" + b"\x00" * 24 + b"\x00" + b"\x00" * 5)  # 75% battery

        # Water log status: byte 5 = 0x06, byte 6 = 0x01 (data found!)
        rp_has_logs = bytes.fromhex("52500004030601")

        # PT Packet containing 1 sip record (300ml, 65tds, 21.0C)
        pt_header = bytes.fromhex("5054000d0d06")
        pt_rec = bytes.fromhex("1a09170e0000012c004100d200")
        pt_packet = pt_header + pt_rec

        written_commands = []

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            written_commands.append((cmd, label))
            if label == "bottle-data":
                return [bytes(rp_bottle_data)]
            elif label == "request-logs":
                return [rp_has_logs, pt_packet]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            success = self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertTrue(success)

        # Verify sip stored in DB
        sips = db.execute("SELECT intake_ml, tds, temp_c FROM sips").fetchall()
        self.assertEqual(len(sips), 1)
        self.assertEqual(sips[0][0], 300)
        self.assertEqual(sips[0][1], 65)
        self.assertAlmostEqual(sips[0][2], 21.0)

        # Verify HA sensors published for temperature and tds
        mock_publish.assert_any_call("temperature", 21.0, unit="°C", friendly_name="WaterH Water Temperature", device_class="temperature")
        mock_publish.assert_any_call("tds", 65, unit="ppm", friendly_name="WaterH Water Quality (TDS)", icon="mdi:water-check")

        # Verify ack command written with 13 bytes (1 sip * 13)
        ack_labels = [label for cmd, label in written_commands if label == "ack-logs"]
        self.assertIn("ack-logs", ack_labels)
        expected_ack_cmd = col.cmd_ack_water_logs(13)
        self.assertTrue(any(cmd == expected_ack_cmd for cmd, label in written_commands))

        # Verify sync logged
        sync_row = db.execute("SELECT sip_count, new_count, acked_bytes FROM syncs").fetchone()
        self.assertEqual(sync_row, (1, 1, 13))
        db.close()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_missing_bottle_data_response(self, mock_publish, mock_sleep):
        """Verify sync_cycle completes gracefully when bottle data response is absent."""
        db = col.init_db()

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            if label == "request-logs":
                return [bytes.fromhex("52500004030600")]  # No logs
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            success = self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertTrue(success)

        # Battery should not be published if no RP packet
        battery_calls = [c for c in mock_publish.call_args_list if c[0][0] == "battery"]
        self.assertEqual(len(battery_calls), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
