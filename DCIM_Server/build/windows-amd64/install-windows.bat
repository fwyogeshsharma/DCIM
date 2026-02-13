@echo off
REM DCIM Server - Windows Installer
REM Run as Administrator

echo ============================================
echo DCIM Server Installer
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
set INSTALL_DIR=C:\Program Files\dcim-server
set CONFIG_FILE=%INSTALL_DIR%\config.yaml
set COOLING_CONFIG=%INSTALL_DIR%\cooling_config.yaml
set BINARY=%INSTALL_DIR%\dcim-server.exe

echo Installing to: %INSTALL_DIR%
echo.

REM Create installation directory
if not exist "%INSTALL_DIR%" (
    echo Creating installation directory...
    mkdir "%INSTALL_DIR%"
)

REM Copy binary
echo Copying server binary...
copy /Y dcim-server.exe "%BINARY%" >nul
if %errorlevel% neq 0 (
    echo ERROR: Failed to copy binary
    pause
    exit /b 1
)

REM Copy configuration files
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

if exist cooling_config.yaml (
    if not exist "%COOLING_CONFIG%" (
        echo Copying cooling configuration file...
        copy /Y cooling_config.yaml "%COOLING_CONFIG%" >nul
    ) else (
        echo Cooling configuration file already exists, skipping...
    )
)

REM Copy license file
if exist license.json (
    if not exist "%INSTALL_DIR%\license.json" (
        echo Copying license file...
        copy /Y license.json "%INSTALL_DIR%\license.json" >nul
    ) else (
        echo License file already exists, skipping...
    )
)

REM Copy migrations directory
if exist migrations (
    echo Copying database migrations...
    if not exist "%INSTALL_DIR%\migrations" mkdir "%INSTALL_DIR%\migrations"
    xcopy /Y /Q migrations\*.sql "%INSTALL_DIR%\migrations\" >nul
)

REM Copy certificates directory
if exist certs (
    echo Copying certificates...
    if not exist "%INSTALL_DIR%\certs" mkdir "%INSTALL_DIR%\certs"
    xcopy /Y /Q certs\*.* "%INSTALL_DIR%\certs\" >nul
) else (
    echo WARNING: certs directory not found
    echo You must configure certificates before starting the server
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
echo Server installed to: %INSTALL_DIR%
echo Configuration: %CONFIG_FILE%
echo.
echo To manage the service:
echo   - Open Services (services.msc)
echo   - Look for "DCIM Server"
echo.
echo To uninstall:
echo   - Run uninstall-windows.bat
echo.
pause
