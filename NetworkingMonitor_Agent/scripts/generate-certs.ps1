# mTLS Certificate Generation Script for Windows
# Network Monitor Agent

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "mTLS Certificate Generation Script" -ForegroundColor Cyan
Write-Host "Network Monitor Agent" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if OpenSSL is installed
$opensslPath = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $opensslPath) {
    Write-Host "ERROR: OpenSSL not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install OpenSSL:" -ForegroundColor Yellow
    Write-Host "  1. Download from: https://slproweb.com/products/Win32OpenSSL.html" -ForegroundColor Yellow
    Write-Host "  2. Install the 'Win64 OpenSSL' version" -ForegroundColor Yellow
    Write-Host "  3. Add OpenSSL to your PATH" -ForegroundColor Yellow
    Write-Host "  4. Re-run this script" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Write-Host "OpenSSL found: $opensslPath" -ForegroundColor Green
Write-Host ""

# Create directory
$certDir = "certs"
if (-not (Test-Path $certDir)) {
    New-Item -ItemType Directory -Path $certDir | Out-Null
}

Write-Host "Step 1/3: Generating Certificate Authority (CA)..." -ForegroundColor Cyan
Write-Host "---------------------------------------------------" -ForegroundColor Cyan

# Generate CA
& openssl genrsa -out "$certDir/ca.key" 4096 2>$null
& openssl req -new -x509 -days 3650 -key "$certDir/ca.key" -out "$certDir/ca.crt" `
  -subj "/C=US/ST=California/L=San Francisco/O=NetworkMonitor/CN=NetworkMonitor Root CA"

Write-Host "CA certificate generated (valid for 10 years)" -ForegroundColor Green
Write-Host ""

Write-Host "Step 2/3: Generating Server Certificate..." -ForegroundColor Cyan
Write-Host "-------------------------------------------" -ForegroundColor Cyan

# Get server hostname
$serverName = Read-Host "Enter server hostname or IP (e.g., monitor.example.com or localhost)"

if ([string]::IsNullOrWhiteSpace($serverName)) {
    Write-Host "Error: Server hostname cannot be empty" -ForegroundColor Red
    exit 1
}

# Create OpenSSL config for server cert with SAN
$serverConfigContent = @"
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
CN=$serverName

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $serverName
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
"@

$serverConfigContent | Out-File -FilePath "$certDir/server.cnf" -Encoding ASCII

& openssl genrsa -out "$certDir/server.key" 2048 2>$null
& openssl req -new -key "$certDir/server.key" -out "$certDir/server.csr" -config "$certDir/server.cnf" 2>$null
& openssl x509 -req -in "$certDir/server.csr" -CA "$certDir/ca.crt" -CAkey "$certDir/ca.key" `
  -CAcreateserial -out "$certDir/server.crt" -days 365 `
  -extfile "$certDir/server.cnf" -extensions v3_req 2>$null

Remove-Item "$certDir/server.csr" -Force
Remove-Item "$certDir/server.cnf" -Force

Write-Host "Server certificate generated for: $serverName (valid for 1 year)" -ForegroundColor Green
Write-Host "  Includes SANs: $serverName, localhost, 127.0.0.1" -ForegroundColor Gray
Write-Host ""

Write-Host "Step 3/3: Generating Client Certificate..." -ForegroundColor Cyan
Write-Host "-------------------------------------------" -ForegroundColor Cyan

# Get agent hostname
$defaultAgentName = $env:COMPUTERNAME
$agentName = Read-Host "Enter agent/client identifier (default: $defaultAgentName)"

if ([string]::IsNullOrWhiteSpace($agentName)) {
    $agentName = $defaultAgentName
}

# Create OpenSSL config for client cert with SAN
$clientConfigContent = @"
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
CN=$agentName

[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $agentName
"@

$clientConfigContent | Out-File -FilePath "$certDir/client.cnf" -Encoding ASCII

& openssl genrsa -out "$certDir/client.key" 2048 2>$null
& openssl req -new -key "$certDir/client.key" -out "$certDir/client.csr" -config "$certDir/client.cnf" 2>$null
& openssl x509 -req -in "$certDir/client.csr" -CA "$certDir/ca.crt" -CAkey "$certDir/ca.key" `
  -CAcreateserial -out "$certDir/client.crt" -days 365 `
  -extfile "$certDir/client.cnf" -extensions v3_req 2>$null

Remove-Item "$certDir/client.csr" -Force
Remove-Item "$certDir/client.cnf" -Force

Write-Host "Client certificate generated for: $agentName (valid for 1 year)" -ForegroundColor Green
Write-Host "  Includes SAN: $agentName" -ForegroundColor Gray
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Certificate Generation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Generated files in '$certDir/':" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Certificate Authority:" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate - distribute to all agents/servers)" -ForegroundColor Gray
Write-Host "    ca.key        (CA private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Server Certificates:" -ForegroundColor White
Write-Host "    server.crt    (Copy to server)" -ForegroundColor Gray
Write-Host "    server.key    (Copy to server)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Client/Agent Certificates:" -ForegroundColor White
Write-Host "    client.crt    (Agent certificate)" -ForegroundColor Gray
Write-Host "    client.key    (Agent private key)" -ForegroundColor Gray
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Agent Setup:" -ForegroundColor White
Write-Host "     Certificates already in .\certs\ directory"
Write-Host "     Update config.yaml:"
Write-Host "         server:"
Write-Host "           url: https://$serverName`:8443/api/v1"
Write-Host "           tls:"
Write-Host "             enabled: true"
Write-Host "             client_cert_path: ./certs/client.crt"
Write-Host "             client_key_path: ./certs/client.key"
Write-Host "             ca_cert_path: ./certs/ca.crt"
Write-Host ""
Write-Host "  2. Server Setup:" -ForegroundColor White
Write-Host "     Copy ca.crt, server.crt, server.key to server"
Write-Host "     Configure server to use mTLS (see MTLS_CERTIFICATE_GUIDE.md)"
Write-Host ""
Write-Host "  3. Test Connection:" -ForegroundColor White
Write-Host "     Test with curl (if available):"
Write-Host "       curl -v https://$serverName`:8443/api/v1/metrics"
Write-Host "         --cert certs/client.crt"
Write-Host "         --key certs/client.key"
Write-Host "         --cacert certs/ca.crt"
Write-Host ""
Write-Host "For detailed instructions, see: MTLS_CERTIFICATE_GUIDE.md" -ForegroundColor Cyan
Write-Host ""
