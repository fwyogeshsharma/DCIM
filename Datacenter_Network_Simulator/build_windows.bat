@echo off
REM ================================================================
REM  Datacenter Network Simulator - Windows Build Script
REM ================================================================

echo.
echo ============================================
echo  Building Datacenter Network Simulator
echo ============================================
echo.

REM Activate the project virtual environment so the correct Python,
REM pip, and pyinstaller are used instead of any system-wide install.
if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found at .venv\
    echo Please create it first:  python -m venv .venv
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

REM Verify Python is from the venv
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found after venv activation.
    pause
    exit /b 1
)

REM Install / upgrade dependencies into the venv
echo [1/3] Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

REM Clear PyInstaller build cache so stale .pyc bytecode from old
REM package versions cannot sneak into the bundle.
echo.
echo [2/3] Cleaning previous build artifacts...
if exist "build\Datacenter-Network-Simulator" (
    rmdir /s /q "build\Datacenter-Network-Simulator"
)
if exist "dist\Datacenter-Network-Simulator.exe" (
    del /q "dist\Datacenter-Network-Simulator.exe"
)

REM Build using the spec file (keeps all settings in one place and
REM ensures rth_pyasn1_compat.py runtime hook is always included).
echo.
echo [3/3] Building Windows executable...
pyinstaller Datacenter-Network-Simulator.spec
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete!
echo  Output: dist\Datacenter-Network-Simulator.exe
echo ============================================
echo.
pause