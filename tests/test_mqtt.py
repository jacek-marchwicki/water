"""
Unit tests for Home Assistant MQTT Auto-Discovery and command handling.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestHAConnection(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()
        self.cmd_queue = asyncio.Queue()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def test_ha_connection_init(self):
        """Verify initial property assignment."""
        conn = col.HAConnection("192.168.1.100", 1883, "user", "pass", "homeassistant")
        self.assertEqual(conn.host, "192.168.1.100")
        self.assertEqual(conn.port, 1883)
        self.assertEqual(conn.user, "user")
        self.assertEqual(conn.password, "pass")
        self.assertEqual(conn.prefix, "homeassistant")
        self.assertFalse(conn.connected)

    def test_start_when_mqtt_package_missing(self):
        """Verify start returns early when HAS_MQTT is False."""
        conn = col.HAConnection("192.168.1.100", 1883, "user", "pass", "homeassistant")
        with patch.object(col, "HAS_MQTT", False):
            conn.start(self.loop, self.cmd_queue)
            self.assertIsNone(conn.client)

    def test_start_when_host_unconfigured(self):
        """Verify start returns early when MQTT_HOST is blank and supervisor returns empty."""
        conn = col.HAConnection("", 1883, "", "", "homeassistant")
        with patch.object(col, "HAS_MQTT", True), patch(
            "collector.collector.get_supervisor_token", return_value=""
        ):
            conn.start(self.loop, self.cmd_queue)
            self.assertIsNone(conn.client)

    @patch("collector.collector.fetch_mqtt_service_credentials")
    @patch("collector.collector.get_supervisor_token")
    def test_start_auto_fetches_supervisor_credentials(self, mock_token, mock_fetch_creds):
        """Verify start automatically discovers supervisor credentials if username is missing."""
        mock_token.return_value = "sup_token"
        mock_fetch_creds.return_value = ("core-mosquitto", 1883, "auto_user", "auto_pass")

        mock_client = MagicMock()
        mock_mqtt = MagicMock()
        mock_mqtt.Client.return_value = mock_client

        conn = col.HAConnection("", 1883, "", "", "homeassistant")
        with patch.object(col, "HAS_MQTT", True), patch.object(col, "mqtt", mock_mqtt, create=True):
            conn.start(self.loop, self.cmd_queue)

        self.assertEqual(conn.host, "core-mosquitto")
        self.assertEqual(conn.user, "auto_user")
        self.assertEqual(conn.password, "auto_pass")
        mock_client.username_pw_set.assert_called_once_with("auto_user", "auto_pass")
        mock_client.connect_async.assert_called_once_with("core-mosquitto", 1883, keepalive=60)
        mock_client.loop_start.assert_called_once()

    def test_on_connect_success(self):
        """Verify on_connect subscribes to commands and publishes discovery on rc=0."""
        mock_client = MagicMock()
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.client = mock_client

        with patch.object(conn, "publish_discovery") as mock_disc:
            conn._on_connect(mock_client, None, {}, 0)
            self.assertTrue(conn.connected)
            mock_disc.assert_called_once()
            mock_client.subscribe.assert_called_once_with("waterh/cmd/#")

    def test_on_connect_failure(self):
        """Verify on_connect logs failure when rc != 0."""
        mock_client = MagicMock()
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.client = mock_client

        conn._on_connect(mock_client, None, {}, 4)  # 4 = Bad username or password
        self.assertFalse(conn.connected)
        mock_client.subscribe.assert_not_called()

    def test_publish_state(self):
        """Verify publish_state publishes to expected subpath with retain=True."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.client = MagicMock()

        conn.publish_state("sensor/battery", 85)
        conn.client.publish.assert_called_once_with("waterh/sensor/battery/state", "85", retain=True)

    def test_publish_discovery(self):
        """Verify publish_discovery registers all entities with Home Assistant."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.client = MagicMock()

        conn.publish_discovery()

        # Should publish configs for: today_intake, battery, temperature, tds, daily_goal, status, flash_led, recalibrate, sync_time, led_mode, set_goal, wake_time, sleep_time, reminder_interval, reminder
        self.assertEqual(conn.client.publish.call_count, 22)  # 15 configs + 7 baseline states

        published_topics = [c[0][0] for c in conn.client.publish.call_args_list]
        self.assertIn("homeassistant/sensor/waterh/today_intake/config", published_topics)
        self.assertIn("homeassistant/sensor/waterh/battery/config", published_topics)
        self.assertIn("homeassistant/button/waterh/flash_led/config", published_topics)
        self.assertIn("homeassistant/button/waterh/sync_time/config", published_topics)
        self.assertIn("homeassistant/select/waterh/led_mode/config", published_topics)
        self.assertIn("homeassistant/number/waterh/set_goal/config", published_topics)
        self.assertIn("homeassistant/time/waterh/wake_time/config", published_topics)
        self.assertIn("homeassistant/time/waterh/sleep_time/config", published_topics)
        self.assertIn("homeassistant/number/waterh/reminder_interval/config", published_topics)
        self.assertIn("homeassistant/switch/waterh/reminder/config", published_topics)

    def test_on_message_flash(self):
        """Verify MQTT flash command enqueues flash command."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.loop = self.loop
        conn.cmd_queue = self.cmd_queue

        msg = MagicMock()
        msg.topic = "waterh/cmd/flash"
        msg.payload = b""

        conn._on_message(None, None, msg)

        # Run loop briefly to process threadsafe call
        self.loop.stop()
        self.loop.run_forever()

        self.assertFalse(self.cmd_queue.empty())
        cmd, label = self.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_flash_led())
        self.assertEqual(label, "flash (MQTT)")

    def test_on_message_led_mode(self):
        """Verify MQTT led_mode command enqueues LED command and publishes state."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.loop = self.loop
        conn.cmd_queue = self.cmd_queue
        conn.publish_state = MagicMock()

        msg = MagicMock()
        msg.topic = "waterh/cmd/led_mode"
        msg.payload = b"breathe"

        conn._on_message(None, None, msg)

        self.loop.stop()
        self.loop.run_forever()

        self.assertFalse(self.cmd_queue.empty())
        cmd, label = self.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_set_led("breathe", "blue"))
        self.assertEqual(label, "led breathe (MQTT)")
        conn.publish_state.assert_called_once_with("select/led_mode", "breathe")

    def test_on_message_set_goal(self):
        """Verify MQTT set_goal command updates goal and enqueues command."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.loop = self.loop
        conn.cmd_queue = self.cmd_queue

        msg = MagicMock()
        msg.topic = "waterh/cmd/set_goal"
        msg.payload = b"2400"

        with patch("collector.collector.set_goal_ml") as mock_set_goal:
            conn._on_message(None, None, msg)

            self.loop.stop()
            self.loop.run_forever()

            mock_set_goal.assert_called_once_with(2400)
            self.assertFalse(self.cmd_queue.empty())
            cmd, label = self.cmd_queue.get_nowait()
            self.assertEqual(cmd, col.cmd_set_goal(2400))
            self.assertEqual(label, "goal 2400ml (MQTT)")

    def test_on_message_set_goal_invalid(self):
        """Verify MQTT set_goal command ignores non-numeric payloads."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.loop = self.loop
        conn.cmd_queue = self.cmd_queue

        msg = MagicMock()
        msg.topic = "waterh/cmd/set_goal"
        msg.payload = b"invalid_number"

        with patch("collector.collector.set_goal_ml") as mock_set_goal:
            conn._on_message(None, None, msg)
            mock_set_goal.assert_not_called()
            self.assertTrue(self.cmd_queue.empty())

    def test_on_message_recalibrate(self):
        """Verify MQTT recalibrate command enqueues recalibration."""
        conn = col.HAConnection("127.0.0.1", 1883, "", "", "homeassistant")
        conn.loop = self.loop
        conn.cmd_queue = self.cmd_queue

        msg = MagicMock()
        msg.topic = "waterh/cmd/recalibrate"
        msg.payload = b""

        conn._on_message(None, None, msg)

        self.loop.stop()
        self.loop.run_forever()

        self.assertFalse(self.cmd_queue.empty())
        cmd, label = self.cmd_queue.get_nowait()
        self.assertEqual(cmd, col.cmd_recalibrate(True))
        self.assertEqual(label, "recalibrate (MQTT)")


if __name__ == "__main__":
    unittest.main()
