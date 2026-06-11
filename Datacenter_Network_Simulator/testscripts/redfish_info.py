"""
Redfish info probe — fetch and display everything a server BMC exposes.

Point it at a running Redfish/BMC endpoint (started by the simulator) and it
walks the resource tree and prints System, Chassis (Thermal + Power), and
Manager details for that machine.

Usage:
    python testscripts/redfish_info.py <server-ip>
    python testscripts/redfish_info.py 10.1.0.20 --port 443 --user admin --pass password
    python testscripts/redfish_info.py 10.1.0.20 --scheme https --insecure

Defaults match the simulator MVP: plain HTTP, port 443, admin/password.
Pure stdlib — no external dependencies.
"""
from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import urllib.error
import urllib.request

# Windows consoles default to cp1252 and choke on the box-drawing / °/× glyphs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ── tiny ANSI helpers (auto-off when not a TTY) ──────────────────────────────
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def bold(s):  return _c("1", s)
def cyan(s):  return _c("36", s)
def green(s): return _c("32", s)
def grey(s):  return _c("90", s)
def red(s):   return _c("31", s)
def yellow(s):return _c("33", s)


class Client:
    def __init__(self, base: str, user: str, pw: str, insecure: bool):
        self.base = base.rstrip("/")
        self._auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
        self._ctx = ssl._create_unverified_context() if insecure else None

    def get(self, path: str):
        """GET an absolute-or-relative Redfish path. Returns (status, obj|None)."""
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, headers={
            "Authorization": self._auth,
            "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=8, context=self._ctx) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else None
            except Exception:
                return e.code, None
        except Exception as exc:
            return 0, {"_error": str(exc)}


def kv(label: str, value, width: int = 22):
    if value is None or value == "":
        value = grey("—")
    print(f"  {label.ljust(width)} {value}")


def section(title: str):
    print()
    print(bold(cyan(f"── {title} " + "─" * max(2, 50 - len(title)))))


def members(client: Client, collection: dict) -> list[dict]:
    """Resolve every member of a Redfish collection."""
    out = []
    for m in (collection or {}).get("Members", []):
        oid = m.get("@odata.id")
        if not oid:
            continue
        st, obj = client.get(oid)
        if st == 200 and obj:
            out.append(obj)
    return out


def show_system(sys_obj: dict):
    section(f"System: {sys_obj.get('Name', '?')}")
    kv("Manufacturer",  sys_obj.get("Manufacturer"))
    kv("Model",         sys_obj.get("Model"))
    kv("Serial Number", sys_obj.get("SerialNumber"))
    kv("UUID",          sys_obj.get("UUID"))
    kv("Host Name",     sys_obj.get("HostName"))
    kv("BIOS Version",  sys_obj.get("BiosVersion"))
    ps = sys_obj.get("PowerState")
    kv("Power State",   green(ps) if ps == "On" else yellow(ps))
    status = sys_obj.get("Status", {})
    kv("Health",        status.get("Health"))

    proc = sys_obj.get("ProcessorSummary", {})
    kv("Processors",    f"{proc.get('Count', '?')} × {proc.get('Model', '?')}")
    mem = sys_obj.get("MemorySummary", {})
    kv("Total Memory",  f"{mem.get('TotalSystemMemoryGiB', '?')} GiB")

    oem = (sys_obj.get("Oem") or {}).get("Simulator") or {}
    if oem:
        cpu = oem.get("CpuUtilizationPercent")
        kv("CPU Utilization", f"{cpu}%" if cpu is not None else None)
        mu, dt, du = oem.get("MemoryUsedBytes"), oem.get("DiskTotalBytes"), oem.get("DiskUsedBytes")
        if mu is not None:
            kv("Memory Used", f"{mu / 1024**3:.1f} GiB")
        if dt:
            kv("Disk", f"{du / 1024**3:.0f} / {dt / 1024**3:.0f} GiB used")


def show_chassis(client: Client, ch: dict):
    section(f"Chassis: {ch.get('Name', '?')}")
    kv("Chassis Type", ch.get("ChassisType"))
    kv("Manufacturer", ch.get("Manufacturer"))
    kv("Model",        ch.get("Model"))
    kv("Power State",  ch.get("PowerState"))

    # Thermal
    t_ref = (ch.get("Thermal") or {}).get("@odata.id")
    if t_ref:
        st, thermal = client.get(t_ref)
        if st == 200 and thermal:
            temps = thermal.get("Temperatures", [])
            if temps:
                print(grey("  Temperatures:"))
                for t in temps:
                    reading = t.get("ReadingCelsius")
                    crit = t.get("UpperThresholdCritical")
                    hot = crit is not None and reading is not None and reading >= crit
                    val = f"{reading} °C" if reading is not None else "—"
                    val = red(val) if hot else green(val)
                    crit_s = grey(f"(crit {crit} °C)") if crit is not None else ""
                    print(f"    {str(t.get('Name', '?')).ljust(18)} {val}  {crit_s}")

    # Power
    p_ref = (ch.get("Power") or {}).get("@odata.id")
    if p_ref:
        st, power = client.get(p_ref)
        if st == 200 and power:
            for pc in power.get("PowerControl", []):
                w = pc.get("PowerConsumedWatts")
                kv("Power Consumed", f"{w} W" if w is not None else None)
                pm = pc.get("PowerMetrics", {})
                if pm:
                    kv("  Avg / Min / Max",
                       f"{pm.get('AverageConsumedWatts','?')} / "
                       f"{pm.get('MinConsumedWatts','?')} / "
                       f"{pm.get('MaxConsumedWatts','?')} W")


def show_manager(mgr: dict):
    section(f"Manager (BMC): {mgr.get('Name', '?')}")
    kv("Manager Type",     mgr.get("ManagerType"))
    kv("Manufacturer",     mgr.get("Manufacturer"))
    kv("Model",            mgr.get("Model"))
    kv("Firmware Version", mgr.get("FirmwareVersion"))
    kv("Power State",      mgr.get("PowerState"))
    kv("Health",           (mgr.get("Status") or {}).get("Health"))


def main():
    ap = argparse.ArgumentParser(description="Fetch and display all Redfish info for a server BMC.")
    ap.add_argument("ip", help="server BMC IP address")
    ap.add_argument("--port", type=int, default=443, help="BMC port (default 443)")
    ap.add_argument("--user", default="admin", help="username (default admin)")
    ap.add_argument("--pass", dest="pw", default="password", help="password (default password)")
    ap.add_argument("--scheme", choices=["http", "https"], default="http",
                    help="http (MVP default) or https")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verify (https self-signed)")
    args = ap.parse_args()

    base = f"{args.scheme}://{args.ip}:{args.port}"
    client = Client(base, args.user, args.pw, args.insecure)

    print(bold(f"Redfish probe → {base}/redfish/v1/"))

    st, root = client.get("/redfish/v1/")
    if st != 200 or not root:
        detail = (root or {}).get("_error") or f"HTTP {st}"
        print(red(f"  Cannot reach ServiceRoot: {detail}"))
        print(grey("  Is Redfish running and is this IP bound? Try --scheme https / --insecure, "
                   "or check --port."))
        sys.exit(1)

    kv("Redfish Version", root.get("RedfishVersion"))

    # Systems
    sref = (root.get("Systems") or {}).get("@odata.id")
    if sref:
        st, coll = client.get(sref)
        if st == 401:
            print(red("  401 Unauthorized — wrong --user/--pass."))
            sys.exit(1)
        for s in members(client, coll):
            show_system(s)

    # Chassis
    cref = (root.get("Chassis") or {}).get("@odata.id")
    if cref:
        _, coll = client.get(cref)
        for ch in members(client, coll):
            show_chassis(client, ch)

    # Managers
    mref = (root.get("Managers") or {}).get("@odata.id")
    if mref:
        _, coll = client.get(mref)
        for mgr in members(client, coll):
            show_manager(mgr)

    print()
    print(green("Done."))


if __name__ == "__main__":
    main()
