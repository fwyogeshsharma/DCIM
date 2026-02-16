# Simple test for Cooling Metrics API
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

$body = Get-Content "test_payloads\new_structure_normal.json" -Raw
$url = "https://localhost:8443/api/v1/cooling-metrics"

try {
    $response = Invoke-RestMethod -Uri $url -Method POST -Body $body -ContentType "application/json" -Headers @{"X-Agent-ID" = "Aman-PC-UI"}
    Write-Host "SUCCESS!"
    $response | ConvertTo-Json -Depth 10
}
catch {
    Write-Host "FAILED: $($_.Exception.Message)"
    if ($_.ErrorDetails.Message) {
        Write-Host "Server Response: $($_.ErrorDetails.Message)"
    }
}
