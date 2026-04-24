@echo off
REM ================================================================
REM  Datacenter Network Simulator - Windows Build Script
REM ================================================================

echo.
echo ============================================
echo  Building Datacenter Network Simulator
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
    --name "Datacenter-Network-Simulator" ^
    --add-data "datasets;datasets" ^
    --add-data "topologies;topologies" ^
    --add-data "core;core" ^
    --add-data "ui;ui" ^
    --add-data "simulator;simulator" ^
    --add-data "proto;proto" ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    --hidden-import networkx ^
    --hidden-import pysnmp ^
    --hidden-import snmpsim ^
    --hidden-import snmpsim.commands ^
    --hidden-import snmpsim.commands.responder ^
    --collect-all snmpsim ^
    --hidden-import dbm ^
    --hidden-import dbm.dumb ^
    --hidden-import google.protobuf ^
    --hidden-import google.protobuf.descriptor ^
    --hidden-import google.protobuf.descriptor_pb2 ^
    --hidden-import google.protobuf.descriptor_pool ^
    --hidden-import google.protobuf.message ^
    --hidden-import google.protobuf.reflection ^
    --hidden-import google.protobuf.symbol_database ^
    --collect-all protobuf ^
    --collect-all google ^
    app/main.py

if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Build complete!
echo.
echo Output: dist\Datacenter-Network-Simulator.exe
echo.
pause
