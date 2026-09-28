"""
Unit tests for server.py using FastAPI TestClient and real aiosqlite.
Tests dynamic goal configuration, settings persistence, and goal reporting
across /api/today, /api/history, /api/widget, /commands/goal, and /api/goal.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiosqlite
from fastapi.testclient import TestClient

import server.server as srv


class IsolatedServerTestCase(unittest.TestCase):
    """Provides an isolated temporary aiosqlite database and TestClient for server.py."""

    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        self.temp_dir = tempfile.mkdtemp(prefix="waterh_srv_test_")
        self.db_path = Path(self.temp_dir) / "test_server.db"

        # Save previous globals
        self._orig_db = srv.db
        self._orig_db_path = srv.DB_PATH

        srv.DB_PATH = str(self.db_path)
        self.client_cm = TestClient(srv.app)
        self.client = self.client_cm.__enter__()

    def tearDown(self):
        if hasattr(self, "client_cm"):
            self.client_cm.__exit__(None, None, None)
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
        """Verify get_goal_ml returns default 1800 when no setting in DB and no env var."""
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
        self.run_async(srv.db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2400')"))
        self.run_async(srv.db.commit())

        # Should prefer DB setting over env var
        with patch.dict(os.environ, {"WATERH_GOAL_ML": "1500"}):
            goal = self.run_async(srv.get_goal_ml())
            self.assertEqual(goal, 2400)

    def test_post_commands_goal_endpoint(self):
        """Verify POST /commands/goal persists new goal and returns ok status."""
        resp = self.client.post("/commands/goal", json={"ml": 2250})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "goal_ml": 2250})

        # Verify persisted in database
        row = self.run_async((self.run_async(srv.db.execute("SELECT value FROM settings WHERE key = 'goal_ml'"))).fetchone())
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "2250")

        # Verify get_goal_ml returns 2250
        goal = self.run_async(srv.get_goal_ml())
        self.assertEqual(goal, 2250)

    def test_post_api_goal_endpoint(self):
        """Verify POST /api/goal also updates and persists the daily goal."""
        resp = self.client.post("/api/goal", json={"ml": 2700})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "goal_ml": 2700})

        row = self.run_async((self.run_async(srv.db.execute("SELECT value FROM settings WHERE key = 'goal_ml'"))).fetchone())
        self.assertEqual(row["value"], "2700")

    def test_today_endpoint_uses_dynamic_goal(self):
        """Verify GET /api/today uses dynamic goal_ml and calculates goal_pct accurately."""
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.run_async(srv.db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 200, 22.5)",
            (f"{today_date}T09:00:00",)
        ))
        self.run_async(srv.db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 300, 23.0)",
            (f"{today_date}T11:00:00",)
        ))
        # Store goal 2000 in DB
        self.run_async(srv.db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2000')"))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/today")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["total_ml"], 500)
        self.assertEqual(data["goal_ml"], 2000)
        self.assertEqual(data["goal_pct"], 25)  # 500 / 2000 * 100 = 25%
        self.assertEqual(data["sip_count"], 2)
        self.assertEqual(data["last_temp_c"], 23.0)

    def test_today_endpoint_goal_pct_capped_at_100(self):
        """Verify GET /api/today caps goal_pct at 100% when intake exceeds goal."""
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.run_async(srv.db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 2500, 21.0)",
            (f"{today_date}T10:00:00",)
        ))
        self.run_async(srv.db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '1800')"))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/today")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["total_ml"], 2500)
        self.assertEqual(data["goal_ml"], 1800)
        self.assertEqual(data["goal_pct"], 100)

    def test_history_endpoint_includes_goal_ml(self):
        """Verify GET /api/history returns goal_ml matching the active database goal."""
        self.run_async(srv.db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '2200')"))
        today = srv.datetime.now(srv.WATERH_TZ).date()
        d1 = (today - srv.timedelta(days=2)).isoformat()
        d2 = (today - srv.timedelta(days=1)).isoformat()
        self.run_async(srv.db.execute("INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 1200, 20.0)", (f"{d1}T10:00:00",)))
        self.run_async(srv.db.execute("INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 1600, 21.0)", (f"{d2}T10:00:00",)))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/history?days=7")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertIn("goal_ml", data)
        self.assertEqual(data["goal_ml"], 2200)
        self.assertEqual(len(data["days"]), 2)
        self.assertEqual(data["best_day_ml"], 1600)
        self.assertEqual(data["avg_daily_ml"], 1400)
        self.assertEqual(data["current_streak"], 2)

    def test_widget_endpoint_reflects_dynamic_goal(self):
        """Verify GET /api/widget delegates to today() and includes correct goal_pct."""
        today_date = srv.datetime.now(srv.WATERH_TZ).date().isoformat()
        self.run_async(srv.db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c) VALUES (?, 900, 22.0)",
            (f"{today_date}T08:00:00",)
        ))
        self.run_async(srv.db.execute("INSERT INTO settings (key, value) VALUES ('goal_ml', '1800')"))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/widget")
        self.assertEqual(resp.status_code, 200)
        widget_data = resp.json()

        self.assertEqual(widget_data["today_ml"], 900)
        self.assertEqual(widget_data["goal_pct"], 50)
        self.assertEqual(widget_data["sip_count"], 1)
        self.assertEqual(widget_data["last_temp_c"], 22.0)

    def test_lifespan_creates_settings_table(self):
        """Verify lifespan context manager creates settings table in newly initialized database."""
        current_db = srv.db
        async def run_lifespan():
            temp_db = Path(self.temp_dir) / "lifespan_test.db"
            with patch("server.server.DB_PATH", str(temp_db)):
                async with srv.lifespan(srv.app):
                    row = await (await srv.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")).fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["name"], "settings")

        try:
            self.run_async(run_lifespan())
        finally:
            srv.db = current_db

    def test_status_endpoint_with_heartbeat_returns_iso_last_seen(self):
        """Verify GET /api/status returns ISO formatted last_seen with timezone from heartbeats."""
        self.run_async(srv.db.execute(
            "INSERT INTO heartbeats (state, detail, collector_ts, received_at) VALUES ('connected', 'sync ok', '2026-09-26T21:20:00', '2026-09-26 21:20:00')"
        ))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["state"], "connected")
        self.assertEqual(data["detail"], "sync ok")
        self.assertTrue(data["last_seen"].startswith("2026-09-26T21:20:00+00:00"))

    def test_status_endpoint_fallback_returns_iso_last_seen(self):
        """Verify GET /api/status fallback uses sips table and returns ISO formatted last_seen."""
        self.run_async(srv.db.execute(
            "INSERT INTO sips (timestamp, intake_ml, created_at) VALUES ('2026-09-26T19:00:00', 250, '2026-09-26 19:00:00')"
        ))
        self.run_async(srv.db.commit())

        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["state"], "unknown")
        self.assertTrue(data["last_seen"].startswith("2026-09-26T19:00:00+00:00"))

    def test_heartbeat_with_battery_and_charging_reflected_in_status_and_today(self):
        """Verify POST /api/heartbeat records battery and charging, and status/today endpoints return them."""
        hb_resp = self.client.post(
            "/api/heartbeat",
            json={
                "state": "connected",
                "detail": "charging on dock",
                "timestamp": "2026-09-26T21:30:00",
                "battery": 92,
                "charging": True,
            },
            headers={"Authorization": f"Bearer {srv.API_TOKEN}"}
        )
        self.assertEqual(hb_resp.status_code, 200)

        # Status endpoint
        status_resp = self.client.get("/api/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.json()
        self.assertEqual(status_data["battery"], 92)
        self.assertTrue(status_data["charging"])

        # Today endpoint
        today_resp = self.client.get("/api/today")
        self.assertEqual(today_resp.status_code, 200)
        today_data = today_resp.json()
        self.assertEqual(today_data["battery"], 92)
        self.assertTrue(today_data["charging"])


if __name__ == "__main__":
    unittest.main()

