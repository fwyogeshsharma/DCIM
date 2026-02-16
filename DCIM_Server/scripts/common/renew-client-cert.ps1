# Renew Client Certificate
# Renews a client certificate using the existing CA

param(
    [Parameter(Mandatory=$true)]
    [string]$AgentName,

    [int]$ValidityDays = 365,
    [switch]$Backup = $true,
    [string]$OutputDir = "..\..\certs\agents"
)

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Client Certificate Renewal" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$CACert = "..\..\certs\ca.crt"
$CAKey = "..\..\certs\ca.key"
$AgentDir = "$OutputDir\$AgentName"
$ClientCert = "$AgentDir\client.crt"
$ClientKey = "$AgentDir\client.key"

# Check if CA exists
if (-not (Test-Path $CACert) -or -not (Test-Path $CAKey)) {
    Write-Host "Error: CA certificate not found!" -ForegroundColor Red
    exit 1
}

# Check if agent directory exists
if (-not (Test-Path $AgentDir)) {
    Write-Host "Error: Agent directory not found: $AgentDir" -ForegroundColor Red
    Write-Host "Create new certificate with: .\generate-client-cert.ps1 -AgentName $AgentName" -ForegroundColor Yellow
    exit 1
}

# Backup existing certificates
if ($Backup -and (Test-Path $ClientCert)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    Write-Host "Backing up existing certificate..." -ForegroundColor Yellow
    Copy-Item $ClientCert "$ClientCert.$timestamp.bak" -Force
    Copy-Item $ClientKey "$ClientKey.$timestamp.bak" -Force
    Write-Host "[OK] Backup created" -ForegroundColor Green
    Write-Host ""
}

# Display current certificate info
if (Test-Path $ClientCert) {
    Write-Host "Current certificate expires:" -ForegroundColor Cyan
    openssl x509 -in $ClientCert -noout -enddate
    Write-Host ""
}

Write-Host "Renewing client certificate for: $AgentName" -ForegroundColor Yellow
Write-Host "Validity: $ValidityDays days" -ForegroundColor Gray
Write-Host ""

# Generate new private key
Write-Host "1. Generating new private key..." -ForegroundColor Cyan
openssl genrsa -out $ClientKey 2048 2>&1 | Out-Null
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
ST=State
L=City
O=Organization
OU=IT Department
CN=$AgentName

[v3_req]
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
"@

$configFile = "$AgentDir\client_renewal.cnf"
$clientConfig | Out-File -FilePath $configFile -Encoding ASCII

# Generate CSR
Write-Host "2. Generating certificate signing request..." -ForegroundColor Cyan
openssl req -new -key $ClientKey -out "$AgentDir\client.csr" -config $configFile 2>&1 | Out-Null
Write-Host "   [OK] CSR generated" -ForegroundColor Green

# Sign with CA
Write-Host "3. Signing certificate with CA..." -ForegroundColor Cyan
openssl x509 -req -in "$AgentDir\client.csr" `
    -CA $CACert `
    -CAkey $CAKey `
    -CAcreateserial `
    -out $ClientCert `
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

# Clean up
Remove-Item "$AgentDir\client.csr" -Force -ErrorAction SilentlyContinue
Remove-Item $configFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Certificate Renewed!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Display new certificate info
Write-Host "New certificate details:" -ForegroundColor Cyan
openssl x509 -in $ClientCert -noout -subject -issuer -dates
Write-Host ""

Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Copy renewed certificate to agent:" -ForegroundColor White
Write-Host "     Copy-Item $AgentDir\* \\agent-machine\path\to\certs\" -ForegroundColor Gray
Write-Host "  2. Restart the agent to use new certificate" -ForegroundColor White
Write-Host "  3. Verify connection to server" -ForegroundColor White
Write-Host ""

$newExpiry = (Get-Date).AddDays($ValidityDays).ToString("yyyy-MM-dd")
Write-Host "Certificate valid until: $newExpiry" -ForegroundColor Green
Write-Host ""
