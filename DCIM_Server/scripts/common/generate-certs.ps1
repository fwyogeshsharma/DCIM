# mTLS Certificate Generation Script - Improved Version
# DCIM Server - With automatic OpenSSL config fix and error handling

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "mTLS Certificate Generation Script" -ForegroundColor Cyan
Write-Host "DCIM Server (Improved)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ============================================
# Fix OpenSSL Configuration Issues
# ============================================
Write-Host "Configuring OpenSSL environment..." -ForegroundColor Yellow

# Clear problematic OpenSSL config environment variables
$env:OPENSSL_CONF = $null

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
Write-Host "Environment configured successfully" -ForegroundColor Green
Write-Host ""

# ============================================
# Helper Functions
# ============================================

function Test-OpenSSLCommand {
    param(
        [string]$Description,
        [string]$CommandLine,
        [string]$ExpectedFile
    )

    Write-Host "  $Description..." -ForegroundColor Cyan

    # Execute via cmd /c and capture exit code
    # Suppress normal output but show errors if command fails
    $output = cmd /c "$CommandLine 2>&1"
    $exitCode = $LASTEXITCODE

    # Check if command succeeded
    if ($exitCode -ne 0) {
        Write-Host "  [FAILED] Command failed with exit code: $exitCode" -ForegroundColor Red
        Write-Host "  Output: $output" -ForegroundColor Gray
        return $false
    }

    # Check if expected file was created (most reliable test)
    if ($ExpectedFile) {
        # Give file system a moment to sync
        Start-Sleep -Milliseconds 300

        if (-not (Test-Path $ExpectedFile)) {
            Write-Host "  [FAILED] Expected file not created: $ExpectedFile" -ForegroundColor Red
            return $false
        }

        # Verify file has reasonable content (at least 50 bytes for certificates)
        $fileInfo = Get-Item $ExpectedFile -ErrorAction SilentlyContinue
        if (-not $fileInfo -or $fileInfo.Length -lt 50) {
            Write-Host "  [FAILED] File is empty or too small (${fileInfo.Length} bytes): $ExpectedFile" -ForegroundColor Red
            if ($fileInfo.Length -gt 0) {
                Write-Host "  File content: $(Get-Content $ExpectedFile -Raw)" -ForegroundColor Gray
            }
            return $false
        }
    }

    Write-Host "  [OK] Success" -ForegroundColor Green
    return $true
}

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

function Get-ExpiryDate {
    param([int]$Days)
    return (Get-Date).AddDays($Days).ToString("yyyy-MM-dd")
}

# ============================================
# Create Certificate Directory
# ============================================
$certDir = "..\..\certs"
if (-not (Test-Path $certDir)) {
    New-Item -ItemType Directory -Path $certDir | Out-Null
}

# Create a minimal OpenSSL config to avoid Strawberry Perl's hardcoded path issues
$minimalConfig = @"
[ req ]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn

[ dn ]
C=US
ST=California
L=SanFrancisco
O=DCIM
CN=Minimal
"@

$minimalConfig | Out-File -FilePath "$certDir/minimal.cnf" -Encoding ASCII

# Point OpenSSL to this config file
$env:OPENSSL_CONF = (Resolve-Path "$certDir/minimal.cnf").Path

Write-Host "Created minimal OpenSSL config: $env:OPENSSL_CONF" -ForegroundColor Green
Write-Host ""

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
Write-Host ""

# Generate CA private key
$success = Test-OpenSSLCommand -Description "Generating CA private key" `
    -CommandLine "openssl genrsa -out $certDir/ca.key 4096" `
    -ExpectedFile "$certDir/ca.key"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate CA private key" -ForegroundColor Red
    exit 1
}

# Generate CA certificate
$success = Test-OpenSSLCommand -Description "Generating CA certificate" `
    -CommandLine "openssl req -new -x509 -days $caDays -key $certDir/ca.key -out $certDir/ca.crt -subj /C=US/ST=California/L=SanFrancisco/O=DCIM/CN=DCIM-RootCA" `
    -ExpectedFile "$certDir/ca.crt"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate CA certificate" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[SUCCESS] CA certificate generated!" -ForegroundColor Green
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
Write-Host ""

# Detect if serverName is an IP address or a hostname
$isIPAddress = $serverName -match '^\d{1,3}(\.\d{1,3}){3}$' -or $serverName -match '^[0-9a-fA-F:]+:[0-9a-fA-F:]+$'

if ($isIPAddress) {
    $altNamesSection = @"
DNS.1 = localhost
IP.1 = $serverName
IP.2 = 127.0.0.1
IP.3 = ::1
"@
} else {
    $altNamesSection = @"
DNS.1 = $serverName
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
"@
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
L=SanFrancisco
O=DCIM
CN=$serverName

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
$altNamesSection
"@

$serverConfigContent | Out-File -FilePath "$certDir/server.cnf" -Encoding ASCII

# Generate server private key
$success = Test-OpenSSLCommand -Description "Generating server private key" `
    -CommandLine "openssl genrsa -out $certDir/server.key 2048" `
    -ExpectedFile "$certDir/server.key"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate server private key" -ForegroundColor Red
    exit 1
}

# Generate server CSR
$success = Test-OpenSSLCommand -Description "Generating server certificate request" `
    -CommandLine "openssl req -new -key $certDir/server.key -out $certDir/server.csr -config $certDir/server.cnf" `
    -ExpectedFile "$certDir/server.csr"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate server CSR" -ForegroundColor Red
    exit 1
}

# Sign server certificate
$success = Test-OpenSSLCommand -Description "Signing server certificate with CA" `
    -CommandLine "openssl x509 -req -in $certDir/server.csr -CA $certDir/ca.crt -CAkey $certDir/ca.key -CAcreateserial -out $certDir/server.crt -days $serverDays -sha256 -extfile $certDir/server.cnf -extensions v3_req" `
    -ExpectedFile "$certDir/server.crt"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to sign server certificate" -ForegroundColor Red
    exit 1
}

# Clean up CSR and config
Remove-Item "$certDir/server.csr" -ErrorAction SilentlyContinue
Remove-Item "$certDir/server.cnf" -ErrorAction SilentlyContinue
Remove-Item "$certDir/ca.srl" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[SUCCESS] Server certificate generated!" -ForegroundColor Green
Write-Host "  Hostname: $serverName" -ForegroundColor Gray
Write-Host "  Valid for: $serverDisplayPeriod" -ForegroundColor Gray
Write-Host "  Expires on: $serverExpiryDate" -ForegroundColor Gray
Write-Host ""

# ============================================
# Step 3: Generate Client/Agent Certificate
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Step 3/3: Client/Agent Certificate" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$clientName = Read-Host "Enter agent/client identifier (default: FABER)"
if ([string]::IsNullOrWhiteSpace($clientName)) {
    $clientName = "FABER"
}

Write-Host ""

$clientValidity = Get-CertificateValidityDays -CertType "Client/Agent" -DefaultYears 1
$clientDays = $clientValidity.Days
$clientDisplayPeriod = $clientValidity.DisplayPeriod
$clientExpiryDate = Get-ExpiryDate -Days $clientDays

Write-Host "Generating client certificate..." -ForegroundColor Cyan
Write-Host ""

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
L=SanFrancisco
O=DCIM
CN=$clientName

[v3_req]
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $clientName
"@

$clientConfigContent | Out-File -FilePath "$certDir/client.cnf" -Encoding ASCII

# Generate client private key
$success = Test-OpenSSLCommand -Description "Generating client private key" `
    -CommandLine "openssl genrsa -out $certDir/client.key 2048" `
    -ExpectedFile "$certDir/client.key"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate client private key" -ForegroundColor Red
    exit 1
}

# Generate client CSR
$success = Test-OpenSSLCommand -Description "Generating client certificate request" `
    -CommandLine "openssl req -new -key $certDir/client.key -out $certDir/client.csr -config $certDir/client.cnf" `
    -ExpectedFile "$certDir/client.csr"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate client CSR" -ForegroundColor Red
    exit 1
}

# Sign client certificate
$success = Test-OpenSSLCommand -Description "Signing client certificate with CA" `
    -CommandLine "openssl x509 -req -in $certDir/client.csr -CA $certDir/ca.crt -CAkey $certDir/ca.key -CAcreateserial -out $certDir/client.crt -days $clientDays -sha256 -extfile $certDir/client.cnf -extensions v3_req" `
    -ExpectedFile "$certDir/client.crt"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to sign client certificate" -ForegroundColor Red
    exit 1
}

# Clean up CSR and config
Remove-Item "$certDir/client.csr" -ErrorAction SilentlyContinue
Remove-Item "$certDir/client.cnf" -ErrorAction SilentlyContinue

# Clean up other temporary files
Remove-Item "$certDir/minimal.cnf" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[SUCCESS] Client certificate generated!" -ForegroundColor Green
Write-Host "  Client ID: $clientName" -ForegroundColor Gray
Write-Host "  Valid for: $clientDisplayPeriod" -ForegroundColor Gray
Write-Host "  Expires on: $clientExpiryDate" -ForegroundColor Gray
Write-Host ""

# ============================================
# Verify All Certificates
# ============================================
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Verifying Certificates..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Verify all expected files exist
$allFilesExist = $true
$expectedFiles = @(
    "$certDir/ca.crt",
    "$certDir/ca.key",
    "$certDir/server.crt",
    "$certDir/server.key",
    "$certDir/client.crt",
    "$certDir/client.key"
)

foreach ($file in $expectedFiles) {
    if (Test-Path $file) {
        Write-Host "[OK] $file" -ForegroundColor Green
    } else {
        Write-Host "[MISSING] $file" -ForegroundColor Red
        $allFilesExist = $false
    }
}

if (-not $allFilesExist) {
    Write-Host ""
    Write-Host "ERROR: Some certificate files are missing!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[SUCCESS] All certificate files verified!" -ForegroundColor Green
Write-Host ""

# ============================================
# Save Certificate Information
# ============================================
$certInfo = @"
# Certificate Information
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

## Certificate Authority (CA)
- Valid for: $caDisplayPeriod
- Expires: $caExpiryDate
- Files: ca.crt, ca.key

## Server Certificate
- Hostname: $serverName
- Valid for: $serverDisplayPeriod
- Expires: $serverExpiryDate
- Files: server.crt, server.key

## Client Certificate
- Client ID: $clientName
- Valid for: $clientDisplayPeriod
- Expires: $clientExpiryDate
- Files: client.crt, client.key

## Renewal Reminders
Set calendar reminders to renew certificates at least 30 days before expiry:
- CA: Renew by $(Get-ExpiryDate -Days ($caDays - 30))
- Server: Renew by $(Get-ExpiryDate -Days ($serverDays - 30))
- Client: Renew by $(Get-ExpiryDate -Days ($clientDays - 30))

## Verification Commands
View CA certificate:
  openssl x509 -in $certDir/ca.crt -text -noout

View server certificate:
  openssl x509 -in $certDir/server.crt -text -noout

View client certificate:
  openssl x509 -in $certDir/client.crt -text -noout

Verify certificate chains:
  openssl verify -CAfile $certDir/ca.crt $certDir/server.crt
  openssl verify -CAfile $certDir/ca.crt $certDir/client.crt
"@

$certInfo | Out-File -FilePath "$certDir/CERTIFICATE_INFO.txt" -Encoding UTF8

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
Write-Host "  [CLIENT] $clientName" -ForegroundColor White
Write-Host "     Valid for: $clientDisplayPeriod" -ForegroundColor Gray
Write-Host "     Expires: $clientExpiryDate" -ForegroundColor Gray
Write-Host ""

Write-Host "Generated files in '$certDir' directory:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Certificate Authority:" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate - distribute to all agents/servers)" -ForegroundColor Gray
Write-Host "    ca.key        (CA private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Server Certificates:" -ForegroundColor White
Write-Host "    server.crt    (Server certificate)" -ForegroundColor Gray
Write-Host "    server.key    (Server private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Client/Agent Certificates:" -ForegroundColor White
Write-Host "    client.crt    (Agent certificate)" -ForegroundColor Gray
Write-Host "    client.key    (Agent private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Start DCIM Server:" -ForegroundColor White
Write-Host "     cd .." -ForegroundColor Gray
Write-Host "     .\dcim-server.exe -config config.yaml" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Configure agents with:" -ForegroundColor White
Write-Host "     - certs/client.crt (or generate new with generate-client-cert.ps1)" -ForegroundColor Gray
Write-Host "     - certs/client.key" -ForegroundColor Gray
Write-Host "     - certs/ca.crt" -ForegroundColor Gray
Write-Host ""
Write-Host "  3. Generate additional agent certificates:" -ForegroundColor White
Write-Host "     .\scripts\generate-client-cert.ps1 -AgentName 'agent-02'" -ForegroundColor Gray
Write-Host ""

Write-Host "WARNING: SECURITY NOTES" -ForegroundColor Red
Write-Host "  * Keep all .key files SECURE" -ForegroundColor Yellow
Write-Host "  * Never commit keys to version control" -ForegroundColor Yellow
Write-Host "  * Set reminders to renew before expiry" -ForegroundColor Yellow
Write-Host ""
