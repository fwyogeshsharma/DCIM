#!/bin/bash
# Renew Server Certificate
# Renews the server certificate using the existing CA

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Check arguments
if [ "$#" -lt 1 ]; then
    echo -e "${CYAN}Usage: $0 <server-name> [validity-days] [--no-backup]${NC}"
    echo ""
    echo -e "${YELLOW}Arguments:${NC}"
    echo "  server-name     Server identifier (e.g., PROD-01)"
    echo "  validity-days   Certificate validity in days (default: 365)"
    echo "  --no-backup     Skip backup of existing certificates"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 PROD-01"
    echo "  $0 PROD-01 730"
    echo "  $0 PROD-01 365 --no-backup"
    echo ""
    exit 1
fi

SERVER_NAME="$1"
VALIDITY_DAYS="${2:-365}"
BACKUP=true

# Check for --no-backup flag
if [ "$3" == "--no-backup" ]; then
    BACKUP=false
fi

echo -e "${CYAN}================================"
echo "Server Certificate Renewal"
echo -e "================================${NC}"
echo ""

# Set paths
CERTS_DIR="../../certs"
SERVER_DIR="$CERTS_DIR/servers/$SERVER_NAME"
SERVER_CERT="$SERVER_DIR/server.crt"
SERVER_KEY="$SERVER_DIR/server.key"
CA_CERT="$CERTS_DIR/ca.crt"
CA_KEY="$CERTS_DIR/ca.key"

# Check if server directory exists
if [ ! -d "$SERVER_DIR" ]; then
    echo -e "${RED}ERROR: Server directory not found: $SERVER_DIR${NC}"
    echo ""
    echo -e "${YELLOW}Run generate-certs.sh first to create server '$SERVER_NAME'${NC}"
    echo ""
    exit 1
fi

# Check if CA exists
if [ ! -f "$CA_CERT" ] || [ ! -f "$CA_KEY" ]; then
    echo -e "${RED}ERROR: CA certificate not found!${NC}"
    echo ""
    echo -e "${YELLOW}Required files missing:${NC}"
    [ ! -f "$CA_CERT" ] && echo "  - $CA_CERT"
    [ ! -f "$CA_KEY" ] && echo "  - $CA_KEY"
    echo ""
    exit 1
fi

# Backup existing certificates
if [ "$BACKUP" = true ] && [ -f "$SERVER_CERT" ]; then
    TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
    BACKUP_DIR="$SERVER_DIR/backups"

    mkdir -p "$BACKUP_DIR"

    echo -e "${YELLOW}Backing up existing certificates...${NC}"
    cp "$SERVER_CERT" "$BACKUP_DIR/server.crt.$TIMESTAMP"
    cp "$SERVER_KEY" "$BACKUP_DIR/server.key.$TIMESTAMP"
    echo -e "${GREEN}[OK] Backup created in $BACKUP_DIR${NC}"
    echo ""
fi

# Display current certificate info
if [ -f "$SERVER_CERT" ]; then
    echo -e "${CYAN}Current certificate expires:${NC}"
    openssl x509 -in "$SERVER_CERT" -noout -enddate
    echo ""
fi

# Get server hostname from existing certificate
SERVER_HOSTNAME="localhost"
if [ -f "$SERVER_CERT" ]; then
    SERVER_HOSTNAME=$(openssl x509 -in "$SERVER_CERT" -noout -subject | sed -n 's/.*CN\s*=\s*\([^,/]*\).*/\1/p' | xargs)
fi

echo -e "${CYAN}Server: $SERVER_NAME${NC}"
echo -e "${CYAN}Hostname: $SERVER_HOSTNAME${NC}"
echo ""
echo -e "${YELLOW}Generating new server certificate (valid for $VALIDITY_DAYS days)...${NC}"
echo ""

# Generate new server private key
echo -e "  ${CYAN}Generating new private key...${NC}"
openssl genrsa -out "$SERVER_KEY" 2048 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Private key generated${NC}"

# Create server certificate config
cat > "$SERVER_DIR/server_renewal.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=US
ST=California
L=SanFrancisco
O=DCIM
CN=$SERVER_HOSTNAME

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SERVER_HOSTNAME
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

# Generate certificate signing request
echo -e "  ${CYAN}Generating certificate signing request...${NC}"
openssl req -new -key "$SERVER_KEY" -out "$SERVER_DIR/server.csr" \
    -config "$SERVER_DIR/server_renewal.cnf" 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] CSR generated${NC}"

# Sign with CA
echo -e "  ${CYAN}Signing certificate with CA...${NC}"
openssl x509 -req -in "$SERVER_DIR/server.csr" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$SERVER_CERT" \
    -days "$VALIDITY_DAYS" -sha256 \
    -extfile "$SERVER_DIR/server_renewal.cnf" -extensions v3_req 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Certificate signed${NC}"

# Clean up temporary files
rm -f "$SERVER_DIR/server.csr" "$SERVER_DIR/server_renewal.cnf" "$CERTS_DIR/ca.srl"

echo ""
echo -e "${GREEN}================================"
echo "Certificate Renewed!"
echo -e "================================${NC}"
echo ""

# Display new certificate info
echo -e "${CYAN}New certificate details:${NC}"
openssl x509 -in "$SERVER_CERT" -noout -subject -issuer -dates
echo ""

echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Restart DCIM Server to use new certificate"
echo "  2. Test connection: curl -k https://localhost:8443/health"
echo "  3. Update any clients/agents if needed"
echo ""

# Calculate new expiry date
EXPIRY_DATE=$(date -d "+$VALIDITY_DAYS days" '+%Y-%m-%d' 2>/dev/null || date -v "+${VALIDITY_DAYS}d" '+%Y-%m-%d' 2>/dev/null)
RENEWAL_DATE=$(date -d "+$((VALIDITY_DAYS - 30)) days" '+%Y-%m-%d' 2>/dev/null || date -v "+$((VALIDITY_DAYS - 30))d" '+%Y-%m-%d' 2>/dev/null)

echo -e "${GREEN}Certificate valid until: $EXPIRY_DATE${NC}"
echo -e "${GRAY}Set reminder to renew 30 days before: $RENEWAL_DATE${NC}"
echo ""
