"""
Unit tests for collector startup, main entry point, and TCP command server startup.
"""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.base import IsolatedCollectorTestCase
import collector.collector as col


class TestMainAndServer(IsolatedCollectorTestCase):
    def setUp(self):
        super().setUp()
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    @patch("asyncio.start_server", new_callable=AsyncMock)
    def test_start_cmd_server(self, mock_start_server):
        """Verify start_cmd_server starts an asyncio TCP server on configured port."""
        mock_server = MagicMock()
        mock_start_server.return_value = mock_server

        server = self.run_async(col.start_cmd_server())
        self.assertEqual(server, mock_server)
        mock_start_server.assert_awaited_once_with(col.handle_cmd_request, "0.0.0.0", col.CMD_PORT)

    @patch("asyncio.run")
    @patch("collector.collector.get_goal_ml", return_value=2300)
    def test_main_initialization(self, mock_get_goal, mock_asyncio_run):
        """Verify main() retrieves stored goal and launches ble_loop via asyncio.run()."""
        def fake_asyncio_run(coro):
            coro.close()

        mock_asyncio_run.side_effect = fake_asyncio_run

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "token_xyz"}):
            col.main()

            mock_get_goal.assert_called_once()
            self.assertEqual(col.GOAL_ML, 2300)
            mock_asyncio_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
