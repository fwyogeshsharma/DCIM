# Renew Server Certificate
# Renews the server certificate using the existing CA

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerName,

    [int]$ValidityDays = 365,
    [switch]$Backup = $true
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Server Certificate Renewal" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Set paths
$certsDir = "..\..\certs"
$serverDir = "$certsDir\servers\$ServerName"
$serverCert = "$serverDir\server.crt"
$serverKey = "$serverDir\server.key"
$caCert = "$certsDir\ca.crt"
$caKey = "$certsDir\ca.key"

# Check if server directory exists
if (-not (Test-Path $serverDir)) {
    Write-Host "Error: Server directory not found: $serverDir" -ForegroundColor Red
    Write-Host "Run generate-certs.ps1 first to create server '$ServerName'" -ForegroundColor Yellow
    exit 1
}

# Check if CA exists
if (-not (Test-Path $caCert) -or -not (Test-Path $caKey)) {
    Write-Host "Error: CA certificate not found!" -ForegroundColor Red
    Write-Host "CA files required: $caCert, $caKey" -ForegroundColor Yellow
    exit 1
}

# Backup existing certificates
if ($Backup -and (Test-Path $serverCert)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "$serverDir\backups"

    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir | Out-Null
    }

    Write-Host "Backing up existing certificates..." -ForegroundColor Yellow
    Copy-Item $serverCert "$backupDir\server.crt.$timestamp" -Force
    Copy-Item $serverKey "$backupDir\server.key.$timestamp" -Force
    Write-Host "[OK] Backup created in $backupDir" -ForegroundColor Green
    Write-Host ""
}

# Display current certificate info
if (Test-Path $serverCert) {
    Write-Host "Current certificate expires:" -ForegroundColor Cyan
    openssl x509 -in $serverCert -noout -enddate
    Write-Host ""
}

# Get server hostname from existing certificate or prompt
$serverHostname = "localhost"
if (Test-Path $serverCert) {
    $certSubject = openssl x509 -in $serverCert -noout -subject
    if ($certSubject -match "CN\s*=\s*([^,/]+)") {
        $serverHostname = $matches[1].Trim()
    }
}

Write-Host "Server: $ServerName" -ForegroundColor Cyan
Write-Host "Hostname: $serverHostname" -ForegroundColor Cyan
Write-Host ""
Write-Host "Generating new server certificate (valid for $ValidityDays days)..." -ForegroundColor Yellow
Write-Host ""

# Generate new server private key
Write-Host "1. Generating new private key..." -ForegroundColor Cyan
cmd /c "openssl genrsa -out `"$serverKey`" 2048 2>nul"
Write-Host "   [OK] Private key generated" -ForegroundColor Green

# Create server certificate config
$serverConfig = @"
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
CN=$serverHostname

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = $serverHostname
DNS.2 = localhost
IP.1 = 127.0.0.1
IP.2 = ::1
"@

$configFile = "$serverDir\server_renewal.cnf"
$serverConfig | Out-File -FilePath $configFile -Encoding ASCII

# Generate certificate signing request
Write-Host "2. Generating certificate signing request..." -ForegroundColor Cyan
cmd /c "openssl req -new -key `"$serverKey`" -out `"$serverDir\server.csr`" -config `"$configFile`" 2>nul"
Write-Host "   [OK] CSR generated" -ForegroundColor Green

# Sign with CA
Write-Host "3. Signing certificate with CA..." -ForegroundColor Cyan
cmd /c "openssl x509 -req -in `"$serverDir\server.csr`" -CA `"$caCert`" -CAkey `"$caKey`" -CAcreateserial -out `"$serverCert`" -days $ValidityDays -sha256 -extfile `"$configFile`" -extensions v3_req 2>nul"

if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Certificate signed" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] Failed to sign certificate" -ForegroundColor Red
    exit 1
}

# Clean up temporary files
Remove-Item "$serverDir\server.csr" -Force -ErrorAction SilentlyContinue
Remove-Item $configFile -Force -ErrorAction SilentlyContinue
Remove-Item "$certsDir\ca.srl" -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Certificate Renewed!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Display new certificate info
Write-Host "New certificate details:" -ForegroundColor Cyan
openssl x509 -in $serverCert -noout -subject -issuer -dates
Write-Host ""

Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Restart DCIM Server to use new certificate" -ForegroundColor White
Write-Host "  2. Test connection: curl.exe -k https://localhost:8443/health" -ForegroundColor White
Write-Host "  3. Update any clients/agents if needed" -ForegroundColor White
Write-Host ""

# Calculate new expiry date
$newExpiry = (Get-Date).AddDays($ValidityDays).ToString("yyyy-MM-dd")
Write-Host "Certificate valid until: $newExpiry" -ForegroundColor Green
Write-Host "Set reminder to renew 30 days before: $((Get-Date).AddDays($ValidityDays - 30).ToString('yyyy-MM-dd'))" -ForegroundColor Gray
Write-Host ""
