@echo off
echo ========================================
echo DCIM Aggregator - Service Installation Helper
echo ========================================
echo.
echo This script will help you install the required services.
echo Please run this script as Administrator.
echo.
pause

:menu
cls
echo ========================================
echo Choose what to install:
echo ========================================
echo.
echo 1. Install PostgreSQL 15
echo 2. Install TimescaleDB Extension
echo 3. Install Redis (Memurai)
echo 4. Install Redis (WSL2)
echo 5. Setup Database and User
echo 6. Run Migrations
echo 7. Check Installation Status
echo 8. Exit
echo.
set /p choice="Enter your choice (1-8): "

if "%choice%"=="1" goto install_postgres
if "%choice%"=="2" goto install_timescale
if "%choice%"=="3" goto install_redis_memurai
if "%choice%"=="4" goto install_redis_wsl
if "%choice%"=="5" goto setup_database
if "%choice%"=="6" goto run_migrations
if "%choice%"=="7" goto check_status
if "%choice%"=="8" goto end

goto menu

:install_postgres
cls
echo ========================================
echo Installing PostgreSQL 15
echo ========================================
echo.
echo Please follow these steps:
echo.
echo 1. Opening PostgreSQL download page...
start https://www.postgresql.org/download/windows/
echo.
echo 2. Download and run the PostgreSQL 15 installer
echo 3. Use default settings during installation
echo 4. Set a password for the 'postgres' user (remember it!)
echo 5. Install Stack Builder when prompted
echo.
pause
goto menu

:install_timescale
cls
echo ========================================
echo Installing TimescaleDB Extension
echo ========================================
echo.
echo Please follow these steps:
echo.
echo 1. Opening TimescaleDB download page...
start https://www.timescale.com/download
echo.
echo 2. Download TimescaleDB for Windows
echo 3. Run the installer
echo 4. Select your PostgreSQL 15 installation
echo 5. Complete the installation
echo.
echo NOTE: TimescaleDB is optional but recommended for better performance
echo.
pause
goto menu

:install_redis_memurai
cls
echo ========================================
echo Installing Redis (Memurai)
echo ========================================
echo.
echo Memurai is a Windows-native Redis implementation.
echo.
echo 1. Opening Memurai download page...
start https://www.memurai.com/get-memurai
echo.
echo 2. Download Memurai
echo 3. Run the installer
echo 4. Memurai will start automatically as a Windows service
echo.
pause
goto menu

:install_redis_wsl
cls
echo ========================================
echo Installing Redis via WSL2
echo ========================================
echo.
echo Installing WSL2 (Windows Subsystem for Linux)...
echo.
wsl --install
echo.
echo After WSL2 is installed and you've restarted:
echo.
echo 1. Open WSL2 (Ubuntu)
echo 2. Run: sudo apt update
echo 3. Run: sudo apt install redis-server
echo 4. Start Redis: sudo service redis-server start
echo.
pause
goto menu

:setup_database
cls
echo ========================================
echo Setting Up Database
echo ========================================
echo.
echo Checking if PostgreSQL is running...
pg_isready -h localhost -p 5432 >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PostgreSQL is not running!
    echo Please start PostgreSQL first or install it.
    pause
    goto menu
)
echo [OK] PostgreSQL is running!
echo.

echo Creating database and user...
echo.
echo Enter the PostgreSQL superuser password when prompted.
echo.

psql -U postgres -h localhost -c "CREATE DATABASE dcim_aggregator;"
psql -U postgres -h localhost -c "CREATE USER dcim WITH PASSWORD 'dcim_password';"
psql -U postgres -h localhost -c "GRANT ALL PRIVILEGES ON DATABASE dcim_aggregator TO dcim;"
psql -U postgres -h localhost -c "ALTER DATABASE dcim_aggregator OWNER TO dcim;"

echo.
echo Enabling TimescaleDB extension (if installed)...
psql -U dcim -h localhost -d dcim_aggregator -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

echo.
echo Database setup complete!
echo.
echo Database: dcim_aggregator
echo User: dcim
echo Password: dcim_password
echo.
pause
goto menu

:run_migrations
cls
echo ========================================
echo Running Database Migrations
echo ========================================
echo.

if not exist "node_modules" (
    echo Installing dependencies first...
    call npm install
    echo.
)

echo Running migrations...
call npm run migrate

echo.
if errorlevel 1 (
    echo [ERROR] Migration failed!
    echo Please check your database connection and try again.
) else (
    echo [SUCCESS] Migrations completed successfully!
)
echo.
pause
goto menu

:check_status
cls
echo ========================================
echo Installation Status Check
echo ========================================
echo.

echo Checking Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Node.js not installed
    echo        Install from: https://nodejs.org/
) else (
    for /f "tokens=*" %%i in ('node --version') do set NODE_VERSION=%%i
    echo [PASS] Node.js !NODE_VERSION!
)
echo.

echo Checking PostgreSQL...
pg_isready -h localhost -p 5432 >nul 2>&1
if errorlevel 1 (
    echo [FAIL] PostgreSQL not running on port 5432
    echo        Install or start PostgreSQL service
) else (
    echo [PASS] PostgreSQL is running
    psql --version 2>nul
)
echo.

echo Checking TimescaleDB...
psql -U dcim -h localhost -d dcim_aggregator -c "SELECT extname FROM pg_extension WHERE extname = 'timescaledb';" >nul 2>&1
if errorlevel 1 (
    echo [WARN] TimescaleDB not installed (optional)
    echo        Performance will be reduced without it
) else (
    echo [PASS] TimescaleDB extension installed
)
echo.

echo Checking Redis...
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Redis not running on port 6379
    echo        Install Memurai or Redis via WSL2
) else (
    echo [PASS] Redis is running
    for /f "tokens=*" %%i in ('redis-cli --version') do set REDIS_VERSION=%%i
    echo        !REDIS_VERSION!
)
echo.

echo Checking Database...
psql -U dcim -h localhost -d dcim_aggregator -c "SELECT COUNT(*) FROM servers;" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Database not set up correctly
    echo        Run option 5 to setup database
    echo        Then run option 6 to run migrations
) else (
    echo [PASS] Database tables exist
)
echo.

echo Checking Dependencies...
if exist "node_modules" (
    echo [PASS] Node modules installed
) else (
    echo [FAIL] Node modules not installed
    echo        Run: npm install
)
echo.

echo ========================================
echo Summary
echo ========================================
echo.
echo If all checks passed, you can run: start-local.bat
echo.
pause
goto menu

:end
echo.
echo Exiting...
exit /b 0
