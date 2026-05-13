#!/bin/bash
# Build a standalone Linux binary with PyInstaller.
set -e
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv not found. Create it first:"
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

source .venv/bin/activate
pip install -r requirements.txt

rm -rf "build/Datacenter-Network-Simulator" "dist/Datacenter-Network-Simulator"

pyinstaller Datacenter-Network-Simulator-linux.spec

echo ""
echo "Built: dist/Datacenter-Network-Simulator"
echo "Run with: sudo dist/Datacenter-Network-Simulator"
