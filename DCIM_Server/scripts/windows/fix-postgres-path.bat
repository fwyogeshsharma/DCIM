@echo off
REM Fix PostgreSQL PATH - Windows Batch Wrapper
REM Executes the PowerShell script

setlocal

set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%fix-postgres-path.ps1

if not exist "%PS_SCRIPT%" (
    echo ERROR: PowerShell script not found: %PS_SCRIPT%
    exit /b 1
)

echo Running PostgreSQL PATH Fix Script...
echo This script requires administrator privileges.
echo.

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*

exit /b %ERRORLEVEL%
