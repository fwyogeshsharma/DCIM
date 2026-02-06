#!/bin/bash
# DICM Agent - macOS Installer
# Run with sudo

set -e

echo "============================================"
echo "DICM Agent Installer"
echo "============================================"
echo

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This installer must be run as root"
    echo "Please run: sudo $0"
    exit 1
fi

# Configuration
INSTALL_DIR="/usr/local/opt/dcim-agent"
CONFIG_DIR="/usr/local/etc/dcim-agent"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
BINARY="$INSTALL_DIR/dcim-agent"
PLIST_FILE="/Library/LaunchDaemons/com.faberlabs.dcim-agent.plist"
DATA_DIR="/usr/local/var/dcim-agent"
LOG_DIR="/usr/local/var/log/dcim-agent"

echo "Installation paths:"
echo "  Binary:        $BINARY"
echo "  Configuration: $CONFIG_FILE"
echo "  Data:          $DATA_DIR"
echo "  Logs:          $LOG_DIR"
echo

# Create directories
echo "Creating directories..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$LOG_DIR"

# Copy binary
echo "Installing binary..."
if [ -f "dcim-agent" ]; then
    cp dcim-agent "$BINARY"
    chmod +x "$BINARY"
else
    echo "ERROR: dcim-agent binary not found"
    exit 1
fi

# Copy or create config file
if [ -f "config.yaml" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Installing configuration file..."
        cp config.yaml "$CONFIG_FILE"
        echo "Configuration file installed with system paths"
    else
        echo "Configuration file already exists, skipping..."
    fi
fi

# Handle TLS certificates
CERTS_DIR="$CONFIG_DIR/certs"
mkdir -p "$CERTS_DIR"

if [ -d "certs" ]; then
    echo "Copying TLS certificates..."
    cp certs/* "$CERTS_DIR/" 2>/dev/null || true
    chmod 600 "$CERTS_DIR"/*.crt "$CERTS_DIR"/*.key 2>/dev/null || true
    echo "Certificates installed"
fi

# Check for required certificates (macOS uses /usr/local paths)
MISSING_CERTS=""
[ ! -f "$CERTS_DIR/ca.crt" ] && MISSING_CERTS="${MISSING_CERTS}  - ca.crt\n"
[ ! -f "$CERTS_DIR/client.crt" ] && MISSING_CERTS="${MISSING_CERTS}  - client.crt\n"
[ ! -f "$CERTS_DIR/client.key" ] && MISSING_CERTS="${MISSING_CERTS}  - client.key\n"

if [ -n "$MISSING_CERTS" ]; then
    echo
    echo "WARNING: Missing TLS certificates in $CERTS_DIR:"
    echo -e "$MISSING_CERTS"
    echo "The agent will not start without these certificates."
    echo
    echo "To fix this:"
    echo "1. Generate certificates on the DCIM Server (Windows):"
    echo "   cd C:\\Anupam\\Faber\\Projects\\DCIM\\DCIM_Server\\scripts"
    echo "   .\\generate-certs.ps1"
    echo
    echo "2. Copy the certs folder to the same directory as this installer"
    echo "3. Re-run this installer"
    echo
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Create launchd plist file
echo "Creating launchd service..."
cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.faberlabs.dcim-agent</string>

    <key>ProgramArguments</key>
    <array>
        <string>$BINARY</string>
        <string>-config</string>
        <string>$CONFIG_FILE</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/stderr.log</string>

    <key>WorkingDirectory</key>
    <string>$DATA_DIR</string>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

# Set permissions
echo "Setting permissions..."
chown -R root:wheel "$INSTALL_DIR"
chown -R root:wheel "$CONFIG_DIR"
chown -R root:wheel "$DATA_DIR"
chown -R root:wheel "$LOG_DIR"
chmod 600 "$CONFIG_FILE"
chmod 644 "$PLIST_FILE"

# Load service
echo "Loading service..."
launchctl load "$PLIST_FILE"

# Check status
sleep 2
if launchctl list | grep -q "com.faberlabs.dcim-agent"; then
    echo
    echo "============================================"
    echo "Installation Complete!"
    echo "============================================"
    echo
    echo "Service is loaded and running"
    echo
    echo "Useful commands:"
    echo "  Status:  launchctl list | grep dcim-agent"
    echo "  Stop:    sudo launchctl unload $PLIST_FILE"
    echo "  Start:   sudo launchctl load $PLIST_FILE"
    echo "  Logs:    tail -f $LOG_DIR/agent.log"
    echo
else
    echo
    echo "WARNING: Service installed but not loaded"
    echo "Try loading manually: sudo launchctl load $PLIST_FILE"
fi
