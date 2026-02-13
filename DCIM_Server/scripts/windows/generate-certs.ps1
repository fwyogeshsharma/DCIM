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

# Check if CA already exists
if ((Test-Path "$certDir/ca.crt") -and (Test-Path "$certDir/ca.key")) {
    Write-Host "Existing CA certificate found!" -ForegroundColor Yellow
    Write-Host ""

    # Display existing CA info
    $caInfo = openssl x509 -in "$certDir/ca.crt" -noout -subject -dates
    Write-Host "Current CA:" -ForegroundColor Cyan
    Write-Host $caInfo -ForegroundColor Gray
    Write-Host ""

    $useExisting = Read-Host "Use existing CA? (y/n, default: y)"
    if ([string]::IsNullOrWhiteSpace($useExisting) -or $useExisting -eq "y") {
        Write-Host ""
        Write-Host "[OK] Using existing CA certificate" -ForegroundColor Green
        Write-Host ""
        $skipCA = $true
    } else {
        Write-Host ""
        Write-Host "WARNING: Creating new CA will invalidate ALL existing certificates!" -ForegroundColor Red
        $confirm = Read-Host "Are you sure? (type 'yes' to confirm)"
        if ($confirm -ne "yes") {
            Write-Host "Aborted" -ForegroundColor Yellow
            exit 0
        }
        $skipCA = $false
    }
} else {
    $skipCA = $false
}

if (-not $skipCA) {
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
    # Create CA config file with proper extensions
    $caConfig = @"
[req]
default_bits = 4096
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_ca

[dn]
C=US
ST=California
L=SanFrancisco
O=DCIM
CN=DCIM-RootCA

[v3_ca]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer:always
"@

    $caConfigFile = "$certDir/ca.cnf"
    $caConfig | Out-File -FilePath $caConfigFile -Encoding ASCII

    # Generate CA certificate with proper CA extensions
    $success = Test-OpenSSLCommand -Description "Generating CA certificate" `
        -CommandLine "openssl req -new -x509 -days $caDays -key $certDir/ca.key -out $certDir/ca.crt -config $caConfigFile" `
        -ExpectedFile "$certDir/ca.crt"

    if (-not $success) {
        Write-Host ""
        Write-Host "ERROR: Failed to generate CA certificate" -ForegroundColor Red
        exit 1
    }

    # Clean up CA config file
    Remove-Item $caConfigFile -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "[SUCCESS] CA certificate generated!" -ForegroundColor Green
    Write-Host "  Valid for: $caDisplayPeriod" -ForegroundColor Gray
    Write-Host "  Expires on: $caExpiryDate" -ForegroundColor Gray
    Write-Host ""
} else {
    # Skip CA generation - using existing
    Write-Host "Skipping CA generation (using existing)" -ForegroundColor Green
    Write-Host ""
}

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

$serverIdentifier = Read-Host "Enter server identifier/name (e.g., PROD-SERVER-01, default: $serverName)"
if ([string]::IsNullOrWhiteSpace($serverIdentifier)) {
    $serverIdentifier = $serverName
}

Write-Host ""

$serverValidity = Get-CertificateValidityDays -CertType "Server" -DefaultYears 1
$serverDays = $serverValidity.Days
$serverDisplayPeriod = $serverValidity.DisplayPeriod
$serverExpiryDate = Get-ExpiryDate -Days $serverDays

Write-Host "Generating server certificate..." -ForegroundColor Cyan
Write-Host ""

# Create servers directory structure
$serversDir = "$certDir\servers"
$serverDir = "$serversDir\$serverIdentifier"

if (-not (Test-Path $serversDir)) {
    New-Item -ItemType Directory -Path $serversDir -Force | Out-Null
    Write-Host "  [OK] Created servers directory" -ForegroundColor Green
}

if (-not (Test-Path $serverDir)) {
    New-Item -ItemType Directory -Path $serverDir -Force | Out-Null
    Write-Host "  [OK] Created server directory: $serverDir" -ForegroundColor Green
}

Write-Host ""

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
DNS.1 = $serverName
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
"@

$serverConfigContent | Out-File -FilePath "$serverDir/server.cnf" -Encoding ASCII

# Generate server private key
$success = Test-OpenSSLCommand -Description "Generating server private key" `
    -CommandLine "openssl genrsa -out $serverDir/server.key 2048" `
    -ExpectedFile "$serverDir/server.key"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate server private key" -ForegroundColor Red
    exit 1
}

# Generate server CSR
$success = Test-OpenSSLCommand -Description "Generating server certificate request" `
    -CommandLine "openssl req -new -key $serverDir/server.key -out $serverDir/server.csr -config $serverDir/server.cnf" `
    -ExpectedFile "$serverDir/server.csr"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate server CSR" -ForegroundColor Red
    exit 1
}

# Sign server certificate
$success = Test-OpenSSLCommand -Description "Signing server certificate with CA" `
    -CommandLine "openssl x509 -req -in $serverDir/server.csr -CA $certDir/ca.crt -CAkey $certDir/ca.key -CAcreateserial -out $serverDir/server.crt -days $serverDays -sha256 -extfile $serverDir/server.cnf -extensions v3_req" `
    -ExpectedFile "$serverDir/server.crt"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to sign server certificate" -ForegroundColor Red
    exit 1
}

# Copy CA certificate to server directory
Copy-Item "$certDir/ca.crt" -Destination "$serverDir/ca.crt" -Force
Write-Host "  [OK] Copied CA certificate to server directory" -ForegroundColor Green

# Clean up CSR and config
Remove-Item "$serverDir/server.csr" -ErrorAction SilentlyContinue
Remove-Item "$serverDir/server.cnf" -ErrorAction SilentlyContinue
Remove-Item "$certDir/ca.srl" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[SUCCESS] Server certificate generated!" -ForegroundColor Green
Write-Host "  Hostname: $serverName" -ForegroundColor Gray
Write-Host "  Server ID: $serverIdentifier" -ForegroundColor Gray
Write-Host "  Location: $serverDir" -ForegroundColor Gray
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

# Create agents directory structure inside server directory
$agentsDir = "$serverDir\agents"
$clientDir = "$agentsDir\$clientName"

if (-not (Test-Path $agentsDir)) {
    New-Item -ItemType Directory -Path $agentsDir -Force | Out-Null
    Write-Host "  [OK] Created agents directory" -ForegroundColor Green
}

if (-not (Test-Path $clientDir)) {
    New-Item -ItemType Directory -Path $clientDir -Force | Out-Null
    Write-Host "  [OK] Created client directory: $clientDir" -ForegroundColor Green
}

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

$clientConfigContent | Out-File -FilePath "$clientDir/client.cnf" -Encoding ASCII

# Generate client private key
$success = Test-OpenSSLCommand -Description "Generating client private key" `
    -CommandLine "openssl genrsa -out $clientDir/client.key 2048" `
    -ExpectedFile "$clientDir/client.key"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate client private key" -ForegroundColor Red
    exit 1
}

# Generate client CSR
$success = Test-OpenSSLCommand -Description "Generating client certificate request" `
    -CommandLine "openssl req -new -key $clientDir/client.key -out $clientDir/client.csr -config $clientDir/client.cnf" `
    -ExpectedFile "$clientDir/client.csr"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to generate client CSR" -ForegroundColor Red
    exit 1
}

# Sign client certificate
$success = Test-OpenSSLCommand -Description "Signing client certificate with CA" `
    -CommandLine "openssl x509 -req -in $clientDir/client.csr -CA $certDir/ca.crt -CAkey $certDir/ca.key -CAcreateserial -out $clientDir/client.crt -days $clientDays -sha256 -extfile $clientDir/client.cnf -extensions v3_req" `
    -ExpectedFile "$clientDir/client.crt"

if (-not $success) {
    Write-Host ""
    Write-Host "ERROR: Failed to sign client certificate" -ForegroundColor Red
    exit 1
}

# Copy CA certificate to client directory (agents need this to verify server)
Copy-Item "$certDir/ca.crt" -Destination "$clientDir/ca.crt" -Force
Write-Host "  [OK] Copied CA certificate to client directory" -ForegroundColor Green

# Clean up CSR and config
Remove-Item "$clientDir/client.csr" -ErrorAction SilentlyContinue
Remove-Item "$clientDir/client.cnf" -ErrorAction SilentlyContinue

# Clean up other temporary files
Remove-Item "$certDir/minimal.cnf" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "[SUCCESS] Client certificate generated!" -ForegroundColor Green
Write-Host "  Client ID: $clientName" -ForegroundColor Gray
Write-Host "  Location: $clientDir" -ForegroundColor Gray
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
    "$serverDir/ca.crt",
    "$serverDir/server.crt",
    "$serverDir/server.key",
    "$clientDir/ca.crt",
    "$clientDir/client.crt",
    "$clientDir/client.key"
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
- Server ID: $serverIdentifier
- Hostname: $serverName
- Valid for: $serverDisplayPeriod
- Expires: $serverExpiryDate
- Location: servers/$serverIdentifier/
- Files: ca.crt, server.crt, server.key

## Client Certificate
- Client ID: $clientName
- Valid for: $clientDisplayPeriod
- Expires: $clientExpiryDate
- Location: servers/$serverIdentifier/agents/$clientName/
- Files: ca.crt, client.crt, client.key

## Renewal Reminders
Set calendar reminders to renew certificates at least 30 days before expiry:
- CA: Renew by $(Get-ExpiryDate -Days ($caDays - 30))
- Server: Renew by $(Get-ExpiryDate -Days ($serverDays - 30))
- Client: Renew by $(Get-ExpiryDate -Days ($clientDays - 30))

## Verification Commands
View CA certificate:
  openssl x509 -in $certDir/ca.crt -text -noout

View server certificate:
  openssl x509 -in $serverDir/server.crt -text -noout

View client certificate:
  openssl x509 -in $clientDir/client.crt -text -noout

Verify certificate chains:
  openssl verify -CAfile $certDir/ca.crt $serverDir/server.crt
  openssl verify -CAfile $clientDir/ca.crt $clientDir/client.crt
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
Write-Host "  Certificate Authority (in certs/):" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate - distribute to all agents/servers)" -ForegroundColor Gray
Write-Host "    ca.key        (CA private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Server Certificates (in certs/servers/$serverIdentifier/):" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate copy)" -ForegroundColor Gray
Write-Host "    server.crt    (Server certificate)" -ForegroundColor Gray
Write-Host "    server.key    (Server private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""
Write-Host "  Client/Agent Certificates (in certs/servers/$serverIdentifier/agents/$clientName/):" -ForegroundColor White
Write-Host "    ca.crt        (CA certificate copy)" -ForegroundColor Gray
Write-Host "    client.crt    (Agent certificate)" -ForegroundColor Gray
Write-Host "    client.key    (Agent private key - KEEP SECURE!)" -ForegroundColor Gray
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. Configure DCIM Server to use certificates from:" -ForegroundColor White
Write-Host "     certs/servers/$serverIdentifier/ca.crt" -ForegroundColor Gray
Write-Host "     certs/servers/$serverIdentifier/server.crt" -ForegroundColor Gray
Write-Host "     certs/servers/$serverIdentifier/server.key" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Start DCIM Server:" -ForegroundColor White
Write-Host "     cd .." -ForegroundColor Gray
Write-Host "     .\dcim-server.exe -config config.yaml" -ForegroundColor Gray
Write-Host ""
Write-Host "  3. Configure agents with certificates from:" -ForegroundColor White
Write-Host "     certs/servers/$serverIdentifier/agents/$clientName/ca.crt" -ForegroundColor Gray
Write-Host "     certs/servers/$serverIdentifier/agents/$clientName/client.crt" -ForegroundColor Gray
Write-Host "     certs/servers/$serverIdentifier/agents/$clientName/client.key" -ForegroundColor Gray
Write-Host ""
Write-Host "  4. Generate additional agent certificates:" -ForegroundColor White
Write-Host "     .\scripts\generate-client-cert.ps1 -ServerName '$serverIdentifier' -AgentName 'agent-02'" -ForegroundColor Gray
Write-Host ""

Write-Host "WARNING: SECURITY NOTES" -ForegroundColor Red
Write-Host "  * Keep all .key files SECURE" -ForegroundColor Yellow
Write-Host "  * Never commit keys to version control" -ForegroundColor Yellow
Write-Host "  * Set reminders to renew before expiry" -ForegroundColor Yellow
Write-Host ""
