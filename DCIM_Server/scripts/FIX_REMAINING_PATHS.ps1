# Fix remaining corrupted paths
$ErrorActionPreference = "Continue"

Write-Host "Fixing remaining path issues..." -ForegroundColor Cyan
Write-Host ""

$fixCount = 0

# Fix common directory
Get-ChildItem "$PSScriptRoot\common\*.ps1" | ForEach-Object {
    $content = Get-Content $_.FullName -Raw
    $original = $content

    # Fix corrupted paths with control characters
    $content = $content -replace '\.\.\.\.[\x00-\x1F]*rts', '..\..\certs'

    if ($content -ne $original) {
        Set-Content $_.FullName -Value $content -NoNewline
        Write-Host "[FIXED] $($_.Name)" -ForegroundColor Green
        $fixCount++
    } else {
        Write-Host "[OK] $($_.Name)" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "Files fixed: $fixCount" -ForegroundColor $(if($fixCount -gt 0){'Green'}else{'Gray'})
Write-Host ""
