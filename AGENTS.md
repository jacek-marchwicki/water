# Agent Guidelines

Operational guidelines for AI agents working in this repository.

## Testing & Verification

- **Always run tests before completing work:**
  ```bash
  pip install -r requirements-test.txt
  pytest tests
  ```
  Or via standard library `unittest`:
  ```bash
  python3 -m unittest discover tests
  ```
- **Hardware isolation:** Never attempt to connect to live Bluetooth adapters, BlueZ, or network brokers during testing. All external interfaces (`bleak`, `bluetoothctl`, `urllib`, MQTT brokers, sockets) must remain mocked.
- **State isolation:** All collector tests must inherit from `IsolatedCollectorTestCase` in `tests/base.py`. This guarantees an isolated temporary SQLite database and automatic restoration of global collector state (`DB_PATH`, `GOAL_ML`, `cmd_queue`, `ha_conn`, `ha_api`, etc.).
- **Dual test runner compatibility:** Maintain compatibility with both standard library `unittest` (zero external dependencies) and `pytest`.

## Python Compatibility

- Target Python version is **3.9+**.
- When using PEP 604 type unions (`X | Y`), always include `from __future__ import annotations` at the top of the file to preserve Python 3.9 compatibility.
- Keep external library imports (`bleak`, `paho-mqtt`) wrapped with graceful import fallbacks where appropriate so test discovery and linting do not fail on environments without hardware libraries installed.

## Project Structure

- `collector/`: Core BLE polling service, local SQLite storage, MQTT discovery/reporting, and embedded HTTP command server. Single source of truth for the collector service (`collector/collector.py`).
- `waterh-collector/`: Home Assistant OS add-on definition (`Dockerfile`, `config.yaml`, `run.sh`).
- `deploy_addon.sh`: SSH deployment script that packages the add-on directly to Home Assistant.
- `tests/`: Unit test suite (100% offline, mocked).
- `frontend/`: Dashboard single-page application (single source of truth for web assets).
- `server/`: Remote API server (FastAPI + SQLite).
- `protocol.md`: Decompiled GATT specifications, packet layouts, and command reference.

## Code Duplication & Single Source of Truth

- **Single Source of Truth:** `collector/collector.py` and `frontend/` are the canonical sources.
- **No Duplicate Files in `waterh-collector/`:** Never copy or commit `collector.py` into `waterh-collector/collector/` or `frontend/` into `waterh-collector/frontend/`. Both directories are ignored in `.gitignore` and must not exist in git or the local repository.
- **Add-on Packaging:** `./deploy_addon.sh` uses multi-directory `tar` streaming to package `waterh-collector/`, `collector/`, and `frontend/` directly to Home Assistant over SSH. No manual `cp` or preparatory sync steps are required.
