@echo off
REM ================================================================
REM  SNMP Network Topology Simulator - Windows Build Script
REM ================================================================

echo.
echo ============================================
echo  Building SNMP Network Topology Simulator
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH.
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM Run PyInstaller
echo.
echo [2/3] Building Windows executable...
pyinstaller ^
    --onefile ^
    --windowed ^
    --name "SNMP-Topology-Simulator" ^
    --add-data "datasets;datasets" ^
    --add-data "topologies;topologies" ^
    --add-data "core;core" ^
    --add-data "ui;ui" ^
    --add-data "simulator;simulator" ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    --hidden-import networkx ^
    --hidden-import pysnmp ^
    app/main.py

if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Build complete!
echo.
echo Output: dist\SNMP-Topology-Simulator.exe
echo.
pause
