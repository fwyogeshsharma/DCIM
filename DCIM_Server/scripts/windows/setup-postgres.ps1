# Setup PostgreSQL for DCIM Server
# Creates database and verifies configuration

$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "PostgreSQL Setup for DCIM Server" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Configuration from config.yaml
$dbName = "dcim_db"
$dbUser = "postgres"
$dbHost = "localhost"
$dbPort = 5432

# Step 1: Check if PostgreSQL is installed
Write-Host "Step 1: Checking PostgreSQL installation..." -ForegroundColor Yellow

try {
    $version = psql --version
    Write-Host "   [OK] PostgreSQL installed: $version" -ForegroundColor Green
} catch {
    Write-Host "   [ERROR] PostgreSQL not found!" -ForegroundColor Red
    Write-Host "   Install PostgreSQL from: https://www.postgresql.org/download/windows/" -ForegroundColor Yellow
    Write-Host "   Or use: winget install PostgreSQL.PostgreSQL" -ForegroundColor Yellow
    exit 1
}

Write-Host ""

# Step 2: Check if PostgreSQL service is running
Write-Host "Step 2: Checking PostgreSQL service..." -ForegroundColor Yellow

$service = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($null -eq $service) {
    Write-Host "   [WARNING] PostgreSQL service not found" -ForegroundColor Yellow
    Write-Host "   This might be OK if using custom installation" -ForegroundColor Gray
} elseif ($service.Status -ne "Running") {
    Write-Host "   [WARNING] PostgreSQL service is $($service.Status)" -ForegroundColor Yellow
    Write-Host "   Attempting to start service..." -ForegroundColor Yellow
    try {
        Start-Service $service.Name
        Write-Host "   [OK] Service started" -ForegroundColor Green
    } catch {
        Write-Host "   [ERROR] Failed to start service: $_" -ForegroundColor Red
        Write-Host "   Start manually or check installation" -ForegroundColor Yellow
    }
} else {
    Write-Host "   [OK] PostgreSQL service is running" -ForegroundColor Green
}

Write-Host ""

# Step 3: Test connection to PostgreSQL
Write-Host "Step 3: Testing PostgreSQL connection..." -ForegroundColor Yellow
Write-Host "   Enter PostgreSQL password for user '$dbUser'" -ForegroundColor Gray

try {
    $testConnection = psql -U $dbUser -h $dbHost -p $dbPort -c "SELECT version();" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   [OK] Connected to PostgreSQL" -ForegroundColor Green
    } else {
        Write-Host "   [ERROR] Connection failed: $testConnection" -ForegroundColor Red
        Write-Host "   Check username and password in config.yaml" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "   [ERROR] Connection failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 4: Check if database exists
Write-Host "Step 4: Checking if database '$dbName' exists..." -ForegroundColor Yellow

$dbExists = psql -U $dbUser -h $dbHost -p $dbPort -lqt 2>&1 | Select-String -Pattern "^\s*$dbName\s"

if ($dbExists) {
    Write-Host "   [OK] Database '$dbName' already exists" -ForegroundColor Green

    # Ask if user wants to recreate
    Write-Host ""
    $recreate = Read-Host "Do you want to DROP and recreate the database? (This will DELETE all data) [y/N]"
    if ($recreate -eq 'y' -or $recreate -eq 'Y') {
        Write-Host "   [WARNING] Dropping database..." -ForegroundColor Yellow
        psql -U $dbUser -h $dbHost -p $dbPort -c "DROP DATABASE $dbName;" 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   [OK] Database dropped" -ForegroundColor Green
        } else {
            Write-Host "   [ERROR] Failed to drop database" -ForegroundColor Red
            exit 1
        }
        $dbExists = $false
    }
}

# Step 5: Create database if it doesn't exist
if (-not $dbExists) {
    Write-Host ""
    Write-Host "Step 5: Creating database '$dbName'..." -ForegroundColor Yellow

    $createDb = psql -U $dbUser -h $dbHost -p $dbPort -c "CREATE DATABASE $dbName;" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   [OK] Database '$dbName' created successfully" -ForegroundColor Green
    } else {
        Write-Host "   [ERROR] Failed to create database: $createDb" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""

# Step 6: Verify database connection
Write-Host "Step 6: Verifying database connection..." -ForegroundColor Yellow

$verifyDb = psql -U $dbUser -h $dbHost -p $dbPort -d $dbName -c "SELECT current_database();" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   [OK] Successfully connected to '$dbName'" -ForegroundColor Green
} else {
    Write-Host "   [ERROR] Failed to connect to database: $verifyDb" -ForegroundColor Red
    exit 1
}

Write-Host ""

# Step 7: Check config.yaml
Write-Host "Step 7: Checking config.yaml..." -ForegroundColor Yellow

if (-not (Test-Path "config.yaml")) {
    Write-Host "   [ERROR] config.yaml not found!" -ForegroundColor Red
    exit 1
}

$config = Get-Content "config.yaml" -Raw

# Check database type
if ($config -match "type:\s*`"postgres`"") {
    Write-Host "   [OK] Database type set to 'postgres'" -ForegroundColor Green
} else {
    Write-Host "   [WARNING] Database type is not 'postgres'" -ForegroundColor Yellow
    Write-Host "   Update config.yaml: database.type: 'postgres'" -ForegroundColor Yellow
}

# Check SSL mode
if ($config -match "sslmode:\s*`"require`"") {
    Write-Host "   [WARNING] SSL mode is set to 'require'" -ForegroundColor Yellow
    Write-Host "   For local development, change to 'disable' in config.yaml:" -ForegroundColor Yellow
    Write-Host "   database.postgres.sslmode: 'disable'" -ForegroundColor Gray
    Write-Host ""
    $changeSsl = Read-Host "Do you want to change sslmode to 'disable' now? [Y/n]"
    if ($changeSsl -ne 'n' -and $changeSsl -ne 'N') {
        $config = $config -replace "sslmode:\s*`"require`"", 'sslmode: "disable"'
        $config | Set-Content "config.yaml" -NoNewline
        Write-Host "   [OK] Updated sslmode to 'disable'" -ForegroundColor Green
    }
} elseif ($config -match "sslmode:\s*`"disable`"") {
    Write-Host "   [OK] SSL mode set to 'disable' (good for local dev)" -ForegroundColor Green
} else {
    Write-Host "   [INFO] SSL mode not found in config" -ForegroundColor Gray
}

Write-Host ""

# Step 8: Summary
Write-Host "================================" -ForegroundColor Green
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "Database Configuration:" -ForegroundColor Cyan
Write-Host "  Database: $dbName" -ForegroundColor White
Write-Host "  Host: $dbHost" -ForegroundColor White
Write-Host "  Port: $dbPort" -ForegroundColor White
Write-Host "  User: $dbUser" -ForegroundColor White
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "  1. Review config.yaml settings" -ForegroundColor White
Write-Host "  2. Build the server (if needed):" -ForegroundColor White
Write-Host "     .\build.ps1" -ForegroundColor Gray
Write-Host "  3. Run the DCIM Server:" -ForegroundColor White
Write-Host "     .\build\windows-amd64\dcim-server.exe" -ForegroundColor Gray
Write-Host ""
Write-Host "The server will automatically:" -ForegroundColor Yellow
Write-Host "  - Create all tables (agents, metrics, alerts, etc.)" -ForegroundColor Gray
Write-Host "  - Create indexes" -ForegroundColor Gray
Write-Host "  - Initialize the schema" -ForegroundColor Gray
Write-Host ""
Write-Host "Data Persistence:" -ForegroundColor Yellow
Write-Host "  All data will persist across server restarts" -ForegroundColor Green
Write-Host ""
Write-Host "To verify tables after server starts:" -ForegroundColor Cyan
Write-Host "  psql -U $dbUser -d $dbName -c `"\dt`"" -ForegroundColor Gray
Write-Host ""
