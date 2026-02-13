# Fix PostgreSQL PATH - Find and add PostgreSQL bin directory to PATH

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "PostgreSQL PATH Fix" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Searching for PostgreSQL installation..." -ForegroundColor Yellow
Write-Host ""

# Common PostgreSQL installation paths
$commonPaths = @(
    "C:\Program Files\PostgreSQL\*\bin",
    "C:\Program Files (x86)\PostgreSQL\*\bin",
    "C:\PostgreSQL\*\bin",
    "$env:LOCALAPPDATA\Programs\PostgreSQL\*\bin",
    "C:\Program Files\pgAdmin 4\*\runtime",
    "$env:ProgramW6432\PostgreSQL\*\bin"
)

$psqlPaths = @()

foreach ($pathPattern in $commonPaths) {
    $found = Get-ChildItem -Path $pathPattern -ErrorAction SilentlyContinue | Where-Object { Test-Path "$($_.FullName)\psql.exe" }
    if ($found) {
        $psqlPaths += $found.FullName
    }
}

if ($psqlPaths.Count -eq 0) {
    Write-Host "[ERROR] PostgreSQL installation not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please check if PostgreSQL is installed:" -ForegroundColor Yellow
    Write-Host "  1. Check 'Add or Remove Programs' for PostgreSQL" -ForegroundColor Gray
    Write-Host "  2. Or install from: https://www.postgresql.org/download/windows/" -ForegroundColor Gray
    Write-Host "  3. Or use: winget install PostgreSQL.PostgreSQL" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

Write-Host "[OK] Found PostgreSQL installation(s):" -ForegroundColor Green
Write-Host ""

for ($i = 0; $i -lt $psqlPaths.Count; $i++) {
    Write-Host "  $($i + 1). $($psqlPaths[$i])" -ForegroundColor White

    # Show version if possible
    try {
        $version = & "$($psqlPaths[$i])\psql.exe" --version 2>&1
        Write-Host "     Version: $version" -ForegroundColor Gray
    } catch {
        Write-Host "     (Version check failed)" -ForegroundColor Gray
    }
    Write-Host ""
}

# Select path
$selectedPath = $psqlPaths[0]
if ($psqlPaths.Count -gt 1) {
    Write-Host "Multiple installations found." -ForegroundColor Yellow
    $choice = Read-Host "Select which one to use (1-$($psqlPaths.Count), or press Enter for #1)"
    if ([string]::IsNullOrWhiteSpace($choice)) {
        $choice = 1
    }
    $selectedPath = $psqlPaths[[int]$choice - 1]
}

Write-Host ""
Write-Host "Selected: $selectedPath" -ForegroundColor Cyan
Write-Host ""

# Check if already in PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -like "*$selectedPath*") {
    Write-Host "[OK] PostgreSQL is already in your PATH!" -ForegroundColor Green
    Write-Host ""
    Write-Host "However, you need to restart PowerShell for changes to take effect." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please:" -ForegroundColor Cyan
    Write-Host "  1. Close this PowerShell window" -ForegroundColor White
    Write-Host "  2. Open a NEW PowerShell window" -ForegroundColor White
    Write-Host "  3. Run: .\scripts\setup-postgres.ps1" -ForegroundColor White
    Write-Host ""
    exit 0
}

# Add to PATH
Write-Host "Do you want to add PostgreSQL to your PATH?" -ForegroundColor Yellow
Write-Host "This will make 'psql' command available in all terminals." -ForegroundColor Gray
Write-Host ""
$addPath = Read-Host "Add to PATH? [Y/n]"

if ($addPath -eq 'n' -or $addPath -eq 'N') {
    Write-Host ""
    Write-Host "[INFO] Not adding to PATH" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To use psql, run with full path:" -ForegroundColor Cyan
    Write-Host "  & '$selectedPath\psql.exe' -U postgres" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "Adding to PATH..." -ForegroundColor Yellow

try {
    # Add to User PATH
    $newPath = $currentPath + ";" + $selectedPath
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

    Write-Host "[OK] Added to PATH successfully!" -ForegroundColor Green
    Write-Host ""

    # Update current session PATH
    $env:Path += ";$selectedPath"

    Write-Host "Testing psql command..." -ForegroundColor Yellow
    $testPsql = psql --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] psql command works: $testPsql" -ForegroundColor Green
    } else {
        Write-Host "[WARNING] psql command not working in current session" -ForegroundColor Yellow
        Write-Host "You need to restart PowerShell" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "================================" -ForegroundColor Green
    Write-Host "PATH Updated Successfully!" -ForegroundColor Green
    Write-Host "================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next Steps:" -ForegroundColor Cyan
    Write-Host "  1. Close this PowerShell window" -ForegroundColor White
    Write-Host "  2. Open a NEW PowerShell window" -ForegroundColor White
    Write-Host "  3. Run: cd C:\Anupam\Faber\Projects\DCIM_Server" -ForegroundColor White
    Write-Host "  4. Run: .\scripts\setup-postgres.ps1" -ForegroundColor White
    Write-Host ""

} catch {
    Write-Host "[ERROR] Failed to update PATH: $_" -ForegroundColor Red
    Write-Host ""
    Write-Host "Manual steps to add to PATH:" -ForegroundColor Yellow
    Write-Host "  1. Open System Properties > Environment Variables" -ForegroundColor Gray
    Write-Host "  2. Edit 'Path' in User variables" -ForegroundColor Gray
    Write-Host "  3. Add: $selectedPath" -ForegroundColor Gray
    Write-Host "  4. Click OK and restart PowerShell" -ForegroundColor Gray
    Write-Host ""
    exit 1
}
