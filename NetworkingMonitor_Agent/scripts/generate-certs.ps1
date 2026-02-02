# mTLS Certificate Generation Script for Windows
# Network Monitor Agent - Enhanced with Custom Validity Periods

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "mTLS Certificate Generation Script" -ForegroundColor Cyan
Write-Host "Network Monitor Agent" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Function to prompt for certificate validity period
function Get-CertificateValidityDays {
    param(
        [string]$CertType,
        [int]$DefaultYears = 1
    )

    Write-Host "Certificate Validity Period for $CertType" -ForegroundColor Yellow
    Write-Host "-------------------------------------------" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Choose validity period:" -ForegroundColor White
    Write-Host "  1. Days" -ForegroundColor Gray
    Write-Host "  2. Months" -ForegroundColor Gray
    Write-Host "  3. Years (default: $DefaultYears year)" -ForegroundColor Gray
    Write-Host ""

    $choice = Read-Host "Select option (1-3, or press Enter for default)"

    if ([string]::IsNullOrWhiteSpace($choice)) {
        $choice = "3"
    }

    $days = 0
    $displayPeriod = ""

    switch ($choice) {
        "1" {
            $numDays = Read-Host "Enter number of days (1-36500)"
            if ([string]::IsNullOrWhiteSpace($numDays) -or -not [int]::TryParse($numDays, [ref]$days) -or $days -lt 1 -or $days -gt 36500) {
                Write-Host "Invalid input. Using default: $($DefaultYears * 365) days" -ForegroundColor Yellow
                $days = $DefaultYears * 365
                $displayPeriod = "$DefaultYears year$(if($DefaultYears -gt 1){'s'})"
            } else {
                $displayPeriod = "$days day$(if($days -gt 1){'s'})"
            }
        }
        "2" {
            $numMonths = Read-Host "Enter number of months (1-1200)"
            if ([string]::IsNullOrWhiteSpace($numMonths) -or -not [int]::TryParse($numMonths, [ref]$null) -or [int]$numMonths -lt 1 -or [int]$numMonths -gt 1200) {
                Write-Host "Invalid input. Using default: $($DefaultYears * 12) months" -ForegroundColor Yellow
                $days = $DefaultYears * 365
                $displayPeriod = "$DefaultYears year$(if($DefaultYears -gt 1){'s'})"
            } else {
                $days = [int]$numMonths * 30
                $displayPeriod = "$numMonths month$(if([int]$numMonths -gt 1){'s'})"
            }
        }
        "3" {
            $numYears = Read-Host "Enter number of years (1-100, or press Enter for $DefaultYears)"
            if ([string]::IsNullOrWhiteSpace($numYears)) {
                $numYears = $DefaultYears
            }
            if (-not [int]::TryParse($numYears, [ref]$null) -or [int]$numYears -lt 1 -or [int]$numYears -gt 100) {
                Write-Host "Invalid input. Using default: $DefaultYears year$(if($DefaultYears -gt 1){'s'})" -ForegroundColor Yellow
                $days = $DefaultYears * 365
                $displayPeriod = "$DefaultYears year$(if($DefaultYears -gt 1){'s'})"
            } else {
                $days = [int]$numYears * 365
                $displayPeriod = "$numYears year$(if([int]$numYears -gt 1){'s'})"
            }
        }
        default {
            Write-Host "Invalid choice. Using default: $DefaultYears year$(if($DefaultYears -gt 1){'s'})" -ForegroundColor Yellow
            $days = $DefaultYears * 365
            $displayPeriod = "$DefaultYears year$(if($DefaultYears -gt 1){'s'})"
        }
    }

    Write-Host ""
    Write-Host "[OK] Certificate will be valid for: $displayPeriod ($days days)" -ForegroundColor Green
    Write-Host ""

    return @{
        Days = $days
        DisplayPeriod = $displayPeriod
    }
}

# Function to calculate expiry date
function Get-ExpiryDate {
    param([int]$Days)
    return (Get-Date).AddDays($Days).ToString("yyyy-MM-dd")
}

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

# ============================================
# Step 1: Generate Certificate Authority (CA)
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Step 1/3: Certificate Authority (CA)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$caValidity = Get-CertificateValidityDays -CertType "Certificate Authority (CA)" -DefaultYears 1
$caDays = $caValidity.Days
$caDisplayPeriod = $caValidity.DisplayPeriod
$caExpiryDate = Get-ExpiryDate -Days $caDays

Write-Host "Generating CA certificate..." -ForegroundColor Cyan

# Generate CA
& openssl genrsa -out "$certDir/ca.key" 4096 2>$null
& openssl req -new -x509 -days $caDays -key "$certDir/ca.key" -out "$certDir/ca.crt" `
  -subj "/C=US/ST=California/L=San Francisco/O=NetworkMonitor/CN=NetworkMonitor-RootCA"

Write-Host "[OK] CA certificate generated successfully!" -ForegroundColor Green
Write-Host "  Valid for: $caDisplayPeriod" -ForegroundColor Gray
Write-Host "  Expires on: $caExpiryDate" -ForegroundColor Gray
Write-Host ""

# ============================================
# Step 2: Generate Server Certificate
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Step 2/3: Server Certificate" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get server hostname
$serverName = Read-Host "Enter server hostname or IP (e.g., monitor.example.com or localhost)"

if ([string]::IsNullOrWhiteSpace($serverName)) {
    Write-Host "Error: Server hostname cannot be empty" -ForegroundColor Red
    exit 1
}

Write-Host ""

$serverValidity = Get-CertificateValidityDays -CertType "Server" -DefaultYears 1
$serverDays = $serverValidity.Days
$serverDisplayPeriod = $serverValidity.DisplayPeriod
$serverExpiryDate = Get-ExpiryDate -Days $serverDays

Write-Host "Generating server certificate..." -ForegroundColor Cyan

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
  -CAcreateserial -out "$certDir/server.crt" -days $serverDays `
  -extfile "$certDir/server.cnf" -extensions v3_req 2>$null

Remove-Item "$certDir/server.csr" -Force
Remove-Item "$certDir/server.cnf" -Force

Write-Host "[OK] Server certificate generated successfully!" -ForegroundColor Green
Write-Host "  Hostname: $serverName" -ForegroundColor Gray
Write-Host "  Valid for: $serverDisplayPeriod" -ForegroundColor Gray
Write-Host "  Expires on: $serverExpiryDate" -ForegroundColor Gray
Write-Host "  SANs: $serverName, localhost, 127.0.0.1, ::1" -ForegroundColor Gray
Write-Host ""

# ============================================
# Step 3: Generate Client Certificate
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Step 3/3: Client/Agent Certificate" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Get agent hostname
$defaultAgentName = $env:COMPUTERNAME
$agentName = Read-Host "Enter agent/client identifier (default: $defaultAgentName)"

if ([string]::IsNullOrWhiteSpace($agentName)) {
    $agentName = $defaultAgentName
}

Write-Host ""

$clientValidity = Get-CertificateValidityDays -CertType "Client/Agent" -DefaultYears 1
$clientDays = $clientValidity.Days
$clientDisplayPeriod = $clientValidity.DisplayPeriod
$clientExpiryDate = Get-ExpiryDate -Days $clientDays

Write-Host "Generating client certificate..." -ForegroundColor Cyan

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
  -CAcreateserial -out "$certDir/client.crt" -days $clientDays `
  -extfile "$certDir/client.cnf" -extensions v3_req 2>$null

Remove-Item "$certDir/client.csr" -Force
Remove-Item "$certDir/client.cnf" -Force

Write-Host "[OK] Client certificate generated successfully!" -ForegroundColor Green
Write-Host "  Client ID: $agentName" -ForegroundColor Gray
Write-Host "  Valid for: $clientDisplayPeriod" -ForegroundColor Gray
Write-Host "  Expires on: $clientExpiryDate" -ForegroundColor Gray
Write-Host "  SAN: $agentName" -ForegroundColor Gray
Write-Host ""

# ============================================
# Summary
# ============================================
Write-Host "========================================" -ForegroundColor Green
Write-Host "Certificate Generation Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

Write-Host "Certificate Validity Summary:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  [CA] Certificate Authority:" -ForegroundColor White
Write-Host "     Valid for: $caDisplayPeriod" -ForegroundColor Gray
Write-Host "     Expires: $caExpiryDate" -ForegroundColor Gray
Write-Host ""
Write-Host "  [SERVER] $serverName" -ForegroundColor White
Write-Host "     Valid for: $serverDisplayPeriod" -ForegroundColor Gray
Write-Host "     Expires: $serverExpiryDate" -ForegroundColor Gray
Write-Host ""
Write-Host "  [CLIENT] $agentName" -ForegroundColor White
Write-Host "     Valid for: $clientDisplayPeriod" -ForegroundColor Gray
Write-Host "     Expires: $clientExpiryDate" -ForegroundColor Gray
Write-Host ""

Write-Host "Generated files in '$certDir' directory:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Certificate Authority:" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate - distribute to all agents/servers)" -ForegroundColor Gray
Write-Host "    ca.key        (CA private key - KEEP SECURE!)" -ForegroundColor Red
Write-Host ""
Write-Host "  Server Certificates:" -ForegroundColor White
Write-Host "    server.crt    (Copy to server)" -ForegroundColor Gray
Write-Host "    server.key    (Copy to server - KEEP SECURE!)" -ForegroundColor Red
Write-Host ""
Write-Host "  Client/Agent Certificates:" -ForegroundColor White
Write-Host "    client.crt    (Agent certificate)" -ForegroundColor Gray
Write-Host "    client.key    (Agent private key - KEEP SECURE!)" -ForegroundColor Red
Write-Host ""

Write-Host "WARNING: IMPORTANT SECURITY NOTES" -ForegroundColor Yellow
Write-Host ""
Write-Host "  * Keep all .key files SECURE - never share or commit to version control" -ForegroundColor Red
Write-Host "  * CA key (ca.key) is especially critical - compromised CA = all certs invalid" -ForegroundColor Red
Write-Host "  * Set reminder to renew certificates before expiry:" -ForegroundColor Yellow
Write-Host "      - CA expires: $caExpiryDate" -ForegroundColor Gray
Write-Host "      - Server expires: $serverExpiryDate" -ForegroundColor Gray
Write-Host "      - Client expires: $clientExpiryDate" -ForegroundColor Gray
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Cyan
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
Write-Host "       curl -v https://$serverName`:8443/api/v1/metrics \"
Write-Host "         --cert certs/client.crt \"
Write-Host "         --key certs/client.key \"
Write-Host "         --cacert certs/ca.crt"
Write-Host ""
Write-Host "  4. Verify Certificates:" -ForegroundColor White
Write-Host "     openssl x509 -in certs/ca.crt -text -noout"
Write-Host "     openssl x509 -in certs/server.crt -text -noout"
Write-Host "     openssl x509 -in certs/client.crt -text -noout"
Write-Host ""
Write-Host "For detailed instructions, see: MTLS_CERTIFICATE_GUIDE.md" -ForegroundColor Cyan
Write-Host ""

# Create certificate info file
$certInfoContent = @"
# Certificate Information
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

## Certificate Authority (CA)
- Valid for: $caDisplayPeriod
- Expires: $caExpiryDate
- File: ca.crt

## Server Certificate
- Hostname: $serverName
- Valid for: $serverDisplayPeriod
- Expires: $serverExpiryDate
- Files: server.crt, server.key

## Client Certificate
- Client ID: $agentName
- Valid for: $clientDisplayPeriod
- Expires: $clientExpiryDate
- Files: client.crt, client.key

## Renewal Reminders
Set calendar reminders to renew certificates at least 30 days before expiry:
- CA: Renew by $(Get-ExpiryDate -Days ($caDays - 30))
- Server: Renew by $(Get-ExpiryDate -Days ($serverDays - 30))
- Client: Renew by $(Get-ExpiryDate -Days ($clientDays - 30))

## Verification Commands
View CA certificate details:
  openssl x509 -in certs/ca.crt -text -noout

View server certificate details:
  openssl x509 -in certs/server.crt -text -noout

View client certificate details:
  openssl x509 -in certs/client.crt -text -noout

Verify server certificate chain:
  openssl verify -CAfile certs/ca.crt certs/server.crt

Verify client certificate chain:
  openssl verify -CAfile certs/ca.crt certs/client.crt

## Security Best Practices
1. Store private keys (.key files) securely
2. Never commit private keys to version control
3. Use proper file permissions (restrict access to keys)
4. Back up CA key in a secure location
5. Implement certificate rotation before expiry
6. Monitor certificate expiration dates
7. Use strong passphrases for production CA keys
"@

$certInfoContent | Out-File -FilePath "$certDir/CERTIFICATE_INFO.txt" -Encoding UTF8

Write-Host "[INFO] Certificate information saved to: $certDir/CERTIFICATE_INFO.txt" -ForegroundColor Cyan
Write-Host ""
