@echo off
REM ================================================================
REM  Datacenter Network Simulator - Quick Launch Script
REM ================================================================

echo Starting Datacenter Network Simulator...
echo.

cd /d "%~dp0"

REM ---- Require Administrator (needed to enable the loopback adapter and to
REM      add the simulated device IPs via AddIPAddress). Self-elevate if not. ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    exit /b
)

REM ---- Install (if missing) and enable the Microsoft KM-TEST Loopback Adapter ----
REM      The simulator binds the simulated device IPs on this adapter, so it
REM      must be present and 'Up' before the app starts. install_loopback.ps1
REM      creates the device via SetupAPI if it does not already exist.
echo Checking Microsoft KM-TEST Loopback Adapter...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_loopback.ps1"
echo.

REM ---- Build the web UI (served from webui\dist by the REST API) ----
REM Skip with: set SKIP_WEBUI_BUILD=1
if "%SKIP_WEBUI_BUILD%"=="1" goto skip_webui
if not exist webui goto skip_webui
where npm >nul 2>nul
if errorlevel 1 (
    echo WARNING: npm not found - skipping web UI build ^(serving existing webui\dist^).
    goto skip_webui
)
echo Building web UI...
pushd webui
if not exist node_modules call npm install
call npm run build
popd
:skip_webui

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe app/main.py
) else (
    python app/main.py
)

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Application exited with error code %errorlevel%
    echo.
    echo Make sure dependencies are installed:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
)
