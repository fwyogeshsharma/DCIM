# Stop any running proxy server on port 3001

Write-Host "Checking for proxy server on port 3001..." -ForegroundColor Cyan

$processes = Get-NetTCPConnection -LocalPort 3001 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique

if ($processes) {
    Write-Host "Found proxy server process(es): $($processes -join ', ')" -ForegroundColor Yellow

    foreach ($pid in $processes) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "Stopped process $pid" -ForegroundColor Green
        } catch {
            Write-Host "Could not stop process $pid - may require admin privileges" -ForegroundColor Red
        }
    }

    Start-Sleep -Seconds 1
    Write-Host "Port 3001 is now free!" -ForegroundColor Green
} else {
    Write-Host "No proxy server running on port 3001" -ForegroundColor Green
}
