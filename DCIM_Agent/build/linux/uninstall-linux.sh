#!/bin/bash
# DICM Agent - Linux Uninstaller
# Run with sudo

set -e

echo "============================================"
echo "DICM Agent Uninstaller"
echo "============================================"
echo

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This uninstaller must be run as root"
    echo "Please run: sudo $0"
    exit 1
fi

# Configuration
INSTALL_DIR="/opt/dcim-agent"
CONFIG_DIR="/etc/dcim-agent"
SERVICE_FILE="/etc/systemd/system/dcim-agent.service"
DATA_DIR="/var/lib/dcim-agent"
LOG_DIR="/var/log/dcim-agent"

# Stop service
echo "Stopping service..."
systemctl stop dcim-agent.service || true

# Disable service
echo "Disabling service..."
systemctl disable dcim-agent.service || true

# Remove service file
echo "Removing service file..."
rm -f "$SERVICE_FILE"

# Reload systemd
systemctl daemon-reload

# Remove binary
echo "Removing binary..."
rm -rf "$INSTALL_DIR"

# Ask about data removal
read -p "Remove configuration and database files? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Removing all data..."
    rm -rf "$CONFIG_DIR"
    rm -rf "$DATA_DIR"
    rm -rf "$LOG_DIR"
else
    echo "Keeping configuration and data files"
fi

echo
echo "============================================"
echo "Uninstallation Complete!"
echo "============================================"
