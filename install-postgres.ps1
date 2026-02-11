$ProgressPreference = 'SilentlyContinue'
$outPath = Join-Path $env:TEMP 'postgresql-17.exe'
Write-Host "Downloading PostgreSQL 17 to $outPath ..."
Invoke-WebRequest -Uri 'https://get.enterprisedb.com/postgresql/postgresql-17.7-2-windows-x64.exe' -OutFile $outPath -UseBasicParsing
Write-Host "Download complete. Installing with admin privileges..."
Start-Process -FilePath $outPath -ArgumentList '--mode unattended --superpassword admin --serverport 5432' -Verb RunAs -Wait
Write-Host "Installation finished."
if (Test-Path 'C:\Program Files\PostgreSQL\17\bin\psql.exe') {
    Write-Host "[OK] PostgreSQL 17 installed successfully!" -ForegroundColor Green
} else {
    Write-Host "[ERROR] psql.exe not found after install" -ForegroundColor Red
}
