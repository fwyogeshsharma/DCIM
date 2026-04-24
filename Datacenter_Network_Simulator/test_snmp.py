"""
SNMP Device Tester
==================
Tests one or more simulated devices by issuing SNMPv2c GET and WALK requests
and printing a formatted results table.

Usage examples
--------------
  # Test a single device (community = IP by default)
  python test_snmp.py 10.0.0.1

  # Test multiple devices
  python test_snmp.py 10.0.0.1 10.0.0.2 10.0.0.3

  # Custom port / community / timeout
  python test_snmp.py 10.0.0.1 --port 1161 --community public --timeout 3

  # Also walk the interface table
  python test_snmp.py 10.0.0.1 --interfaces

  # Also walk LLDP neighbours
  python test_snmp.py 10.0.0.1 --lldp

  # Full test (system + interfaces + LLDP)
  python test_snmp.py 10.0.0.1 --full

  # Quiet — only print failures
  python test_snmp.py 10.0.0.1 10.0.0.2 --quiet
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import warnings

# Silence the pysnmp-lextudio → pysnmp rename noise (emitted as RuntimeWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning, module="pysnmp")
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Colour helpers ────────────────────────────────────────────────────────────

_USE_COLOUR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def green(t):  return _c(t, "32")
def red(t):    return _c(t, "31")
def yellow(t): return _c(t, "33")
def cyan(t):   return _c(t, "36")
def bold(t):   return _c(t, "1")
def grey(t):   return _c(t, "90")


# ── OID catalogue ─────────────────────────────────────────────────────────────

SYSTEM_OIDS: List[Tuple[str, str]] = [
    ("1.3.6.1.2.1.1.1.0",  "sysDescr"),
    ("1.3.6.1.2.1.1.2.0",  "sysObjectID"),
    ("1.3.6.1.2.1.1.3.0",  "sysUpTime"),
    ("1.3.6.1.2.1.1.4.0",  "sysContact"),
    ("1.3.6.1.2.1.1.5.0",  "sysName"),
    ("1.3.6.1.2.1.1.6.0",  "sysLocation"),
    ("1.3.6.1.2.1.1.7.0",  "sysServices"),
]

IFACE_TABLE_OID   = "1.3.6.1.2.1.2.2"      # IF-MIB ifTable
LLDP_REM_OID      = "1.0.8802.1.1.2.1.4.1" # LLDP-MIB remote table


# ── Result model ──────────────────────────────────────────────────────────────

@dataclass
class OIDResult:
    oid:   str
    name:  str
    value: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.value is not None

@dataclass
class DeviceResult:
    ip:               str
    community:        str
    port:             int
    elapsed_ms:       float           = 0.0
    system:           List[OIDResult] = field(default_factory=list)
    interfaces:       List[dict]      = field(default_factory=list)
    lldp_neighbours:  List[dict]      = field(default_factory=list)
    unreachable:      bool            = False
    error:            Optional[str]   = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.system if r.ok)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.system if not r.ok)


# ── pysnmp async helpers ──────────────────────────────────────────────────────

async def _snmp_get(ip: str, community: str, port: int,
                    oids: List[str], timeout: int) -> Dict[str, str]:
    """Return {oid_str: value_str} for a list of OIDs via SNMPv2c GET."""
    from pysnmp.hlapi.asyncio import (
        getCmd, SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity,
    )
    engine = SnmpEngine()
    results = {}
    for oid in oids:
        try:
            errorIndication, errorStatus, errorIndex, varBinds = await getCmd(
                engine,
                CommunityData(community, mpModel=1),
                UdpTransportTarget((ip, port), timeout=timeout, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            if errorIndication:
                results[oid] = f"ERROR: {errorIndication}"
            elif errorStatus:
                results[oid] = f"ERROR: {errorStatus.prettyPrint()}"
            else:
                for vb in varBinds:
                    results[oid] = vb[1].prettyPrint()
        except Exception as exc:
            results[oid] = f"ERROR: {exc}"
    engine.transportDispatcher.closeDispatcher()
    return results


async def _snmp_walk(ip: str, community: str, port: int,
                     base_oid: str, timeout: int,
                     max_rows: int = 200) -> List[Tuple[str, str]]:
    """Return [(oid_str, value_str)] for a subtree walk."""
    from pysnmp.hlapi.asyncio import (
        walkCmd, SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity,
    )
    engine = SnmpEngine()
    rows = []
    try:
        async for errorIndication, errorStatus, _, varBinds in walkCmd(
            engine,
            CommunityData(community, mpModel=1),
            UdpTransportTarget((ip, port), timeout=timeout, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(base_oid)),
            lexicographicMode=False,
        ):
            if errorIndication or errorStatus:
                break
            for vb in varBinds:
                rows.append((str(vb[0]), vb[1].prettyPrint()))
            if len(rows) >= max_rows:
                break
    except Exception:
        pass
    finally:
        try:
            engine.transportDispatcher.closeDispatcher()
        except Exception:
            pass
    return rows


# ── Per-device test ───────────────────────────────────────────────────────────

async def _test_device(ip: str, community: str, port: int, timeout: int,
                       do_interfaces: bool, do_lldp: bool) -> DeviceResult:
    result = DeviceResult(ip=ip, community=community, port=port)
    t0 = time.perf_counter()

    # ── System OIDs ───────────────────────────────────────────────────────────
    oid_list = [oid for oid, _ in SYSTEM_OIDS]
    try:
        raw = await _snmp_get(ip, community, port, oid_list, timeout)
    except Exception as exc:
        result.unreachable = True
        result.error = str(exc)
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    for oid, name in SYSTEM_OIDS:
        val = raw.get(oid, "")
        if val.startswith("ERROR:"):
            result.system.append(OIDResult(oid=oid, name=name, error=val[7:].strip()))
        elif (not val or val in ("noSuchInstance", "noSuchObject", "endOfMibView")
              or val.lower().startswith("no such")):
            result.system.append(OIDResult(oid=oid, name=name,
                                            error=val or "no value"))
        else:
            result.system.append(OIDResult(oid=oid, name=name, value=val))

    # Mark unreachable if every system OID failed
    if all(not r.ok for r in result.system):
        result.unreachable = True
        errors = {r.error for r in result.system}
        result.error = next(iter(errors), "no response")
        result.elapsed_ms = (time.perf_counter() - t0) * 1000
        return result

    # ── Interface table walk ───────────────────────────────────────────────────
    # IF-MIB stores each column for all interfaces before moving to the next
    # column.  With N interfaces and ~10 useful columns we need up to N*10 rows.
    if do_interfaces:
        rows = await _snmp_walk(ip, community, port, IFACE_TABLE_OID, timeout, 2000)
        iface_map: Dict[int, dict] = {}
        for oid_str, val in rows:
            parts = oid_str.split(".")
            if len(parts) < 2:
                continue
            try:
                col = int(parts[-2])  # column
                idx = int(parts[-1])  # interface index
            except ValueError:
                continue
            iface_map.setdefault(idx, {"index": idx})
            if col == 2:   iface_map[idx]["descr"]       = val
            elif col == 5: iface_map[idx]["speed"]       = val
            elif col == 7: iface_map[idx]["admin"]       = "up" if val == "1" else "down"
            elif col == 8: iface_map[idx]["oper"]        = "up" if val == "1" else "down"
            elif col == 10:iface_map[idx]["in_octets"]   = val
            elif col == 16:iface_map[idx]["out_octets"]  = val
        result.interfaces = sorted(iface_map.values(), key=lambda x: x["index"])

    # ── LLDP walk ─────────────────────────────────────────────────────────────
    if do_lldp:
        rows = await _snmp_walk(ip, community, port, LLDP_REM_OID, timeout, 200)
        # LLDP remote table OID: 1.0.8802.1.1.2.1.4.1.1.{col}.{timemark}.{port}.{idx}
        # parts[-4]=col  parts[-3]=timemark  parts[-2]=port  parts[-1]=idx
        nbr_map: Dict[str, dict] = {}
        for oid_str, val in rows:
            parts = oid_str.split(".")
            if len(parts) < 4:
                continue
            try:
                col      = int(parts[-4])
                port_num = int(parts[-2])
                idx      = int(parts[-1])
            except ValueError:
                continue
            key = f"{port_num}.{idx}"
            nbr_map.setdefault(key, {"port": port_num, "idx": idx})
            if col == 5:   nbr_map[key]["chassis_id"] = val
            elif col == 7: nbr_map[key]["port_id"]    = val
            elif col == 9: nbr_map[key]["sys_name"]   = val
        result.lldp_neighbours = list(nbr_map.values())

    result.elapsed_ms = (time.perf_counter() - t0) * 1000
    return result


# ── Rendering ─────────────────────────────────────────────────────────────────

def _print_device(result: DeviceResult, quiet: bool) -> None:
    status = red("UNREACHABLE") if result.unreachable else (
        green("OK") if result.failed == 0 else yellow(f"{result.failed} FAILED")
    )
    print(bold(f"\n{'─'*64}"))
    print(bold(f"  {cyan(result.ip)}   community={result.community}   port={result.port}"
               f"   {status}   {grey(f'{result.elapsed_ms:.0f} ms')}"))
    print(bold(f"{'─'*64}"))

    if result.unreachable:
        print(f"  {red('✗')}  {result.error}")
        return

    # System OIDs
    col_w = max(len(r.name) for r in result.system) + 2
    for r in result.system:
        if not r.ok:
            if not quiet:
                print(f"  {red('✗')}  {r.name:<{col_w}} {red(r.error or 'no value')}")
        else:
            val = r.value
            if len(val) > 80:
                val = val[:77] + "…"
            if not quiet:
                print(f"  {green('✓')}  {r.name:<{col_w}} {val}")

    # Summary line
    total = len(result.system)
    print(f"\n  {green(str(result.passed))}/{total} system OIDs OK", end="")
    if result.failed:
        print(f"  {red(str(result.failed))} failed", end="")
    print()

    # Interfaces
    if result.interfaces:
        print(f"\n  {bold('Interfaces')} ({len(result.interfaces)} found):")
        fmt = "    {idx:>4}  {descr:<28} {admin:>5}/{oper:<5}  {speed}"
        print(grey(fmt.format(idx="idx", descr="ifDescr", admin="admin",
                              oper="oper", speed="speed")))
        for iface in result.interfaces[:20]:
            speed_raw = iface.get("speed", "")
            try:
                speed_bps = int(speed_raw)
                if speed_bps >= 1_000_000_000:
                    speed = f"{speed_bps // 1_000_000_000}G"
                elif speed_bps >= 1_000_000:
                    speed = f"{speed_bps // 1_000_000}M"
                else:
                    speed = speed_raw
            except ValueError:
                speed = speed_raw
            admin = iface.get("admin", "?")
            oper  = iface.get("oper",  "?")
            colour = green if oper == "up" else grey
            print(colour(fmt.format(
                idx   = iface["index"],
                descr = iface.get("descr", "—")[:28],
                admin = admin,
                oper  = oper,
                speed = speed,
            )))
        if len(result.interfaces) > 20:
            print(grey(f"    … {len(result.interfaces) - 20} more interfaces not shown"))

    # LLDP
    if result.lldp_neighbours:
        print(f"\n  {bold('LLDP Neighbours')} ({len(result.lldp_neighbours)} found):")
        fmt2 = "    port={port:<4} {sys_name:<24} chassis={chassis_id:<18} port-id={port_id}"
        for nbr in result.lldp_neighbours[:15]:
            print(cyan(fmt2.format(
                port       = nbr.get("port", "?"),
                sys_name   = nbr.get("sys_name",   "—")[:24],
                chassis_id = nbr.get("chassis_id", "—"),
                port_id    = nbr.get("port_id",    "—"),
            )))
        if len(result.lldp_neighbours) > 15:
            print(grey(f"    … {len(result.lldp_neighbours) - 15} more neighbours not shown"))


def _print_summary(results: List[DeviceResult]) -> None:
    print(bold(f"\n{'═'*64}"))
    print(bold("  SUMMARY"))
    print(bold(f"{'═'*64}"))
    total = len(results)
    ok    = sum(1 for r in results if not r.unreachable and r.failed == 0)
    warn  = sum(1 for r in results if not r.unreachable and r.failed  > 0)
    fail  = sum(1 for r in results if r.unreachable)
    fmt = "  {ip:<20} {status:<20} {elapsed}"
    print(grey(fmt.format(ip="IP", status="Status", elapsed="ms")))
    for r in results:
        if r.unreachable:
            status = red("UNREACHABLE")
        elif r.failed == 0:
            status = green("OK")
        else:
            status = yellow(f"{r.failed} OID(s) failed")
        print(fmt.format(
            ip      = r.ip,
            status  = status,
            elapsed = grey(f"{r.elapsed_ms:.0f}"),
        ))
    print()
    print(f"  Devices : {total}   "
          f"{green(str(ok))} OK   "
          f"{yellow(str(warn))} partial   "
          f"{red(str(fail))} unreachable")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="test_snmp.py",
        description="Test simulated SNMP devices by IP address.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    p.add_argument("ips", nargs="+", metavar="IP",
                   help="One or more device IP addresses to test")
    p.add_argument("--port", "-p", type=int, default=161,
                   help="SNMP UDP port (default: 161)")
    p.add_argument("--community", "-c", default=None,
                   help="Community string (default: same as device IP)")
    p.add_argument("--timeout", "-t", type=int, default=5,
                   help="Per-OID timeout in seconds (default: 5)")
    p.add_argument("--interfaces", "-i", action="store_true",
                   help="Walk the IF-MIB interface table")
    p.add_argument("--lldp", "-l", action="store_true",
                   help="Walk the LLDP-MIB remote neighbour table")
    p.add_argument("--full", "-f", action="store_true",
                   help="Equivalent to --interfaces --lldp")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Only print failures and summary")
    return p.parse_args()


async def _main(args: argparse.Namespace) -> int:
    do_ifaces = args.interfaces or args.full
    do_lldp   = args.lldp or args.full

    print(bold(f"\ndataCenter SNMP Tester  —  {len(args.ips)} device(s)"))
    print(grey(f"port={args.port}  timeout={args.timeout}s  "
               f"interfaces={'yes' if do_ifaces else 'no'}  "
               f"lldp={'yes' if do_lldp else 'no'}\n"))

    tasks = [
        _test_device(
            ip        = ip,
            community = args.community if args.community else ip,
            port      = args.port,
            timeout   = args.timeout,
            do_interfaces = do_ifaces,
            do_lldp       = do_lldp,
        )
        for ip in args.ips
    ]
    results: List[DeviceResult] = await asyncio.gather(*tasks)

    for result in results:
        _print_device(result, quiet=args.quiet)

    _print_summary(results)

    return 1 if any(r.unreachable or r.failed > 0 for r in results) else 0


if __name__ == "__main__":
    args = _parse_args()

    # Windows requires ProactorEventLoop for asyncio UDP
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        rc = asyncio.run(_main(args))
    except KeyboardInterrupt:
        print("\nAborted.")
        rc = 130

    sys.exit(rc)
