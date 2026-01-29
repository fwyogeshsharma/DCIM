# Network Monitor Agent - Status Check Script
# Shows service status, recent logs, and connection health

param(
    [string]$LogPath = "C:\Users\anupa\OneDrive\Desktop\windows\agent.log"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Network Monitor Agent - Status Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check service status
Write-Host "[1] Service Status:" -ForegroundColor Yellow
$service = Get-Service -Name "NetworkMonitorAgent" -ErrorAction SilentlyContinue

if ($service) {
    $statusColor = if ($service.Status -eq 'Running') { 'Green' } else { 'Red' }
    Write-Host "  Name:         $($service.Name)" -ForegroundColor White
    Write-Host "  Display Name: $($service.DisplayName)" -ForegroundColor White
    Write-Host "  Status:       $($service.Status)" -ForegroundColor $statusColor
    Write-Host "  Start Type:   $($service.StartType)" -ForegroundColor White
} else {
    Write-Host "  ERROR: Service not found!" -ForegroundColor Red
    exit 1
}
Write-Host ""

# Check if log file exists
Write-Host "[2] Log File:" -ForegroundColor Yellow
if (Test-Path $LogPath) {
    $logFile = Get-Item $LogPath
    Write-Host "  Location:     $LogPath" -ForegroundColor White
    Write-Host "  Size:         $([math]::Round($logFile.Length / 1KB, 2)) KB" -ForegroundColor White
    Write-Host "  Last Updated: $($logFile.LastWriteTime)" -ForegroundColor White
} else {
    Write-Host "  WARNING: Log file not found at $LogPath" -ForegroundColor Yellow
    Write-Host "  Service may not have started yet or is using a different path" -ForegroundColor Yellow
}
Write-Host ""

# Analyze recent logs
Write-Host "[3] Recent Activity (Last 30 lines):" -ForegroundColor Yellow
if (Test-Path $LogPath) {
    $logLines = Get-Content $LogPath -Tail 30

    # Count message types
    $infoCount = ($logLines | Select-String -Pattern "\[INFO\]").Count
    $warnCount = ($logLines | Select-String -Pattern "\[WARN\]").Count
    $errorCount = ($logLines | Select-String -Pattern "\[ERROR\]").Count

    Write-Host "  INFO:     $infoCount messages" -ForegroundColor Green
    Write-Host "  WARNINGS: $warnCount messages" -ForegroundColor Yellow
    Write-Host "  ERRORS:   $errorCount messages" -ForegroundColor $(if ($errorCount -gt 0) { 'Red' } else { 'Green' })
    Write-Host ""

    # Show recent errors if any
    $recentErrors = $logLines | Select-String -Pattern "\[ERROR\]"
    if ($recentErrors) {
        Write-Host "  Recent Errors:" -ForegroundColor Red
        $recentErrors | Select-Object -Last 5 | ForEach-Object {
            Write-Host "    $_" -ForegroundColor Red
        }
        Write-Host ""
    }

    # Show successful sends
    $successSends = $logLines | Select-String -Pattern "Sent.*metrics successfully"
    if ($successSends) {
        Write-Host "  Recent Successful Transmissions:" -ForegroundColor Green
        $successSends | Select-Object -Last 3 | ForEach-Object {
            Write-Host "    $_" -ForegroundColor Green
        }
        Write-Host ""
    }

    # Show alerts
    $alerts = $logLines | Select-String -Pattern "Generated.*alerts"
    if ($alerts) {
        Write-Host "  Recent Alerts:" -ForegroundColor Yellow
        $alerts | Select-Object -Last 3 | ForEach-Object {
            Write-Host "    $_" -ForegroundColor Yellow
        }
        Write-Host ""
    }
}

# Connection status
Write-Host "[4] Connection Status:" -ForegroundColor Yellow
if (Test-Path $LogPath) {
    $recentLines = Get-Content $LogPath -Tail 50

    $connectionErrors = $recentLines | Select-String -Pattern "connection refused|connection reset|timeout|no such host"
    $successfulSends = $recentLines | Select-String -Pattern "Sent.*successfully"

    if ($connectionErrors -and -not $successfulSends) {
        Write-Host "  Status: OFFLINE" -ForegroundColor Red
        Write-Host "  Cannot reach monitoring server" -ForegroundColor Red
        Write-Host "  Data is being stored locally and will be sent when server is available" -ForegroundColor Yellow
    } elseif ($successfulSends) {
        $lastSuccess = $successfulSends | Select-Object -Last 1
        Write-Host "  Status: ONLINE" -ForegroundColor Green
        Write-Host "  Successfully sending data to server" -ForegroundColor Green
        Write-Host "  Last successful send:" -ForegroundColor Gray
        Write-Host "    $lastSuccess" -ForegroundColor Gray
    } else {
        Write-Host "  Status: UNKNOWN" -ForegroundColor Yellow
        Write-Host "  Not enough data yet (service may have just started)" -ForegroundColor Yellow
    }
}
Write-Host ""

# Database status
Write-Host "[5] Local Database:" -ForegroundColor Yellow
$dbPath = Join-Path (Split-Path $LogPath -Parent) "agent.db"
if (Test-Path $dbPath) {
    $dbFile = Get-Item $dbPath
    Write-Host "  Location: $dbPath" -ForegroundColor White
    Write-Host "  Size:     $([math]::Round($dbFile.Length / 1KB, 2)) KB" -ForegroundColor White
    Write-Host "  Status:   Storing data locally" -ForegroundColor Green
} else {
    Write-Host "  Database not found (service may not have started collecting yet)" -ForegroundColor Yellow
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Quick Commands:" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "View live logs:      Get-Content '$LogPath' -Wait -Tail 20" -ForegroundColor Gray
Write-Host "View all logs:       Get-Content '$LogPath'" -ForegroundColor Gray
Write-Host "Open in notepad:     notepad '$LogPath'" -ForegroundColor Gray
Write-Host "Restart service:     Restart-Service NetworkMonitorAgent" -ForegroundColor Gray
Write-Host "Stop service:        Stop-Service NetworkMonitorAgent" -ForegroundColor Gray
Write-Host "Start service:       Start-Service NetworkMonitorAgent" -ForegroundColor Gray
Write-Host ""
