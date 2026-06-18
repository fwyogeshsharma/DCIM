@echo off
setlocal enabledelayedexpansion
REM ================================================================
REM  Datacenter Network Simulator - Quick Launch Script
REM ================================================================

echo Starting Datacenter Network Simulator...
echo.

cd /d "%~dp0"

REM ---- Pick the Python interpreter (venv if present, else system) ----
if exist .venv\Scripts\python.exe (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

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

REM ---- Set up REST API / Web UI authentication (first run creates auth.env) ----
"%PY%" bootstrap_auth.py

REM ---- Load auth.env into this process so the app sees the credentials.
REM      Values are single-quoted; the PBKDF2 hash contains '$' (literal in
REM      batch) but no single quote, so stripping all quotes is safe. eol=#
REM      skips comment lines. ----
if exist auth.env (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("auth.env") do (
        set "_val=%%B"
        set "_val=!_val:'=!"
        set "%%A=!_val!"
    )
    set "_val="
)
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

"%PY%" app/main.py

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
