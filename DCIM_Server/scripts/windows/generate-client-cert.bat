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
    echo ERROR: Server name and Agent name are required
    echo Usage: generate-client-cert.bat ServerName AgentName [ValidityDays]
    echo.
    echo Example: generate-client-cert.bat JPR-MP-SERVER-WIN-HP-AS-01 JPR-MP-AGENT-WIN-HP-AY-01
    echo          generate-client-cert.bat JPR-MP-SERVER-WIN-HP-AS-01 JPR-MP-AGENT-WIN-HP-AY-01 730
    echo.
    exit /b 1
)

if "%2"=="" (
    echo ERROR: Agent name is required
    echo Usage: generate-client-cert.bat ServerName AgentName [ValidityDays]
    echo.
    echo Example: generate-client-cert.bat JPR-MP-SERVER-WIN-HP-AS-01 JPR-MP-AGENT-WIN-HP-AY-01
    echo.
    exit /b 1
)

echo Running Client Certificate Generation Script...
echo.

powershell -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ServerName "%1" -AgentName "%2" %3 %4 %5 %6

exit /b %ERRORLEVEL%
