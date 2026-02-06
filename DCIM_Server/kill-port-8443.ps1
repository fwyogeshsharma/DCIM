# Kill Process Using Port 8443
# Quick script to free up port 8443 for DCIM Server

Write-Host "Checking for processes using port 8443..." -ForegroundColor Cyan

$connections = Get-NetTCPConnection -LocalPort 8443 -ErrorAction SilentlyContinue

if ($connections) {
    foreach ($conn in $connections) {
        $processId = $conn.OwningProcess
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue

        if ($process) {
            Write-Host ""
            Write-Host "Found process using port 8443:" -ForegroundColor Yellow
            Write-Host "  PID: $processId" -ForegroundColor White
            Write-Host "  Name: $($process.ProcessName)" -ForegroundColor White
            Write-Host "  Path: $($process.Path)" -ForegroundColor Gray
            Write-Host ""

            $confirm = Read-Host "Kill this process? (y/n)"

            if ($confirm -eq 'y' -or $confirm -eq 'Y') {
                try {
                    Stop-Process -Id $processId -Force
                    Write-Host "✓ Process killed successfully!" -ForegroundColor Green
                } catch {
                    Write-Host "✗ Failed to kill process: $_" -ForegroundColor Red
                    Write-Host ""
                    Write-Host "Try running as Administrator:" -ForegroundColor Yellow
                    Write-Host "  Right-click PowerShell → Run as Administrator" -ForegroundColor Gray
                }
            } else {
                Write-Host "Skipped." -ForegroundColor Gray
            }
        }
    }
} else {
    Write-Host "✓ Port 8443 is free!" -ForegroundColor Green
}

Write-Host ""
