#!/bin/bash
# Network Monitor Agent - Linux Installer
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
INSTALL_DIR="/opt/network-monitor-agent"
CONFIG_DIR="/etc/network-monitor-agent"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
BINARY="$INSTALL_DIR/network-monitor-agent"
SERVICE_FILE="/etc/systemd/system/network-monitor-agent.service"
DATA_DIR="/var/lib/network-monitor-agent"
LOG_DIR="/var/log/network-monitor-agent"

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
        sed -i "s|path: \"./agent.db\"|path: \"$DATA_DIR/agent.db\"|g" "$CONFIG_FILE"
        sed -i "s|file: \"./agent.log\"|file: \"$LOG_DIR/agent.log\"|g" "$CONFIG_FILE"
    else
        echo "Configuration file already exists, skipping..."
    fi
fi

# Create systemd service file
echo "Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Network Monitor Agent
After=network.target

[Service]
Type=simple
User=root
Group=root
ExecStart=$BINARY -config $CONFIG_FILE
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=network-monitor-agent

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR $LOG_DIR

[Install]
WantedBy=multi-user.target
EOF

# Set permissions
echo "Setting permissions..."
chown -R root:root "$INSTALL_DIR"
chown -R root:root "$CONFIG_DIR"
chown -R root:root "$DATA_DIR"
chown -R root:root "$LOG_DIR"
chmod 600 "$CONFIG_FILE"

# Reload systemd
echo "Reloading systemd..."
systemctl daemon-reload

# Enable and start service
echo "Enabling service..."
systemctl enable network-monitor-agent.service

echo "Starting service..."
systemctl start network-monitor-agent.service

# Check status
sleep 2
if systemctl is-active --quiet network-monitor-agent.service; then
    echo
    echo "============================================"
    echo "Installation Complete!"
    echo "============================================"
    echo
    echo "Service is running successfully"
    echo
    echo "Useful commands:"
    echo "  Status:  systemctl status network-monitor-agent"
    echo "  Stop:    systemctl stop network-monitor-agent"
    echo "  Start:   systemctl start network-monitor-agent"
    echo "  Restart: systemctl restart network-monitor-agent"
    echo "  Logs:    journalctl -u network-monitor-agent -f"
    echo
else
    echo
    echo "WARNING: Service installed but not running"
    echo "Check status: systemctl status network-monitor-agent"
    echo "Check logs:   journalctl -u network-monitor-agent"
fi
