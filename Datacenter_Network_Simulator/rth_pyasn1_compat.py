"""
PyInstaller runtime hook — pyasn1.compat.octets compatibility shim.

pyasn1 >= 0.5.0 removed the pyasn1.compat.octets submodule. Some versions of
snmpsim and pysnmp still reference it via `from pyasn1.compat.octets import null`.
This hook registers a minimal stub before any application code runs so the import
succeeds without modifying any installed package.
"""
import sys
import types

def _install_compat_octets():
    import pyasn1.compat as _compat_pkg

    if not hasattr(_compat_pkg, 'octets'):
        _mod = types.ModuleType('pyasn1.compat.octets')
        # `null` was a Python 2/3 compat alias for b'' (empty bytes).
        _mod.null = b''
        sys.modules['pyasn1.compat.octets'] = _mod
        _compat_pkg.octets = _mod

_install_compat_octets()