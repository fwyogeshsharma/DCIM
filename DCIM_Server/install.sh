#!/bin/bash
# DCIM Server Installation Script for Linux
# This script installs and configures the DCIM Server as a systemd service

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Default installation directory
INSTALL_DIR="/opt/dcim-server"
SERVICE_NAME="dcim-server"
SERVICE_USER="dcim"

echo -e "${CYAN}================================${NC}"
echo -e "${CYAN}DCIM Server - Installation${NC}"
echo -e "${CYAN}================================${NC}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root${NC}"
    echo "Please run: sudo $0"
    exit 1
fi

# Check if binary exists
if [ ! -f "dcim-server" ]; then
    echo -e "${RED}Error: dcim-server binary not found${NC}"
    echo "Please build the server first: go build -o dcim-server cmd/dcim-server/main.go"
    exit 1
fi

echo -e "${YELLOW}Installing DCIM Server to ${INSTALL_DIR}${NC}"

# Create installation directory
echo "Creating installation directory..."
mkdir -p ${INSTALL_DIR}
mkdir -p ${INSTALL_DIR}/certs
mkdir -p ${INSTALL_DIR}/data
mkdir -p ${INSTALL_DIR}/logs

# Copy binary
echo "Copying binary..."
cp dcim-server ${INSTALL_DIR}/
chmod +x ${INSTALL_DIR}/dcim-server

# Copy config if exists
if [ -f "config.yaml" ]; then
    echo "Copying configuration..."
    cp config.yaml ${INSTALL_DIR}/
else
    echo -e "${YELLOW}Warning: config.yaml not found, you'll need to create it${NC}"
fi

# Copy certificates if exist
if [ -d "certs" ] && [ "$(ls -A certs)" ]; then
    echo "Copying certificates..."
    cp certs/* ${INSTALL_DIR}/certs/
else
    echo -e "${YELLOW}Warning: No certificates found in certs/ directory${NC}"
    echo "You'll need to copy certificates manually to ${INSTALL_DIR}/certs/"
fi

# Copy license if exists
if [ -f "license.json" ]; then
    echo "Copying license..."
    cp license.json ${INSTALL_DIR}/
else
    echo -e "${YELLOW}Warning: license.json not found${NC}"
    echo "You can generate one later with: ${INSTALL_DIR}/dcim-server -generate-license"
fi

# Create service user
echo "Creating service user..."
if ! id -u ${SERVICE_USER} > /dev/null 2>&1; then
    useradd -r -s /bin/false -d ${INSTALL_DIR} ${SERVICE_USER}
    echo -e "${GREEN}Created user: ${SERVICE_USER}${NC}"
else
    echo "User ${SERVICE_USER} already exists"
fi

# Set ownership
echo "Setting file ownership..."
chown -R ${SERVICE_USER}:${SERVICE_USER} ${INSTALL_DIR}

# Create systemd service file
echo "Creating systemd service..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=DCIM Server - Data Center Infrastructure Monitoring
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/dcim-server -config ${INSTALL_DIR}/config.yaml
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}/data ${INSTALL_DIR}/logs

# Resource limits
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
echo "Reloading systemd..."
systemctl daemon-reload

echo ""
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo ""
echo "Installation directory: ${INSTALL_DIR}"
echo "Service user: ${SERVICE_USER}"
echo ""
echo "Next steps:"
echo -e "${CYAN}1. Configure the server:${NC}"
echo "   sudo nano ${INSTALL_DIR}/config.yaml"
echo ""
echo -e "${CYAN}2. Copy certificates (if not already done):${NC}"
echo "   sudo cp /path/to/certs/* ${INSTALL_DIR}/certs/"
echo "   sudo chown ${SERVICE_USER}:${SERVICE_USER} ${INSTALL_DIR}/certs/*"
echo ""
echo -e "${CYAN}3. Generate license (if needed):${NC}"
echo "   sudo -u ${SERVICE_USER} ${INSTALL_DIR}/dcim-server -generate-license -license-output ${INSTALL_DIR}/license.json"
echo ""
echo -e "${CYAN}4. Start the service:${NC}"
echo "   sudo systemctl start ${SERVICE_NAME}"
echo "   sudo systemctl enable ${SERVICE_NAME}  # Enable on boot"
echo ""
echo -e "${CYAN}5. Check status:${NC}"
echo "   sudo systemctl status ${SERVICE_NAME}"
echo "   sudo journalctl -u ${SERVICE_NAME} -f  # View logs"
echo ""
echo -e "${CYAN}6. Configure firewall (if needed):${NC}"
echo "   sudo ufw allow 8443/tcp"
echo "   # or for firewalld:"
echo "   sudo firewall-cmd --permanent --add-port=8443/tcp"
echo "   sudo firewall-cmd --reload"
echo ""
