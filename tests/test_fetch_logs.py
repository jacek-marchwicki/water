"""
Unit tests for fetch_logs.py.
Completely isolated and mocked without network or live SSH access.
Compatible with both standard library unittest and pytest.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import fetch_logs


class TestFetchLogsUnit(unittest.TestCase):
    def test_parse_target(self):
        user, host = fetch_logs.parse_target("root@homeassistant.local")
        self.assertEqual(user, "root")
        self.assertEqual(host, "homeassistant.local")

        user, host = fetch_logs.parse_target("192.168.2.209", default_user="admin")
        self.assertEqual(user, "admin")
        self.assertEqual(host, "192.168.2.209")

        user, host = fetch_logs.parse_target("user@10.0.0.5:22")
        self.assertEqual(user, "user")
        self.assertEqual(host, "10.0.0.5:22")

    def test_build_ssh_command(self):
        cmd = fetch_logs.build_ssh_command(
            "homeassistant.local",
            "ha apps logs local_waterh_collector",
            port=22,
            user="root",
            timeout=10.0,
        )
        self.assertIn("ssh", cmd)
        self.assertIn("-o", cmd)
        self.assertIn("StrictHostKeyChecking=accept-new", cmd)
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("ConnectTimeout=10", cmd)
        self.assertEqual(cmd[-2], "root@homeassistant.local")
        self.assertEqual(cmd[-1], "ha apps logs local_waterh_collector")

    def test_build_ssh_command_custom_port_and_key(self):
        cmd = fetch_logs.build_ssh_command(
            "myhost.local",
            "echo test",
            port=2222,
            user="jacek",
            key="~/.ssh/id_rsa",
        )
        self.assertIn("-p", cmd)
        self.assertIn("2222", cmd)
        self.assertIn("-i", cmd)
        self.assertEqual(cmd[-2], "jacek@myhost.local")

    def test_build_remote_log_command(self):
        cmd = fetch_logs.build_remote_log_command(
            addon="local_waterh_collector",
            lines=25,
            follow=False,
            boot=None,
        )
        self.assertIn("ha apps logs local_waterh_collector -n 25 --no-progress", cmd)
        self.assertIn("|| ha addons logs local_waterh_collector -n 25 --no-progress", cmd)

        # Follow mode
        cmd_f = fetch_logs.build_remote_log_command(addon="my_addon", lines=10, follow=True)
        self.assertIn("-f", cmd_f)
        self.assertNotIn(" -n ", cmd_f)

        # All lines (lines=None)
        cmd_all = fetch_logs.build_remote_log_command(addon="my_addon", lines=None)
        self.assertNotIn(" -n ", cmd_all)

        # Boot specified
        cmd_boot = fetch_logs.build_remote_log_command(addon="my_addon", lines=10, boot="-1")
        self.assertIn("-b -1", cmd_boot)

    def test_strip_ansi(self):
        text = "\033[31mError message\033[0m with \033[1mformatting\033[0m"
        self.assertEqual(fetch_logs.strip_ansi(text), "Error message with formatting")

    def test_parse_log_line_standard(self):
        raw = "22:42:17 [WARNING] [BLE] Using default WRITE characteristic: 0000ffe9"
        parsed = fetch_logs.parse_log_line(raw)
        self.assertEqual(parsed["timestamp"], "22:42:17")
        self.assertEqual(parsed["level"], "WARNING")
        self.assertEqual(parsed["tag"], "BLE")
        self.assertEqual(parsed["message"], "Using default WRITE characteristic: 0000ffe9")
        self.assertEqual(parsed["raw"], raw)

    def test_parse_log_line_no_tag(self):
        raw = "22:31:37 [INFO] Connected to broker successfully"
        parsed = fetch_logs.parse_log_line(raw)
        self.assertEqual(parsed["timestamp"], "22:31:37")
        self.assertEqual(parsed["level"], "INFO")
        self.assertIsNone(parsed["tag"])
        self.assertEqual(parsed["message"], "Connected to broker successfully")

    def test_parse_log_line_unstructured(self):
        raw = "Traceback (most recent call last):"
        parsed = fetch_logs.parse_log_line(raw)
        self.assertIsNone(parsed["timestamp"])
        self.assertIsNone(parsed["level"])
        self.assertIsNone(parsed["tag"])
        self.assertEqual(parsed["message"], raw)
        self.assertEqual(parsed["raw"], raw)

    def test_matches_filters_level(self):
        info_entry = fetch_logs.parse_log_line("22:00:00 [INFO] [BLE] All good")
        warn_entry = fetch_logs.parse_log_line("22:00:01 [WARNING] [BLE] Watch out")
        err_entry = fetch_logs.parse_log_line("22:00:02 [ERROR] [BLE] Broken")

        # Exact level
        self.assertTrue(fetch_logs.matches_filters(info_entry, level="INFO"))
        self.assertFalse(fetch_logs.matches_filters(warn_entry, level="INFO"))
        self.assertTrue(fetch_logs.matches_filters(err_entry, level="ERROR"))

        # Min level
        self.assertFalse(fetch_logs.matches_filters(info_entry, min_level="WARNING"))
        self.assertTrue(fetch_logs.matches_filters(warn_entry, min_level="WARNING"))
        self.assertTrue(fetch_logs.matches_filters(err_entry, min_level="WARNING"))

    def test_matches_filters_tag(self):
        ble_entry = fetch_logs.parse_log_line("22:00:00 [INFO] [BLE] BLE message")
        mqtt_entry = fetch_logs.parse_log_line("22:00:00 [INFO] [MQTT] MQTT message")

        self.assertTrue(fetch_logs.matches_filters(ble_entry, tag="BLE"))
        self.assertFalse(fetch_logs.matches_filters(mqtt_entry, tag="BLE"))
        self.assertTrue(fetch_logs.matches_filters(mqtt_entry, tag="[MQTT]"))

    def test_matches_filters_grep_and_exclude(self):
        import re
        entry1 = fetch_logs.parse_log_line("22:00:00 [INFO] [BLE] Connected to WaterH")
        entry2 = fetch_logs.parse_log_line("22:00:01 [INFO] [BLE] Disconnected from WaterH")

        grep_pat = re.compile("disconnect", re.IGNORECASE)
        self.assertFalse(fetch_logs.matches_filters(entry1, grep_pattern=grep_pat))
        self.assertTrue(fetch_logs.matches_filters(entry2, grep_pattern=grep_pat))

        excl_pat = re.compile("disconnect", re.IGNORECASE)
        self.assertTrue(fetch_logs.matches_filters(entry1, exclude_pattern=excl_pat))
        self.assertFalse(fetch_logs.matches_filters(entry2, exclude_pattern=excl_pat))

    def test_format_log_entry(self):
        entry = {
            "timestamp": "22:00:00",
            "level": "INFO",
            "tag": "BLE",
            "message": "Connected",
            "raw": "22:00:00 [INFO] [BLE] Connected",
        }
        # Plain text
        plain = fetch_logs.format_log_entry(entry, color=False)
        self.assertEqual(plain, "22:00:00 [INFO] [BLE] Connected")

        # Colorized text
        colored = fetch_logs.format_log_entry(entry, color=True)
        self.assertIn("\033[", colored)
        self.assertIn("Connected", colored)


class TestFetchLogsIntegration(unittest.TestCase):
    @patch("fetch_logs.subprocess.run")
    def test_fetch_waterh_logs_success(self, mock_run):
        sample_output = (
            "22:30:00 [INFO] [BLE] Scanning for device...\n"
            "22:30:05 [WARNING] [BLE] Disconnected\n"
            "22:30:10 [ERROR] [BLE] Connection error: timeout\n"
        )
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = sample_output
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        entries = fetch_logs.fetch_waterh_logs(lines=10)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["level"], "INFO")
        self.assertEqual(entries[1]["level"], "WARNING")
        self.assertEqual(entries[2]["level"], "ERROR")

    @patch("fetch_logs.subprocess.run")
    def test_fetch_waterh_logs_filter_level(self, mock_run):
        sample_output = (
            "22:30:00 [INFO] [BLE] Scanning for device...\n"
            "22:30:05 [WARNING] [BLE] Disconnected\n"
            "22:30:10 [ERROR] [BLE] Connection error: timeout\n"
        )
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = sample_output
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        entries = fetch_logs.fetch_waterh_logs(level="ERROR")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["message"], "Connection error: timeout")

    @patch("fetch_logs.subprocess.run")
    def test_fetch_waterh_logs_raw(self, mock_run):
        sample_output = "Raw log output line 1\nRaw log output line 2\n"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = sample_output
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        out = fetch_logs.fetch_waterh_logs(raw=True)
        self.assertEqual(out, sample_output)

    @patch("fetch_logs.subprocess.run")
    def test_fetch_waterh_logs_error_handling(self, mock_run):
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.stdout = ""
        mock_proc.stderr = "ssh: connect to host homeassistant.local port 22: Connection refused"
        mock_run.return_value = mock_proc

        with self.assertRaises(RuntimeError) as ctx:
            fetch_logs.fetch_waterh_logs(host="homeassistant.local", timeout=1.0)
        self.assertIn("Connection refused", str(ctx.exception))

    @patch("fetch_logs.subprocess.run")
    def test_fetch_waterh_logs_slug_fallback(self, mock_run):
        # First call returns 'does not exist', second call succeeds
        mock_fail = MagicMock()
        mock_fail.returncode = 0
        mock_fail.stdout = "App local_waterh_collector does not exist\n"
        mock_fail.stderr = ""

        mock_succ = MagicMock()
        mock_succ.returncode = 0
        mock_succ.stdout = "22:00:00 [INFO] [BLE] Working with fallback slug\n"
        mock_succ.stderr = ""

        mock_run.side_effect = [mock_fail, mock_succ]

        entries = fetch_logs.fetch_waterh_logs(addon="local_waterh_collector")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["message"], "Working with fallback slug")

    @patch("fetch_logs.subprocess.run")
    def test_main_cli_json_and_output(self, mock_run):
        sample_output = "22:30:00 [INFO] [BLE] Testing CLI\n"
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = sample_output
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        with tempfile.NamedTemporaryFile("w+", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with patch("sys.stdout", new=io.StringIO()) as mock_stdout:
                ret = fetch_logs.main(["--json", "-o", tmp_path])
                self.assertEqual(ret, 0)
                json_str = mock_stdout.getvalue()
                data = json.loads(json_str)
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["message"], "Testing CLI")

            # Check file was also written
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
                data_f = json.loads(content)
                self.assertEqual(len(data_f), 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
