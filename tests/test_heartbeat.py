"""
Unit tests for collector heartbeat and status reporting.
"""
from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestHeartbeat(IsolatedCollectorTestCase):
    @patch("collector.collector.publish_ha_sensor")
    @patch("urllib.request.urlopen")
    def test_post_heartbeat_without_api_token(self, mock_urlopen, mock_publish_sensor):
        """Verify heartbeat publishes HA status sensor but does not make HTTP request when API_TOKEN is empty."""
        col.API_TOKEN = ""
        col.post_heartbeat("connected", "sync ok")

        mock_publish_sensor.assert_called_once_with(
            "status",
            "connected: sync ok",
            friendly_name="WaterH Collector Status",
            icon="mdi:bluetooth-connect",
        )
        mock_urlopen.assert_not_called()

    @patch("collector.collector.publish_ha_sensor")
    @patch("urllib.request.urlopen")
    def test_post_heartbeat_with_api_token(self, mock_urlopen, mock_publish_sensor):
        """Verify heartbeat sends POST request to HEARTBEAT_URL with auth headers when token is set."""
        col.API_TOKEN = "test_bearer_token"
        col.post_heartbeat("scanning", "")

        mock_publish_sensor.assert_called_once_with(
            "status",
            "scanning",
            friendly_name="WaterH Collector Status",
            icon="mdi:bluetooth-connect",
        )
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.full_url, col.HEARTBEAT_URL)
        self.assertEqual(req.headers.get("Authorization"), "Bearer test_bearer_token")
        self.assertEqual(req.headers.get("Content-type"), "application/json")

        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["state"], "scanning")
        self.assertEqual(payload["detail"], "")
        self.assertIn("timestamp", payload)

    @patch("collector.collector.publish_ha_sensor")
    @patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network unreachable"))
    def test_post_heartbeat_handles_http_failure(self, mock_urlopen, mock_publish_sensor):
        """Verify heartbeat does not raise exception when HTTP request fails."""
        col.API_TOKEN = "test_bearer_token"
        try:
            col.post_heartbeat("error", "bluetooth down")
        except Exception as e:
            self.fail(f"post_heartbeat unexpectedly raised {e}")


if __name__ == "__main__":
    unittest.main()
