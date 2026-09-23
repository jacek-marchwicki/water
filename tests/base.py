"""
Base test case providing clean isolation of database, queues, and globals.
Compatible with both pytest and python3 -m unittest.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import collector.collector as col


class IsolatedCollectorTestCase(unittest.TestCase):
    """
    Test case base class ensuring each test runs in an isolated temporary
    environment without polluting production files, databases, or shared state.
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp(prefix="waterh_test_")
        self.db_path = Path(self.temp_dir) / "test_waterh.db"

        # Save previous globals
        self._orig_db_path = col.DB_PATH
        self._orig_goal_ml = col.GOAL_ML
        self._orig_ha_conn = col.ha_conn
        self._orig_ha_api = col.ha_api
        self._orig_cmd_queue = col.cmd_queue
        self._orig_write_char = col.WRITE_CHAR
        self._orig_notify_char = col.NOTIFY_CHAR
        self._orig_api_token = col.API_TOKEN

        # Set isolated globals
        col.DB_PATH = str(self.db_path)
        col.GOAL_ML = 1800
        col.ha_conn = None
        col.ha_api = None
        col.cmd_queue = asyncio.Queue()
        col.WRITE_CHAR = "0000ffe9-0000-1000-8000-00805f9b34fb"
        col.NOTIFY_CHAR = "0000ffe4-0000-1000-8000-00805f9b34fb"
        col.API_TOKEN = ""

    def tearDown(self):
        # Restore globals
        col.DB_PATH = self._orig_db_path
        col.GOAL_ML = self._orig_goal_ml
        col.ha_conn = self._orig_ha_conn
        col.ha_api = self._orig_ha_api
        col.cmd_queue = self._orig_cmd_queue
        col.WRITE_CHAR = self._orig_write_char
        col.NOTIFY_CHAR = self._orig_notify_char
        col.API_TOKEN = self._orig_api_token

        # Clean up temporary directory
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        super().tearDown()
