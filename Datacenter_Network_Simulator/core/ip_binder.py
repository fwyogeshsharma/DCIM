"""
IP Binder - Adds/removes virtual IP addresses on network adapters so SNMPSim
can listen on each device's real IP at port 161.

On Windows: uses netsh / Win32 AddIPAddress API (requires Administrator).
On Linux:   uses `ip addr add/del` (requires root / CAP_NET_ADMIN).
"""
from __future__ import annotations
import ctypes
import os
import socket as _socket
import struct as _struct
import subprocess
import sys
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
from typing import Dict, List, Tuple, Callable, Optional


# ------------------------------------------------------------------ #
#  Shared helpers                                                      #
# ------------------------------------------------------------------ #

def _mask_to_prefix(mask: str) -> int:
    """Convert a dotted-decimal subnet mask to a CIDR prefix length."""
    return sum(bin(int(x)).count('1') for x in mask.split('.'))


# ------------------------------------------------------------------ #
#  Admin check                                                         #
# ------------------------------------------------------------------ #

def is_admin() -> bool:
    """Return True when the process has the privileges needed to bind IPs."""
    if sys.platform == "win32":
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    else:
        try:
            return os.geteuid() == 0
        except AttributeError:
            return False


# ------------------------------------------------------------------ #
#  Interface enumeration                                               #
# ------------------------------------------------------------------ #

def get_interfaces() -> List[Tuple[str, str]]:
    """
    Return a list of (adapter_name, display_label) for every network adapter.
    """
    if sys.platform == "win32":
        return _get_interfaces_windows()
    return _get_interfaces_linux()


def _get_interfaces_windows() -> List[Tuple[str, str]]:
    ifaces: List[Tuple[str, str]] = []

    # --- PowerShell (preferred) ---
    try:
        ps_cmd = (
            "Get-NetAdapter | "
            "Select-Object Name,InterfaceDescription,Status | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            for line in lines[1:]:
                parts = re.split(r'","', line.strip('"'))
                if len(parts) >= 3:
                    name   = parts[0].strip('"')
                    desc   = parts[1].strip('"')
                    status = parts[2].strip('"')
                    if name:
                        label = f"{name}  —  {desc}  [{status}]"
                        ifaces.append((name, label))
    except Exception:
        pass

    if ifaces:
        return ifaces

    # --- netsh fallback ---
    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0].lower() in ("enabled", "disabled"):
                name = " ".join(parts[3:])
                ifaces.append((name, name))
    except Exception:
        pass

    return ifaces


def _get_interfaces_linux() -> List[Tuple[str, str]]:
    ifaces: List[Tuple[str, str]] = []

    try:
        result = subprocess.run(
            ["ip", "-o", "link", "show"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            m = re.match(r'\d+:\s+(\S+?)[@:]', line)
            if m:
                name = m.group(1)
                if name != "lo":
                    ifaces.append((name, name))
    except Exception:
        pass

    if not ifaces:
        # Fallback: read kernel sysfs directly
        try:
            for name in sorted(os.listdir("/sys/class/net")):
                if name != "lo":
                    ifaces.append((name, name))
        except Exception:
            pass

    return ifaces


def get_interface_ips(interface_name: str) -> List[str]:
    """Return the list of IPv4 addresses currently assigned to an interface."""
    if sys.platform == "win32":
        return _get_interface_ips_windows(interface_name)
    return _get_interface_ips_linux(interface_name)


def _get_interface_ips_windows(interface_name: str) -> List[str]:
    ips: List[str] = []
    try:
        ps_cmd = (
            f"Get-NetIPAddress -InterfaceAlias '{interface_name}' "
            "-AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                ip = line.strip()
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
                    ips.append(ip)
    except Exception:
        pass
    return ips


def _get_interface_ips_linux(interface_name: str) -> List[str]:
    ips: List[str] = []
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show", interface_name],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', line)
            if m:
                ips.append(m.group(1))
    except Exception:
        pass
    return ips


# ------------------------------------------------------------------ #
#  Add / Remove single IP                                              #
# ------------------------------------------------------------------ #

def add_ip(interface: str, ip: str, mask: str = "255.255.255.0") -> Tuple[bool, str]:
    """Add ip/mask as a secondary address on interface. Returns (success, message)."""
    if sys.platform == "win32":
        return _add_ip_windows(interface, ip, mask)
    return _add_ip_linux(interface, ip, mask)


def _add_ip_windows(interface: str, ip: str, mask: str) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "netsh", "interface", "ip", "add", "address",
                f"name={interface}",
                f"addr={ip}",
                f"mask={mask}",
            ],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode == 0 or "already" in out.lower():
            return True, f"Bound {ip}"
        return False, out or f"netsh exit {result.returncode}"
    except Exception as e:
        return False, str(e)


def _add_ip_linux(interface: str, ip: str, mask: str) -> Tuple[bool, str]:
    prefix = _mask_to_prefix(mask)
    try:
        result = subprocess.run(
            ["ip", "addr", "add", f"{ip}/{prefix}", "dev", interface],
            capture_output=True, text=True, timeout=15,
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode == 0 or "File exists" in out or "already assigned" in out.lower():
            return True, f"Bound {ip}"
        return False, out or f"ip addr add exit {result.returncode}"
    except Exception as e:
        return False, str(e)


def remove_ip(interface: str, ip: str) -> Tuple[bool, str]:
    """Remove ip from interface. Returns (success, message)."""
    if sys.platform == "win32":
        return _remove_ip_windows(interface, ip)
    return _remove_ip_linux(interface, ip)


def _remove_ip_windows(interface: str, ip: str) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "netsh", "interface", "ip", "delete", "address",
                f"name={interface}",
                f"addr={ip}",
            ],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode == 0 or "not found" in out.lower() or "element not found" in out.lower():
            return True, f"Removed {ip}"
        return False, out or f"netsh exit {result.returncode}"
    except Exception as e:
        return False, str(e)


def _remove_ip_linux(interface: str, ip: str) -> Tuple[bool, str]:
    # Look up the prefix currently assigned to this IP so `ip addr del` matches.
    prefix = "24"
    try:
        show = subprocess.run(
            ["ip", "-4", "addr", "show", interface],
            capture_output=True, text=True, timeout=10,
        )
        for line in show.stdout.splitlines():
            m = re.search(r'inet\s+' + re.escape(ip) + r'/(\d+)', line)
            if m:
                prefix = m.group(1)
                break
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["ip", "addr", "del", f"{ip}/{prefix}", "dev", interface],
            capture_output=True, text=True, timeout=15,
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, f"Removed {ip}"
        if "Cannot assign requested address" in out or "not found" in out.lower():
            return True, f"Already removed {ip}"
        return False, out or f"ip addr del exit {result.returncode}"
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------ #
#  Fast Win32 API helpers (Windows only)                              #
# ------------------------------------------------------------------ #

def _get_if_index_ctypes(interface_name: str) -> Optional[int]:
    """Get IfIndex via GetAdaptersAddresses (64-bit Windows, no subprocess).

    IP_ADAPTER_ADDRESSES 64-bit offsets:
      0  ULONG  Length
      4  DWORD  IfIndex
      8  ptr    *Next
     16  ptr    AdapterName
     24-48      address list pointers (skipped)
     56  PWCHAR DnsSuffix
     64  PWCHAR Description
     72  PWCHAR FriendlyName   ← what we match on
    """
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None  # only validated for 64-bit layout
    try:
        AF_UNSPEC = 0
        GAA_FLAGS = 0x000F  # skip unicast|anycast|multicast|dns-server
        iphlpapi  = ctypes.windll.iphlpapi

        size = ctypes.c_ulong(0)
        iphlpapi.GetAdaptersAddresses(AF_UNSPEC, GAA_FLAGS, None, None, ctypes.byref(size))
        if not size.value:
            return None

        buf = ctypes.create_string_buffer(size.value)
        if iphlpapi.GetAdaptersAddresses(AF_UNSPEC, GAA_FLAGS, None, buf, ctypes.byref(size)) != 0:
            return None

        base   = ctypes.addressof(buf)
        offset = 0
        while 0 <= offset < size.value:
            if_idx  = ctypes.c_ulong.from_buffer_copy(buf, offset + 4).value
            next_p  = ctypes.c_uint64.from_buffer_copy(buf, offset + 8).value
            fname_p = ctypes.c_uint64.from_buffer_copy(buf, offset + 72).value
            if fname_p and ctypes.wstring_at(fname_p) == interface_name:
                return if_idx
            if not next_p:
                break
            offset = next_p - base
    except Exception:
        pass
    return None


def _get_if_index(interface_name: str) -> Optional[int]:
    """Return the Windows IfIndex for a named adapter.

    Tries ctypes GetAdaptersAddresses first (fast, no subprocess), then falls
    back to a PowerShell call for edge cases (e.g. 32-bit Python or API errors).
    """
    idx = _get_if_index_ctypes(interface_name)
    if idx is not None:
        return idx
    try:
        safe = interface_name.replace("'", "''")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-NetAdapter -Name '{safe}').IfIndex"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        val = result.stdout.strip()
        if val.isdigit():
            return int(val)
    except Exception:
        pass
    return None


def _ip_dword(ip: str) -> int:
    """Convert a dotted-decimal IP string to a DWORD in network byte order
    as expected by the Win32 AddIPAddress / DeleteIPAddress APIs."""
    return _struct.unpack("<I", _socket.inet_aton(ip))[0]


def _add_ip_api(if_index: int, ip: str, mask: str) -> Tuple[bool, int, str]:
    """
    Add ip/mask to interface if_index via Windows AddIPAddress.
    Returns (success, nte_context, message).
    nte_context is needed for fast removal via DeleteIPAddress.
    """
    try:
        nte_ctx  = ctypes.c_ulong(0)
        nte_inst = ctypes.c_ulong(0)
        err = ctypes.windll.iphlpapi.AddIPAddress(
            _ip_dword(ip), _ip_dword(mask), if_index,
            ctypes.byref(nte_ctx), ctypes.byref(nte_inst),
        )
        if err == 0:
            return True, nte_ctx.value, f"Bound {ip}"
        if err in (183, 5003, 5010):    # ERROR_ALREADY_EXISTS variants
            return True, 0, f"Already bound {ip}"
        return False, 0, f"AddIPAddress error {err}"
    except Exception as exc:
        return False, 0, str(exc)


def _remove_ip_api(nte_context: int) -> Tuple[bool, str]:
    """Remove an IP address via Windows DeleteIPAddress using its NTEContext."""
    try:
        err = ctypes.windll.iphlpapi.DeleteIPAddress(ctypes.c_ulong(nte_context))
        return err == 0, ("ok" if err == 0 else f"DeleteIPAddress error {err}")
    except Exception as exc:
        return False, str(exc)


# ------------------------------------------------------------------ #
#  Parallel add / remove (public API)                                  #
# ------------------------------------------------------------------ #

def add_ips_fast(
    interface: str,
    ips: List[str],
    mask: str = "255.255.255.0",
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    workers: int = 1,
    cancelled_fn: Optional[Callable[[], bool]] = None,
) -> Tuple[List[str], Dict[str, int]]:
    """
    Bind IPs in parallel.

    On Windows: uses AddIPAddress Win32 API; returns (bound_ips, {ip: nte_context}).
    On Linux:   uses `ip addr add`; returns (bound_ips, {ip: ip}).

    Pass the returned context dict to remove_ips_fast() for fast unbind.
    """
    if sys.platform != "win32":
        total     = len(ips)
        done      = [0]
        lock      = threading.Lock()
        bound: List[str] = []

        def _bind_one(ip: str) -> Tuple[str, bool]:
            ok, msg = _add_ip_linux(interface, ip, mask)
            with lock:
                done[0] += 1
                n = done[0]
            if log_cb:
                log_cb(f"  {'OK' if ok else 'FAIL'} {ip}: {msg}",
                       "success" if ok else "error")
            if progress_cb:
                progress_cb(n, total)
            return ip, ok

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for ip in ips:
                if cancelled_fn and cancelled_fn():
                    break
                futures[pool.submit(_bind_one, ip)] = ip
            for fut in _as_completed(futures):
                ip, ok = fut.result()
                if ok:
                    bound.append(ip)

        # Return ip as its own "context" — remove_ips_fast on Linux only needs
        # the IP + interface name, so nte_context values are never read.
        return bound, {ip: ip for ip in bound}

    # ── Windows fast path via Win32 API ──────────────────────────────────
    if_index = _get_if_index(interface)
    if if_index is None:
        if log_cb:
            log_cb(f"Adapter '{interface}' not found via Win32 — falling back to netsh", "warning")
        bound_ips = _run_netsh_batch_tracked("add", interface, ips, mask, log_cb, progress_cb)
        return bound_ips, {}

    total     = len(ips)
    done      = [0]
    lock      = threading.Lock()
    bound: List[str]         = []
    contexts: Dict[str, int] = {}

    def _bind_one_win(ip: str) -> Tuple[str, bool, int]:
        ok, ctx, msg = _add_ip_api(if_index, ip, mask)
        with lock:
            done[0] += 1
            n = done[0]
        if log_cb:
            log_cb(f"  {'OK' if ok else 'FAIL'} {ip}: {msg}",
                   "success" if ok else "error")
        if progress_cb:
            progress_cb(n, total)
        return ip, ok, ctx

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for ip in ips:
            if cancelled_fn and cancelled_fn():
                break
            futures[pool.submit(_bind_one_win, ip)] = ip
        for fut in _as_completed(futures):
            ip, ok, ctx = fut.result()
            if ok:
                bound.append(ip)
                if ctx:
                    contexts[ip] = ctx

    return bound, contexts


def remove_ips_fast(
    interface: str,
    ips: List[str],
    nte_contexts: Optional[Dict[str, int]] = None,
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    workers: int = 4,
) -> Tuple[int, int]:
    """
    Remove IPs in parallel.

    On Windows: uses DeleteIPAddress for IPs with a stored NTEContext; netsh fallback otherwise.
    On Linux:   uses `ip addr del`; nte_contexts is ignored.
    """
    if not ips:
        return 0, 0

    if sys.platform != "win32":
        total     = len(ips)
        done      = [0]
        lock      = threading.Lock()

        def _remove_one(ip: str) -> bool:
            ok, msg = _remove_ip_linux(interface, ip)
            with lock:
                done[0] += 1
                n = done[0]
            if log_cb:
                log_cb(f"  {'OK' if ok else 'FAIL'} {ip}: {msg}",
                       "info" if ok else "warning")
            if progress_cb:
                progress_cb(n, total)
            return ok

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_remove_one, ips))

        ok_count = sum(results)
        return ok_count, len(results) - ok_count

    # ── Windows path ──────────────────────────────────────────────────────
    if not nte_contexts:
        return remove_ips_batch(interface, ips, log_cb, progress_cb)

    total     = len(ips)
    done      = [0]
    lock      = threading.Lock()

    def _remove_one_win(ip: str) -> bool:
        ctx = nte_contexts.get(ip, 0)
        if ctx:
            ok, msg = _remove_ip_api(ctx)
        else:
            ok, msg = _remove_ip_windows(interface, ip)
        with lock:
            done[0] += 1
            n = done[0]
        if log_cb:
            log_cb(f"  {'OK' if ok else 'FAIL'} {ip}: {msg}",
                   "info" if ok else "warning")
        if progress_cb:
            progress_cb(n, total)
        return ok

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_remove_one_win, ips))

    ok_count = sum(results)
    return ok_count, len(results) - ok_count


# ------------------------------------------------------------------ #
#  Batch helpers                                                       #
# ------------------------------------------------------------------ #

def add_ips_batch(
    interface: str,
    ips: List[str],
    mask: str = "255.255.255.0",
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """
    Add every IP in ips to interface.
    On Windows: uses a single PowerShell process (avoids per-subprocess overhead).
    On Linux:   delegates to parallel add_ips_fast().
    Returns (success_count, failure_count).
    """
    if not ips:
        return 0, 0
    if sys.platform == "win32":
        return _run_netsh_batch("add", interface, ips, mask, log_cb, progress_cb)
    bound, _ = add_ips_fast(interface, ips, mask, log_cb, progress_cb)
    return len(bound), len(ips) - len(bound)


def remove_ips_batch(
    interface: str,
    ips: List[str],
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """
    Remove every IP in ips from interface.
    On Windows: uses a single PowerShell process.
    On Linux:   delegates to parallel remove_ips_fast().
    Returns (success_count, failure_count).
    """
    if not ips:
        return 0, 0
    if sys.platform == "win32":
        return _run_netsh_batch("remove", interface, ips, None, log_cb, progress_cb)
    return remove_ips_fast(interface, ips, None, log_cb, progress_cb)


def _run_netsh_batch_tracked(
    action: str,
    interface: str,
    ips: List[str],
    mask: Optional[str],
    log_cb: Optional[Callable[[str, str], None]],
    progress_cb: Optional[Callable[[int, int], None]],
) -> List[str]:
    """Like _run_netsh_batch but returns the list of IPs that actually succeeded."""
    iface = interface.replace("'", "''")
    ps_lines = ["$ErrorActionPreference = 'SilentlyContinue'"]
    for ip in ips:
        if action == "add":
            cmd = f"netsh interface ip add address 'name={iface}' addr={ip} mask={mask}"
        else:
            cmd = f"netsh interface ip delete address 'name={iface}' addr={ip}"
        ps_lines += [
            f"$out = ({cmd}) 2>&1 | Out-String",
            f"if ($LASTEXITCODE -eq 0 -or $out -match 'already' -or $out -match 'not found' -or $out -match 'element not found') {{",
            f"    Write-Host 'OK:{ip}'",
            f"}} else {{",
            f"    Write-Host ('FAIL:{ip}:' + $out.Trim())",
            f"}}",
        ]
    ps_script = "\n".join(ps_lines)
    ps_file = None
    bound_ips: List[str] = []
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ps1", delete=False, encoding="utf-8") as f:
            f.write(ps_script)
            ps_file = f.name
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        processed = 0
        total = len(ips)
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("OK:"):
                ip = line[3:]
                bound_ips.append(ip)
                processed += 1
                if log_cb:
                    verb = "Bound" if action == "add" else "Removed"
                    log_cb(f"  + {ip} — {verb}", "success" if action == "add" else "info")
            elif line.startswith("FAIL:"):
                rest = line[5:]
                ip, _, msg = rest.partition(":")
                processed += 1
                if log_cb:
                    log_cb(f"  ! {ip} — {msg or 'Failed'}", "error" if action == "add" else "warning")
            if progress_cb and processed <= total:
                progress_cb(processed, total)
        proc.wait()
    except Exception as exc:
        if log_cb:
            log_cb(f"Batch {action} failed: {exc}", "error")
    finally:
        if ps_file and os.path.exists(ps_file):
            try:
                os.unlink(ps_file)
            except OSError:
                pass
    return bound_ips


def _run_netsh_batch(
    action: str,            # "add" or "remove"
    interface: str,
    ips: List[str],
    mask: Optional[str],
    log_cb: Optional[Callable[[str, str], None]],
    progress_cb: Optional[Callable[[int, int], None]],
) -> Tuple[int, int]:
    """
    Write a PowerShell script to a temp file and execute it once.
    The script emits one 'OK:<ip>' or 'FAIL:<ip>' line per IP so the
    caller can track progress from the streamed stdout.
    """
    iface = interface.replace("'", "''")   # escape single quotes for PS

    ps_lines = ["$ErrorActionPreference = 'SilentlyContinue'"]
    for ip in ips:
        if action == "add":
            cmd = f"netsh interface ip add address 'name={iface}' addr={ip} mask={mask}"
        else:
            cmd = f"netsh interface ip delete address 'name={iface}' addr={ip}"

        ps_lines += [
            f"$out = ({cmd}) 2>&1 | Out-String",
            f"if ($LASTEXITCODE -eq 0 -or $out -match 'already' -or $out -match 'not found' -or $out -match 'element not found') {{",
            f"    Write-Host 'OK:{ip}'",
            f"}} else {{",
            f"    Write-Host ('FAIL:{ip}:' + $out.Trim())",
            f"}}",
        ]

    ps_script = "\n".join(ps_lines)

    # Write to a temp .ps1 file to avoid command-line length limits
    ps_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8"
        ) as f:
            f.write(ps_script)
            ps_file = f.name

        proc = subprocess.Popen(
            [
                "powershell",
                "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", ps_file,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        ok_count = fail_count = 0
        processed = 0
        total = len(ips)
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("OK:"):
                ip = line[3:]
                ok_count += 1
                processed += 1
                if log_cb:
                    verb = "Bound" if action == "add" else "Removed"
                    level = "success" if action == "add" else "info"
                    log_cb(f"  {'+'  if action == 'add' else '-'} {ip} — {verb}", level)
            elif line.startswith("FAIL:"):
                rest = line[5:]
                ip, _, msg = rest.partition(":")
                fail_count += 1
                processed += 1
                level = "error" if action == "add" else "warning"
                if log_cb:
                    log_cb(f"  ! {ip} — {msg or 'Failed'}", level)
            if progress_cb and processed <= total:
                progress_cb(processed, total)

        proc.wait()
        return ok_count, fail_count

    except Exception as exc:
        if log_cb:
            log_cb(f"Batch {action} failed: {exc}", "error")
        return 0, len(ips)
    finally:
        if ps_file and os.path.exists(ps_file):
            try:
                os.unlink(ps_file)
            except OSError:
                pass
