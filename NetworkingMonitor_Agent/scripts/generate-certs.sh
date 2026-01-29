#!/bin/bash
set -e

echo "========================================"
echo "mTLS Certificate Generation Script"
echo "Network Monitor Agent"
echo "========================================"
echo ""

# Create directory
CERT_DIR="certs"
mkdir -p "$CERT_DIR"

echo "Step 1/3: Generating Certificate Authority (CA)..."
echo "---------------------------------------------------"

# Generate CA
openssl genrsa -out "$CERT_DIR/ca.key" 4096 2>/dev/null
openssl req -new -x509 -days 3650 -key "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" \
  -subj "/C=US/ST=California/L=San Francisco/O=NetworkMonitor/CN=NetworkMonitor Root CA"

echo "✓ CA certificate generated (valid for 10 years)"
echo ""

echo "Step 2/3: Generating Server Certificate..."
echo "-------------------------------------------"

# Get server hostname
read -p "Enter server hostname or IP (e.g., monitor.example.com): " SERVER_NAME

if [ -z "$SERVER_NAME" ]; then
    echo "Error: Server hostname cannot be empty"
    exit 1
fi

# Create OpenSSL config for server cert with SAN
cat > "$CERT_DIR/server.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=US
ST=California
L=San Francisco
O=NetworkMonitor
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

openssl genrsa -out "$CERT_DIR/server.key" 2048 2>/dev/null
openssl req -new -key "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
  -config "$CERT_DIR/server.cnf" 2>/dev/null
openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
  -CAcreateserial -out "$CERT_DIR/server.crt" -days 365 \
  -extfile "$CERT_DIR/server.cnf" -extensions v3_req 2>/dev/null

rm "$CERT_DIR/server.csr"
rm "$CERT_DIR/server.cnf"

echo "✓ Server certificate generated for: $SERVER_NAME (valid for 1 year)"
echo "  Includes SANs: $SERVER_NAME, localhost, 127.0.0.1"
echo ""

echo "Step 3/3: Generating Client Certificate..."
echo "-------------------------------------------"

# Get agent hostname
AGENT_NAME=$(hostname)
read -p "Enter agent/client identifier (default: $AGENT_NAME): " CUSTOM_AGENT_NAME

if [ ! -z "$CUSTOM_AGENT_NAME" ]; then
    AGENT_NAME="$CUSTOM_AGENT_NAME"
fi

# Create OpenSSL config for client cert with SAN
cat > "$CERT_DIR/client.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=US
ST=California
L=San Francisco
O=NetworkMonitor
CN=$AGENT_NAME

[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $AGENT_NAME
EOF

openssl genrsa -out "$CERT_DIR/client.key" 2048 2>/dev/null
openssl req -new -key "$CERT_DIR/client.key" -out "$CERT_DIR/client.csr" \
  -config "$CERT_DIR/client.cnf" 2>/dev/null
openssl x509 -req -in "$CERT_DIR/client.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
  -CAcreateserial -out "$CERT_DIR/client.crt" -days 365 \
  -extfile "$CERT_DIR/client.cnf" -extensions v3_req 2>/dev/null

rm "$CERT_DIR/client.csr"
rm "$CERT_DIR/client.cnf"

echo "✓ Client certificate generated for: $AGENT_NAME (valid for 1 year)"
echo "  Includes SAN: $AGENT_NAME"
echo ""

# Set permissions
chmod 600 "$CERT_DIR"/*.key
chmod 644 "$CERT_DIR"/*.crt

echo "========================================"
echo "✓ Certificate Generation Complete!"
echo "========================================"
echo ""
echo "Generated files in '$CERT_DIR/':"
echo ""
echo "  Certificate Authority:"
echo "    • ca.crt        (CA certificate - distribute to all agents/servers)"
echo "    • ca.key        (CA private key - KEEP SECURE!)"
echo ""
echo "  Server Certificates:"
echo "    • server.crt    (Copy to server)"
echo "    • server.key    (Copy to server)"
echo ""
echo "  Client/Agent Certificates:"
echo "    • client.crt    (Agent certificate)"
echo "    • client.key    (Agent private key)"
echo ""
echo "Next Steps:"
echo ""
echo "  1. Agent Setup:"
echo "     • Certificates already in ./certs/ directory"
echo "     • Update config.yaml:"
echo "         server:"
echo "           url: \"https://$SERVER_NAME:8443/api/v1\""
echo "           tls:"
echo "             enabled: true"
echo "             client_cert_path: \"./certs/client.crt\""
echo "             client_key_path: \"./certs/client.key\""
echo "             ca_cert_path: \"./certs/ca.crt\""
echo ""
echo "  2. Server Setup:"
echo "     • Copy ca.crt, server.crt, server.key to server"
echo "     • Configure server to use mTLS (see MTLS_CERTIFICATE_GUIDE.md)"
echo ""
echo "  3. Test Connection:"
echo "     curl -v https://$SERVER_NAME:8443/api/v1/metrics \\"
echo "       --cert certs/client.crt \\"
echo "       --key certs/client.key \\"
echo "       --cacert certs/ca.crt"
echo ""
echo "For detailed instructions, see: MTLS_CERTIFICATE_GUIDE.md"
echo ""
