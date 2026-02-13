# Renew Server Certificate
# Renews the server certificate using the existing CA

param(
    [int]$ValidityDays = 365,
    [switch]$Backup = $true
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Server Certificate Renewal" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$CertsDir = "..\..\certs" "..\..\certs"
$ServerCert = "..\..\certs\server.crt"
$ServerKey = "..\..\certs\server.key"
$CACert = "..\..\certs\ca.crt"
$CAKey = "..\..\certs\ca.key"

# Check if CA exists
if (-not (Test-Path $CACert) -or -not (Test-Path $CAKey)) {
    Write-Host "Error: CA certificate not found!" -ForegroundColor Red
    Write-Host "CA files required: $CACert, $CAKey" -ForegroundColor Yellow
    exit 1
}

# Backup existing certificates
if ($Backup -and (Test-Path $ServerCert)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "..\..\certs\backups"

    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir | Out-Null
    }

    Write-Host "Backing up existing certificates..." -ForegroundColor Yellow
    Copy-Item $ServerCert "$backupDir\server.crt.$timestamp" -Force
    Copy-Item $ServerKey "$backupDir\server.key.$timestamp" -Force
    Write-Host "[OK] Backup created in $backupDir" -ForegroundColor Green
    Write-Host ""
}

# Display current certificate info
if (Test-Path $ServerCert) {
    Write-Host "Current certificate expires:" -ForegroundColor Cyan
    openssl x509 -in $ServerCert -noout -enddate
    Write-Host ""
}

Write-Host "Generating new server certificate (valid for $ValidityDays days)..." -ForegroundColor Yellow
Write-Host ""

# Generate new server private key
Write-Host "1. Generating new private key..." -ForegroundColor Cyan
openssl genrsa -out $ServerKey 2048 2>&1 | Out-Null
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
ST=State
L=City
O=Organization
OU=IT Department
CN=dcim-server

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = dcim-server
DNS.3 = dcim-server.local
DNS.4 = *.dcim-server.local
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
"@

$configFile = "..\..\certs\server_renewal.cnf"
$serverConfig | Out-File -FilePath $configFile -Encoding ASCII

# Generate certificate signing request
Write-Host "2. Generating certificate signing request..." -ForegroundColor Cyan
openssl req -new -key $ServerKey -out "..\..\certs\server.csr" -config $configFile 2>&1 | Out-Null
Write-Host "   [OK] CSR generated" -ForegroundColor Green

# Sign with CA
Write-Host "3. Signing certificate with CA..." -ForegroundColor Cyan
openssl x509 -req -in "..\..\certs\server.csr" `
    -CA $CACert `
    -CAkey $CAKey `
    -CAcreateserial `
    -out $ServerCert `
    -days $ValidityDays `
    -sha256 `
    -extfile $configFile `
    -extensions v3_req 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Certificate signed" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] Failed to sign certificate" -ForegroundColor Red
    exit 1
}

# Clean up temporary files
Remove-Item "..\..\certs\server.csr" -Force -ErrorAction SilentlyContinue
Remove-Item $configFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Certificate Renewed!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Display new certificate info
Write-Host "New certificate details:" -ForegroundColor Cyan
openssl x509 -in $ServerCert -noout -subject -issuer -dates
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
