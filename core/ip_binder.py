"""
IP Binder - Adds/removes virtual IP addresses on Windows network adapters
using 'netsh interface ip add/delete address' so SNMPSim can listen on each
device's real IP at port 161.

Requires the application to run as Administrator.
"""
from __future__ import annotations
import ctypes
import subprocess
import sys
import re
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
    Add every IP in *ips* to *interface*.
    Returns (success_count, failure_count).
    """
    ok_count = fail_count = 0
    total = len(ips)
    for i, ip in enumerate(ips):
        success, msg = add_ip(interface, ip, mask)
        if success:
            ok_count += 1
            if log_cb:
                log_cb(f"  + {ip} — {msg}", "success")
        else:
            fail_count += 1
            if log_cb:
                log_cb(f"  ! {ip} — {msg}", "error")
        if progress_cb:
            progress_cb(i + 1, total)
    return ok_count, fail_count


def remove_ips_batch(
    interface: str,
    ips: List[str],
    log_cb: Optional[Callable[[str, str], None]] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """
    Remove every IP in *ips* from *interface*.
    Returns (success_count, failure_count).
    """
    ok_count = fail_count = 0
    total = len(ips)
    for i, ip in enumerate(ips):
        success, msg = remove_ip(interface, ip)
        if success:
            ok_count += 1
            if log_cb:
                log_cb(f"  - {ip} — {msg}", "info")
        else:
            fail_count += 1
            if log_cb:
                log_cb(f"  ! {ip} — {msg}", "warning")
        if progress_cb:
            progress_cb(i + 1, total)
    return ok_count, fail_count
