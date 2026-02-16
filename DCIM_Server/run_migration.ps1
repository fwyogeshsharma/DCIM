# ⚠️ DEPRECATED: This manual migration script is no longer needed!
#
# DCIM Server now includes AUTOMATIC MIGRATIONS that run on startup.
# See MIGRATIONS.md for details.
#
# Database Migration Script for DCIM Server (OLD WAY)
# Adds server tracking, alert deduplication, and alert resolution features

Write-Host ""
Write-Host "⚠️  WARNING: AUTOMATIC MIGRATIONS NOW AVAILABLE!" -ForegroundColor Yellow
Write-Host "=" * 60 -ForegroundColor Yellow
Write-Host ""
Write-Host "DCIM Server now runs migrations automatically on startup." -ForegroundColor White
Write-Host "You don't need this script anymore!" -ForegroundColor White
Write-Host ""
Write-Host "To use automatic migrations:" -ForegroundColor Cyan
Write-Host "  1. Just run: .\dcim-server.exe" -ForegroundColor White
Write-Host "  2. Migrations run automatically from migrations/ folder" -ForegroundColor White
Write-Host "  3. See MIGRATIONS.md for details" -ForegroundColor White
Write-Host ""
Write-Host "=" * 60 -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to cancel, or any key to continue with manual migration..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Write-Host ""

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "DCIM Database Migration (MANUAL - OLD WAY)" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host ""

# Database connection details from config.yaml
$DB_HOST = "localhost"
$DB_PORT = "5432"
$DB_USER = "postgres"
$DB_NAME = "dcim_db"
$DB_PASSWORD = "postgres"

Write-Host "Database: $DB_NAME @ $DB_HOST:$DB_PORT" -ForegroundColor Yellow
Write-Host ""

# Set password environment variable for psql
$env:PGPASSWORD = $DB_PASSWORD

# Check if psql is available
$psqlPath = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psqlPath) {
    Write-Host "ERROR: psql command not found!" -ForegroundColor Red
    Write-Host "Please install PostgreSQL client tools." -ForegroundColor Red
    Write-Host ""
    Write-Host "Download from: https://www.postgresql.org/download/windows/" -ForegroundColor Yellow
    exit 1
}

Write-Host "Running migration..." -ForegroundColor Green

# Run the migration
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f migrate_database.sql

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=" * 60 -ForegroundColor Green
    Write-Host "Migration completed successfully!" -ForegroundColor Green
    Write-Host "=" * 60 -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "1. Restart DCIM_Server: .\dcim-server.exe" -ForegroundColor White
    Write-Host "2. Check logs for server ID: should see 'Server initialized with ID: ...'" -ForegroundColor White
    Write-Host "3. Verify data: psql -d dcim_db -c 'SELECT * FROM servers;'" -ForegroundColor White
} else {
    Write-Host ""
    Write-Host "=" * 60 -ForegroundColor Red
    Write-Host "Migration failed!" -ForegroundColor Red
    Write-Host "=" * 60 -ForegroundColor Red
    Write-Host ""
    Write-Host "Check the error messages above." -ForegroundColor Yellow
    Write-Host "If database doesn't exist, create it first:" -ForegroundColor Yellow
    Write-Host "  psql -U postgres -c 'CREATE DATABASE dcim_db;'" -ForegroundColor White
}

# Clear password from environment
Remove-Item Env:\PGPASSWORD

Write-Host ""
