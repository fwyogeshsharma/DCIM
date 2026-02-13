@echo off
REM mTLS Certificate Generation - Windows Batch Wrapper
REM Executes the PowerShell script

setlocal

set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%generate-certs.ps1

if not exist "%PS_SCRIPT%" (
    echo ERROR: PowerShell script not found: %PS_SCRIPT%
    exit /b 1
)

echo Running Certificate Generation Script...
echo.

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*

exit /b %ERRORLEVEL%
