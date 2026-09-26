#!/bin/bash
# Fetch WaterH logs from Home Assistant over SSH
# Convenience wrapper around fetch_logs.py
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/fetch_logs.py" "$@"
