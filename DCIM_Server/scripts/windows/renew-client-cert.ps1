# Renew Client Certificate
# Renews a client certificate using the existing CA

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerName,

    [Parameter(Mandatory=$true)]
    [string]$AgentName,

    [int]$ValidityDays = 365,
    [switch]$Backup = $true
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Client Certificate Renewal" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Set paths
$certsDir = "..\..\certs"
$serverDir = "$certsDir\servers\$ServerName"
$agentDir = "$serverDir\agents\$AgentName"
$clientCert = "$agentDir\client.crt"
$clientKey = "$agentDir\client.key"
$caCert = "$certsDir\ca.crt"
$caKey = "$certsDir\ca.key"

# Check if CA exists
if (-not (Test-Path $caCert) -or -not (Test-Path $caKey)) {
    Write-Host "Error: CA certificate not found!" -ForegroundColor Red
    Write-Host "CA files required: $caCert, $caKey" -ForegroundColor Yellow
    exit 1
}

# Check if agent directory exists
if (-not (Test-Path $agentDir)) {
    Write-Host "Error: Agent directory not found: $agentDir" -ForegroundColor Red
    Write-Host "Create new certificate with: .\generate-client-cert.ps1 -ServerName '$ServerName' -AgentName '$AgentName'" -ForegroundColor Yellow
    exit 1
}

# Backup existing certificates
if ($Backup -and (Test-Path $clientCert)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "$agentDir\backups"

    if (-not (Test-Path $backupDir)) {
        New-Item -ItemType Directory -Path $backupDir | Out-Null
    }

    Write-Host "Backing up existing certificate..." -ForegroundColor Yellow
    Copy-Item $clientCert "$backupDir\client.crt.$timestamp" -Force
    Copy-Item $clientKey "$backupDir\client.key.$timestamp" -Force
    Write-Host "[OK] Backup created in $backupDir" -ForegroundColor Green
    Write-Host ""
}

# Display current certificate info
if (Test-Path $clientCert) {
    Write-Host "Current certificate expires:" -ForegroundColor Cyan
    openssl x509 -in $clientCert -noout -enddate
    Write-Host ""
}

Write-Host "Renewing client certificate" -ForegroundColor Yellow
Write-Host "  Server: $ServerName" -ForegroundColor Gray
Write-Host "  Agent: $AgentName" -ForegroundColor Gray
Write-Host "  Validity: $ValidityDays days" -ForegroundColor Gray
Write-Host ""

# Generate new private key
Write-Host "1. Generating new private key..." -ForegroundColor Cyan
cmd /c "openssl genrsa -out `"$clientKey`" 2048 2>nul"
Write-Host "   [OK] Private key generated" -ForegroundColor Green

# Create certificate config
$clientConfig = @"
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
CN=$AgentName

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
"@

$configFile = "$agentDir\client_renewal.cnf"
$clientConfig | Out-File -FilePath $configFile -Encoding ASCII

# Generate CSR
Write-Host "2. Generating certificate signing request..." -ForegroundColor Cyan
cmd /c "openssl req -new -key `"$clientKey`" -out `"$agentDir\client.csr`" -config `"$configFile`" 2>nul"
Write-Host "   [OK] CSR generated" -ForegroundColor Green

# Sign with CA
Write-Host "3. Signing certificate with CA..." -ForegroundColor Cyan
cmd /c "openssl x509 -req -in `"$agentDir\client.csr`" -CA `"$caCert`" -CAkey `"$caKey`" -CAcreateserial -out `"$clientCert`" -days $ValidityDays -sha256 -extfile `"$configFile`" -extensions v3_req 2>nul"

if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Certificate signed" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] Failed to sign certificate" -ForegroundColor Red
    exit 1
}

# Clean up temporary files
Remove-Item "$agentDir\client.csr" -Force -ErrorAction SilentlyContinue
Remove-Item $configFile -Force -ErrorAction SilentlyContinue
Remove-Item "$certsDir\ca.srl" -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Certificate Renewed!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Display new certificate info
Write-Host "New certificate details:" -ForegroundColor Cyan
openssl x509 -in $clientCert -noout -subject -issuer -dates
Write-Host ""

Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Copy new certificate to agent machine:" -ForegroundColor White
Write-Host "     scp $agentDir\* user@agent:/path/to/certs/" -ForegroundColor Gray
Write-Host "  2. Restart agent to use new certificate" -ForegroundColor White
Write-Host ""

# Calculate new expiry date
$newExpiry = (Get-Date).AddDays($ValidityDays).ToString("yyyy-MM-dd")
Write-Host "Certificate valid until: $newExpiry" -ForegroundColor Green
Write-Host "Set reminder to renew 30 days before: $((Get-Date).AddDays($ValidityDays - 30).ToString('yyyy-MM-dd'))" -ForegroundColor Gray
Write-Host ""
