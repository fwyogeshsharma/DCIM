"""
IP Binder - Adds/removes virtual IP addresses on Windows network adapters
using 'netsh interface ip add/delete address' so SNMPSim can listen on each
device's real IP at port 161.

Requires the application to run as Administrator.
"""
from __future__ import annotations
import ctypes
import os
import subprocess
import sys
import re
import tempfile
from typing import List, Tuple, Callable, Optional


# ------------------------------------------------------------------ #
#  Admin check                                                         #
# ------------------------------------------------------------------ #

def is_admin() -> bool:
    """Return True when the process has Windows Administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


# ------------------------------------------------------------------ #
#  Interface enumeration                                               #
# ------------------------------------------------------------------ #

def get_interfaces() -> List[Tuple[str, str]]:
    """
    Return a list of (adapter_name, display_label) for every network
    adapter Windows knows about.

    Tries PowerShell first (Win10/11), then falls back to netsh.
    """
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
            # Skip CSV header
            for line in lines[1:]:
                # Strip surrounding quotes and split on ","
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
            # Lines look like: "Enabled    Connected    Dedicated    Ethernet"
            if len(parts) >= 4 and parts[0].lower() in ("enabled", "disabled"):
                name = " ".join(parts[3:])
                ifaces.append((name, name))
    except Exception:
        pass

    return ifaces


def get_interface_ips(interface_name: str) -> List[str]:
    """Return the list of IP addresses currently assigned to an interface."""
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


# ------------------------------------------------------------------ #
#  Add / Remove single IP                                              #
# ------------------------------------------------------------------ #

def add_ip(interface: str, ip: str, mask: str = "255.255.255.0") -> Tuple[bool, str]:
    """
    Add *ip/mask* as a secondary address on *interface* using netsh.
    Returns (success, message).
    """
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
        # netsh returns 0 on success; "already exists" is also acceptable
        if result.returncode == 0 or "already" in out.lower():
            return True, f"Bound {ip}"
        return False, out or f"netsh exit {result.returncode}"
    except Exception as e:
        return False, str(e)


def remove_ip(interface: str, ip: str) -> Tuple[bool, str]:
    """
    Remove *ip* from *interface* using netsh.
    Returns (success, message).
    """
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
    Add every IP in *ips* to *interface* using a single PowerShell process.
    Avoids spawning one subprocess per IP which causes access violations on
    Windows when called from a QThread with many IPs.
    Returns (success_count, failure_count).
    """
    if not ips:
        return 0, 0
    return _run_netsh_batch("add", interface, ips, mask, log_cb, progress_cb)


def remove_ips_batch(
    interface: str,
    ips: List[str],
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """
    Remove every IP in *ips* from *interface* using a single PowerShell process.
    Returns (success_count, failure_count).
    """
    if not ips:
        return 0, 0
    return _run_netsh_batch("remove", interface, ips, None, log_cb, progress_cb)


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
