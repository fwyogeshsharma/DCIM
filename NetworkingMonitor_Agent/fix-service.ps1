# Network Monitor Agent - Service Fix Script
# Run as Administrator

param(
    [string]$InstallDir = "C:\Users\anupa\OneDrive\Desktop\windows"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Network Monitor Agent - Service Fix" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

Write-Host "[1/6] Checking for existing Network Monitor services..." -ForegroundColor Yellow
Write-Host ""

# Find all possible Network Monitor services
$services = Get-WmiObject Win32_Service | Where-Object {
    $_.DisplayName -like "*Network*Monitor*" -or
    $_.Name -like "*NetworkMonitor*" -or
    $_.Name -like "*network-monitor*"
}

if ($services) {
    Write-Host "Found existing service(s):" -ForegroundColor Green
    foreach ($service in $services) {
        Write-Host "  Name:        $($service.Name)" -ForegroundColor White
        Write-Host "  DisplayName: $($service.DisplayName)" -ForegroundColor White
        Write-Host "  State:       $($service.State)" -ForegroundColor White
        Write-Host "  Path:        $($service.PathName)" -ForegroundColor Gray
        Write-Host ""

        # Stop the service
        Write-Host "  Stopping service..." -ForegroundColor Cyan
        try {
            Stop-Service -Name $service.Name -Force -ErrorAction SilentlyContinue
            sc.exe stop $service.Name 2>&1 | Out-Null
            Start-Sleep -Seconds 2
            Write-Host "  Service stopped." -ForegroundColor Green
        } catch {
            Write-Host "  Warning: Could not stop service (may already be stopped)" -ForegroundColor Yellow
        }

        # Delete the service
        Write-Host "  Deleting service..." -ForegroundColor Cyan
        sc.exe delete $service.Name 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        Write-Host "  Service deleted." -ForegroundColor Green
        Write-Host ""
    }
} else {
    Write-Host "No existing Network Monitor services found." -ForegroundColor Gray
}

Write-Host ""
Write-Host "[2/6] Closing Services console (if open)..." -ForegroundColor Yellow
Get-Process | Where-Object {$_.ProcessName -eq "mmc"} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
Write-Host "Done." -ForegroundColor Green
Write-Host ""

Write-Host "[3/6] Verifying service removal..." -ForegroundColor Yellow
Start-Sleep -Seconds 2
$remainingServices = Get-WmiObject Win32_Service | Where-Object {
    $_.DisplayName -like "*Network*Monitor*" -or
    $_.Name -like "*NetworkMonitor*"
}

if ($remainingServices) {
    Write-Host "WARNING: Service still exists. Trying registry cleanup..." -ForegroundColor Yellow
    foreach ($svc in $remainingServices) {
        $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$($svc.Name)"
        if (Test-Path $regPath) {
            Remove-Item -Path $regPath -Recurse -Force -ErrorAction SilentlyContinue
            Write-Host "Removed registry entry: $regPath" -ForegroundColor Green
        }
    }
} else {
    Write-Host "All services removed successfully." -ForegroundColor Green
}
Write-Host ""

Write-Host "[4/6] Checking installation directory..." -ForegroundColor Yellow
if (-not (Test-Path $InstallDir)) {
    Write-Host "ERROR: Installation directory not found: $InstallDir" -ForegroundColor Red
    exit 1
}

$exePath = Join-Path $InstallDir "network-monitor-agent.exe"
$configPath = Join-Path $InstallDir "config.yaml"

if (-not (Test-Path $exePath)) {
    Write-Host "ERROR: Executable not found: $exePath" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $configPath)) {
    Write-Host "WARNING: Config file not found: $configPath" -ForegroundColor Yellow
    Write-Host "Service may not start without config.yaml" -ForegroundColor Yellow
} else {
    Write-Host "Found executable and config." -ForegroundColor Green
}
Write-Host ""

Write-Host "[5/6] Installing service..." -ForegroundColor Yellow
Set-Location $InstallDir

# Install the service
$output = & $exePath -service install 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Service installed successfully!" -ForegroundColor Green
} else {
    Write-Host "ERROR: Service installation failed!" -ForegroundColor Red
    Write-Host "Output: $output" -ForegroundColor Gray
    exit 1
}
Write-Host ""

Write-Host "[6/6] Starting service..." -ForegroundColor Yellow
Start-Sleep -Seconds 2

# Try to start the service
$output = & $exePath -service start 2>&1
Start-Sleep -Seconds 3

# Verify service is running
$newService = Get-Service -Name "NetworkMonitorAgent" -ErrorAction SilentlyContinue

if ($newService) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "Service Status:" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Name:        $($newService.Name)" -ForegroundColor White
    Write-Host "  DisplayName: $($newService.DisplayName)" -ForegroundColor White
    Write-Host "  Status:      $($newService.Status)" -ForegroundColor $(if ($newService.Status -eq 'Running') { 'Green' } else { 'Yellow' })
    Write-Host ""

    if ($newService.Status -eq 'Running') {
        Write-Host "SUCCESS! Service is running." -ForegroundColor Green
        Write-Host ""
        Write-Host "Service management commands:" -ForegroundColor Cyan
        Write-Host "  View status:  Get-Service NetworkMonitorAgent" -ForegroundColor Gray
        Write-Host "  Stop:         sc stop NetworkMonitorAgent" -ForegroundColor Gray
        Write-Host "  Start:        sc start NetworkMonitorAgent" -ForegroundColor Gray
        Write-Host "  Restart:      sc stop NetworkMonitorAgent; sc start NetworkMonitorAgent" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Logs location:" -ForegroundColor Cyan
        $logPath = Join-Path $InstallDir "agent.log"
        Write-Host "  $logPath" -ForegroundColor Gray
        Write-Host ""

        # Show recent log entries
        if (Test-Path $logPath) {
            Write-Host "Recent log entries:" -ForegroundColor Cyan
            Get-Content $logPath -Tail 10 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
        }
    } else {
        Write-Host "WARNING: Service installed but not running!" -ForegroundColor Yellow
        Write-Host "Status: $($newService.Status)" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Try checking the logs:" -ForegroundColor Cyan
        $logPath = Join-Path $InstallDir "agent.log"
        Write-Host "  $logPath" -ForegroundColor Gray
        Write-Host ""
        Write-Host "Or view Windows Event Viewer for errors" -ForegroundColor Cyan
    }
} else {
    Write-Host "ERROR: Service not found after installation!" -ForegroundColor Red
    Write-Host "Something went wrong. Check Windows Event Viewer for details." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Fix script completed!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
