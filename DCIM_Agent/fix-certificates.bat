@echo off
echo.
echo ================================================================================
echo   FIX CERTIFICATES - Add SAN Support
echo ================================================================================
echo.
echo This will:
echo   1. Delete old certificates (without SAN)
echo   2. Generate new certificates (with SAN support)
echo   3. Fix the "certificate relies on legacy Common Name field" error
echo.
echo.

pause

echo.
echo Step 1: Removing old certificates...
if exist certs (
    rmdir /s /q certs
    echo   Old certificates removed
) else (
    echo   No old certificates found
)
echo.

echo Step 2: Generating new certificates with SAN...
echo.
cd scripts
call powershell -ExecutionPolicy Bypass -File generate-certs.ps1
cd ..

echo.
echo ================================================================================
echo   CERTIFICATES REGENERATED!
echo ================================================================================
echo.
echo Next steps:
echo   1. Rebuild agent: go build -o dcim-agent.exe .
echo   2. Test connection with test server
echo.
echo The "x509: certificate relies on legacy Common Name field" error is now fixed!
echo.
pause
