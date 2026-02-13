@echo off
REM DCIM Server - Windows Uninstaller
REM Run as Administrator

echo ============================================
echo DCIM Server Uninstaller
echo ============================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This uninstaller must be run as Administrator
    echo Right-click and select "Run as administrator"
    pause
    exit /b 1
)

set INSTALL_DIR=C:\Program Files\dcim-server
set BINARY=%INSTALL_DIR%\dcim-server.exe

echo Uninstalling from: %INSTALL_DIR%
echo.

REM Stop the service
echo Stopping service...
"%BINARY%" -service stop
timeout /t 3 /nobreak >nul

REM Uninstall the service
echo Uninstalling service...
"%BINARY%" -service uninstall
if %errorlevel% neq 0 (
    echo WARNING: Failed to uninstall service
)

REM Ask about data removal
echo.
set /p REMOVE_DATA="Remove configuration, database, and certificate files? (y/n): "
if /i "%REMOVE_DATA%"=="y" (
    echo Removing all files...
    rmdir /S /Q "%INSTALL_DIR%"
) else (
    echo Removing binary only (keeping config, data, and certificates)...
    del /Q "%BINARY%"
)

echo.
echo ============================================
echo Uninstallation Complete!
echo ============================================
pause
