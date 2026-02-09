# Test DCIM API Connection
Write-Host "Testing DCIM Server API..." -ForegroundColor Cyan

# Test 1: Check if server is responding
Write-Host "`n1. Testing server health..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "https://localhost:8443/api/v1/health" -SkipCertificateCheck -ErrorAction Stop
    Write-Host "   ✓ Server is running (Status: $($response.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "   ✗ Server health check failed: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 2: Check agents endpoint
Write-Host "`n2. Testing agents endpoint..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "https://localhost:8443/api/v1/agents" -SkipCertificateCheck -ErrorAction Stop
    if ($response) {
        Write-Host "   ✓ Agents endpoint accessible" -ForegroundColor Green
        Write-Host "   Total agents: $($response.Count)" -ForegroundColor Cyan
        if ($response.Count -gt 0) {
            Write-Host "`n   Sample agent:" -ForegroundColor Cyan
            $response[0] | Format-List agent_id, hostname, status, ip_address
        } else {
            Write-Host "   ⚠ No agents registered yet" -ForegroundColor Yellow
        }
    }
} catch {
    Write-Host "   ✗ Agents endpoint failed: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 3: Check alerts endpoint
Write-Host "`n3. Testing alerts endpoint..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "https://localhost:8443/api/v1/alerts" -SkipCertificateCheck -ErrorAction Stop
    Write-Host "   ✓ Alerts endpoint accessible" -ForegroundColor Green
    Write-Host "   Total alerts: $($response.Count)" -ForegroundColor Cyan
} catch {
    Write-Host "   ✗ Alerts endpoint failed: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 4: Test from frontend perspective (through Vite proxy)
Write-Host "`n4. Testing through Vite proxy..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:5173/api/v1/agents" -ErrorAction Stop
    Write-Host "   ✓ Vite proxy working" -ForegroundColor Green
    Write-Host "   Agents visible to frontend: $($response.Count)" -ForegroundColor Cyan
} catch {
    Write-Host "   ✗ Vite proxy test failed (Make sure dev server is running)" -ForegroundColor Red
    Write-Host "   Error: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n" -NoNewline
Write-Host "Test Complete!" -ForegroundColor Green
Write-Host "If no agents are showing, you may need to:" -ForegroundColor Yellow
Write-Host "  1. Register an agent with the server" -ForegroundColor Yellow
Write-Host "  2. Start a DCIM_Agent to send metrics" -ForegroundColor Yellow
