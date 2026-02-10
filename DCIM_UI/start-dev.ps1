# DCIM UI Development Startup Script
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Starting DCIM UI Development Environment" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will start:" -ForegroundColor Green
Write-Host "  1. Proxy Server (port 3001)" -ForegroundColor Yellow
Write-Host "  2. UI Dev Server (port 5173)" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Red
Write-Host ""

Set-Location $PSScriptRoot
npm run dev:full
