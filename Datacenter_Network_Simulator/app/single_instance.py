"""
Single-instance guard.

The simulator binds fixed protocol ports that are singletons on a host —
SNMP/161, BACnet/47808 (0xBAC0), gNMI, sFlow, Redfish. A second copy of the
app therefore cannot bind those ports and produces confusing "port in use" /
WinError 10013 errors partway through startup (one instance wins each port,
the other fails). Rather than fail late and per-protocol, refuse to start a
second instance up front.

Mechanism:
  • Windows  → a named kernel mutex (CreateMutexW). The OS releases it
    automatically when the process dies, so there is no stale-lock problem.
  • POSIX    → an flock() on a lock file in the temp dir; the advisory lock
    is dropped when the process exits or the fd closes.

acquire() returns a handle object to keep alive for the process lifetime, or
None if another instance already holds the lock. The handle is intentionally
stashed in a module global by hold() so it is never garbage-collected (which
would release the lock) for the life of the process.
"""
from __future__ import annotations

import os
import sys

_MUTEX_NAME = "Global\\DatacenterNetworkSimulator_SingleInstance"
_LOCK_FILE  = "datacenter_network_simulator.lock"

# Kept alive for the whole process so the lock is never released early.
_held = None


class _Handle:
    """Opaque owner of the OS lock; keeps the underlying resource referenced."""
    def __init__(self, resource):
        self._resource = resource


def acquire():
    """
    Try to become the single running instance.

    Returns a handle (truthy) if this process now owns the lock, or None if
    another instance already holds it.
    """
    if sys.platform == "win32":
        return _acquire_windows()
    return _acquire_posix()


def _acquire_windows():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183

    kernel32.CreateMutexW.restype  = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]

    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_error = kernel32.GetLastError()

    if not handle:
        # Could not create the mutex at all — fail open (allow start) rather
        # than block the app over a lock we cannot evaluate.
        return _Handle(None)

    if last_error == ERROR_ALREADY_EXISTS:
        # Another instance owns it. Close our (non-owning) handle and report.
        kernel32.CloseHandle(handle)
        return None

    return _Handle(handle)


def _acquire_posix():
    import tempfile
    path = os.path.join(tempfile.gettempdir(), _LOCK_FILE)
    try:
        import fcntl
        fd = open(path, "w")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fd.close()
            return None
        fd.write(str(os.getpid()))
        fd.flush()
        return _Handle(fd)
    except Exception:
        # Lock subsystem unavailable — fail open rather than block startup.
        return _Handle(None)


def hold(handle) -> None:
    """Pin the lock handle for the process lifetime (prevents GC release)."""
    global _held
    _held = handle
