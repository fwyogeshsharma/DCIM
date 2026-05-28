"""
SNMP SET Tester — Device Identity & Asset Fields
=================================================
Tests SNMP SET for standard MIB-II system OIDs (sysName / sysContact / sysLocation)
and enterprise asset OIDs (rack position, model, power, etc.) via the simulator's
management agent on port 1161.

After a successful SET the simulator regenerates the .snmprec file automatically.
Use --verify to confirm the new value is visible via the main snmpsim port (1611).

Usage examples
--------------
  # Walk all identity/asset fields for a device
  python test_snmp_set.py 10.50.0.4

  # Walk multiple devices
  python test_snmp_set.py 10.50.0.4 10.50.0.5

  # Set sysName
  python test_snmp_set.py 10.50.0.4 --set sysName DC1-LF09-NEW

  # Set sysLocation (quote strings with spaces)
  python test_snmp_set.py 10.50.0.4 --set sysLocation "USA, Chicago, DC1, Floor 2"

  # Set asset fields
  python test_snmp_set.py 10.50.0.4 --set datacenter DC2 --set rack_unit 15

  # Set then verify via snmpsim (port 1611) that snmprec was regenerated
  python test_snmp_set.py 10.50.0.4 --set sysName DC1-LF09-NEW --verify

  # Set by raw OID
  python test_snmp_set.py 10.50.0.4 --set-oid 1.3.6.1.2.1.1.5.0 MyDevice

  # Print OID table and exit
  python test_snmp_set.py --list-oids

  # Simulator on a remote host
  python test_snmp_set.py 10.50.0.4 --agent 192.168.1.50
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
def blue(t):   return _c(t, "34")


# ── OID catalogue ─────────────────────────────────────────────────────────────

_SYS_BASE   = "1.3.6.1.2.1.1"
_ASSET_BASE = "1.3.6.1.4.1.99999.4"

# OID → (field_name, type "str"|"int", description)
_SYSTEM_OID_INFO: Dict[str, Tuple[str, str, str]] = {
    f"{_SYS_BASE}.4.0": ("sysContact",  "str", "Contact person / email"),
    f"{_SYS_BASE}.5.0": ("sysName",     "str", "Device hostname"),
    f"{_SYS_BASE}.6.0": ("sysLocation", "str", "Physical location string"),
}

_ASSET_OID_INFO: Dict[str, Tuple[str, str, str]] = {
    f"{_ASSET_BASE}.1.0":  ("country",         "str", "Country"),
    f"{_ASSET_BASE}.2.0":  ("datacenter_city", "str", "Datacenter city"),
    f"{_ASSET_BASE}.3.0":  ("datacenter",      "str", "Datacenter name"),
    f"{_ASSET_BASE}.4.0":  ("floor",           "str", "Floor"),
    f"{_ASSET_BASE}.5.0":  ("room",            "str", "Room"),
    f"{_ASSET_BASE}.6.0":  ("rack_row",        "int", "Rack row number"),
    f"{_ASSET_BASE}.7.0":  ("rack_num",        "int", "Rack number"),
    f"{_ASSET_BASE}.8.0":  ("rack_unit",       "int", "Rack unit (U position)"),
    f"{_ASSET_BASE}.9.0":  ("model_name",      "str", "Hardware model name"),
    f"{_ASSET_BASE}.10.0": ("power_draw_w",    "int", "Power draw (watts)"),
}

_ALL_OID_INFO: Dict[str, Tuple[str, str, str]] = {**_SYSTEM_OID_INFO, **_ASSET_OID_INFO}

# field_name → OID  (for --set field_name value lookup)
_FIELD_TO_OID: Dict[str, str] = {info[0]: oid for oid, info in _ALL_OID_INFO.items()}

_ALL_OID_SORTED: List[str] = sorted(
    _ALL_OID_INFO.keys(),
    key=lambda o: tuple(int(x) for x in o.split(".")),
)

# MIB-II OIDs to verify via snmpsim after SET (same field, different port)
_SNMPSIM_VERIFY_OID: Dict[str, str] = {
    f"{_SYS_BASE}.4.0": f"{_SYS_BASE}.4.0",   # sysContact
    f"{_SYS_BASE}.5.0": f"{_SYS_BASE}.5.0",   # sysName
    f"{_SYS_BASE}.6.0": f"{_SYS_BASE}.6.0",   # sysLocation
}


# ── Protocol helpers ──────────────────────────────────────────────────────────

_pMod  = snmp_api.PROTOCOL_MODULES[1]   # SNMPv2c
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


def _build_msg(community: str, pdu) -> bytes:
    msg = _pMod.Message()
    for name in ("set_defaults", "setDefaults"):
        fn = getattr(_pMod.apiMessage, name, None)
        if fn:
            fn(msg)
            break
    _pMod.apiMessage.set_version(msg, 1)
    _pMod.apiMessage.set_community(msg, community.encode())
    _pMod.apiMessage.set_pdu(msg, pdu)
    return ber_encoder.encode(msg)


def _oid_obj(oid_str: str) -> univ.ObjectIdentifier:
    return univ.ObjectIdentifier(tuple(int(x) for x in oid_str.split(".")))


# ── UDP send / receive ────────────────────────────────────────────────────────

def _udp(sock: socket.socket, agent: str, port: int, data: bytes) -> Optional[bytes]:
    try:
        sock.sendto(data, (agent, port))
        return sock.recv(65535)
    except socket.timeout:
        return None
    except OSError:
        return None


# ── GET (single OID) ──────────────────────────────────────────────────────────

def _get_one(sock: socket.socket, agent: str, port: int,
             community: str, oid_str: str) -> Optional[str]:
    """Returns value as string (decoded OctetString or str(Integer)), None on failure."""
    pdu = _pMod.GetRequestPDU()
    _pdu_defaults(pdu)
    _pMod.apiPDU.set_request_id(pdu, _next_req_id())
    _pMod.apiPDU.set_varbinds(pdu, [(_oid_obj(oid_str), univ.Null())])

    raw = _udp(sock, agent, port, _build_msg(community, pdu))
    if raw is None:
        return None
    try:
        rsp_msg, _ = ber_decoder.decode(raw, asn1Spec=_pMod.Message())
        rsp_pdu = _pMod.apiMessage.get_pdu(rsp_msg)
        if int(_pMod.apiPDU.get_error_status(rsp_pdu)):
            return None
        vbs = list(_pMod.apiPDU.get_varbinds(rsp_pdu))
        if not vbs:
            return None
        _, val = vbs[0]
        if isinstance(val, univ.OctetString):
            return bytes(val).decode("utf-8", errors="replace")
        return str(int(val))
    except Exception:
        return None


# ── GETNEXT ───────────────────────────────────────────────────────────────────

def _getnext(sock: socket.socket, agent: str, port: int,
             community: str, oid_str: str) -> Optional[Tuple[str, str]]:
    """Returns (next_oid_str, value_str) or None."""
    pdu = _pMod.GetNextRequestPDU()
    _pdu_defaults(pdu)
    _pMod.apiPDU.set_request_id(pdu, _next_req_id())
    _pMod.apiPDU.set_varbinds(pdu, [(_oid_obj(oid_str), univ.Null())])

    raw = _udp(sock, agent, port, _build_msg(community, pdu))
    if raw is None:
        return None
    try:
        rsp_msg, _ = ber_decoder.decode(raw, asn1Spec=_pMod.Message())
        rsp_pdu = _pMod.apiMessage.get_pdu(rsp_msg)
        if int(_pMod.apiPDU.get_error_status(rsp_pdu)):
            return None
        vbs = list(_pMod.apiPDU.get_varbinds(rsp_pdu))
        if not vbs:
            return None
        ret_oid, val = vbs[0]
        ret_oid_str = str(ret_oid)
        if isinstance(val, univ.OctetString):
            val_str = bytes(val).decode("utf-8", errors="replace")
            # endOfMibView sentinel strings
            if val_str in ("endOfMibView", "noSuchObject", "noSuchInstance"):
                return None
        else:
            val_str = str(int(val))
        return ret_oid_str, val_str
    except Exception:
        return None


# ── SET ───────────────────────────────────────────────────────────────────────

def _do_set(sock: socket.socket, agent: str, port: int,
            community: str, oid_str: str, value: str, typ: str) -> Optional[str]:
    """
    Returns None on success, error string on failure.
    typ: "str" → OctetString, "int" → Integer
    """
    if typ == "int":
        try:
            asn1_val = univ.Integer(int(value))
        except ValueError:
            return f"value '{value}' must be an integer for this field"
    else:
        asn1_val = univ.OctetString(value.encode("utf-8"))

    pdu = _pMod.SetRequestPDU()
    _pdu_defaults(pdu)
    _pMod.apiPDU.set_request_id(pdu, _next_req_id())
    _pMod.apiPDU.set_varbinds(pdu, [(_oid_obj(oid_str), asn1_val)])

    raw = _udp(sock, agent, port, _build_msg(community, pdu))
    if raw is None:
        return "timeout — is the simulator running on port 1161?"
    try:
        rsp_msg, _ = ber_decoder.decode(raw, asn1Spec=_pMod.Message())
        rsp_pdu = _pMod.apiMessage.get_pdu(rsp_msg)
        err_status = int(_pMod.apiPDU.get_error_status(rsp_pdu))
        if err_status:
            err_idx = int(_pMod.apiPDU.get_error_index(rsp_pdu))
            names = {2: "noSuchName", 6: "noAccess", 7: "wrongType", 10: "noCreation"}
            return f"{names.get(err_status, f'error-status={err_status}')} (varbind {err_idx})"
        return None
    except Exception as e:
        return f"decode error: {e}"


# ── Walk all identity/asset OIDs ──────────────────────────────────────────────

def _walk(sock: socket.socket, agent: str, port: int,
          community: str) -> Optional[List[Tuple[str, str]]]:
    """
    Returns [(oid_str, value_str)] for all known identity/asset OIDs,
    or None if agent is unreachable.
    """
    # Use GET per-OID (more reliable than GETNEXT across disjoint OID spaces)
    results: List[Tuple[str, str]] = []
    got_any_response = False

    for oid_str in _ALL_OID_SORTED:
        val = _get_one(sock, agent, port, community, oid_str)
        if val is not None:
            got_any_response = True
            results.append((oid_str, val))
        elif not got_any_response and oid_str == _ALL_OID_SORTED[0]:
            # First OID timed out — agent unreachable
            return None

    return results if got_any_response else None


# ── Display ───────────────────────────────────────────────────────────────────

def _print_walk(device_ip: str, rows: Optional[List[Tuple[str, str]]], elapsed_ms: float):
    print(bold(f"\n{'─'*72}"))
    print(bold(f"  {cyan(device_ip)}   {grey(f'{elapsed_ms:.0f} ms')}"))
    print(bold(f"{'─'*72}"))

    if rows is None:
        print(f"  {red('✗')}  No response — management agent not running or wrong port/address")
        return

    label_w = max((len(info[0]) for info in _ALL_OID_INFO.values()), default=20) + 2

    for section_label, oid_map in (("MIB-II System", _SYSTEM_OID_INFO),
                                   ("Asset / Location", _ASSET_OID_INFO)):
        print(f"\n  {grey(section_label)}")
        for oid_str in sorted(oid_map, key=lambda o: tuple(int(x) for x in o.split("."))):
            field, typ, desc = oid_map[oid_str]
            val = next((v for o, v in rows if o == oid_str), None)
            if val is None:
                print(f"    {grey('?')}  {field:<{label_w}} {grey('(no response)')}")
            elif val == "":
                print(f"    {grey('-')}  {field:<{label_w}} {grey('(empty)')}")
            else:
                print(f"    {green('✓')}  {field:<{label_w}} {bold(val)}")

    print(f"\n  {len(rows)} field(s) read")


# ── Verify via snmpsim ────────────────────────────────────────────────────────

def _verify_snmpsim(sock: socket.socket, agent: str, snmp_port: int,
                    community: str, sets: List[Tuple[str, str, str]]) -> bool:
    """GET the MIB-II OIDs from snmpsim port to confirm snmprec was regenerated."""
    print(f"\n  {grey('Verifying via snmpsim port')} {grey(str(snmp_port))} {grey('...')}")
    # Give the regeneration a moment to complete
    time.sleep(0.5)

    verify_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    verify_sock.bind(("", 0))
    verify_sock.settimeout(5.0)
    all_ok = True
    try:
        for oid_str, expected_val, typ in sets:
            if oid_str not in _SNMPSIM_VERIFY_OID:
                continue   # asset OIDs not directly visible in snmpsim
            actual = _get_one(verify_sock, agent, snmp_port, community, oid_str)
            field = _ALL_OID_INFO[oid_str][0]
            if actual is None:
                print(f"    {yellow('?')}  {field}: snmpsim unreachable on port {snmp_port}")
                all_ok = False
            elif actual.strip() == expected_val.strip():
                print(f"    {green('✓')}  {field} = {bold(actual)}")
            else:
                print(f"    {red('✗')}  {field}: expected {bold(expected_val)!r}, got {bold(actual)!r}")
                all_ok = False
    finally:
        verify_sock.close()
    return all_ok


# ── Per-device run ────────────────────────────────────────────────────────────

def _run_device(sock: socket.socket, device_ip: str, agent: str,
                mgmt_port: int, snmp_port: int,
                sets: List[Tuple[str, str, str]],  # (oid, value, type)
                verify: bool) -> bool:
    all_ok = True

    if sets:
        print(bold(f"\n{'─'*72}"))
        print(bold(f"  {cyan(device_ip)}  SET {len(sets)} field(s)"))
        print(bold(f"{'─'*72}"))
        label_w = max(len(_ALL_OID_INFO[o][0]) for o, _, _ in sets if o in _ALL_OID_INFO) + 2
        for oid_str, value, typ in sets:
            info = _ALL_OID_INFO.get(oid_str)
            field = info[0] if info else oid_str
            err = _do_set(sock, agent, mgmt_port, device_ip, oid_str, value, typ)
            if err:
                print(f"  {red('✗')}  {field:<{label_w}} {red(err)}")
                all_ok = False
            else:
                print(f"  {green('✓')}  {field:<{label_w}} {bold(repr(value) if typ == 'str' else value)}")

        if verify:
            ok = _verify_snmpsim(sock, agent, snmp_port, device_ip, sets)
            if not ok:
                all_ok = False

    if not sets:
        t0 = time.perf_counter()
        rows = _walk(sock, agent, mgmt_port, device_ip)
        elapsed = (time.perf_counter() - t0) * 1000
        _print_walk(device_ip, rows, elapsed)
        if rows is None:
            all_ok = False

    return all_ok


# ── --list-oids ───────────────────────────────────────────────────────────────

def _print_oid_table():
    print(bold(f"\n{'─'*80}"))
    print(bold(f"  {'Field':<20} {'Type':<5} {'OID':<32} Description"))
    print(bold(f"{'─'*80}"))
    for section, oid_map in (("MIB-II System (sysName / sysContact / sysLocation)", _SYSTEM_OID_INFO),
                             ("Enterprise Asset (1.3.6.1.4.1.99999.4.*)", _ASSET_OID_INFO)):
        print(f"\n  {grey(section)}")
        for oid in sorted(oid_map, key=lambda o: tuple(int(x) for x in o.split("."))):
            field, typ, desc = oid_map[oid]
            type_col = blue("str") if typ == "str" else yellow("int")
            print(f"  {cyan(field):<29} {type_col:<14} {grey(oid):<32} {desc}")
    print(f"\n  {len(_ALL_OID_INFO)} fields total\n")
    print(f"  {bold('Usage:')} --set <field_name> <value>")
    print(f"  {grey('Example:')} --set sysName DC1-SW01  --set rack_unit 15\n")


# ── Argument parsing ──────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="test_snmp_set.py",
        description="Test SNMP SET for device identity and asset fields (management agent port 1161).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.split("\n")[16:]),
    )
    p.add_argument("ips", nargs="*", metavar="IP",
                   help="Device IP(s) — used as SNMP community string")
    p.add_argument("--agent", "-a", default="127.0.0.1",
                   help="Host running the simulator (default: 127.0.0.1)")
    p.add_argument("--port", "-p", type=int, default=1161,
                   help="Management agent UDP port for SET (default: 1161)")
    p.add_argument("--snmp-port", type=int, default=1611,
                   help="Main snmpsim port used by --verify (default: 1611)")
    p.add_argument("--timeout", "-t", type=float, default=5.0,
                   help="Request timeout in seconds (default: 5)")
    p.add_argument("--set", nargs=2, action="append", metavar=("FIELD", "VALUE"),
                   dest="set_fields", default=[],
                   help="Set a field by name (e.g. --set sysName MyDevice)")
    p.add_argument("--set-oid", nargs=2, action="append", metavar=("OID", "VALUE"),
                   dest="set_oids", default=[],
                   help="Set a field by raw OID")
    p.add_argument("--verify", "-v", action="store_true",
                   help="After SET, confirm MIB-II values via snmpsim port (--snmp-port)")
    p.add_argument("--list-oids", action="store_true",
                   help="Print the full OID/field table and exit")
    return p.parse_args()


# ── Entry point ───────────────────────────────────────────────────────────────

def _main(args: argparse.Namespace) -> int:
    if args.list_oids:
        _print_oid_table()
        return 0

    if not args.ips:
        print(red("Error: at least one device IP required."))
        print(f"  Run {bold('python test_snmp_set.py --list-oids')} to see all fields.")
        return 1

    sets: List[Tuple[str, str, str]] = []  # (oid, value, type)

    for field_name, value in args.set_fields:
        oid_str = _FIELD_TO_OID.get(field_name)
        if oid_str is None:
            print(red(f"Unknown field '{field_name}'."))
            print(f"  Run {bold('python test_snmp_set.py --list-oids')} to see all field names.")
            return 1
        typ = _ALL_OID_INFO[oid_str][1]
        if typ == "int":
            try:
                int(value)
            except ValueError:
                print(red(f"Field '{field_name}' requires an integer value, got '{value}'"))
                return 1
        sets.append((oid_str, value, typ))

    for oid_str, value in args.set_oids:
        info = _ALL_OID_INFO.get(oid_str)
        if info is None:
            print(red(f"Unknown OID '{oid_str}'."))
            print(f"  Run {bold('python test_snmp_set.py --list-oids')} to see all OIDs.")
            return 1
        _, typ, _ = info
        if typ == "int":
            try:
                int(value)
            except ValueError:
                print(red(f"OID {oid_str} requires an integer value, got '{value}'"))
                return 1
        sets.append((oid_str, value, typ))

    mode = f"SET {len(sets)} field(s)" if sets else "WALK"
    print(bold(f"\ndataCenter SNMP SET Tester  —  {mode}  ({len(args.ips)} device(s))"))
    print(grey(f"mgmt={args.agent}:{args.port}  snmpsim={args.agent}:{args.snmp_port}"
               f"  community=<device-ip>  timeout={args.timeout}s"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", 0))
    sock.settimeout(args.timeout)

    rc = 0
    try:
        for ip in args.ips:
            ok = _run_device(sock, ip, args.agent, args.port, args.snmp_port,
                             sets, args.verify)
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
