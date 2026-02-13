#!/bin/bash
# mTLS Certificate Generation Script - Cross-platform Version
# Works on Linux, macOS, and Windows (Git Bash/WSL)

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

echo -e "${CYAN}========================================"
echo -e "mTLS Certificate Generation Script"
echo -e "DCIM Server (Cross-platform)"
echo -e "========================================${NC}"
echo ""

# ============================================
# Check Dependencies
# ============================================
echo -e "${YELLOW}Checking dependencies...${NC}"

if ! command -v openssl &> /dev/null; then
    echo -e "${RED}ERROR: OpenSSL not found!${NC}"
    echo ""
    echo -e "${YELLOW}Please install OpenSSL:${NC}"
    echo "  Linux (Debian/Ubuntu): sudo apt-get install openssl"
    echo "  Linux (RHEL/CentOS):   sudo yum install openssl"
    echo "  macOS:                 brew install openssl"
    echo "  Windows:               Download from https://slproweb.com/products/Win32OpenSSL.html"
    echo ""
    exit 1
fi

OPENSSL_VERSION=$(openssl version)
echo -e "${GREEN}[OK] OpenSSL found: $OPENSSL_VERSION${NC}"
echo ""

# ============================================
# Helper Functions
# ============================================

get_certificate_validity_days() {
    local cert_type=$1
    local default_years=${2:-1}

    echo -e "${YELLOW}Certificate Validity Period for $cert_type${NC}"
    echo "-------------------------------------------"
    echo ""
    echo "Choose validity period:"
    echo "  1. Days"
    echo "  2. Months"
    echo "  3. Years (default: $default_years year)"
    echo ""

    read -p "Select option (1-3, or press Enter for default): " choice

    if [ -z "$choice" ]; then
        choice=3
    fi

    local days=0
    local display_period=""

    case $choice in
        1)
            read -p "Enter number of days (1-36500): " num_days
            if [ -z "$num_days" ] || ! [[ "$num_days" =~ ^[0-9]+$ ]] || [ "$num_days" -lt 1 ] || [ "$num_days" -gt 36500 ]; then
                echo -e "${YELLOW}Invalid input. Using default: $((default_years * 365)) days${NC}"
                days=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                days=$num_days
                display_period="$num_days day(s)"
            fi
            ;;
        2)
            read -p "Enter number of months (1-1200): " num_months
            if [ -z "$num_months" ] || ! [[ "$num_months" =~ ^[0-9]+$ ]] || [ "$num_months" -lt 1 ] || [ "$num_months" -gt 1200 ]; then
                echo -e "${YELLOW}Invalid input. Using default: $((default_years * 12)) months${NC}"
                days=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                days=$((num_months * 30))
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
                days=$((default_years * 365))
                display_period="$default_years year(s)"
            else
                days=$((num_years * 365))
                display_period="$num_years year(s)"
            fi
            ;;
        *)
            echo -e "${YELLOW}Invalid choice. Using default: $default_years year(s)${NC}"
            days=$((default_years * 365))
            display_period="$default_years year(s)"
            ;;
    esac

    echo ""
    echo -e "${GREEN}[OK] Certificate will be valid for: $display_period ($days days)${NC}"
    echo ""

    echo "$days"
}

# ============================================
# Create Certificate Directory
# ============================================
CERT_DIR="../../certs"
mkdir -p "$CERT_DIR"

# ============================================
# Step 1: Generate Certificate Authority (CA)
# ============================================
echo -e "${CYAN}========================================"
echo "Step 1/3: Certificate Authority (CA)"
echo -e "========================================${NC}"
echo ""

CA_DAYS=$(get_certificate_validity_days "Certificate Authority (CA)" 1)

echo -e "${CYAN}Generating CA certificate...${NC}"
echo ""

# Generate CA private key
echo -e "  ${CYAN}Generating CA private key...${NC}"
openssl genrsa -out "$CERT_DIR/ca.key" 4096 2>&1 | grep -v "^\..*"
if [ -f "$CERT_DIR/ca.key" ]; then
    echo -e "  ${GREEN}[OK] CA private key generated${NC}"
else
    echo -e "  ${RED}[FAILED] Could not generate CA private key${NC}"
    exit 1
fi

# Generate CA certificate
echo -e "  ${CYAN}Generating CA certificate...${NC}"
openssl req -new -x509 -days "$CA_DAYS" -key "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
    -subj "/C=US/ST=California/L=SanFrancisco/O=DCIM/CN=DCIM-RootCA" 2>&1 | grep -v "^\..*"

if [ -f "$CERT_DIR/ca.crt" ]; then
    echo -e "  ${GREEN}[OK] CA certificate generated${NC}"
else
    echo -e "  ${RED}[FAILED] Could not generate CA certificate${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}[SUCCESS] CA certificate generated!${NC}"
echo ""

# ============================================
# Step 2: Generate Server Certificate
# ============================================
echo -e "${CYAN}========================================"
echo "Step 2/3: Server Certificate"
echo -e "========================================${NC}"
echo ""

read -p "Enter server hostname or IP (e.g., monitor.example.com or localhost): " SERVER_NAME

if [ -z "$SERVER_NAME" ]; then
    echo -e "${RED}Error: Server hostname cannot be empty${NC}"
    exit 1
fi

echo ""

read -p "Enter server identifier/name (e.g., PROD-SERVER-01, default: $SERVER_NAME): " SERVER_IDENTIFIER
if [ -z "$SERVER_IDENTIFIER" ]; then
    SERVER_IDENTIFIER="$SERVER_NAME"
fi

echo ""

SERVER_DAYS=$(get_certificate_validity_days "Server" 1)

echo -e "${CYAN}Generating server certificate...${NC}"
echo ""

# Create servers directory structure
SERVERS_DIR="$CERT_DIR/servers"
SERVER_DIR="$SERVERS_DIR/$SERVER_IDENTIFIER"

mkdir -p "$SERVERS_DIR"
echo -e "  ${GREEN}[OK] Created servers directory${NC}"

mkdir -p "$SERVER_DIR"
echo -e "  ${GREEN}[OK] Created server directory: $SERVER_DIR${NC}"

echo ""

# Create OpenSSL config for server cert with SAN
cat > "$SERVER_DIR/server.cnf" <<EOF
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
CN=$SERVER_NAME

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $SERVER_NAME
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

# Generate server private key
echo -e "  ${CYAN}Generating server private key...${NC}"
openssl genrsa -out "$SERVER_DIR/server.key" 2048 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Server private key generated${NC}"

# Generate server CSR
echo -e "  ${CYAN}Generating server certificate request...${NC}"
openssl req -new -key "$SERVER_DIR/server.key" -out "$SERVER_DIR/server.csr" -config "$SERVER_DIR/server.cnf" 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Server CSR generated${NC}"

# Sign server certificate
echo -e "  ${CYAN}Signing server certificate with CA...${NC}"
openssl x509 -req -in "$SERVER_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$SERVER_DIR/server.crt" -days "$SERVER_DAYS" -sha256 \
    -extfile "$SERVER_DIR/server.cnf" -extensions v3_req 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Server certificate signed${NC}"

# Copy CA certificate to server directory
cp "$CERT_DIR/ca.crt" "$SERVER_DIR/ca.crt"
echo -e "  ${GREEN}[OK] Copied CA certificate to server directory${NC}"

# Clean up
rm -f "$SERVER_DIR/server.csr" "$SERVER_DIR/server.cnf" "$CERT_DIR/ca.srl"

echo ""
echo -e "${GREEN}[SUCCESS] Server certificate generated!${NC}"
echo "  Hostname: $SERVER_NAME"
echo "  Server ID: $SERVER_IDENTIFIER"
echo "  Location: $SERVER_DIR"
echo ""

# ============================================
# Step 3: Generate Client/Agent Certificate
# ============================================
echo -e "${CYAN}========================================"
echo "Step 3/3: Client/Agent Certificate"
echo -e "========================================${NC}"
echo ""

read -p "Enter agent/client identifier (default: FABER): " CLIENT_NAME
if [ -z "$CLIENT_NAME" ]; then
    CLIENT_NAME="FABER"
fi

echo ""

CLIENT_DAYS=$(get_certificate_validity_days "Client/Agent" 1)

echo -e "${CYAN}Generating client certificate...${NC}"
echo ""

# Create agents directory structure inside server directory
AGENTS_DIR="$SERVER_DIR/agents"
CLIENT_DIR="$AGENTS_DIR/$CLIENT_NAME"

mkdir -p "$AGENTS_DIR"
echo -e "  ${GREEN}[OK] Created agents directory${NC}"

mkdir -p "$CLIENT_DIR"
echo -e "  ${GREEN}[OK] Created client directory: $CLIENT_DIR${NC}"

echo ""

# Create OpenSSL config for client cert
cat > "$CLIENT_DIR/client.cnf" <<EOF
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
CN=$CLIENT_NAME

[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $CLIENT_NAME
EOF

# Generate client private key
echo -e "  ${CYAN}Generating client private key...${NC}"
openssl genrsa -out "$CLIENT_DIR/client.key" 2048 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client private key generated${NC}"

# Generate client CSR
echo -e "  ${CYAN}Generating client certificate request...${NC}"
openssl req -new -key "$CLIENT_DIR/client.key" -out "$CLIENT_DIR/client.csr" -config "$CLIENT_DIR/client.cnf" 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client CSR generated${NC}"

# Sign client certificate
echo -e "  ${CYAN}Signing client certificate with CA...${NC}"
openssl x509 -req -in "$CLIENT_DIR/client.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$CLIENT_DIR/client.crt" -days "$CLIENT_DAYS" -sha256 \
    -extfile "$CLIENT_DIR/client.cnf" -extensions v3_req 2>&1 | grep -v "^\..*"
echo -e "  ${GREEN}[OK] Client certificate signed${NC}"

# Copy CA certificate to client directory
cp "$CERT_DIR/ca.crt" "$CLIENT_DIR/ca.crt"
echo -e "  ${GREEN}[OK] Copied CA certificate to client directory${NC}"

# Clean up
rm -f "$CLIENT_DIR/client.csr" "$CLIENT_DIR/client.cnf" "$CERT_DIR/ca.srl"

echo ""
echo -e "${GREEN}[SUCCESS] Client certificate generated!${NC}"
echo "  Client ID: $CLIENT_NAME"
echo "  Location: $CLIENT_DIR"
echo ""

# ============================================
# Verify All Certificates
# ============================================
echo -e "${CYAN}========================================"
echo "Verifying Certificates..."
echo -e "========================================${NC}"
echo ""

ALL_FILES_EXIST=true
EXPECTED_FILES=(
    "$CERT_DIR/ca.crt"
    "$CERT_DIR/ca.key"
    "$SERVER_DIR/ca.crt"
    "$SERVER_DIR/server.crt"
    "$SERVER_DIR/server.key"
    "$CLIENT_DIR/ca.crt"
    "$CLIENT_DIR/client.crt"
    "$CLIENT_DIR/client.key"
)

for file in "${EXPECTED_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}[OK] $file${NC}"
    else
        echo -e "${RED}[MISSING] $file${NC}"
        ALL_FILES_EXIST=false
    fi
done

if [ "$ALL_FILES_EXIST" = false ]; then
    echo ""
    echo -e "${RED}ERROR: Some certificate files are missing!${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}[SUCCESS] All certificate files verified!${NC}"
echo ""

# ============================================
# Save Certificate Information
# ============================================
cat > "$CERT_DIR/CERTIFICATE_INFO.txt" <<EOF
# Certificate Information
# Generated: $(date '+%Y-%m-%d %H:%M:%S')

## Certificate Authority (CA)
- Valid for: $CA_DAYS days
- Files: ca.crt, ca.key

## Server Certificate
- Server ID: $SERVER_IDENTIFIER
- Hostname: $SERVER_NAME
- Valid for: $SERVER_DAYS days
- Location: servers/$SERVER_IDENTIFIER/
- Files: ca.crt, server.crt, server.key

## Client Certificate
- Client ID: $CLIENT_NAME
- Valid for: $CLIENT_DAYS days
- Location: servers/$SERVER_IDENTIFIER/agents/$CLIENT_NAME/
- Files: ca.crt, client.crt, client.key

## Verification Commands
View CA certificate:
  openssl x509 -in $CERT_DIR/ca.crt -text -noout

View server certificate:
  openssl x509 -in $SERVER_DIR/server.crt -text -noout

View client certificate:
  openssl x509 -in $CLIENT_DIR/client.crt -text -noout

Verify certificate chains:
  openssl verify -CAfile $CERT_DIR/ca.crt $SERVER_DIR/server.crt
  openssl verify -CAfile $CLIENT_DIR/ca.crt $CLIENT_DIR/client.crt
EOF

# ============================================
# Summary
# ============================================
echo -e "${GREEN}========================================"
echo "Certificate Generation Complete!"
echo -e "========================================${NC}"
echo ""

echo -e "${YELLOW}Generated files in '$CERT_DIR' directory:${NC}"
echo ""
echo "  Certificate Authority (in certs/):"
echo "    ca.crt        (CA certificate - distribute to all agents/servers)"
echo "    ca.key        (CA private key - KEEP SECURE!)"
echo ""
echo "  Server Certificates (in certs/servers/$SERVER_IDENTIFIER/):"
echo "    ca.crt        (CA certificate copy)"
echo "    server.crt    (Server certificate)"
echo "    server.key    (Server private key - KEEP SECURE!)"
echo ""
echo "  Client/Agent Certificates (in certs/servers/$SERVER_IDENTIFIER/agents/$CLIENT_NAME/):"
echo "    ca.crt        (CA certificate copy)"
echo "    client.crt    (Agent certificate)"
echo "    client.key    (Agent private key - KEEP SECURE!)"
echo ""

echo -e "${YELLOW}Next Steps:${NC}"
echo ""
echo "  1. Configure DCIM Server to use certificates from:"
echo "     certs/servers/$SERVER_IDENTIFIER/ca.crt"
echo "     certs/servers/$SERVER_IDENTIFIER/server.crt"
echo "     certs/servers/$SERVER_IDENTIFIER/server.key"
echo ""
echo "  2. Start DCIM Server:"
echo "     cd .."
echo "     ./dcim-server -config config.yaml"
echo ""
echo "  3. Configure agents with certificates from:"
echo "     certs/servers/$SERVER_IDENTIFIER/agents/$CLIENT_NAME/ca.crt"
echo "     certs/servers/$SERVER_IDENTIFIER/agents/$CLIENT_NAME/client.crt"
echo "     certs/servers/$SERVER_IDENTIFIER/agents/$CLIENT_NAME/client.key"
echo ""
echo "  4. Generate additional agent certificates:"
echo "     ./scripts/generate-client-cert.sh $SERVER_IDENTIFIER agent-02"
echo ""

echo -e "${RED}WARNING: SECURITY NOTES${NC}"
echo -e "${YELLOW}  * Keep all .key files SECURE${NC}"
echo -e "${YELLOW}  * Never commit keys to version control${NC}"
echo -e "${YELLOW}  * Set reminders to renew before expiry${NC}"
echo ""
