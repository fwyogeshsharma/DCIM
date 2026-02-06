@echo off
REM Network Monitor Agent - Windows Installer
REM Run as Administrator

echo ============================================
echo Network Monitor Agent Installer
echo ============================================
echo.

REM Check for admin privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: This installer must be run as Administrator
    echo Right-click and select "Run as administrator"
    pause
    exit /b 1
)

REM Set installation directory
set INSTALL_DIR=C:\Program Files\dcim-agent
set CONFIG_FILE=%INSTALL_DIR%\config.yaml
set BINARY=%INSTALL_DIR%\dcim-agent.exe

echo Installing to: %INSTALL_DIR%
echo.

REM Create installation directory
if not exist "%INSTALL_DIR%" (
    echo Creating installation directory...
    mkdir "%INSTALL_DIR%"
)

REM Copy binary
echo Copying agent binary...
copy /Y dcim-agent.exe "%BINARY%" >nul
if %errorlevel% neq 0 (
    echo ERROR: Failed to copy binary
    pause
    exit /b 1
)

REM Copy or create config file
if exist config.yaml (
    if not exist "%CONFIG_FILE%" (
        echo Copying configuration file...
        copy /Y config.yaml "%CONFIG_FILE%" >nul
    ) else (
        echo Configuration file already exists, skipping...
    )
) else (
    echo WARNING: config.yaml not found in current directory
)

REM Install as Windows service
echo Installing Windows service...
"%BINARY%" -service install
if %errorlevel% neq 0 (
    echo ERROR: Failed to install service
    pause
    exit /b 1
)

REM Start the service
echo Starting service...
"%BINARY%" -service start
if %errorlevel% neq 0 (
    echo WARNING: Service installed but failed to start
    echo You can start it manually from Services (services.msc)
) else (
    echo Service started successfully
)

echo.
echo ============================================
echo Installation Complete!
echo ============================================
echo.
echo Agent installed to: %INSTALL_DIR%
echo Configuration: %CONFIG_FILE%
echo.
echo To manage the service:
echo   - Open Services (services.msc)
echo   - Look for "Network Monitor Agent"
echo.
echo To uninstall:
echo   - Run: %BINARY% -service stop
echo   - Run: %BINARY% -service uninstall
echo.
pause
