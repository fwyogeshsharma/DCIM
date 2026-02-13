# Generate Additional Client Certificates
# Creates unique client certificates for additional agents
# All signed by the same CA

param(
    [Parameter(Mandatory=$true)]
    [string]$AgentName,

    [string]$OutputDir = "..\..\certs\agents",
    [int]$ValidityDays = 0  # 0 = ask user
)

$ErrorActionPreference = "Stop"

# Function to get certificate validity period
function Get-CertificateValidityDays {
    param(
        [string]$CertType = "Client/Agent Certificate",
        [int]$DefaultYears = 1
    )

    Write-Host ""
    Write-Host "Certificate Validity Period for $CertType" -ForegroundColor Cyan
    Write-Host "-------------------------------------------" -ForegroundColor Cyan
    Write-Host "Choose validity period:" -ForegroundColor Yellow
    Write-Host "  1. Days" -ForegroundColor White
    Write-Host "  2. Months" -ForegroundColor White
    Write-Host "  3. Years (default: $DefaultYears year)" -ForegroundColor White
    Write-Host ""

    $choice = Read-Host "Select option (1-3, or press Enter for default)"

    if ([string]::IsNullOrWhiteSpace($choice)) {
        $days = $DefaultYears * 365
        $displayPeriod = "$DefaultYears year"
        if ($DefaultYears -gt 1) { $displayPeriod += "s" }
    }
    else {
        switch ($choice) {
            "1" {
                $value = Read-Host "Enter number of days (1-36500)"
                if ([string]::IsNullOrWhiteSpace($value)) {
                    $days = $DefaultYears * 365
                    $displayPeriod = "$DefaultYears year"
                } else {
                    $days = [int]$value
                    if ($days -lt 1 -or $days -gt 36500) {
                        Write-Host "[WARNING] Invalid value. Using default: $DefaultYears year" -ForegroundColor Yellow
                        $days = $DefaultYears * 365
                        $displayPeriod = "$DefaultYears year"
                    } else {
                        $displayPeriod = "$days days"
                    }
                }
            }
            "2" {
                $value = Read-Host "Enter number of months (1-1200)"
                if ([string]::IsNullOrWhiteSpace($value)) {
                    $days = $DefaultYears * 365
                    $displayPeriod = "$DefaultYears year"
                } else {
                    $months = [int]$value
                    if ($months -lt 1 -or $months -gt 1200) {
                        Write-Host "[WARNING] Invalid value. Using default: $DefaultYears year" -ForegroundColor Yellow
                        $days = $DefaultYears * 365
                        $displayPeriod = "$DefaultYears year"
                    } else {
                        $days = $months * 30
                        $displayPeriod = "$months month"
                        if ($months -gt 1) { $displayPeriod += "s" }
                    }
                }
            }
            "3" {
                $value = Read-Host "Enter number of years (1-100)"
                if ([string]::IsNullOrWhiteSpace($value)) {
                    $days = $DefaultYears * 365
                    $displayPeriod = "$DefaultYears year"
                } else {
                    $years = [int]$value
                    if ($years -lt 1 -or $years -gt 100) {
                        Write-Host "[WARNING] Invalid value. Using default: $DefaultYears year" -ForegroundColor Yellow
                        $days = $DefaultYears * 365
                        $displayPeriod = "$DefaultYears year"
                    } else {
                        $days = $years * 365
                        $displayPeriod = "$years year"
                        if ($years -gt 1) { $displayPeriod += "s" }
                    }
                }
            }
            default {
                Write-Host "[WARNING] Invalid choice. Using default: $DefaultYears year" -ForegroundColor Yellow
                $days = $DefaultYears * 365
                $displayPeriod = "$DefaultYears year"
            }
        }
    }

    Write-Host "[OK] Certificate will be valid for: $displayPeriod ($days days)" -ForegroundColor Green

    return @{
        Days = $days
        DisplayPeriod = $displayPeriod
    }
}

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Generate Client Certificate" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Trim and validate agent name
$AgentName = $AgentName.Trim()
if ([string]::IsNullOrWhiteSpace($AgentName)) {
    Write-Host "Error: Agent name cannot be empty" -ForegroundColor Red
    exit 1
}

# Get script directory and set paths relative to parent (DCIM_Server root)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$CertsDir = "..\..\certs" Join-Path $rootDir "certs"

# Check if CA exists
if (-not (Test-Path "..\..\certs\ca.crt") -or -not (Test-Path "..\..\certs\ca.key")) {
    Write-Host "Error: CA certificate not found!" -ForegroundColor Red
    Write-Host "Make sure ..\..\certs\ca.crt and ..\..\certs\ca.key exist" -ForegroundColor Yellow
    Write-Host "Run .\scripts\generate-certs.ps1 first to create CA" -ForegroundColor Yellow
    exit 1
}

# Create output directory (relative to certs dir)
$OutputDir = Join-Path $certsDir "agents"
$agentDir = "$OutputDir\$AgentName"
if (-not (Test-Path $agentDir)) {
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
}

Write-Host "Generating client certificate for: $AgentName" -ForegroundColor Yellow
Write-Host "Output directory: $agentDir" -ForegroundColor Gray

# Ask for validity period if not provided
if ($ValidityDays -eq 0) {
    $validityInfo = Get-CertificateValidityDays -CertType "Client Certificate for $AgentName" -DefaultYears 1
    $ValidityDays = $validityInfo.Days
    $validityDisplay = $validityInfo.DisplayPeriod
} else {
    $validityDisplay = "$ValidityDays days"
}

Write-Host ""

# Generate private key
Write-Host "1. Generating private key..." -ForegroundColor Cyan
cmd /c "openssl genrsa -out `"$agentDir\client.key`" 2048 2>nul"

if ($LASTEXITCODE -ne 0) {
    Write-Host "   [ERROR] Failed to generate private key" -ForegroundColor Red
    exit 1
}
Write-Host "   [OK] Private key generated" -ForegroundColor Green

# Create certificate config
$config = @"
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

$configFile = "$agentDir\client.cnf"
$config | Out-File -FilePath $configFile -Encoding ASCII

# Generate CSR
Write-Host "2. Generating certificate signing request..." -ForegroundColor Cyan
cmd /c "openssl req -new -key `"$agentDir\client.key`" -out `"$agentDir\client.csr`" -config `"$configFile`" 2>nul"

if ($LASTEXITCODE -ne 0) {
    Write-Host "   [ERROR] Failed to generate CSR" -ForegroundColor Red
    exit 1
}
Write-Host "   [OK] CSR generated" -ForegroundColor Green

# Sign with CA
Write-Host "3. Signing certificate with CA (valid for $validityDisplay)..." -ForegroundColor Cyan
cmd /c "openssl x509 -req -in `"$agentDir\client.csr`" -CA `"..\..\certs\ca.crt`" -CAkey `"..\..\certs\ca.key`" -CAcreateserial -out `"$agentDir\client.crt`" -days $ValidityDays -sha256 -extfile `"$configFile`" -extensions v3_req 2>nul"

if ($LASTEXITCODE -ne 0) {
    Write-Host "   [ERROR] Failed to sign certificate" -ForegroundColor Red
    exit 1
}
Write-Host "   [OK] Certificate signed" -ForegroundColor Green

# Copy CA certificate
Copy-Item "..\..\certs\ca.crt" "$agentDir\ca.crt" -Force

# Clean up temporary files
Remove-Item "$agentDir\client.csr" -Force
Remove-Item "$agentDir\client.cnf" -Force

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Certificate Generated!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "Certificate details:" -ForegroundColor Cyan
Write-Host "  Agent Name (CN): $AgentName" -ForegroundColor White
Write-Host "  Location: $agentDir" -ForegroundColor White
Write-Host "  Validity: $validityDisplay" -ForegroundColor White

# Calculate expiry date
$expiryDate = (Get-Date).AddDays($ValidityDays).ToString("yyyy-MM-dd")
$renewalDate = (Get-Date).AddDays($ValidityDays - 30).ToString("yyyy-MM-dd")
Write-Host "  Expires: $expiryDate" -ForegroundColor White
Write-Host "  Renewal Reminder: Set for $renewalDate (30 days before expiry)" -ForegroundColor Gray
Write-Host ""
Write-Host "Files created:" -ForegroundColor Cyan
Write-Host "  $agentDir\ca.crt      - CA certificate" -ForegroundColor White
Write-Host "  $agentDir\client.crt  - Client certificate" -ForegroundColor White
Write-Host "  $agentDir\client.key  - Client private key" -ForegroundColor White
Write-Host ""
Write-Host "Copy these files to your agent machine:" -ForegroundColor Yellow
Write-Host "  scp $agentDir\* user@agent-host:/path/to/agent/certs/" -ForegroundColor Gray
Write-Host ""

# Verify certificate
Write-Host "Certificate verification:" -ForegroundColor Cyan
openssl x509 -in "$agentDir\client.crt" -noout -subject -issuer -dates

Write-Host ""
Write-Host "Done!" -ForegroundColor Green
