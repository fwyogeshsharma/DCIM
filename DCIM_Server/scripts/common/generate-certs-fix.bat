@echo off
REM Fix OpenSSL config path issue and generate certificates

echo Setting OpenSSL configuration...
set OPENSSL_CONF=

echo.
echo Running certificate generation script...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0generate-certs.ps1"

pause
