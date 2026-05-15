# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('datasets', 'datasets'), ('topologies', 'topologies'), ('core', 'core'), ('ui', 'ui'), ('simulator', 'simulator'), ('proto', 'proto'), ('api', 'api')]
binaries = []
hiddenimports = [
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets',
    'networkx', 'pysnmp', 'snmpsim', 'snmpsim.commands', 'snmpsim.commands.responder',
    'dbm', 'dbm.dumb',
    'google.protobuf', 'google.protobuf.descriptor', 'google.protobuf.descriptor_pb2',
    'google.protobuf.descriptor_pool', 'google.protobuf.message',
    'google.protobuf.reflection', 'google.protobuf.symbol_database',
    'fastapi', 'fastapi.routing', 'fastapi.middleware.cors',
    'uvicorn', 'uvicorn.main', 'uvicorn.config', 'uvicorn.lifespan.on',
    'uvicorn.protocols.http.httptools_impl', 'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.loops.asyncio',
    'starlette', 'starlette.routing', 'starlette.middleware',
    'starlette.middleware.cors', 'starlette.middleware.errors',
    'anyio', 'anyio._backends._asyncio',
    'h11', 'httptools',
    'api', 'api.main', 'api.state', 'api.routers', 'api.models',
    'api.routers.topology', 'api.routers.binding', 'api.routers.snmp',
    'api.routers.gnmi', 'api.routers.rules', 'api.routers.traps', 'api.routers.devices',
    'api.models.schemas',
]
tmp_ret = collect_all('snmpsim')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pysnmp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyasn1')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('protobuf')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('google')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('anyio')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('h11')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rth_pyasn1_compat.py'],
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
