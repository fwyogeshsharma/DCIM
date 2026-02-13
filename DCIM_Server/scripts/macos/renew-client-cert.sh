#!/bin/bash
# Renew Client Certificate
# Renews a client certificate using the existing CA

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Check arguments
if [ "$#" -lt 2 ]; then
    echo -e "${CYAN}Usage: $0 <server-name> <agent-name> [validity-days] [--no-backup]${NC}"
    echo ""
    echo -e "${YELLOW}Arguments:${NC}"
    echo "  server-name     Server identifier (e.g., PROD-01)"
    echo "  agent-name      Agent/client identifier (e.g., agent-02)"
    echo "  validity-days   Certificate validity in days (default: 365)"
    echo "  --no-backup     Skip backup of existing certificates"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 PROD-01 agent-02"
    echo "  $0 PROD-01 agent-02 730"
    echo "  $0 PROD-01 agent-02 365 --no-backup"
    echo ""
    exit 1
fi

SERVER_NAME="$1"
AGENT_NAME="$2"
VALIDITY_DAYS="${3:-365}"
BACKUP=true

# Check for --no-backup flag
if [ "$4" == "--no-backup" ]; then
    BACKUP=false
fi

echo -e "${CYAN}================================"
echo "Client Certificate Renewal"
echo -e "================================${NC}"
echo ""

# Set paths
CERTS_DIR="../../certs"
SERVER_DIR="$CERTS_DIR/servers/$SERVER_NAME"
AGENT_DIR="$SERVER_DIR/agents/$AGENT_NAME"
CLIENT_CERT="$AGENT_DIR/client.crt"
CLIENT_KEY="$AGENT_DIR/client.key"
CA_CERT="$CERTS_DIR/ca.crt"
CA_KEY="$CERTS_DIR/ca.key"

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

# Check if agent directory exists
if [ ! -d "$AGENT_DIR" ]; then
    echo -e "${RED}ERROR: Agent directory not found: $AGENT_DIR${NC}"
    echo ""
    echo -e "${YELLOW}Create new certificate with: ./generate-client-cert.sh $SERVER_NAME $AGENT_NAME${NC}"
    echo ""
    exit 1
fi

# Backup existing certificates
if [ "$BACKUP" = true ] && [ -f "$CLIENT_CERT" ]; then
    TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
    BACKUP_DIR="$AGENT_DIR/backups"

    mkdir -p "$BACKUP_DIR"

    echo -e "${YELLOW}Backing up existing certificate...${NC}"
    cp "$CLIENT_CERT" "$BACKUP_DIR/client.crt.$TIMESTAMP"
    cp "$CLIENT_KEY" "$BACKUP_DIR/client.key.$TIMESTAMP"
    echo -e "${GREEN}[OK] Backup created in $BACKUP_DIR${NC}"
    echo ""
fi

# Display current certificate info
if [ -f "$CLIENT_CERT" ]; then
    echo -e "${CYAN}Current certificate expires:${NC}"
    openssl x509 -in "$CLIENT_CERT" -noout -enddate
    echo ""
fi

echo -e "${YELLOW}Renewing client certificate${NC}"
echo -e "${GRAY}  Server: $SERVER_NAME${NC}"
echo -e "${GRAY}  Agent: $AGENT_NAME${NC}"
echo -e "${GRAY}  Validity: $VALIDITY_DAYS days${NC}"
echo ""

# Generate new private key
echo -e "  ${CYAN}Generating new private key...${NC}"
openssl genrsa -out "$CLIENT_KEY" 2048 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Private key generated${NC}"

# Create certificate config
cat > "$AGENT_DIR/client_renewal.cnf" <<EOF
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
CN=$AGENT_NAME

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
EOF

# Generate CSR
echo -e "  ${CYAN}Generating certificate signing request...${NC}"
openssl req -new -key "$CLIENT_KEY" -out "$AGENT_DIR/client.csr" \
    -config "$AGENT_DIR/client_renewal.cnf" 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] CSR generated${NC}"

# Sign with CA
echo -e "  ${CYAN}Signing certificate with CA...${NC}"
openssl x509 -req -in "$AGENT_DIR/client.csr" \
    -CA "$CA_CERT" -CAkey "$CA_KEY" \
    -CAcreateserial -out "$CLIENT_CERT" \
    -days "$VALIDITY_DAYS" -sha256 \
    -extfile "$AGENT_DIR/client_renewal.cnf" -extensions v3_req 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Certificate signed${NC}"

# Clean up temporary files
rm -f "$AGENT_DIR/client.csr" "$AGENT_DIR/client_renewal.cnf" "$CERTS_DIR/ca.srl"

echo ""
echo -e "${GREEN}================================"
echo "Certificate Renewed!"
echo -e "================================${NC}"
echo ""

# Display new certificate info
echo -e "${CYAN}New certificate details:${NC}"
openssl x509 -in "$CLIENT_CERT" -noout -subject -issuer -dates
echo ""

echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Copy new certificate to agent machine:"
echo "     scp -r $AGENT_DIR user@agent:/path/to/certs/"
echo "  2. Restart agent to use new certificate"
echo ""

# Calculate new expiry date
EXPIRY_DATE=$(date -d "+$VALIDITY_DAYS days" '+%Y-%m-%d' 2>/dev/null || date -v "+${VALIDITY_DAYS}d" '+%Y-%m-%d' 2>/dev/null)
RENEWAL_DATE=$(date -d "+$((VALIDITY_DAYS - 30)) days" '+%Y-%m-%d' 2>/dev/null || date -v "+$((VALIDITY_DAYS - 30))d" '+%Y-%m-%d' 2>/dev/null)

echo -e "${GREEN}Certificate valid until: $EXPIRY_DATE${NC}"
echo -e "${GRAY}Set reminder to renew 30 days before: $RENEWAL_DATE${NC}"
echo ""
