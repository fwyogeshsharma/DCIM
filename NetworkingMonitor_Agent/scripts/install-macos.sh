#!/bin/bash
# Network Monitor Agent - macOS Installer
# Run with sudo

set -e

echo "============================================"
echo "Network Monitor Agent Installer"
echo "============================================"
echo

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This installer must be run as root"
    echo "Please run: sudo $0"
    exit 1
fi

# Configuration
INSTALL_DIR="/usr/local/opt/network-monitor-agent"
CONFIG_DIR="/usr/local/etc/network-monitor-agent"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
BINARY="$INSTALL_DIR/network-monitor-agent"
PLIST_FILE="/Library/LaunchDaemons/com.faber.network-monitor-agent.plist"
DATA_DIR="/usr/local/var/network-monitor-agent"
LOG_DIR="/usr/local/var/log/network-monitor-agent"

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
if [ -f "network-monitor-agent" ]; then
    cp network-monitor-agent "$BINARY"
    chmod +x "$BINARY"
else
    echo "ERROR: network-monitor-agent binary not found"
    exit 1
fi

# Copy or create config file
if [ -f "config.yaml" ]; then
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Installing configuration file..."
        cp config.yaml "$CONFIG_FILE"

        # Update paths in config
        sed -i '' "s|path: \"./agent.db\"|path: \"$DATA_DIR/agent.db\"|g" "$CONFIG_FILE"
        sed -i '' "s|file: \"./agent.log\"|file: \"$LOG_DIR/agent.log\"|g" "$CONFIG_FILE"
    else
        echo "Configuration file already exists, skipping..."
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
    <string>com.faber.network-monitor-agent</string>

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
if launchctl list | grep -q "com.faber.network-monitor-agent"; then
    echo
    echo "============================================"
    echo "Installation Complete!"
    echo "============================================"
    echo
    echo "Service is loaded and running"
    echo
    echo "Useful commands:"
    echo "  Status:  launchctl list | grep network-monitor-agent"
    echo "  Stop:    sudo launchctl unload $PLIST_FILE"
    echo "  Start:   sudo launchctl load $PLIST_FILE"
    echo "  Logs:    tail -f $LOG_DIR/agent.log"
    echo
else
    echo
    echo "WARNING: Service installed but not loaded"
    echo "Try loading manually: sudo launchctl load $PLIST_FILE"
fi
