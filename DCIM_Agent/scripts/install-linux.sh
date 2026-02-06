#!/bin/bash
# DICM Agent - Linux Installer
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
INSTALL_DIR="/opt/dcim-agent"
CONFIG_DIR="/etc/dcim-agent"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
BINARY="$INSTALL_DIR/dcim-agent"
SERVICE_FILE="/etc/systemd/system/dcim-agent.service"
DATA_DIR="/var/lib/dcim-agent"
LOG_DIR="/var/log/dcim-agent"

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

# Check for required certificates
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

# Create systemd service file
echo "Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=DICM Agent
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
SyslogIdentifier=dcim-agent

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR $LOG_DIR
ReadOnlyPaths=$CONFIG_DIR

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
systemctl enable dcim-agent.service

echo "Starting service..."
systemctl start dcim-agent.service

# Check status
sleep 2
if systemctl is-active --quiet dcim-agent.service; then
    echo
    echo "============================================"
    echo "Installation Complete!"
    echo "============================================"
    echo
    echo "Service is running successfully"
    echo
    echo "Useful commands:"
    echo "  Status:  systemctl status dcim-agent"
    echo "  Stop:    systemctl stop dcim-agent"
    echo "  Start:   systemctl start dcim-agent"
    echo "  Restart: systemctl restart dcim-agent"
    echo "  Logs:    journalctl -u dcim-agent -f"
    echo
else
    echo
    echo "WARNING: Service installed but not running"
    echo "Check status: systemctl status dcim-agent"
    echo "Check logs:   journalctl -u dcim-agent"
fi
