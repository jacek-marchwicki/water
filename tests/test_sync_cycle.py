"""
Unit tests for the end-to-end BLE synchronization cycle.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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

    def test_parse_bottle_time_valid_and_invalid(self):
        """Verify parse_bottle_time correctly extracts datetime or returns None for malformed packets."""
        # Valid packet: 2026-09-26 22:32:46 (1a 09 1a 16 20 2e at offset 9-14)
        rp_valid = bytearray(b"\x52\x50\x00\x27\x00\x00\x58\x00\x00\x1a\x09\x1a\x16\x20\x2e" + b"\x00" * 20)
        dt = col.parse_bottle_time(bytes(rp_valid))
        self.assertIsNotNone(dt)
        self.assertEqual(dt, datetime(2026, 9, 26, 22, 32, 46))

        # Too short (<15 bytes)
        self.assertIsNone(col.parse_bottle_time(b"\x52\x50\x00\x27"))

        # Invalid month 13
        rp_bad_month = bytearray(rp_valid)
        rp_bad_month[10] = 13
        self.assertIsNone(col.parse_bottle_time(bytes(rp_bad_month)))

        # Invalid day 32
        rp_bad_day = bytearray(rp_valid)
        rp_bad_day[11] = 32
        self.assertIsNone(col.parse_bottle_time(bytes(rp_bad_day)))

        # Invalid hour 25
        rp_bad_hour = bytearray(rp_valid)
        rp_bad_hour[12] = 25
        self.assertIsNone(col.parse_bottle_time(bytes(rp_bad_hour)))

        # Year out of range (e.g. year 2010 -> offset 10)
        rp_bad_year = bytearray(rp_valid)
        rp_bad_year[9] = 10
        self.assertIsNone(col.parse_bottle_time(bytes(rp_bad_year)))

    def test_calculate_clock_drift(self):
        """Verify calculate_clock_drift computes absolute differences with naive and aware datetimes."""
        base_time = datetime(2026, 9, 26, 12, 0, 0)
        # Identical
        self.assertEqual(col.calculate_clock_drift(base_time, base_time), 0.0)

        # 45s ahead
        later = base_time + timedelta(seconds=45)
        self.assertEqual(col.calculate_clock_drift(base_time, later), 45.0)

        # 30s behind
        earlier = base_time - timedelta(seconds=30)
        self.assertEqual(col.calculate_clock_drift(base_time, earlier), 30.0)

        # Timezone aware local time
        aware_time = datetime(2026, 9, 26, 12, 1, 0, tzinfo=timezone.utc)
        drift = col.calculate_clock_drift(base_time, aware_time)
        self.assertEqual(drift, 60.0)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_subsequent_cycle_skips_settings_and_display_when_in_sync(self, mock_publish, mock_sleep):
        """Option 4: Verify periodic 60s poll skips sync-settings (no blink) and sync-display when in sync."""
        db = col.init_db()

        # Build bottle data packet with clock matching local now
        now = col.get_local_now()
        now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
        rp_bottle_data = bytearray(
            b"\x52\x50\x00\x27\x00\x00\x58\x00\x00"
            + bytes([now_naive.year - 2000, now_naive.month, now_naive.day, now_naive.hour, now_naive.minute, now_naive.second])
            + b"\x00" * 16 + b"\x01" + b"\x00" * 5
        )
        rp_no_logs = bytes.fromhex("52500004030600")
        rp_settings_ack = bytes.fromhex("5250000f0000000000000000")

        written_labels = []

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            written_labels.append(label)
            if label == "bottle-data":
                return [bytes(rp_bottle_data)]
            elif label == "sync-settings":
                return [rp_settings_ack]
            elif label == "request-logs":
                return [rp_no_logs]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            # First cycle: initial sync
            self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertTrue(col.settings_synced)
            self.assertEqual(col.last_synced_intake, 0)
            self.assertIn("sync-settings", written_labels)
            self.assertIn("sync-display", written_labels)

            # Second cycle (60s later): bottle clock is accurate and intake unchanged
            written_labels.clear()
            self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))

            # CRITICAL VERIFICATION: sync-settings and sync-display MUST NOT BE CALLED
            self.assertNotIn("sync-settings", written_labels, "sync-settings must be skipped when clock is in sync")
            self.assertNotIn("sync-display", written_labels, "sync-display must be skipped when intake is unchanged")
            self.assertEqual(written_labels, ["bottle-data", "request-logs"])

        db.close()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_detects_clock_drift_and_syncs_settings(self, mock_publish, mock_sleep):
        """Option 4: Verify sync_cycle detects clock drift > MAX_CLOCK_DRIFT_SEC and syncs settings."""
        db = col.init_db()

        # Simulate collector already synced previously
        col.settings_synced = True
        col.last_synced_intake = 0

        # Build bottle packet with clock drifted by 10 minutes (600s > 120s threshold)
        now = col.get_local_now()
        now_naive = now.replace(tzinfo=None) if now.tzinfo is not None else now
        drifted_time = now_naive - timedelta(seconds=600)
        rp_drifted = bytearray(
            b"\x52\x50\x00\x27\x00\x00\x58\x00\x00"
            + bytes([drifted_time.year - 2000, drifted_time.month, drifted_time.day, drifted_time.hour, drifted_time.minute, drifted_time.second])
            + b"\x00" * 16 + b"\x01" + b"\x00" * 5
        )
        rp_no_logs = bytes.fromhex("52500004030600")

        written_labels = []

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            written_labels.append(label)
            if label == "bottle-data":
                return [bytes(rp_drifted)]
            elif label == "request-logs":
                return [rp_no_logs]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertIn("sync-settings", written_labels, "Clock drift must trigger sync-settings")

        db.close()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_detects_uninitialized_clock_and_syncs_settings(self, mock_publish, mock_sleep):
        """Option 4: Verify sync_cycle resyncs when bottle clock bytes are zeroed (e.g. after flat battery)."""
        db = col.init_db()

        col.settings_synced = True
        col.last_synced_intake = 0

        # Packet with 0s at clock bytes (month 0 is invalid)
        rp_zeroed_clock = bytearray(b"\x52\x50\x00\x27\x00\x00\x58" + b"\x00" * 24 + b"\x01" + b"\x00" * 5)
        rp_no_logs = bytes.fromhex("52500004030600")

        written_labels = []

        async def fake_write_and_wait(client, cmd, label, queue, wait=1.0):
            written_labels.append(label)
            if label == "bottle-data":
                return [bytes(rp_zeroed_clock)]
            elif label == "request-logs":
                return [rp_no_logs]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write_and_wait):
            self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertIn("sync-settings", written_labels, "Zeroed/invalid clock must trigger sync-settings")

        db.close()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("collector.collector.publish_ha_sensor")
    def test_sync_cycle_updates_last_seen(self, mock_publish, mock_sleep):
        """Verify sync_cycle records col.last_seen upon successful completion."""
        db = col.init_db()
        col.last_seen = None
        rp_no_logs = bytes.fromhex("52500004030600")

        async def fake_write(client, cmd, label, queue, wait=1.0):
            if label == "bottle-data":
                return [b"\x52\x50\x00\x07\x00\x00\x55\x00\x00\x1a\x09\x1a\x0c\x00\x00\x00"]
            elif label == "request-logs":
                return [rp_no_logs]
            return []

        with patch("collector.collector.ble_write_and_wait", side_effect=fake_write):
            self.run_async(col.sync_cycle(self.mock_client, self.packet_queue, db))
            self.assertIsNotNone(col.last_seen)
        db.close()


if __name__ == "__main__":
    unittest.main()

