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
