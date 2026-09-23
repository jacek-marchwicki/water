"""
Pytest configuration and shared fixtures for collector unit tests.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import collector.collector as collector_mod


@pytest.fixture(autouse=True)
def isolate_collector_state(tmp_path, monkeypatch):
    """
    Ensure every test runs with an isolated temporary SQLite database,
    fresh asyncio command queue, and clean mock state.
    """
    db_file = tmp_path / "test_waterh.db"
    monkeypatch.setattr(collector_mod, "DB_PATH", str(db_file))
    monkeypatch.setattr(collector_mod, "GOAL_ML", 1800)
    monkeypatch.setattr(collector_mod, "ha_conn", None)
    monkeypatch.setattr(collector_mod, "ha_api", None)
    monkeypatch.setattr(collector_mod, "cmd_queue", asyncio.Queue())
    monkeypatch.setattr(collector_mod, "WRITE_CHAR", "0000ffe9-0000-1000-8000-00805f9b34fb")
    monkeypatch.setattr(collector_mod, "NOTIFY_CHAR", "0000ffe4-0000-1000-8000-00805f9b34fb")
    yield db_file


@pytest.fixture
def test_db(isolate_collector_state):
    """Provides an initialized SQLite database connection for tests."""
    db = collector_mod.init_db()
    yield db
    db.close()
