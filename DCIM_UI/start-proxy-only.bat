@echo off
echo ============================================
echo Starting DCIM Proxy Server
echo ============================================
echo.
echo Proxy will run on: http://localhost:3001
echo Forwarding to DCIM Server: https://localhost:8443
echo.
echo Press Ctrl+C to stop
echo.

cd /d "%~dp0"
node proxy-server.js
