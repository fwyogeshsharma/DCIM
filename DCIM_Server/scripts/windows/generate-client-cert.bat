@echo off
REM Generate Client Certificate - Windows Batch Wrapper
REM Executes the PowerShell script

setlocal

set SCRIPT_DIR=%~dp0
set PS_SCRIPT=%SCRIPT_DIR%generate-client-cert.ps1

if not exist "%PS_SCRIPT%" (
    echo ERROR: PowerShell script not found: %PS_SCRIPT%
    exit /b 1
)

if "%1"=="" (
    echo ERROR: Agent name is required
    echo Usage: generate-client-cert.bat AgentName
    echo Example: generate-client-cert.bat agent-02
    exit /b 1
)

echo Running Client Certificate Generation Script...
echo.

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -AgentName "%1" %2 %3 %4 %5 %6

exit /b %ERRORLEVEL%
