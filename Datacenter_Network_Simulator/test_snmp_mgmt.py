"""
SNMP Management Tester
======================
Tests per-device threshold configuration via the simulator's management agent
(port 1161).  Community string = device IP — same convention as snmpsim port 161.

With no --set flags the script walks all 24 configurable thresholds and prints
their current values (per-device override if set, otherwise the global default).

Usage examples
--------------
  # Walk all thresholds for device 10.50.0.4
  python test_snmp_mgmt.py 10.50.0.4

  # Walk for multiple devices
  python test_snmp_mgmt.py 10.50.0.4 10.50.0.5

  # Set HighCPU threshold to 85 % for device 10.50.0.4
  python test_snmp_mgmt.py 10.50.0.4 --set HighCPU 85

  # Set multiple thresholds at once
  python test_snmp_mgmt.py 10.50.0.4 --set HighCPU 85 --set HighTemperature 40

  # Set by raw OID (required for composite-rule sub-conditions)
  python test_snmp_mgmt.py 10.50.0.4 --set-oid 1.3.6.1.4.1.99999.3.8.1.0 90

  # Set then verify by walking
  python test_snmp_mgmt.py 10.50.0.4 --set HighCPU 85 --verify

  # Simulator on a remote host
  python test_snmp_mgmt.py 10.50.0.4 --agent 192.168.1.50

  # Print the full OID table and exit
  python test_snmp_mgmt.py --list-rules
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
import warnings
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore", category=RuntimeWarning, module="pysnmp")

from pyasn1.codec.ber import decoder as ber_decoder, encoder as ber_encoder
from pyasn1.type import univ
from pysnmp.proto import api as snmp_api


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


# ── OID catalogue (mirrors core/snmp_set_agent.py) ────────────────────────────

_BASE = "1.3.6.1.4.1.99999.3"

# key: OID string
# value: (rule_name, label, multiplier, unit)
#   multiplier: SNMP integer = stored_float × multiplier
#   e.g. airflow 3.5 m/s is stored as integer 35 (multiplier=10)
_OID_INFO: Dict[str, Tuple[str, str, int, str]] = {
    f"{_BASE}.1.0":    ("HighCPU",                  "HighCPU threshold",                   1,  "%"),
    f"{_BASE}.2.0":    ("HighCPUSustained",          "HighCPUSustained threshold",          1,  "%"),
    f"{_BASE}.3.0":    ("CPUNormal",                 "CPUNormal threshold",                 1,  "%"),
    f"{_BASE}.4.0":    ("HighMemory",                "HighMemory threshold",                1,  "%"),
    f"{_BASE}.5.0":    ("MemoryNormal",              "MemoryNormal threshold",              1,  "%"),
    f"{_BASE}.6.0":    ("HighTemperature",           "HighTemperature threshold",           1,  "°C"),
    f"{_BASE}.7.0":    ("TemperatureNormal",         "TemperatureNormal threshold",         1,  "°C"),
    f"{_BASE}.8.1.0":  ("CriticalCPUAndTemp",        "CriticalCPUAndTemp CPU threshold",    1,  "%"),
    f"{_BASE}.8.2.0":  ("CriticalCPUAndTemp",        "CriticalCPUAndTemp Temp threshold",   1,  "°C"),
    f"{_BASE}.9.0":    ("RackFailure",               "RackFailure min devices",             1,  ""),
    f"{_BASE}.10.0":   ("SensorAmbientTempHigh",     "SensorAmbientTempHigh threshold",     1,  "°C"),
    f"{_BASE}.11.0":   ("SensorAmbientTempCritical", "SensorAmbientTempCritical threshold", 1,  "°C"),
    f"{_BASE}.12.0":   ("SensorAmbientTempNormal",   "SensorAmbientTempNormal threshold",   1,  "°C"),
    f"{_BASE}.13.0":   ("SensorHighHumidity",        "SensorHighHumidity threshold",        1,  "%"),
    f"{_BASE}.14.0":   ("SensorCriticalHumidity",    "SensorCriticalHumidity threshold",    1,  "%"),
    f"{_BASE}.15.0":   ("SensorLowHumidity",         "SensorLowHumidity threshold",         1,  "%"),
    f"{_BASE}.16.1.0": ("SensorHumidityNormal",      "SensorHumidityNormal low bound",      1,  "%"),
    f"{_BASE}.16.2.0": ("SensorHumidityNormal",      "SensorHumidityNormal high bound",     1,  "%"),
    f"{_BASE}.17.0":   ("SensorHighDewPoint",        "SensorHighDewPoint threshold",        1,  "°C"),
    f"{_BASE}.18.0":   ("SensorDewPointNormal",      "SensorDewPointNormal threshold",      1,  "°C"),
    f"{_BASE}.19.0":   ("SensorHighAirflow",         "SensorHighAirflow threshold",         10, "m/s"),
    f"{_BASE}.20.0":   ("SensorLowAirflow",          "SensorLowAirflow threshold",          10, "m/s"),
    f"{_BASE}.21.1.0": ("SensorAirflowNormal",       "SensorAirflowNormal low bound",       10, "m/s"),
    f"{_BASE}.21.2.0": ("SensorAirflowNormal",       "SensorAirflowNormal high bound",      10, "m/s"),
}

_OID_SORTED: List[str] = sorted(
    _OID_INFO.keys(),
    key=lambda o: tuple(int(x) for x in o.split(".")),
)

# rule_name → [(oid, label, multiplier, unit)]
_RULE_TO_OIDS: Dict[str, List[Tuple[str, str, int, str]]] = {}
for _oid, (_rule, _lbl, _mult, _unit) in _OID_INFO.items():
    _RULE_TO_OIDS.setdefault(_rule, []).append((_oid, _lbl, _mult, _unit))


# ── Protocol helpers ──────────────────────────────────────────────────────────

_pMod = snmp_api.PROTOCOL_MODULES[1]   # SNMPv2c
_req_id = 0


def _next_req_id() -> int:
    global _req_id
    _req_id = (_req_id + 1) & 0x7FFFFFFF
    return _req_id


def _pdu_defaults(pdu):
    for name in ("set_defaults", "setDefaults"):
        fn = getattr(_pMod.apiPDU, name, None)
        if fn:
            fn(pdu)
            return


def _msg_defaults(msg):
    for name in ("set_defaults", "setDefaults"):
        fn = getattr(_pMod.apiMessage, name, None)
        if fn:
            fn(msg)
            return


def _build_msg(community: str, pdu) -> bytes:
    msg = _pMod.Message()
    _msg_defaults(msg)
    _pMod.apiMessage.set_version(msg, 1)
    _pMod.apiMessage.set_community(msg, community.encode())
    _pMod.apiMessage.set_pdu(msg, pdu)
    return ber_encoder.encode(msg)


# ── UDP send / receive ────────────────────────────────────────────────────────
# Single persistent socket per session — reused across all requests.
# Creating a new socket per request caused recv() to time out on Windows
# because each new unbound socket gets a different ephemeral port, and
# ICMP port-unreachable errors from stale ports can corrupt subsequent recvs.

def _udp(sock: socket.socket, agent: str, port: int,
         data: bytes) -> Optional[bytes]:
    """Send *data* and return the response bytes, or None on timeout/error."""
    try:
        sock.sendto(data, (agent, port))
        return sock.recv(65535)
    except socket.timeout:
        return None
    except OSError:
        return None


# ── GETNEXT ───────────────────────────────────────────────────────────────────

def _getnext(sock: socket.socket, agent: str, port: int, community: str,
             oid_str: str) -> Optional[Tuple[str, int]]:
    pdu = _pMod.GetNextRequestPDU()
    _pdu_defaults(pdu)
    _pMod.apiPDU.set_request_id(pdu, _next_req_id())
    _pMod.apiPDU.set_varbinds(pdu, [
        (univ.ObjectIdentifier(tuple(int(x) for x in oid_str.split("."))), univ.Null())
    ])

    raw = _udp(sock, agent, port, _build_msg(community, pdu))
    if raw is None:
        return None

    rsp_msg, _ = ber_decoder.decode(raw, asn1Spec=_pMod.Message())
    rsp_pdu = _pMod.apiMessage.get_pdu(rsp_msg)
    if int(_pMod.apiPDU.get_error_status(rsp_pdu)):
        return None

    vbs = list(_pMod.apiPDU.get_varbinds(rsp_pdu))
    if not vbs:
        return None
    oid_ret, val = vbs[0]
    try:
        int_val = int(val)
    except Exception:
        return None  # endOfMibView / noSuchObject — OctetString, not Integer
    return str(oid_ret), int_val


# ── SET ───────────────────────────────────────────────────────────────────────

def _do_set(sock: socket.socket, agent: str, port: int, community: str,
            oid_str: str, int_val: int) -> Optional[str]:
    """Returns None on success, error string on failure."""
    pdu = _pMod.SetRequestPDU()
    _pdu_defaults(pdu)
    _pMod.apiPDU.set_request_id(pdu, _next_req_id())
    _pMod.apiPDU.set_varbinds(pdu, [
        (univ.ObjectIdentifier(tuple(int(x) for x in oid_str.split("."))),
         univ.Integer(int_val))
    ])

    raw = _udp(sock, agent, port, _build_msg(community, pdu))
    if raw is None:
        return "timeout — is the simulator running?"

    rsp_msg, _ = ber_decoder.decode(raw, asn1Spec=_pMod.Message())
    rsp_pdu = _pMod.apiMessage.get_pdu(rsp_msg)
    err_status = int(_pMod.apiPDU.get_error_status(rsp_pdu))
    if err_status:
        err_idx = int(_pMod.apiPDU.get_error_index(rsp_pdu))
        names = {2: "noSuchName", 6: "noAccess", 7: "wrongType"}
        return f"{names.get(err_status, f'error-status={err_status}')} (varbind index {err_idx})"
    return None


# ── Walk all management OIDs ──────────────────────────────────────────────────

def _walk(sock: socket.socket, agent: str, port: int, community: str,
          ) -> Optional[List[Tuple[str, int]]]:
    """
    Returns [(oid_str, raw_int_value)] for all OIDs under _BASE,
    or None if the agent is unreachable.
    """
    results: List[Tuple[str, int]] = []
    current = _BASE
    while True:
        pair = _getnext(sock, agent, port, community, current)
        if pair is None:
            if not results:
                return None   # first request timed out — agent unreachable
            break
        oid_str, val = pair
        if not oid_str.startswith(_BASE + "."):
            break
        # Guard: server must advance the OID; same OID back = endOfMibView
        if tuple(int(x) for x in oid_str.split(".")) <= \
                tuple(int(x) for x in current.split(".")):
            break
        results.append((oid_str, val))
        current = oid_str
    return results


# ── Display ───────────────────────────────────────────────────────────────────

def _fmt_val(raw_int: int, multiplier: int, unit: str) -> str:
    v = raw_int / multiplier
    s = f"{v:g}"
    return f"{s} {unit}".strip() if unit else s


def _print_walk_results(device_ip: str, rows: List[Tuple[str, int]], elapsed_ms: float):
    print(bold(f"\n{'─'*70}"))
    print(bold(f"  {cyan(device_ip)}   {grey(f'{elapsed_ms:.0f} ms')}"))
    print(bold(f"{'─'*70}"))

    if not rows:
        print(f"  {red('✗')}  No response — simulator not running or wrong agent address")
        return

    label_w = max(len(_OID_INFO[oid][1]) for oid, _ in rows if oid in _OID_INFO) + 2

    for oid_str, raw_val in rows:
        info = _OID_INFO.get(oid_str)
        if info:
            _, label, mult, unit = info
            val_str = _fmt_val(raw_val, mult, unit)
            print(f"  {green('✓')}  {label:<{label_w}} {bold(val_str)}")
        else:
            print(f"  {grey('?')}  {oid_str:<{label_w}} {raw_val}")

    print(f"\n  {len(rows)} threshold(s) read")


# ── Per-device test ───────────────────────────────────────────────────────────

def _run_device(sock: socket.socket, device_ip: str,
                agent: str, port: int,
                sets: List[Tuple[str, int]], verify: bool) -> bool:
    all_ok = True

    # ── SET phase ─────────────────────────────────────────────────────────────
    if sets:
        print(bold(f"\n{'─'*70}"))
        print(bold(f"  {cyan(device_ip)}  SET {len(sets)} threshold(s)"))
        print(bold(f"{'─'*70}"))
        for oid_str, int_val in sets:
            info = _OID_INFO.get(oid_str)
            label = info[1] if info else oid_str
            mult  = info[2] if info else 1
            unit  = info[3] if info else ""
            err = _do_set(sock, agent, port, device_ip, oid_str, int_val)
            if err:
                print(f"  {red('✗')}  {label:<45} {red(err)}")
                all_ok = False
            else:
                val_str = _fmt_val(int_val, mult, unit)
                print(f"  {green('✓')}  {label:<45} {bold(val_str)}")

    # ── WALK phase (always when no SETs; with SETs only if --verify) ──────────
    if not sets or verify:
        t0 = time.perf_counter()
        rows = _walk(sock, agent, port, device_ip)
        elapsed = (time.perf_counter() - t0) * 1000
        _print_walk_results(device_ip, rows or [], elapsed)
        if rows is None:
            all_ok = False

    return all_ok


# ── --list-rules ──────────────────────────────────────────────────────────────

def _print_oid_table():
    print(bold(f"\n{'─'*80}"))
    print(bold(f"  {'Rule Name':<30} {'OID':<30} {'Label / Unit'}"))
    print(bold(f"{'─'*80}"))
    for oid in _OID_SORTED:
        rule, label, mult, unit = _OID_INFO[oid]
        unit_str = f" ({unit})" if unit else ""
        mult_str = grey(f"  ×{mult}") if mult != 1 else ""
        print(f"  {cyan(rule):<39} {grey(oid):<30} {label}{unit_str}{mult_str}")
    print(f"\n  {len(_OID_INFO)} OIDs total  |  {len(_RULE_TO_OIDS)} rules\n")


# ── Argument parsing ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="test_snmp_mgmt.py",
        description="Test per-device threshold configuration (SNMP SET, port 1161).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.split("\n")[12:]),
    )
    p.add_argument("ips", nargs="*", metavar="IP",
                   help="Device IP address(es) — used as SNMP community string")
    p.add_argument("--agent", "-a", default="127.0.0.1",
                   help="Host running the simulator (default: 127.0.0.1)")
    p.add_argument("--port", "-p", type=int, default=1161,
                   help="Management agent UDP port (default: 1161)")
    p.add_argument("--timeout", "-t", type=float, default=5.0,
                   help="Request timeout in seconds (default: 5)")
    p.add_argument("--set", nargs=2, action="append", metavar=("RULE", "VALUE"),
                   dest="set_rules", default=[],
                   help="Set threshold by rule name (e.g. --set HighCPU 85)")
    p.add_argument("--set-oid", nargs=2, action="append", metavar=("OID", "VALUE"),
                   dest="set_oids", default=[],
                   help="Set threshold by raw OID (for composite sub-conditions)")
    p.add_argument("--verify", "-v", action="store_true",
                   help="Walk all thresholds after SET to confirm the new values")
    p.add_argument("--list-rules", action="store_true",
                   help="Print the full OID/rule table and exit")
    return p.parse_args()


# ── Entry point ───────────────────────────────────────────────────────────────

def _main(args: argparse.Namespace) -> int:
    if args.list_rules:
        _print_oid_table()
        return 0

    if not args.ips:
        print(red("Error: at least one device IP is required. Use --list-rules to see OIDs."))
        return 1

    # Resolve --set RuleName value → (oid, raw_int)
    sets: List[Tuple[str, int]] = []

    for rule_name, val_str in args.set_rules:
        entries = _RULE_TO_OIDS.get(rule_name)
        if not entries:
            print(red(f"Unknown rule '{rule_name}'."))
            print(f"  Run {bold('python test_snmp_mgmt.py --list-rules')} to see all rule names.")
            return 1
        if len(entries) > 1:
            print(yellow(f"Rule '{rule_name}' has {len(entries)} sub-conditions — use --set-oid:"))
            for oid, label, mult, unit in entries:
                hint = f"  [value × {mult}, e.g. 3.5 m/s → 35]" if mult != 1 else ""
                print(f"    --set-oid {oid} <value>   # {label} ({unit}){hint}")
            return 1
        oid, _, mult, _ = entries[0]
        try:
            sets.append((oid, round(float(val_str) * mult)))
        except ValueError:
            print(red(f"Invalid value '{val_str}' for {rule_name} — must be a number"))
            return 1

    for oid_str, val_str in args.set_oids:
        if oid_str not in _OID_INFO:
            print(red(f"Unknown OID '{oid_str}'."))
            print(f"  Run {bold('python test_snmp_mgmt.py --list-rules')} to see all OIDs.")
            return 1
        _, _, mult, _ = _OID_INFO[oid_str]
        try:
            sets.append((oid_str, round(float(val_str) * mult)))
        except ValueError:
            print(red(f"Invalid value '{val_str}' — must be a number"))
            return 1

    # Header
    mode = f"SET {len(sets)} OID(s)" if sets else "WALK"
    print(bold(f"\ndataCenter SNMP Management Tester  —  {mode}  ({len(args.ips)} device(s))"))
    print(grey(f"agent={args.agent}:{args.port}  community=<device-ip>  timeout={args.timeout}s"))

    # Single socket reused for all devices and all requests in this session
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))          # let OS assign a fixed local port for this session
    sock.settimeout(args.timeout)

    rc = 0
    try:
        for ip in args.ips:
            ok = _run_device(sock, ip, args.agent, args.port, sets, args.verify)
            if not ok:
                rc = 1
    finally:
        sock.close()

    print()
    return rc


if __name__ == "__main__":
    args = _parse_args()
    try:
        rc = _main(args)
    except KeyboardInterrupt:
        print("\nAborted.")
        rc = 130
    sys.exit(rc)
