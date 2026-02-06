#!/bin/bash
# DICM Agent - macOS Uninstaller
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
INSTALL_DIR="/usr/local/opt/dcim-agent"
CONFIG_DIR="/usr/local/etc/dcim-agent"
PLIST_FILE="/Library/LaunchDaemons/com.faberlabs.dcim-agent.plist"
DATA_DIR="/usr/local/var/dcim-agent"
LOG_DIR="/usr/local/var/log/dcim-agent"

# Unload service
echo "Unloading service..."
launchctl unload "$PLIST_FILE" 2>/dev/null || true

# Remove plist file
echo "Removing service file..."
rm -f "$PLIST_FILE"

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
