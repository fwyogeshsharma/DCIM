@echo off
echo ========================================
echo DCIM Aggregator - Local Setup (No Docker)
echo ========================================
echo.

REM Check if Node.js is installed
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js is not installed. Please install Node.js 20+ from https://nodejs.org/
    pause
    exit /b 1
)

REM Check if PostgreSQL is running
pg_isready -h localhost -p 5432 >nul 2>&1
if errorlevel 1 (
    echo [WARNING] PostgreSQL is not running on localhost:5432
    echo Please start PostgreSQL service or install it from:
    echo https://www.postgresql.org/download/windows/
    echo.
    echo After installation, create database:
    echo   CREATE DATABASE dcim_aggregator;
    echo   CREATE USER dcim WITH PASSWORD 'dcim_password';
    echo   GRANT ALL PRIVILEGES ON DATABASE dcim_aggregator TO dcim;
    echo.
    pause
    exit /b 1
)

REM Check if Redis is running
redis-cli ping >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Redis is not running on localhost:6379
    echo.
    echo Please install Redis from one of these options:
    echo 1. Memurai (Redis for Windows): https://www.memurai.com/
    echo 2. Redis on WSL2: wsl --install, then: sudo apt install redis-server
    echo.
    pause
    exit /b 1
)

echo [1/4] All services are running!
echo   - PostgreSQL: localhost:5432 [OK]
echo   - Redis: localhost:6379 [OK]
echo.

echo [2/4] Installing dependencies...
if not exist "node_modules" (
    call npm install
) else (
    echo Dependencies already installed.
)

echo.
echo [3/4] Running database migrations...
call npm run migrate

if errorlevel 1 (
    echo [ERROR] Migration failed. Please check your database connection.
    pause
    exit /b 1
)

echo.
echo [4/4] Starting aggregator service...
echo.
echo ========================================
echo Aggregator is running!
echo ========================================
echo.
echo API Endpoint: http://localhost:3002
echo Health Check: http://localhost:3002/health
echo.
echo Press Ctrl+C to stop the server
echo ========================================
echo.

REM Start the aggregator
call npm run dev
