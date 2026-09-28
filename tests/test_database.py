"""
Unit tests for WaterH SQLite database operations.
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch, MagicMock

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestDatabase(IsolatedCollectorTestCase):
    def test_init_db_creates_tables_and_indexes(self):
        """Verify init_db initializes the database with required tables and is idempotent."""
        db = col.init_db()
        cursor = db.cursor()

        # Verify tables exist
        tables = [row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        self.assertIn("sips", tables)
        self.assertIn("syncs", tables)
        self.assertIn("settings", tables)

        # Verify idempotence (can be called repeatedly without error)
        db2 = col.init_db()
        self.assertIsNotNone(db2)
        db.close()
        db2.close()

    def test_get_goal_ml_default(self):
        """Verify get_goal_ml returns default 1800 when no value is stored."""
        self.assertEqual(col.get_goal_ml(), 1800)

    def test_get_and_set_goal_ml(self):
        """Verify set_goal_ml updates database, global variable, and HA sensors."""
        mock_ha_conn = MagicMock()
        col.ha_conn = mock_ha_conn

        with patch("collector.collector.publish_ha_sensor") as mock_publish:
            col.set_goal_ml(2200)

            self.assertEqual(col.GOAL_ML, 2200)
            self.assertEqual(col.get_goal_ml(), 2200)
            mock_publish.assert_called_with(
                "daily_goal", 2200, unit="mL", friendly_name="WaterH Daily Goal", icon="mdi:target-variant"
            )
            mock_ha_conn.publish_state.assert_called_with("sensor/daily_goal", 2200)

    def test_set_goal_ml_handles_db_error_gracefully(self):
        """Verify set_goal_ml logs error and does not raise exception on database failure."""
        with patch("collector.collector.init_db", side_effect=sqlite3.OperationalError("disk failure")):
            col.set_goal_ml(2500)
            self.assertEqual(col.GOAL_ML, 2500)

    def test_store_sips_and_deduplication(self):
        """Verify store_sips inserts new records and deduplicates by unique timestamp."""
        db = col.init_db()
        sips = [
            {
                "timestamp": "2026-09-23T10:00:00",
                "intake_ml": 250,
                "temp_c": 21.5,
                "tds": 60,
                "raw": "1a 09 17 0a 00 00 00 fa 00 3c 00 d7 00",
            },
            {
                "timestamp": "2026-09-23T11:00:00",
                "intake_ml": 300,
                "temp_c": 22.0,
                "tds": 62,
                "raw": "1a 09 17 0b 00 00 01 2c 00 3e 00 dc 00",
            },
        ]

        # First store: 2 new sips
        new_count = col.store_sips(db, sips)
        self.assertEqual(new_count, 2)

        # Verify records in database
        rows = db.execute("SELECT timestamp, intake_ml, temp_c, tds, raw_hex, synced FROM sips ORDER BY timestamp").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "2026-09-23T10:00:00")
        self.assertEqual(rows[0][1], 250)
        self.assertAlmostEqual(rows[0][2], 21.5)
        self.assertEqual(rows[0][3], 60)
        self.assertEqual(rows[0][5], 0)  # synced default

        # Second store with one duplicate and one new record
        sips_mixed = [
            sips[0],  # Duplicate
            {
                "timestamp": "2026-09-23T12:00:00",
                "intake_ml": 400,
                "temp_c": 20.0,
                "tds": 58,
                "raw": "raw_hex_here",
            },
        ]
        new_count_2 = col.store_sips(db, sips_mixed)
        self.assertEqual(new_count_2, 1)

        total_rows = db.execute("SELECT COUNT(*) FROM sips").fetchone()[0]
        self.assertEqual(total_rows, 3)
        db.close()

    def test_log_sync(self):
        """Verify log_sync writes sync metadata to the syncs table."""
        db = col.init_db()
        col.log_sync(db, sip_count=5, new_count=3, acked_bytes=65)

        row = db.execute("SELECT sip_count, new_count, acked_bytes FROM syncs").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 5)
        self.assertEqual(row[1], 3)
        self.assertEqual(row[2], 65)
        db.close()

    def test_get_unsynced_and_mark_synced(self):
        """Verify get_unsynced retrieves pending records and mark_synced updates them."""
        db = col.init_db()
        sips = [
            {"timestamp": "2026-09-23T08:00:00", "intake_ml": 100, "temp_c": 20.0, "tds": 50, "raw": "hex1"},
            {"timestamp": "2026-09-23T09:00:00", "intake_ml": 150, "temp_c": 21.0, "tds": 52, "raw": "hex2"},
        ]
        col.store_sips(db, sips)

        unsynced = col.get_unsynced(db)
        self.assertEqual(len(unsynced), 2)
        id1, id2 = unsynced[0]["id"], unsynced[1]["id"]
        self.assertEqual(unsynced[0]["timestamp"], "2026-09-23T08:00:00")
        self.assertEqual(unsynced[0]["intake_ml"], 100)

        # Mark first record as synced
        col.mark_synced(db, [id1])

        unsynced_after = col.get_unsynced(db)
        self.assertEqual(len(unsynced_after), 1)
        self.assertEqual(unsynced_after[0]["id"], id2)

        # Mark with empty list should be safe no-op
        col.mark_synced(db, [])
        self.assertEqual(len(col.get_unsynced(db)), 1)
        db.close()

    def test_sip_storage_and_query_at_0800(self):
        """Verify sips logged at 08:00 are stored and queried with exact local timestamp."""
        from datetime import datetime
        db = col.init_db()
        test_sips = [
            {"timestamp": "2026-09-28T08:00:00", "intake_ml": 250, "temp_c": None, "tds": 50, "raw": "hex"}
        ]
        col.store_sips(db, test_sips)

        now = datetime(2026, 9, 28, 8, 15)
        total = col.get_today_total(db, now_dt=now)
        self.assertEqual(total, 250)

        sips = col.get_today_sips(db, now_dt=now)
        self.assertEqual(len(sips), 1)
        self.assertEqual(sips[0][1], "2026-09-28T08:00:00")
        self.assertEqual(sips[0][2], 250)

        last_time = col.get_last_sip_time(db, now_dt=now)
        self.assertIsNotNone(last_time)
        self.assertEqual(last_time.hour, 8)
        self.assertEqual(last_time.minute, 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
