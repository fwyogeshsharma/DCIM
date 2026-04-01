# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for SNMP Network Topology Simulator

import sys
import os
from pathlib import Path

block_cipher = None

# Data files to bundle
datas = [
    ('datasets', 'datasets'),
    ('topologies', 'topologies'),
    ('core', 'core'),
    ('ui', 'ui'),
    ('simulator', 'simulator'),
]

a = Analysis(
    ['app/main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtNetwork',
        'networkx',
        'networkx.algorithms',
        'networkx.classes',
        'pysnmp',
        'pysnmp.hlapi',
        'pyasn1',
        'pyasn1.type',
        'jinja2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SNMP-Topology-Simulator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
