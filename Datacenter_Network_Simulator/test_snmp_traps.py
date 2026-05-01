#!/usr/bin/env python3
"""
SNMP Trap Receiver — test tool for the SNMP Network Topology Simulator.

Listens on UDP for SNMPv1/v2c traps and prints every incoming trap to the
console, regardless of community string (so it works with devices that use
their IP address as community).

Usage:
    python test_snmp_traps.py                  # binds 0.0.0.0:162  (needs admin)
    python test_snmp_traps.py --port 1620      # non-privileged port
    python test_snmp_traps.py --host 127.0.0.1 --port 1620
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime

from pyasn1.codec.ber import decoder as ber_decoder
from pysnmp.proto import api as snmp_api

# Pull trap OID → display name and severity directly from the simulator's
# own definitions so this file never goes stale when new traps are added.
try:
    from core.trap_definitions import TRAP_DEFINITIONS, SEVERITY_COLOR
    TRAP_OID_LABELS: dict[str, str] = {
        defn.oid: defn.display_name for defn in TRAP_DEFINITIONS.values()
    }
    _TRAP_OID_SEVERITY: dict[str, str] = {
        defn.oid: defn.severity for defn in TRAP_DEFINITIONS.values()
    }
except ImportError:
    # Fallback when run outside the project root
    TRAP_OID_LABELS = {
        "1.3.6.1.6.3.1.1.5.1":  "Cold Start",
        "1.3.6.1.6.3.1.1.5.2":  "Warm Start",
        "1.3.6.1.6.3.1.1.5.3":  "Link Down",
        "1.3.6.1.6.3.1.1.5.4":  "Link Up",
        "1.3.6.1.6.3.1.1.5.5":  "Authentication Failure",
        "1.3.6.1.2.1.15.0.2":   "BGP Session Down",
        "1.3.6.1.2.1.33.2.0.1": "UPS On Battery",
        "1.3.6.1.2.1.33.2.0.2": "UPS Low Battery",
        "1.3.6.1.4.1.99999.1.1":"CPU High Usage",
        "1.3.6.1.4.1.99999.1.2":"Memory High Usage",
        "1.3.6.1.4.1.99999.1.3":"Temperature Alert",
        "1.3.6.1.4.1.99999.1.4":"Link Flap",
        "1.3.6.1.4.1.99999.1.5":"Rack Failure",
    }
    _TRAP_OID_SEVERITY = {
        "1.3.6.1.6.3.1.1.5.3":  "major",
        "1.3.6.1.6.3.1.1.5.5":  "major",
        "1.3.6.1.2.1.15.0.2":   "critical",
        "1.3.6.1.2.1.33.2.0.1": "critical",
        "1.3.6.1.2.1.33.2.0.2": "critical",
        "1.3.6.1.4.1.99999.1.1":"major",
        "1.3.6.1.4.1.99999.1.2":"major",
        "1.3.6.1.4.1.99999.1.3":"critical",
        "1.3.6.1.4.1.99999.1.4":"critical",
        "1.3.6.1.4.1.99999.1.5":"critical",
    }

# ── Varbind OID → human-readable label ───────────────────────────────────────

OID_LABELS: dict[str, str] = {
    # MIB-II system
    "1.3.6.1.2.1.1.1.0":        "sysDescr",
    "1.3.6.1.2.1.1.3.0":        "sysUpTime",
    "1.3.6.1.2.1.1.5.0":        "sysName",
    # IF-MIB
    "1.3.6.1.2.1.2.2.1.1.1":    "ifIndex",
    "1.3.6.1.2.1.2.2.1.2.1":    "ifDescr",
    "1.3.6.1.2.1.2.2.1.7.1":    "ifAdminStatus",
    "1.3.6.1.2.1.2.2.1.8.1":    "ifOperStatus",
    # BGP4-MIB
    "1.3.6.1.2.1.15.3.1.7.0":   "bgpPeerRemoteAddr",
    "1.3.6.1.2.1.15.3.1.14.0":  "bgpPeerState",
    # UPS-MIB
    "1.3.6.1.2.1.33.1.2.1.0":   "upsAlarmId",
    "1.3.6.1.2.1.33.1.2.4.0":   "upsEstimatedMinutesRemaining",
    # Enterprise varbinds (1.3.6.1.4.1.99999.2.x)
    "1.3.6.1.4.1.99999.2.1":    "cpuUsage (%)",
    "1.3.6.1.4.1.99999.2.2":    "memoryUsage (%)",
    "1.3.6.1.4.1.99999.2.3":    "temperature (°C)",
    "1.3.6.1.4.1.99999.2.4":    "linkFlapCount",
    "1.3.6.1.4.1.99999.2.5":    "cpuThreshold (%)",
    "1.3.6.1.4.1.99999.2.6":    "memoryThreshold (%)",
    "1.3.6.1.4.1.99999.2.7":    "temperatureThreshold (°C)",
    "1.3.6.1.4.1.99999.2.8":    "linkFlapWindowSec",
    "1.3.6.1.4.1.99999.2.9":    "rackId",
    "1.3.6.1.4.1.99999.2.10":   "devicesDown",
}

# SNMPv2c snmpTrapOID.0 meta-OID
SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"

# ── Value formatters ──────────────────────────────────────────────────────────

_IF_STATUS = {1: "up(1)", 2: "down(2)", 3: "testing(3)"}
_BGP_STATE = {
    1: "idle(1)", 2: "connect(2)", 3: "active(3)",
    4: "openSent(4)", 5: "openConfirm(5)", 6: "established(6)",
}


def _fmt_val(oid_str: str, val) -> str:
    raw = val.prettyPrint()
    if oid_str in ("1.3.6.1.2.1.2.2.1.8.1", "1.3.6.1.2.1.2.2.1.7.1"):
        try:
            return _IF_STATUS.get(int(raw), raw)
        except (ValueError, TypeError):
            pass
    if oid_str == "1.3.6.1.2.1.15.3.1.14.0":
        try:
            return _BGP_STATE.get(int(raw), raw)
        except (ValueError, TypeError):
            pass
    return raw


# ── Severity marker ───────────────────────────────────────────────────────────

_SEV_MARKERS = {
    "critical":      "[CRIT]",
    "major":         "[ MAJ]",
    "minor":         "[ MIN]",
    "informational": "[ INF]",
}


def _severity_marker(trap_oid: str) -> str:
    sev = _TRAP_OID_SEVERITY.get(trap_oid, "")
    return _SEV_MARKERS.get(sev, "[    ]")


# ── Separator helpers ─────────────────────────────────────────────────────────

_SEP = "─" * 64


def _hdr(trap_label: str, trap_oid: str, src_ip: str, community: str,
         version: str, count: int) -> str:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    marker = _severity_marker(trap_oid)
    return (
        f"\n{_SEP}\n"
        f"  #{count:<4}  [{ts}]  {marker}  {trap_label}\n"
        f"  OID:     {trap_oid}\n"
        f"  Source:  {src_ip}   Community: {community}   ({version})\n"
        f"{_SEP}"
    )


# ── Asyncio DatagramProtocol ──────────────────────────────────────────────────

class TrapProtocol(asyncio.DatagramProtocol):
    def __init__(self):
        self._count = 0
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple):
        src_ip, _src_port = addr
        self._count += 1

        try:
            msg_ver = int(snmp_api.decodeMessageVersion(data))
        except Exception as ex:
            print(f"[{datetime.now():%H:%M:%S}] #{self._count}  Decode error from {src_ip}: {ex}")
            return

        if msg_ver not in snmp_api.PROTOCOL_MODULES:
            print(f"[{datetime.now():%H:%M:%S}] #{self._count}  Unsupported SNMP version {msg_ver} from {src_ip}")
            return

        pMod = snmp_api.PROTOCOL_MODULES[msg_ver]

        try:
            msg, _ = ber_decoder.decode(data, asn1Spec=pMod.Message())
        except Exception as ex:
            print(f"[{datetime.now():%H:%M:%S}] #{self._count}  BER decode error from {src_ip}: {ex}")
            return

        community = bytes(msg.getComponentByPosition(1)).decode("ascii", errors="replace")
        pdu = pMod.apiMessage.get_pdu(msg)

        if msg_ver == snmp_api.SNMP_VERSION_1:
            self._handle_v1(pMod, pdu, src_ip, community)
        else:
            self._handle_v2c(pMod, pdu, src_ip, community)

    def _handle_v1(self, pMod, pdu, src_ip: str, community: str):
        enterprise = str(pMod.apiTrapPDU.get_enterprise(pdu))
        agent_addr = str(pMod.apiTrapPDU.get_agent_address(pdu))
        specific   = int(pMod.apiTrapPDU.get_specific_trap(pdu))
        generic    = int(pMod.apiTrapPDU.get_generic_trap(pdu))

        label = TRAP_OID_LABELS.get(
            enterprise, f"enterprise={enterprise} generic={generic} specific={specific}"
        )
        print(_hdr(label, enterprise, src_ip, community, "SNMPv1", self._count))
        print(f"  Agent address : {agent_addr}")

        for oid, val in pMod.apiTrapPDU.get_varbinds(pdu):
            oid_str = str(oid)
            lbl = OID_LABELS.get(oid_str, oid_str)
            print(f"  {lbl:<38} = {_fmt_val(oid_str, val)}")

    def _handle_v2c(self, pMod, pdu, src_ip: str, community: str):
        var_binds = list(pMod.apiPDU.get_varbinds(pdu))

        # Extract snmpTrapOID.0
        trap_oid = ""
        for oid, val in var_binds:
            if str(oid) == SNMP_TRAP_OID:
                trap_oid = str(val)
                break

        label = TRAP_OID_LABELS.get(trap_oid, trap_oid or "Unknown Trap")
        print(_hdr(label, trap_oid, src_ip, community, "SNMPv2c", self._count))

        for oid, val in var_binds:
            oid_str = str(oid)
            if oid_str == SNMP_TRAP_OID:
                continue  # already shown as trap type
            lbl = OID_LABELS.get(oid_str, oid_str)
            print(f"  {lbl:<38} = {_fmt_val(oid_str, val)}")

    def error_received(self, exc):
        print(f"Socket error: {exc}", file=sys.stderr)

    def connection_lost(self, exc):
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

async def _run(host: str, port: int):
    loop = asyncio.get_running_loop()
    print("=" * 64)
    print("  SNMP Trap Receiver — SNMP Network Topology Simulator")
    print("=" * 64)
    print(f"  Listening on {host}:{port}   (Ctrl+C to stop)")
    print(f"  Watching for {len(TRAP_OID_LABELS)} trap type(s):\n")
    for oid, name in sorted(TRAP_OID_LABELS.items()):
        sev = _TRAP_OID_SEVERITY.get(oid, "")
        marker = _SEV_MARKERS.get(sev, "      ")
        print(f"    {marker}  {name:<28} {oid}")
    print()

    transport, _ = await loop.create_datagram_endpoint(
        TrapProtocol,
        local_addr=(host, port),
    )
    try:
        await asyncio.sleep(float("inf"))
    finally:
        transport.close()


def main():
    parser = argparse.ArgumentParser(
        description="SNMP Trap Receiver — test tool for the SNMP Network Topology Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python test_snmp_traps.py                   # port 162 (needs admin)\n"
            "  python test_snmp_traps.py --port 1620       # non-privileged\n"
            "  python test_snmp_traps.py --host 127.0.0.1 --port 1620\n\n"
            "In the simulator, set Trap Receiver IP to this machine's IP\n"
            "and Port to the port you chose here, then click Apply."
        ),
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument(
        "--port", type=int, default=162,
        help="UDP port to listen on (default: 162; use 1620 to avoid needing admin rights)",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        asyncio.run(_run(args.host, args.port))
    except PermissionError:
        print(f"\nError: cannot bind to port {args.port} — permission denied.")
        print("  Run as Administrator, or use a high port:  --port 1620")
        sys.exit(1)
    except OSError as exc:
        print(f"\nError: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nStopped.")


if __name__ == "__main__":
    main()
