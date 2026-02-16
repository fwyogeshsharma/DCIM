#!/bin/bash
# Generate Additional Client Certificates
# Creates unique client certificates for additional agents under a specific server
# All signed by the same CA

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

# Check arguments
if [ "$#" -lt 2 ]; then
    echo -e "${CYAN}Usage: $0 <server-name> <agent-name> [validity-days]${NC}"
    echo ""
    echo -e "${YELLOW}Arguments:${NC}"
    echo "  server-name     Server identifier (e.g., PROD-01)"
    echo "  agent-name      Agent/client identifier (e.g., agent-02)"
    echo "  validity-days   Certificate validity in days (optional, default: interactive)"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 PROD-01 agent-02"
    echo "  $0 DEV-SERVER agent-03 730"
    echo ""
    exit 1
fi

SERVER_NAME="$1"
AGENT_NAME="$2"
VALIDITY_DAYS="${3:-0}"

echo -e "${CYAN}================================"
echo "Generate Client Certificate"
echo -e "================================${NC}"
echo ""

# Check if OpenSSL is installed
if ! command -v openssl &> /dev/null; then
    echo -e "${RED}ERROR: OpenSSL not found!${NC}"
    echo ""
    echo -e "${YELLOW}Please install OpenSSL:${NC}"
    echo "  Linux (Debian/Ubuntu): sudo apt-get install openssl"
    echo "  Linux (RHEL/CentOS):   sudo yum install openssl"
    echo "  macOS:                 brew install openssl"
    echo ""
    exit 1
fi

echo -e "${GREEN}[OK] OpenSSL found${NC}"
echo ""

# Set paths
CERT_DIR="../../certs"
SERVER_DIR="$CERT_DIR/servers/$SERVER_NAME"
AGENT_DIR="$SERVER_DIR/agents/$AGENT_NAME"

# Check if CA certificate exists
if [ ! -f "$CERT_DIR/ca.crt" ] || [ ! -f "$CERT_DIR/ca.key" ]; then
    echo -e "${RED}ERROR: CA certificate not found!${NC}"
    echo ""
    echo -e "${YELLOW}Required files missing:${NC}"
    [ ! -f "$CERT_DIR/ca.crt" ] && echo "  - $CERT_DIR/ca.crt"
    [ ! -f "$CERT_DIR/ca.key" ] && echo "  - $CERT_DIR/ca.key"
    echo ""
    echo -e "${YELLOW}Generate CA first with:${NC}"
    echo "  ./scripts/linux/generate-certs.sh"
    echo ""
    exit 1
fi

echo -e "${GREEN}[OK] CA certificate found${NC}"
echo ""

# Check if server directory exists
if [ ! -d "$SERVER_DIR" ]; then
    echo -e "${RED}ERROR: Server directory not found: $SERVER_DIR${NC}"
    echo ""
    echo -e "${YELLOW}Run generate-certs.sh first to create server '$SERVER_NAME'${NC}"
    echo ""
    exit 1
fi

echo -e "${GREEN}[OK] Server directory found: $SERVER_DIR${NC}"
echo ""

# Check if agent directory already exists
if [ -d "$AGENT_DIR" ]; then
    echo -e "${YELLOW}WARNING: Directory already exists: $AGENT_DIR${NC}"
    read -p "Overwrite existing certificate? [y/N]: " overwrite
    if [ "$overwrite" != "y" ] && [ "$overwrite" != "Y" ]; then
        echo "Cancelled."
        exit 0
    fi
    echo ""
fi

# Create agent directory
mkdir -p "$AGENT_DIR"
echo -e "${CYAN}Creating client certificate for agent: $AGENT_NAME${NC}"
echo -e "${GRAY}  Server: $SERVER_NAME${NC}"
echo -e "${GRAY}  Output: $AGENT_DIR${NC}"
echo ""

# Get validity period if not specified
if [ "$VALIDITY_DAYS" -eq 0 ]; then
    echo -e "${YELLOW}Certificate Validity Period${NC}"
    echo "-------------------------------------------"
    echo ""
    echo "Choose validity period:"
    echo "  1. Days"
    echo "  2. Months"
    echo "  3. Years (default: 1 year)"
    echo ""

    read -p "Select option (1-3, or press Enter for default): " choice

    if [ -z "$choice" ]; then
        choice=3
    fi

    display_period=""
    default_years=1

    case $choice in
        1)
            read -p "Enter number of days (1-36500): " num_days
            if [ -z "$num_days" ] || ! [[ "$num_days" =~ ^[0-9]+$ ]] || [ "$num_days" -lt 1 ] || [ "$num_days" -gt 36500 ]; then
                echo -e "${YELLOW}Invalid input. Using default: $((default_years * 365)) days${NC}"
                VALIDITY_DAYS=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                VALIDITY_DAYS=$num_days
                display_period="$num_days day(s)"
            fi
            ;;
        2)
            read -p "Enter number of months (1-1200): " num_months
            if [ -z "$num_months" ] || ! [[ "$num_months" =~ ^[0-9]+$ ]] || [ "$num_months" -lt 1 ] || [ "$num_months" -gt 1200 ]; then
                echo -e "${YELLOW}Invalid input. Using default: $((default_years * 12)) months${NC}"
                VALIDITY_DAYS=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                VALIDITY_DAYS=$((num_months * 30))
                display_period="$num_months month(s)"
            fi
            ;;
        3)
            read -p "Enter number of years (1-100, or press Enter for $default_years): " num_years
            if [ -z "$num_years" ]; then
                num_years=$default_years
            fi
            if ! [[ "$num_years" =~ ^[0-9]+$ ]] || [ "$num_years" -lt 1 ] || [ "$num_years" -gt 100 ]; then
                echo -e "${YELLOW}Invalid input. Using default: $default_years year(s)${NC}"
                VALIDITY_DAYS=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                VALIDITY_DAYS=$((num_years * 365))
                display_period="$num_years year(s)"
            fi
            ;;
        *)
            echo -e "${YELLOW}Invalid choice. Using default: $default_years year(s)${NC}"
            VALIDITY_DAYS=$((default_years * 365))
            display_period="$default_years year(s)"
            ;;
    esac

    echo ""
    echo -e "${GREEN}[OK] Certificate will be valid for: $display_period ($VALIDITY_DAYS days)${NC}"
    echo ""
fi

echo -e "${CYAN}Generating client certificate...${NC}"
echo ""

# Create OpenSSL config for client cert
cat > "$AGENT_DIR/client.cnf" <<EOF
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
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $AGENT_NAME
EOF

# Generate client private key
echo -e "  ${CYAN}Generating client private key...${NC}"
openssl genrsa -out "$AGENT_DIR/client.key" 2048 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client private key generated${NC}"

# Generate client CSR
echo -e "  ${CYAN}Generating client certificate request...${NC}"
openssl req -new -key "$AGENT_DIR/client.key" -out "$AGENT_DIR/client.csr" \
    -config "$AGENT_DIR/client.cnf" 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client CSR generated${NC}"

# Sign client certificate
echo -e "  ${CYAN}Signing client certificate with CA...${NC}"
openssl x509 -req -in "$AGENT_DIR/client.csr" \
    -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$AGENT_DIR/client.crt" \
    -days "$VALIDITY_DAYS" -sha256 \
    -extfile "$AGENT_DIR/client.cnf" -extensions v3_req 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client certificate signed${NC}"

# Copy CA certificate to agent directory
cp "$CERT_DIR/ca.crt" "$AGENT_DIR/ca.crt"
echo -e "  ${GREEN}[OK] CA certificate copied${NC}"

# Clean up temporary files
rm -f "$AGENT_DIR/client.csr" "$AGENT_DIR/client.cnf"
rm -f "$CERT_DIR/ca.srl"

echo ""
echo -e "${GREEN}================================"
echo "Client Certificate Created!"
echo -e "================================${NC}"
echo ""

EXPIRY_DATE=$(date -d "+$VALIDITY_DAYS days" '+%Y-%m-%d' 2>/dev/null || date -v "+${VALIDITY_DAYS}d" '+%Y-%m-%d' 2>/dev/null)

echo -e "${YELLOW}Certificate Details:${NC}"
echo "  Server: $SERVER_NAME"
echo "  Agent/Client: $AGENT_NAME"
echo "  Valid for: $VALIDITY_DAYS days"
echo "  Expires: $EXPIRY_DATE"
echo "  Location: $AGENT_DIR"
echo ""

echo -e "${YELLOW}Generated Files:${NC}"
echo "  $AGENT_DIR/ca.crt        - CA certificate"
echo "  $AGENT_DIR/client.crt    - Client certificate"
echo "  $AGENT_DIR/client.key    - Client private key (KEEP SECURE!)"
echo ""

echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo "1. Copy certificates to agent machine:"
echo "   scp -r $AGENT_DIR user@agent:/path/to/agent/certs/"
echo ""
echo "2. Or create a package:"
echo "   tar -czf ${AGENT_NAME}-certs.tar.gz -C $AGENT_DIR ."
echo ""
echo "3. Configure agent to use these certificates in config.yaml:"
echo "   client_cert_path: /path/to/certs/client.crt"
echo "   client_key_path: /path/to/certs/client.key"
echo "   ca_cert_path: /path/to/certs/ca.crt"
echo ""

echo -e "${RED}WARNING: SECURITY${NC}"
echo -e "${YELLOW}  * Keep the .key files SECURE${NC}"
echo -e "${YELLOW}  * Set permissions: chmod 600 $AGENT_DIR/*.key${NC}"
echo -e "${YELLOW}  * Never commit keys to version control${NC}"
echo -e "${YELLOW}  * Use secure methods to transfer keys (scp, not email)${NC}"
echo ""
