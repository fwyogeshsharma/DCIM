@echo off
echo ========================================
echo DCIM Aggregator Quick Start
echo ========================================
echo.

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker is not running. Please start Docker Desktop.
    pause
    exit /b 1
)

echo [1/5] Starting PostgreSQL and Redis...
docker-compose up -d postgres redis
timeout /t 10 /nobreak >nul

echo.
echo [2/5] Checking database connection...
docker-compose exec postgres pg_isready -U dcim
if errorlevel 1 (
    echo [ERROR] PostgreSQL is not ready. Waiting longer...
    timeout /t 10 /nobreak >nul
)

echo.
echo [3/5] Running database migrations...
call npm run migrate

echo.
echo [4/5] Starting aggregator service...
start "DCIM Aggregator" cmd /k npm run dev

echo.
echo [5/5] Aggregator is starting...
timeout /t 5 /nobreak >nul

echo.
echo ========================================
echo Setup Complete!
echo ========================================
echo.
echo Aggregator API: http://localhost:3002
echo Health Check:   http://localhost:3002/health
echo.
echo PostgreSQL:     localhost:5432
echo Redis:          localhost:6379
echo.
echo Press any key to open health check in browser...
pause >nul
start http://localhost:3002/health
