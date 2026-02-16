# Manual Test Script for Cooling Metrics API

# Bypass certificate validation
add-type @"
    using System.Net;
    using System.Security.Cryptography.X509Certificates;
    public class TrustAllCertsPolicy : ICertificatePolicy {
        public bool CheckValidationResult(
            ServicePoint srvPoint, X509Certificate certificate,
            WebRequest request, int certificateProblem) {
            return true;
        }
    }
"@
[System.Net.ServicePointManager]::CertificatePolicy = New-Object TrustAllCertsPolicy
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Cooling Metrics API Test" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Configuration
$url = "https://localhost:8443/api/v1/cooling-metrics"
$certDir = "C:\Anupam\Faber\Projects\DCIM\DCIM_Server\certs\agents\Aman-PC-UI"

# Menu
Write-Host "Select test to run:" -ForegroundColor Yellow
Write-Host "1. Normal Operation"
Write-Host "2. Condenser Failure"
Write-Host ""
$choice = Read-Host "Enter choice (1 or 2)"

switch ($choice) {
    "1" {
        $payloadFile = "test_payloads\new_structure_normal.json"
        Write-Host "Testing: Normal Operation" -ForegroundColor Green
    }
    "2" {
        $payloadFile = "test_payloads\new_structure_condenser_failure.json"
        Write-Host "Testing: Condenser Failure" -ForegroundColor Green
    }
    default {
        Write-Host "Invalid choice. Defaulting to Normal Operation" -ForegroundColor Yellow
        $payloadFile = "test_payloads\new_structure_normal.json"
    }
}

Write-Host ""
Write-Host "URL: $url"
Write-Host "Payload: $payloadFile"
Write-Host "Certificates: $certDir"
Write-Host ""

# Load payload
$body = Get-Content $payloadFile -Raw

# Send request
try {
    $response = Invoke-RestMethod `
        -Uri $url `
        -Method POST `
        -Body $body `
        -ContentType "application/json" `
        -Headers @{"X-Agent-ID" = "Aman-PC-UI"}

    Write-Host "SUCCESS!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Response:" -ForegroundColor Yellow
    $response | ConvertTo-Json -Depth 10
    Write-Host ""
    Write-Host "Summary:" -ForegroundColor Cyan
    Write-Host "  Agent ID: $($response.data.agent_id)"
    Write-Host "  Metrics Stored: $($response.data.metrics_stored)"
    Write-Host "  Alerts Generated: $($response.data.alerts_generated)"
    Write-Host "  Timestamp: $($response.data.timestamp)"
}
catch {
    Write-Host "FAILED!" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red

    if ($_.ErrorDetails.Message) {
        Write-Host ""
        Write-Host "Server Response:" -ForegroundColor Yellow
        Write-Host $_.ErrorDetails.Message
    }
}

Write-Host ""
Write-Host "Press any key to exit..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
