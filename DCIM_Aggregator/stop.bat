@echo off
echo ========================================
echo Stopping DCIM Aggregator
echo ========================================
echo.

echo Stopping Docker containers...
docker-compose down

echo.
echo All services stopped.
pause
