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

- `collector/`: Core BLE polling service, local SQLite storage, MQTT discovery/reporting, and embedded HTTP command server.
- `waterh-collector/`: Home Assistant OS add-on definition (`Dockerfile`, `config.yaml`, `run.sh`).
- `tests/`: Unit test suite (100% offline, mocked).
- `frontend/`: Dashboard single-page application.
- `server/`: Remote API server (FastAPI + SQLite).
- `protocol.md`: Decompiled GATT specifications, packet layouts, and command reference.
