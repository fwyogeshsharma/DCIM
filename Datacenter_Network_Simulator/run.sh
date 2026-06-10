#!/bin/bash
# Launch the Datacenter Network Simulator on Linux.
# Must be run as root (sudo ./run.sh) for IP binding to work.
set -e
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# Load authentication credentials (DCIM_AUTH_SECRET, DCIM_AUTH_PASSWORD_HASH).
# sudo strips the caller's environment, so the secret must be loaded here —
# inside the script — rather than relying on the parent shell's exports.
if [ -f "auth.env" ]; then
    set -a
    . ./auth.env
    set +a
fi

if [ -z "$DCIM_AUTH_SECRET" ]; then
    echo "WARNING: DCIM_AUTH_SECRET not set — REST API will refuse to start." >&2
    echo "         Create ./auth.env (see auth.env.example) and try again." >&2
fi

exec "$PYTHON" app/main.py "$@"
