@echo off
echo ============================================
echo Restarting DCIM Development Environment
echo ============================================
echo.

REM Stop any existing proxy server
echo Stopping existing proxy server...
powershell -ExecutionPolicy Bypass -File "%~dp0stop-proxy.ps1"

echo.
echo Starting proxy and UI servers...
echo.

cd /d "%~dp0"
npm run dev:full
