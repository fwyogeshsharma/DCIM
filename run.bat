@echo off
REM ================================================================
REM  SNMP Network Topology Simulator - Quick Launch Script
REM ================================================================

echo Starting SNMP Network Topology Simulator...
echo.

cd /d "%~dp0"

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
