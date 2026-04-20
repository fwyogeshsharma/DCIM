# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('datasets', 'datasets'), ('topologies', 'topologies'), ('core', 'core'), ('ui', 'ui'), ('simulator', 'simulator'), ('proto', 'proto')]
binaries = []
hiddenimports = ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'networkx', 'pysnmp', 'snmpsim', 'snmpsim.commands', 'snmpsim.commands.responder', 'dbm', 'dbm.dumb', 'google.protobuf', 'google.protobuf.descriptor', 'google.protobuf.descriptor_pb2', 'google.protobuf.descriptor_pool', 'google.protobuf.message', 'google.protobuf.reflection', 'google.protobuf.symbol_database']
tmp_ret = collect_all('snmpsim')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('protobuf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('google')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Datacenter-Network-Simulator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
