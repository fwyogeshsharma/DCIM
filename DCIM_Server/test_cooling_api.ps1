# Test Cooling Metrics API

# Bypass certificate validation for testing
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

$testPayloadPath = "C:\Anupam\Faber\Projects\DCIM\DCIM_Server\test_payloads\new_structure_normal.json"
$url = "https://localhost:8443/api/v1/cooling-metrics"
$agentId = "Aman-PC-UI"

Write-Host "Reading test payload from: $testPayloadPath" -ForegroundColor Cyan
$body = Get-Content $testPayloadPath -Raw

Write-Host "Sending POST request to: $url" -ForegroundColor Cyan
Write-Host "Agent ID: $agentId" -ForegroundColor Cyan
Write-Host ""

try {
    $response = Invoke-WebRequest `
        -Uri $url `
        -Method POST `
        -Body $body `
        -ContentType "application/json" `
        -Headers @{
            "X-Agent-ID" = $agentId
        } `
        -ErrorAction Stop

    Write-Host "SUCCESS!" -ForegroundColor Green
    Write-Host "Status Code: $($response.StatusCode)" -ForegroundColor Green
    Write-Host ""
    Write-Host "Response Body:" -ForegroundColor Yellow
    $response.Content | ConvertFrom-Json | ConvertTo-Json -Depth 10
}
catch {
    Write-Host "FAILED!" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red

    if ($_.ErrorDetails) {
        Write-Host ""
        Write-Host "Error Details:" -ForegroundColor Red
        Write-Host $_.ErrorDetails.Message
    }

    if ($_.Exception.Response) {
        Write-Host ""
        Write-Host "Response Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
        Write-Host "Response Status Description: $($_.Exception.Response.StatusDescription)" -ForegroundColor Red
    }
}
