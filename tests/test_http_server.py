"""
Unit tests for the embedded command HTTP server and Web UI endpoints.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class MockWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes):
        self.data.extend(data)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class TestHttpServer(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        col.cmd_queue = asyncio.Queue()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    async def _send_request(self, method: str, path: str, body: dict | str | bytes | None = None) -> tuple[int, dict, bytes]:
        """Helper to invoke handle_cmd_request in-memory without opening OS network ports."""
        body_bytes = b""
        if body is not None:
            if isinstance(body, dict):
                body_bytes = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                body_bytes = body.encode("utf-8")
            elif isinstance(body, bytes):
                body_bytes = body

        req_lines = [f"{method} {path} HTTP/1.1"]
        if body_bytes:
            req_lines.append(f"Content-Length: {len(body_bytes)}")
            req_lines.append("Content-Type: application/json")
        req_lines.append("\r\n")
        req_data = "\r\n".join(req_lines).encode("utf-8") + body_bytes

        reader = asyncio.StreamReader()
        reader.feed_data(req_data)
        reader.feed_eof()

        writer = MockWriter()
        await col.handle_cmd_request(reader, writer)

        # Parse HTTP response
        resp_data = bytes(writer.data)
        parts = resp_data.split(b"\r\n\r\n", 1)
        headers_raw = parts[0].decode("utf-8", errors="replace")
        resp_body = parts[1] if len(parts) > 1 else b""

        status_line = headers_raw.split("\r\n")[0]
        status_code = int(status_line.split(" ")[1])

        json_data = {}
        if resp_body:
            try:
                json_data = json.loads(resp_body.decode("utf-8"))
            except Exception:
                pass

        return status_code, json_data, resp_body

    def test_send_json_formatting(self):
        """Verify send_json formats headers, status code, and CORS headers correctly."""
        writer = MockWriter()
        col.send_json(writer, 200, {"success": True})
        raw = bytes(writer.data).decode("utf-8")
        self.assertIn("HTTP/1.1 200 OK\r\n", raw)
        self.assertIn("Content-Type: application/json\r\n", raw)
        self.assertIn("Access-Control-Allow-Origin: *\r\n", raw)
        self.assertIn('{"success": true}', raw)

    def test_serve_static_file_extension_content_types(self):
        """Verify serve_static_file maps various file extensions to correct MIME types."""
        test_cases = [
            ("index.html", "text/html; charset=utf-8"),
            ("style.css", "text/css; charset=utf-8"),
            ("app.js", "application/javascript; charset=utf-8"),
            ("icon.svg", "image/svg+xml"),
            ("logo.png", "image/png"),
            ("config.json", "application/json; charset=utf-8"),
        ]
        with patch.object(col, "FRONTEND_DIR", Path(self.temp_dir)):
            for filename, expected_mime in test_cases:
                with self.subTest(filename=filename):
                    file_path = Path(self.temp_dir) / filename
                    file_path.write_bytes(b"content")

                    writer = MockWriter()
                    col.serve_static_file(writer, 200, f"/{filename}")
                    raw = bytes(writer.data).decode("utf-8", errors="replace")
                    self.assertIn(f"Content-Type: {expected_mime}", raw)

    def test_serve_static_file_ingress_token_stripping(self):
        """Verify Home Assistant ingress prefix is stripped when looking up assets."""
        with patch.object(col, "FRONTEND_DIR", Path(self.temp_dir)):
            (Path(self.temp_dir) / "index.html").write_text("<html>WaterH</html>")

            writer = MockWriter()
            col.serve_static_file(writer, 200, "/api/hassio_ingress/abcdef123456/index.html")
            raw = bytes(writer.data).decode("utf-8", errors="replace")
            self.assertIn("HTTP/1.1 200 OK", raw)
            self.assertIn("<html>WaterH</html>", raw)

    def test_serve_static_file_not_found(self):
        """Verify 404 returned when static file does not exist."""
        empty_dir = Path(self.temp_dir) / "empty_frontend"
        empty_dir.mkdir()
        with patch.object(col, "FRONTEND_DIR", empty_dir):
            writer = MockWriter()
            col.serve_static_file(writer, 200, "/nonexistent.png")
            raw = bytes(writer.data).decode("utf-8", errors="replace")
            self.assertIn("HTTP/1.1 404 Not Found", raw)

    def test_get_api_status(self):
        """Verify GET /api/status returns collector connection status."""
        status_code, data, _ = self.run_async(self._send_request("GET", "/api/status"))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["state"], "connected")
        self.assertTrue(data["online"])
        self.assertEqual(data["bottle"], col.BOTTLE_ADDR)

    def test_get_commands_list(self):
        """Verify GET /commands returns supported command documentation."""
        status_code, data, _ = self.run_async(self._send_request("GET", "/commands"))
        self.assertEqual(status_code, 200)
        self.assertIn("commands", data)
        self.assertTrue(any("POST /commands/flash" in c for c in data["commands"]))

    def test_get_api_today_and_api_data(self):
        """Verify GET /api/today and /api/data return aggregated today intake and sips."""
        db = col.init_db()
        # Insert a sip from today and a sip from yesterday
        db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c, tds) VALUES (datetime('now'), 250, 21.0, 50)"
        )
        db.execute(
            "INSERT INTO sips (timestamp, intake_ml, temp_c, tds) VALUES (datetime('now', '-1 day'), 400, 20.0, 45)"
        )
        db.commit()
        db.close()

        # Test /api/today
        status_code, data, _ = self.run_async(self._send_request("GET", "/api/today"))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["total_ml"], 250)
        self.assertEqual(data["sip_count"], 1)
        self.assertEqual(data["goal_ml"], 1800)
        self.assertEqual(data["goal_pct"], 14)

        # Test /api/data
        status_code2, data2, _ = self.run_async(self._send_request("GET", "/api/data"))
        self.assertEqual(status_code2, 200)
        self.assertEqual(data2["today_ml"], 250)
        self.assertEqual(data2["sips_count"], 2)  # All sips (up to 50)

    def test_get_api_history(self):
        """Verify GET /api/history returns daily aggregates and streak."""
        db = col.init_db()
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES ('2026-09-20T10:00:00', 1000)")
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES ('2026-09-21T10:00:00', 1500)")
        db.commit()
        db.close()

        status_code, data, _ = self.run_async(self._send_request("GET", "/api/history"))
        self.assertEqual(status_code, 200)
        self.assertEqual(len(data["days"]), 2)
        self.assertEqual(data["best_day_ml"], 1500)
        self.assertEqual(data["avg_daily_ml"], 1250)
        self.assertEqual(data["current_streak"], 2)
        self.assertEqual(data["goal_ml"], 1800)

    def test_post_commands_flash(self):
        """Verify POST /commands/flash enqueues flash command."""
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/flash"))
        self.assertEqual(status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["queued"], "flash")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_flash_led())
        self.assertEqual(label, "flash")

    def test_post_commands_led(self):
        """Verify POST /commands/led enqueues led command with specified mode and color."""
        payload = {"mode": "calm", "color": "purple"}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/led", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["queued"], "led calm purple")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_set_led("calm", "purple"))

    def test_post_commands_goal(self):
        """Verify POST /commands/goal updates stored goal and enqueues command."""
        payload = {"ml": 2500}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/goal", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["goal_ml"], 2500)
        self.assertEqual(col.GOAL_ML, 2500)

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_set_goal(2500))

    def test_post_commands_intake_success(self):
        """Verify POST /commands/intake logs sip, updates today intake, and enqueues sync."""
        payload = {"ml": 350}
        with patch("collector.collector.publish_ha_sensor") as mock_sensor:
            status_code, data, _ = self.run_async(self._send_request("POST", "/commands/intake", body=payload))
            self.assertEqual(status_code, 200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["added_ml"], 350)
            self.assertEqual(data["today_total_ml"], 350)

            mock_sensor.assert_called_with(
                "today_intake", 350, unit="mL", friendly_name="WaterH Today Intake",
                icon="mdi:cup-water", device_class="water", state_class="total_increasing"
            )

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_sync_today_amount(350))
        self.assertEqual(label, "intake 350ml")

    def test_post_commands_intake_invalid(self):
        """Verify POST /commands/intake with ml <= 0 returns 400 Bad Request."""
        payload = {"ml": 0}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/intake", body=payload))
        self.assertEqual(status_code, 400)
        self.assertIn("error", data)

    def test_post_commands_delete_sip_by_id(self):
        """Verify POST /commands/delete_sip by ID deletes record and recalculates total."""
        db = col.init_db()
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES (datetime('now'), 300)")
        db.commit()
        sip_id = db.execute("SELECT id FROM sips").fetchone()[0]
        db.close()

        status_code, data, _ = self.run_async(
            self._send_request("POST", "/commands/delete_sip", body={"id": sip_id})
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(data["today_total_ml"], 0)

        # Verify record deleted
        db = col.init_db()
        count = db.execute("SELECT COUNT(*) FROM sips").fetchone()[0]
        self.assertEqual(count, 0)
        db.close()

    def test_post_commands_delete_sip_by_timestamp(self):
        """Verify POST /commands/delete_sip by timestamp."""
        ts = "2026-09-23T12:00:00"
        db = col.init_db()
        db.execute("INSERT INTO sips (timestamp, intake_ml) VALUES (?, 450)", (ts,))
        db.commit()
        db.close()

        status_code, data, _ = self.run_async(
            self._send_request("POST", "/commands/delete_sip", body={"timestamp": ts})
        )
        self.assertEqual(status_code, 200)

        db = col.init_db()
        count = db.execute("SELECT COUNT(*) FROM sips").fetchone()[0]
        self.assertEqual(count, 0)
        db.close()

    def test_post_commands_delete_sip_missing_params(self):
        """Verify POST /commands/delete_sip without id or timestamp returns 400."""
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/delete_sip", body={}))
        self.assertEqual(status_code, 400)
        self.assertIn("error", data)

    def test_post_commands_reminder(self):
        """Verify POST /commands/reminder enqueues reminder command."""
        payload = {"on": True, "wake": "07:30", "sleep": "22:00", "interval": 45}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/reminder", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["queued"], "reminder on")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_set_reminder(True, 7, 30, 22, 0, 45))

    def test_post_commands_schedule(self):
        """Verify POST /commands/schedule updates settings and enqueues sync_settings command."""
        payload = {"wake": "08:30", "sleep": "21:30", "interval": 90, "on": True}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/schedule", body=payload))
        self.assertEqual(status_code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("schedule", data)
        self.assertEqual(data["schedule"]["wake_time"], "08:30")
        self.assertEqual(data["schedule"]["sleep_time"], "21:30")
        self.assertEqual(data["schedule"]["interval_min"], 90)
        self.assertTrue(data["schedule"]["reminder_on"])
        self.assertEqual(data["queued"], "reminder on")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertIn("schedule update", label)

    def test_post_commands_calibrate(self):
        """Verify POST /commands/calibrate enqueues recalibrate command."""
        payload = {"full": False}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/calibrate", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["queued"], "calibrate empty")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_recalibrate(False))

    def test_post_commands_raw(self):
        """Verify POST /commands/raw enqueues raw byte sequence."""
        payload = {"hex": "47 54 00 01 ff"}
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/raw", body=payload))
        self.assertEqual(status_code, 200)
        self.assertEqual(data["queued"], "raw 47540001ff")

        self.assertFalse(col.cmd_queue.empty())
        cmd, label = col.cmd_queue.get_nowait()
        self.assertEqual(cmd, bytes.fromhex("47540001ff"))

    def test_post_commands_raw_missing_hex(self):
        """Verify POST /commands/raw with missing hex string returns 400."""
        status_code, data, _ = self.run_async(self._send_request("POST", "/commands/raw", body={"hex": ""}))
        self.assertEqual(status_code, 400)
        self.assertIn("error", data)

    def test_serve_static_file_from_src_subdirectory(self):
        """Verify serve_static_file finds assets located inside FRONTEND_DIR/src."""
        src_dir = Path(self.temp_dir) / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "bundle.js").write_text("console.log('bundled');")

        with patch.object(col, "FRONTEND_DIR", Path(self.temp_dir)):
            writer = MockWriter()
            col.serve_static_file(writer, 200, "/bundle.js")
            raw = bytes(writer.data).decode("utf-8", errors="replace")
            self.assertIn("HTTP/1.1 200 OK", raw)
            self.assertIn("console.log('bundled');", raw)

    def test_serve_static_file_ingress_single_token(self):
        """Verify serve_static_file handles single-token ingress path."""
        (Path(self.temp_dir) / "index.html").write_text("<h1>Ingress Home</h1>")

        with patch.object(col, "FRONTEND_DIR", Path(self.temp_dir)):
            writer = MockWriter()
            col.serve_static_file(writer, 200, "ingress/_/single_token")
            raw = bytes(writer.data).decode("utf-8", errors="replace")
            self.assertIn("HTTP/1.1 200 OK", raw)
            self.assertIn("<h1>Ingress Home</h1>", raw)

    def test_handle_cmd_request_internal_error_500(self):
        """Verify unexpected exception in request handler sends 500 Internal Server Error."""
        reader = asyncio.StreamReader()
        reader.feed_data(b"GET /api/today HTTP/1.1\r\n\r\n")
        reader.feed_eof()

        writer = MockWriter()
        with patch("collector.collector.init_db", side_effect=RuntimeError("Unexpected database fault")):
            self.run_async(col.handle_cmd_request(reader, writer))

        raw = bytes(writer.data).decode("utf-8", errors="replace")
        self.assertIn("HTTP/1.1 500 Internal Server Error", raw)
        self.assertIn("Unexpected database fault", raw)


if __name__ == "__main__":
    unittest.main()

