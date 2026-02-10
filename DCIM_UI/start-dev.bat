@echo off
echo ============================================
echo Starting DCIM UI Development Environment
echo ============================================
echo.
echo This will start:
echo   1. Proxy Server (port 3001)
echo   2. UI Dev Server (port 5173)
echo.
echo Press Ctrl+C to stop all services
echo.

cd /d "%~dp0"
npm run dev:full
