#!/bin/bash
# Launch the Datacenter Network Simulator on Linux.
# Must be run as root (sudo ./run.sh) for IP binding to work.
set -e
cd "$(dirname "$0")"

# Optional --recreate-venv: delete .venv so it is rebuilt fresh below.
if [ "$1" = "--recreate-venv" ]; then
    shift
    if [ -d .venv ]; then
        echo "Removing existing .venv for recreation..."
        rm -rf .venv
    fi
fi

PYTHON=".venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    echo "Creating virtual environment (.venv)..."
    if python3 -m venv .venv; then
        "$PYTHON" -m pip install --upgrade pip
        if [ -f requirements.txt ]; then
            echo "Installing dependencies from requirements.txt..."
            "$PYTHON" -m pip install -r requirements.txt
        fi
    else
        echo "WARNING: could not create .venv — falling back to system python3." >&2
        PYTHON="python3"
    fi
fi

# ---- Linux equivalent of the Windows KM-TEST loopback adapter ----
# Windows hosts the simulated device IPs on a dedicated loopback adapter; on
# Linux a kernel 'dummy' interface plays the same role. Device IPs are bound
# here (via `ip addr add`) instead of on a real NIC, so they are never
# ARP-advertised on the physical LAN and no cable/link is required.
# Idempotent; requires root. Override the name with DCIM_DUMMY_NIC.
DUMMY_NIC="${DCIM_DUMMY_NIC:-dcim0}"
if [ "$(id -u)" = "0" ]; then
    if ip link show "$DUMMY_NIC" >/dev/null 2>&1; then
        ip link set "$DUMMY_NIC" up 2>/dev/null || true
    else
        echo "Creating dummy NIC $DUMMY_NIC for device IP binding..."
        modprobe dummy 2>/dev/null || true
        if ip link add "$DUMMY_NIC" type dummy 2>/dev/null; then
            ip link set "$DUMMY_NIC" up 2>/dev/null || true
        else
            echo "WARNING: could not create dummy NIC $DUMMY_NIC — bind to a real interface instead." >&2
        fi
    fi
    # Restrict the binding dropdown to this NIC unless the user set their own filter.
    if [ -z "$DCIM_ADAPTER_FILTER" ] && ip link show "$DUMMY_NIC" >/dev/null 2>&1; then
        export DCIM_ADAPTER_FILTER="$DUMMY_NIC"
        echo "Binding dropdown restricted to $DUMMY_NIC (set DCIM_ADAPTER_FILTER to override)."
    fi
else
    echo "WARNING: not root — skipping dummy NIC setup. Run with sudo for IP binding." >&2
fi

# First run: create auth.env (DCIM_AUTH_SECRET + admin password hash) if it is
# missing, so the REST API / Web UI authentication is set up before launch.
"$PYTHON" bootstrap_auth.py || true

# Load authentication credentials (DCIM_AUTH_SECRET, DCIM_AUTH_PASSWORD_HASH).
# sudo strips the caller's environment, so the secret must be loaded here —
# inside the script — rather than relying on the parent shell's exports.
#
# Parse literally instead of `source`-ing: the PBKDF2 hash contains '$'
# characters that a sourced double-quoted value would expand and corrupt.
# `read` performs no expansion, so any quoting style works.
if [ -f "auth.env" ]; then
    while IFS='=' read -r _key _val; do
        _key="${_key%%[[:space:]]}"
        case "$_key" in
            ''|\#*) continue ;;            # skip blanks and comments
        esac
        _val="${_val%$'\r'}"              # strip trailing CR (CRLF files)
        # strip one layer of surrounding single or double quotes
        case "$_val" in
            \"*\") _val="${_val#\"}"; _val="${_val%\"}" ;;
            \'*\') _val="${_val#\'}"; _val="${_val%\'}" ;;
        esac
        export "$_key=$_val"
    done < auth.env
    unset _key _val
fi

if [ -z "$DCIM_AUTH_SECRET" ]; then
    echo "WARNING: DCIM_AUTH_SECRET not set — REST API will refuse to start." >&2
    echo "         Create ./auth.env (see auth.env.example) and try again." >&2
fi

# Build the web UI (served from webui/dist by the REST API).
# Skip with SKIP_WEBUI_BUILD=1.
if [ "$SKIP_WEBUI_BUILD" != "1" ] && [ -d "webui" ]; then
    if command -v npm >/dev/null 2>&1; then
        echo "Building web UI..."
        ( cd webui && \
          { [ -d node_modules ] || npm install; } && \
          npm run build )
    else
        echo "WARNING: npm not found — skipping web UI build (serving existing webui/dist)." >&2
    fi
fi

exec "$PYTHON" app/main.py "$@"
