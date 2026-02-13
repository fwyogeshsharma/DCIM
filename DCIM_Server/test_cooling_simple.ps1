# Simple test for Cooling Metrics API

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
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls11 -bor [System.Net.SecurityProtocolType]::Tls

$testPayloadPath = "C:\Anupam\Faber\Projects\DCIM\DCIM_Server\test_payloads\new_structure_normal.json"
$url = "https://localhost:8443/api/v1/cooling-metrics"
$agentId = "Aman-PC-UI"

Write-Host "=== Testing Cooling Metrics API ===" -ForegroundColor Cyan
Write-Host "URL: $url"
Write-Host "Agent ID: $agentId"
Write-Host "Payload: $testPayloadPath"
Write-Host ""

$body = Get-Content $testPayloadPath -Raw

try {
    $response = Invoke-RestMethod `
        -Uri $url `
        -Method POST `
        -Body $body `
        -ContentType "application/json" `
        -Headers @{
            "X-Agent-ID" = $agentId
        }

    Write-Host "✓ SUCCESS!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Response:" -ForegroundColor Yellow
    $response | ConvertTo-Json -Depth 10
}
catch {
    Write-Host "✗ FAILED!" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red

    if ($_.ErrorDetails.Message) {
        Write-Host ""
        Write-Host "Server Response:" -ForegroundColor Yellow
        try {
            $errorObj = $_.ErrorDetails.Message | ConvertFrom-Json
            $errorObj | ConvertTo-Json -Depth 10
        }
        catch {
            Write-Host $_.ErrorDetails.Message
        }
    }
}
