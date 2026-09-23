"""
Unit tests for server.py covering dynamic goal configuration, settings persistence,
and goal reporting across /api/today, /api/history, /api/widget, and /commands/goal.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server.server as srv


class AsyncCursorWrapper:
    """Wraps a standard sqlite3.Cursor in an async interface matching aiosqlite."""

    def __init__(self, cursor: sqlite3.Cursor):
        self._cursor = cursor

    async def fetchone(self):
        return self._cursor.fetchone()

    async def fetchall(self):
        return self._cursor.fetchall()


class AsyncDbWrapper:
    """Wraps a standard sqlite3.Connection in an async interface matching aiosqlite."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    async def execute(self, sql: str, params: tuple | list = ()):
        cursor = self._conn.execute(sql, params)
        return AsyncCursorWrapper(cursor)

    async def commit(self):
        self._conn.commit()

    async def close(self):
        self._conn.close()


class IsolatedServerTestCase(unittest.TestCase):
    """Provides an isolated database and async loop for testing server.py endpoints."""

    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        self.temp_dir = tempfile.mkdtemp(prefix="waterh_srv_test_")
        self.db_path = Path(self.temp_dir) / "test_server.db"

        # Initialize SQLite database with server.py tables
        self.raw_db = sqlite3.connect(str(self.db_path))
        self.raw_db.row_factory = sqlite3.Row
        self.raw_db.execute("""
            CREATE TABLE IF NOT EXISTS sips (
                id INTEGER PRIMARY KEY,
                timestamp TEXT UNIQUE NOT NULL,
                intake_ml INTEGER NOT NULL,
                temp_c REAL,
                unknown INTEGER,
                raw_hex TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.raw_db.execute("CREATE INDEX IF NOT EXISTS idx_sips_date ON sips (DATE(timestamp))")
        self.raw_db.execute("""
            CREATE TABLE IF NOT EXISTS heartbeats (
                id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                detail TEXT,
                collector_ts TEXT,
                received_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.raw_db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.raw_db.commit()

        # Save previous globals
        self._orig_db = srv.db
        self._orig_db_path = srv.DB_PATH

        # Assign async-wrapped connection
        self.async_db = AsyncDbWrapper(self.raw_db)
        srv.db = self.async_db
        srv.DB_PATH = str(self.db_path)

    def tearDown(self):
        self.raw_db.close()
        srv.db = self._orig_db
        srv.DB_PATH = self._orig_db_path
        self.loop.close()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)


class TestServerGoalAndEndpoints(IsolatedServerTestCase):

    def test_get_goal_ml_default_when_no_setting(self):
        """Verify get_goal_ml returns default 1800 when no setting and no env var."""
        with patch.dict(os.environ, {}, clear=False):
            if "WATERH_GOAL_ML" in os.environ:
                del os.environ["WATERH_GOAL_ML"]
            goal = self.run_async(srv.get_goal_ml())
            self.assertEqual(goal, 1800)

    def test_get_goal_ml_from_env_fallback(self):
        """Verify get_goal_ml returns WATERH_GOAL_ML env var when setting not in DB."""
        with patch.dict(os.environ, {"WATERH_GOAL_ML": "2100"}):
            goal = self.run_async(srv.get_goal_ml())
            self.assertEqual(goal, 2100)

    def test_get_goal_ml_from_database_settings(self):
        """Verify get_goal_ml retrieves goal stored in SQLite settings table."""
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2400')")
        self.raw_db.commit()

        # Should prefer DB setting over env var
        with patch.dict(os.environ, {"WATERH_GOAL_ML": "1500"}):
            goal = self.run_async(srv.get_goal_ml())
            self.assertEqual(goal, 2400)

    def test_set_goal_updates_database(self):
        """Verify set_goal endpoint persists new goal to settings table and returns ok."""
        payload = srv.GoalPayload(ml=2250)
        res = self.run_async(srv.set_goal(payload))

        self.assertEqual(res, {"ok": True, "goal_ml": 2250})

        row = self.raw_db.execute("SELECT value FROM settings WHERE key = 'goal_ml'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "2250")

        # Calling get_goal_ml afterwards returns the updated value
        goal = self.run_async(srv.get_goal_ml())
        self.assertEqual(goal, 2250)

    def test_set_goal_overwrites_existing_goal(self):
        """Verify set_goal replaces previously stored goal in settings table."""
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '1800')")
        self.raw_db.commit()

        payload = srv.GoalPayload(ml=3000)
        res = self.run_async(srv.set_goal(payload))
        self.assertEqual(res["goal_ml"], 3000)

        row = self.raw_db.execute("SELECT value FROM settings WHERE key = 'goal_ml'").fetchone()
        self.assertEqual(row["value"], "3000")

    def test_today_endpoint_uses_dynamic_goal(self):
        """Verify /api/today uses dynamic goal_ml and calculates goal_pct accurately."""
        # Insert 2 sips for today
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.raw_db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 200, 22.5)",
            (f"{today_date}T09:00:00",)
        )
        self.raw_db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 300, 23.0)",
            (f"{today_date}T11:00:00",)
        )
        # Store goal 2000 in DB
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2000')")
        self.raw_db.commit()

        data = self.run_async(srv.today())

        self.assertEqual(data["total_ml"], 500)
        self.assertEqual(data["goal_ml"], 2000)
        self.assertEqual(data["goal_pct"], 25)  # 500 / 2000 * 100 = 25%
        self.assertEqual(data["sip_count"], 2)
        self.assertEqual(data["last_temp_c"], 23.0)

    def test_today_endpoint_goal_pct_capped_at_100(self):
        """Verify /api/today caps goal_pct at 100% when intake exceeds goal."""
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.raw_db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 2500, 21.0)",
            (f"{today_date}T10:00:00",)
        )
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '1800')")
        self.raw_db.commit()

        data = self.run_async(srv.today())
        self.assertEqual(data["total_ml"], 2500)
        self.assertEqual(data["goal_ml"], 1800)
        self.assertEqual(data["goal_pct"], 100)

    def test_history_endpoint_includes_goal_ml(self):
        """Verify /api/history returns goal_ml matching the active database goal."""
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2200')")
        self.raw_db.execute("INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES ('2026-09-20T10:00:00', 1200, 20.0)")
        self.raw_db.execute("INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES ('2026-09-21T10:00:00', 1600, 21.0)")
        self.raw_db.commit()

        data = self.run_async(srv.history(days=7))

        self.assertIn("goal_ml", data)
        self.assertEqual(data["goal_ml"], 2200)
        self.assertEqual(len(data["days"]), 2)
        self.assertEqual(data["best_day_ml"], 1600)
        self.assertEqual(data["avg_daily_ml"], 1400)
        self.assertEqual(data["current_streak"], 2)

    def test_widget_endpoint_reflects_dynamic_goal(self):
        """Verify /api/widget delegates to today() and includes correct goal_pct."""
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.raw_db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 900, 22.0)",
            (f"{today_date}T08:00:00",)
        )
        self.raw_db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '1800')")
        self.raw_db.commit()

        widget_data = self.run_async(srv.widget())

        self.assertEqual(widget_data["today_ml"], 900)
        self.assertEqual(widget_data["goal_pct"], 50)
        self.assertEqual(widget_data["sip_count"], 1)
        self.assertEqual(widget_data["last_temp_c"], 22.0)


if __name__ == "__main__":
    unittest.main()
