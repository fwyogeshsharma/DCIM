#!/bin/bash
# Launch the Datacenter Network Simulator on Linux.
# Must be run as root (sudo ./run.sh) for IP binding to work.
set -e
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

exec "$PYTHON" app/main.py "$@"
