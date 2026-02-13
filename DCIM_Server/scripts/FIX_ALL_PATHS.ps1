# Comprehensive Path Fix Script
# Fixes all certificate path issues across all script directories

$ErrorActionPreference = "Continue"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Certificate Path Fix - ALL Scripts"
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$scriptsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$fixCount = 0

# Fix Windows scripts
Write-Host "Fixing Windows scripts..." -ForegroundColor Yellow
Get-ChildItem "$scriptsRoot\windows\*.ps1" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    $original = $content

    # Fix corrupted paths
    $content = $content -replace '\.\.\.\.rts', '..\..\certs'
    $content = $content -replace '"\.\./\.\./certs', '"..\..\certs'

    # Fix undefined variable references
    $content = $content -replace '\$CertsDir\\', '..\..\certs\'
    $content = $content -replace 'CertsDir =', 'CertsDir = "..\..\certs"'

    if ($content -ne $original) {
        Set-Content $_.FullName -Value $content -NoNewline
        Write-Host "  [FIXED] $($_.Name)" -ForegroundColor Green
        $fixCount++
    } else {
        Write-Host "  [OK] $($_.Name)" -ForegroundColor Gray
    }
}

# Fix Common scripts
Write-Host ""
Write-Host "Fixing Common scripts..." -ForegroundColor Yellow
Get-ChildItem "$scriptsRoot\common\*.ps1" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    $original = $content

    # Fix corrupted paths
    $content = $content -replace '\.\.\.\.rts', '..\..\certs'
    $content = $content -replace '"\.\./\.\./certs', '"..\..\certs'
    $content = $content -replace '\$CertsDir\\', '..\..\certs\'
    $content = $content -replace 'CertsDir =', 'CertsDir = "..\..\certs"'

    if ($content -ne $original) {
        Set-Content $_.FullName -Value $content -NoNewline
        Write-Host "  [FIXED] $($_.Name)" -ForegroundColor Green
        $fixCount++
    } else {
        Write-Host "  [OK] $($_.Name)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Summary" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host "  Files fixed: $fixCount" -ForegroundColor $(if($fixCount -gt 0){'Green'}else{'Gray'})
Write-Host ""

if ($fixCount -gt 0) {
    Write-Host "All path issues have been fixed!" -ForegroundColor Green
} else {
    Write-Host "No path issues found." -ForegroundColor Green
}
Write-Host ""
