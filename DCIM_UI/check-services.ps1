# DCIM Services Health Check Script

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "DCIM Services Health Check" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

function Test-Port {
    param($Port, $ServiceName)

    $connection = Test-NetConnection -ComputerName localhost -Port $Port -WarningAction SilentlyContinue -InformationLevel Quiet

    if ($connection) {
        Write-Host "[OK] $ServiceName is RUNNING on port $Port" -ForegroundColor Green
        return $true
    } else {
        Write-Host "[!!] $ServiceName is NOT RUNNING on port $Port" -ForegroundColor Red
        return $false
    }
}

# Check services
$dcimServer = Test-Port -Port 8443 -ServiceName "DCIM Server    "
$proxyServer = Test-Port -Port 3001 -ServiceName "Proxy Server   "
$uiServer = Test-Port -Port 5173 -ServiceName "UI Dev Server  "

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan

# Summary
if ($dcimServer -and $proxyServer -and $uiServer) {
    Write-Host "Status: All services are running!" -ForegroundColor Green
    Write-Host ""
    Write-Host "UI: http://localhost:5173" -ForegroundColor Yellow
} else {
    Write-Host "Status: Some services are not running!" -ForegroundColor Red
    Write-Host ""
    Write-Host "To start missing services:" -ForegroundColor Yellow

    if (-not $dcimServer) {
        Write-Host "  DCIM Server: cd DCIM_Server; .\dcim-server.exe"
    }
    if (-not $proxyServer) {
        Write-Host "  Proxy Server: npm run proxy"
    }
    if (-not $uiServer) {
        Write-Host "  UI Dev Server: npm run dev"
    }
    Write-Host ""
    Write-Host "Or run all together: npm run dev:full" -ForegroundColor Cyan
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
