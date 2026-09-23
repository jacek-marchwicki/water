"""
Unit tests for Home Assistant Supervisor, REST API, and sensor dispatching.
"""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestSupervisorAndRestAPI(IsolatedCollectorTestCase):
    def test_get_supervisor_token_from_env_vars(self):
        """Verify token extraction from various environment variable names."""
        for env_var in ["SUPERVISOR_TOKEN", "HASSIO_TOKEN", "HOMEASSISTANT_TOKEN"]:
            with self.subTest(env_var=env_var):
                with patch.dict(os.environ, {env_var: "mock_token_123"}, clear=True):
                    token = col.get_supervisor_token()
                    self.assertEqual(token, "mock_token_123")

    def test_get_supervisor_token_from_container_file(self):
        """Verify token fallback to container environment file."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.is_file", return_value=True), patch(
                "pathlib.Path.read_text", return_value="file_token_456\n"
            ):
                token = col.get_supervisor_token()
                self.assertEqual(token, "file_token_456")

    def test_get_supervisor_token_missing(self):
        """Verify empty string returned when no token is present anywhere."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.is_file", return_value=False):
                token = col.get_supervisor_token()
                self.assertEqual(token, "")

    def test_fetch_mqtt_service_credentials_empty_token(self):
        """Verify empty token returns blank credentials immediately."""
        self.assertEqual(col.fetch_mqtt_service_credentials(""), ("", 0, "", ""))

    @patch("urllib.request.urlopen")
    def test_fetch_mqtt_service_credentials_success(self, mock_urlopen):
        """Verify fetching credentials from Supervisor MQTT service endpoint."""
        resp_data = {
            "result": "ok",
            "data": {
                "host": "core-mosquitto",
                "port": 1883,
                "username": "addons",
                "password": "secret_password",
            },
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        host, port, user, password = col.fetch_mqtt_service_credentials("valid_token")
        self.assertEqual(host, "core-mosquitto")
        self.assertEqual(port, 1883)
        self.assertEqual(user, "addons")
        self.assertEqual(password, "secret_password")

    @patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused"))
    def test_fetch_mqtt_service_credentials_handles_error(self, mock_urlopen):
        """Verify error during Supervisor API query returns empty credentials."""
        creds = col.fetch_mqtt_service_credentials("valid_token")
        self.assertEqual(creds, ("", 0, "", ""))

    def test_ha_rest_api_empty_token_noop(self):
        """Verify HARestAPI does not perform HTTP request when token is empty."""
        api = col.HARestAPI("")
        with patch("urllib.request.urlopen") as mock_urlopen:
            api.update_sensor("battery", 95)
            mock_urlopen.assert_not_called()

    @patch("urllib.request.urlopen")
    def test_ha_rest_api_update_sensor_success(self, mock_urlopen):
        """Verify HARestAPI sends proper JSON payload and headers."""
        mock_resp = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        api = col.HARestAPI("test_token")
        api.update_sensor(
            "temperature",
            21.5,
            unit="°C",
            friendly_name="Water Temperature",
            icon="mdi:thermometer",
            device_class="temperature",
            state_class="measurement",
        )

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.headers.get("Authorization"), "Bearer test_token")
        self.assertEqual(req.headers.get("Content-type"), "application/json")
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["state"], "21.5")
        self.assertEqual(payload["attributes"]["unit_of_measurement"], "°C")
        self.assertEqual(payload["attributes"]["friendly_name"], "Water Temperature")
        self.assertEqual(payload["attributes"]["device_class"], "temperature")

    @patch("urllib.request.urlopen")
    def test_ha_rest_api_fallback_to_second_url(self, mock_urlopen):
        """Verify HARestAPI attempts second base URL when the first raises an error."""
        mock_urlopen.side_effect = [urllib.error.URLError("First failed"), MagicMock()]

        api = col.HARestAPI("test_token")
        api.update_sensor("battery", 80)

        self.assertEqual(mock_urlopen.call_count, 2)

    def test_publish_ha_sensor_prefers_mqtt(self):
        """Verify publish_ha_sensor routes to MQTT if ha_conn is available."""
        mock_ha_conn = MagicMock()
        mock_ha_api = MagicMock()
        col.ha_conn = mock_ha_conn
        col.ha_api = mock_ha_api

        col.publish_ha_sensor("today_intake", 750, unit="mL")

        mock_ha_conn.publish_state.assert_called_once_with("sensor/today_intake", 750)
        mock_ha_api.update_sensor.assert_not_called()

    def test_publish_ha_sensor_falls_back_to_rest_api(self):
        """Verify publish_ha_sensor falls back to Direct REST API when ha_conn is None."""
        mock_ha_api = MagicMock()
        col.ha_conn = None
        col.ha_api = mock_ha_api

        col.publish_ha_sensor("today_intake", 750, unit="mL", friendly_name="Today Intake")

        mock_ha_api.update_sensor.assert_called_once_with(
            "today_intake",
            750,
            unit="mL",
            friendly_name="Today Intake",
            icon="",
            device_class="",
            state_class="",
        )


if __name__ == "__main__":
    unittest.main()
