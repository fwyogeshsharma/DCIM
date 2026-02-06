# Check Certificate Expiry
# Checks all certificates for expiry and provides renewal recommendations

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Certificate Expiry Check" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

function Get-CertificateExpiry {
    param([string]$CertPath)

    if (-not (Test-Path $CertPath)) {
        return $null
    }

    # Get expiry date
    $expiryOutput = openssl x509 -in $CertPath -noout -enddate 2>&1
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    # Parse date (format: notAfter=Jan 27 06:57:44 2036 GMT)
    $dateStr = $expiryOutput -replace "notAfter=", "" -replace " GMT", ""
    $dateStr = $dateStr.Trim()

    # Try to parse the date with multiple formats
    $expiryDate = $null
    $formats = @(
        "MMM dd HH:mm:ss yyyy",   # Jan 27 06:57:44 2036
        "MMM  d HH:mm:ss yyyy"    # Jan  3 06:57:44 2036 (single digit day)
    )

    foreach ($format in $formats) {
        try {
            $expiryDate = [DateTime]::ParseExact($dateStr, $format, [System.Globalization.CultureInfo]::InvariantCulture)
            break
        } catch {
            # Try next format
        }
    }

    if ($null -eq $expiryDate) {
        Write-Host "Warning: Could not parse date '$dateStr' for $CertPath" -ForegroundColor Yellow
        return $null
    }

    # Calculate days until expiry
    $daysUntilExpiry = [Math]::Floor(($expiryDate - (Get-Date)).TotalDays)

    # Get certificate details
    $subject = openssl x509 -in $CertPath -noout -subject 2>&1
    $subject = $subject -replace "subject=", ""

    return @{
        Path = $CertPath
        Subject = $subject
        ExpiryDate = $expiryDate
        DaysUntilExpiry = $daysUntilExpiry
        IsExpired = $daysUntilExpiry -lt 0
        IsExpiringSoon = $daysUntilExpiry -le 30 -and $daysUntilExpiry -ge 0
    }
}

function Show-CertificateStatus {
    param(
        [string]$Name,
        [object]$CertInfo,
        [string]$RenewalScript
    )

    Write-Host "$Name Certificate:" -ForegroundColor Cyan
    Write-Host "  Path: $($CertInfo.Path)" -ForegroundColor Gray

    if ($CertInfo.IsExpired) {
        $daysExpired = [Math]::Abs($CertInfo.DaysUntilExpiry)
        Write-Host "  Status: EXPIRED" -ForegroundColor Red
        Write-Host "  Expired: $daysExpired days ago" -ForegroundColor Red
        Write-Host "  Expiry Date: $($CertInfo.ExpiryDate.ToString('yyyy-MM-dd'))" -ForegroundColor Gray
        Write-Host "  ACTION REQUIRED: Renew immediately!" -ForegroundColor Red
        Write-Host "  Command: $RenewalScript" -ForegroundColor Yellow
    }
    elseif ($CertInfo.IsExpiringSoon) {
        Write-Host "  Status: EXPIRING SOON" -ForegroundColor Yellow
        Write-Host "  Days Until Expiry: $($CertInfo.DaysUntilExpiry) days" -ForegroundColor Yellow
        Write-Host "  Expiry Date: $($CertInfo.ExpiryDate.ToString('yyyy-MM-dd'))" -ForegroundColor Gray
        Write-Host "  RECOMMENDED: Renew within 7 days" -ForegroundColor Yellow
        Write-Host "  Command: $RenewalScript" -ForegroundColor Yellow
    }
    else {
        Write-Host "  Status: Valid" -ForegroundColor Green
        Write-Host "  Days Until Expiry: $($CertInfo.DaysUntilExpiry) days" -ForegroundColor Green
        Write-Host "  Expiry Date: $($CertInfo.ExpiryDate.ToString('yyyy-MM-dd'))" -ForegroundColor Gray

        $renewalDate = $CertInfo.ExpiryDate.AddDays(-30).ToString('yyyy-MM-dd')
        Write-Host "  Renewal Reminder: Set for $renewalDate" -ForegroundColor Gray
    }

    Write-Host ""
}

# Check CA certificate
$caCert = Get-CertificateExpiry "certs\ca.crt"
if ($caCert) {
    Show-CertificateStatus "CA" $caCert ".\scripts\generate-certs.ps1"
}

# Check server certificate
$serverCert = Get-CertificateExpiry "certs\server.crt"
if ($serverCert) {
    Show-CertificateStatus "Server" $serverCert ".\scripts\renew-server-cert.ps1"
}

# Check client certificate
$clientCert = Get-CertificateExpiry "certs\client.crt"
if ($clientCert) {
    Show-CertificateStatus "Client" $clientCert ".\scripts\renew-client-cert.ps1 -AgentName <agent-name>"
}

# Check agent certificates
$agentCerts = @()
if (Test-Path "certs\agents") {
    $agentDirs = Get-ChildItem "certs\agents" -Directory

    if ($agentDirs.Count -gt 0) {
        Write-Host "Agent Certificates:" -ForegroundColor Cyan
        Write-Host ""

        foreach ($agentDir in $agentDirs) {
            $agentCert = Get-CertificateExpiry "$($agentDir.FullName)\client.crt"
            if ($agentCert) {
                Show-CertificateStatus "  $($agentDir.Name)" $agentCert ".\scripts\renew-client-cert.ps1 -AgentName $($agentDir.Name)"
                $agentCerts += $agentCert
            }
        }
    }
}

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Summary" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Count certificates by status (including agent certificates)
$allCerts = @()
if ($caCert) { $allCerts += $caCert }
if ($serverCert) { $allCerts += $serverCert }
if ($clientCert) { $allCerts += $clientCert }
$allCerts += $agentCerts

$expired = ($allCerts | Where-Object { $_.IsExpired }).Count
$expiringSoon = ($allCerts | Where-Object { $_.IsExpiringSoon }).Count
$valid = ($allCerts | Where-Object { -not $_.IsExpired -and -not $_.IsExpiringSoon }).Count

Write-Host "Total Certificates: $($allCerts.Count)" -ForegroundColor White
Write-Host "  Valid: $valid" -ForegroundColor Green
Write-Host "  Expiring Soon (< 30 days): $expiringSoon" -ForegroundColor Yellow
Write-Host "  Expired: $expired" -ForegroundColor Red
Write-Host ""

if ($expired -gt 0) {
    Write-Host "ACTION REQUIRED: $expired certificate(s) have expired!" -ForegroundColor Red
    exit 1
} elseif ($expiringSoon -gt 0) {
    Write-Host "WARNING: $expiringSoon certificate(s) expiring soon!" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "All certificates are valid." -ForegroundColor Green
    exit 0
}
