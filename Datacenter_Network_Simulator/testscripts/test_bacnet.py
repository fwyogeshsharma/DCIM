#!/usr/bin/env python3
"""
test_bacnet.py  —  BACnet/IP Poll & Subscribe Tester
=====================================================
Poll all or specific objects, subscribe for COV, watch live events.

Usage
-----
  # Poll all objects once
  python test_bacnet.py 192.168.1.212 --instance 40001

  # Poll a specific object
  python test_bacnet.py 192.168.1.212 --instance 40001 --object AI:2001

  # Poll multiple specific objects
  python test_bacnet.py 192.168.1.212 --instance 40001 --object AI:2001 --object BI:1020

  # Poll all objects repeatedly every 30s
  python test_bacnet.py 192.168.1.212 --instance 40001 --loop --interval 30

  # Subscribe all objects, print live COV events
  python test_bacnet.py 192.168.1.212 --instance 40001 --subscribe

  # Subscribe a single object only
  python test_bacnet.py 192.168.1.212 --instance 40001 --subscribe --object AI:2001

  # Subscribe multiple specific objects
  python test_bacnet.py 192.168.1.212 --instance 40001 --subscribe --object AI:2001 --object AI:3001

  # Subscribe with custom lifetime
  python test_bacnet.py 192.168.1.212 --instance 40001 --subscribe --lifetime 300
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.bacnet_object_model import (
    BACNET_PORT,
    OBJ_DEVICE, OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT,
    OBJ_ANALOG_OUTPUT, OBJ_BINARY_OUTPUT, OBJ_ANALOG_VALUE, OBJ_BINARY_VALUE,
    PROP_OBJECT_NAME, PROP_OBJECT_LIST, PROP_PRESENT_VALUE, PROP_UNITS,
    PDU_CONFIRMED_REQUEST, PDU_UNCONFIRMED_REQUEST,
    PDU_SIMPLE_ACK, PDU_COMPLEX_ACK, PDU_ERROR,
    SVC_READ_PROPERTY, SVC_READ_PROPERTY_MULTIPLE,
    SVC_SUBSCRIBE_COV, SVC_UNCONFIRMED_COV_NOTIFICATION,
    build_bvll, build_npdu,
    enc_ctx_uint, enc_ctx_oid, enc_ctx_open, enc_ctx_close,
    parse_bvll, parse_npdu, parse_apdu,
    _decode_tag_hdr, decode_uint, decode_oid_bytes,
)

# ── Colour helpers ────────────────────────────────────────────────────────────

_USE_COLOUR = sys.stdout.isatty()
def _c(t, code): return f"\033[{code}m{t}\033[0m" if _USE_COLOUR else t
def grn(t):  return _c(t, "92")
def red(t):  return _c(t, "91")
def ylw(t):  return _c(t, "93")
def cyn(t):  return _c(t, "96")
def bold(t): return _c(t, "1")
def dim(t):  return _c(t, "2")

# ── Object type table ─────────────────────────────────────────────────────────

_OBJ_BY_NAME = {
    "AI": OBJ_ANALOG_INPUT,  "AO": OBJ_ANALOG_OUTPUT,  "AV": OBJ_ANALOG_VALUE,
    "BI": OBJ_BINARY_INPUT,  "BO": OBJ_BINARY_OUTPUT,  "BV": OBJ_BINARY_VALUE,
    "DEV": OBJ_DEVICE,
}
_OBJ_TO_NAME = {v: k for k, v in _OBJ_BY_NAME.items()}
_OBJ_TO_NAME[OBJ_DEVICE] = "DEV"

_UNIT_CODES = {
    3: "A", 5: "V", 47: "W", 48: "kW", 19: "kWh", 27: "Hz",
    98: "%", 29: "°C", 95: "",
}

# ── Invoke-ID counter ─────────────────────────────────────────────────────────

_iid = 0
def _next_iid() -> int:
    global _iid
    _iid = (_iid + 1) & 0xFF
    return _iid

# ── Argument parsing ──────────────────────────────────────────────────────────

def _parse_object_arg(s: str) -> Tuple[int, int]:
    """Parse 'AI:2001' → (OBJ_ANALOG_INPUT, 2001). Case-insensitive."""
    s = s.strip().upper()
    if ":" not in s:
        raise argparse.ArgumentTypeError(f"Bad object spec {s!r}  use TYPE:INSTANCE e.g. AI:2001")
    prefix, inst_s = s.split(":", 1)
    ot = _OBJ_BY_NAME.get(prefix)
    if ot is None:
        raise argparse.ArgumentTypeError(
            f"Unknown type {prefix!r}  valid: {', '.join(_OBJ_BY_NAME)}")
    try:
        return ot, int(inst_s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Instance must be integer, got {inst_s!r}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="test_bacnet.py",
        description="BACnet/IP poll and subscribe tester.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.split("\n")[10:]),
    )
    p.add_argument("host", help="Device IP address")
    p.add_argument("--instance", "-i", type=int, default=40001,
                   help="BACnet device instance (default: 40001)")
    p.add_argument("--port", "-p", type=int, default=BACNET_PORT,
                   help=f"UDP port (default: {BACNET_PORT})")
    p.add_argument("--timeout", "-t", type=float, default=5.0,
                   help="Request timeout seconds (default: 5)")
    p.add_argument("--object", "-o", dest="objects", type=_parse_object_arg,
                   action="append", default=[],
                   metavar="TYPE:INST",
                   help="Object to poll/subscribe e.g. AI:2001  (repeatable; default: all)")
    p.add_argument("--subscribe", "-s", action="store_true",
                   help="Subscribe for COV and print live events")
    p.add_argument("--lifetime", type=int, default=600,
                   help="COV subscription lifetime seconds (default: 600)")
    p.add_argument("--loop", "-l", action="store_true",
                   help="Poll mode: keep polling until Ctrl+C")
    p.add_argument("--interval", type=int, default=30,
                   help="Poll loop interval seconds (default: 30)")
    return p.parse_args()


# ── UDP helpers ───────────────────────────────────────────────────────────────

def _udp_send_recv(sock: socket.socket, host: str, port: int,
                   pkt: bytes, timeout: float) -> Optional[dict]:
    sock.sendto(pkt, (host, port))
    deadline = time.monotonic() + timeout
    old = sock.gettimeout()
    sock.settimeout(0.3)
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if addr[0] != host:
                continue
            _, npdu = parse_bvll(data)
            if npdu is None:
                continue
            apdu_b = parse_npdu(npdu)
            if apdu_b is None:
                continue
            pdu = parse_apdu(apdu_b)
            if pdu:
                pdu["_raw"] = apdu_b
                return pdu
    finally:
        sock.settimeout(old)
    return None


def _drain_cov_packets(sock: socket.socket, host: str,
                       timeout: float) -> List[bytes]:
    """Collect all COV notification payloads within timeout."""
    results = []
    deadline = time.monotonic() + timeout
    old = sock.gettimeout()
    sock.settimeout(0.1)
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if addr[0] != host:
                continue
            _, npdu = parse_bvll(data)
            if npdu is None:
                continue
            apdu_b = parse_npdu(npdu)
            if apdu_b is None:
                continue
            pdu = parse_apdu(apdu_b)
            if (pdu and pdu["type"] == "unconfirmed" and
                    pdu["service"] == SVC_UNCONFIRMED_COV_NOTIFICATION):
                results.append(pdu["data"])
    finally:
        sock.settimeout(old)
    return results


# ── BACnet PDU builders ───────────────────────────────────────────────────────

def _build_rp(iid: int, obj_type: int, obj_inst: int, prop_id: int) -> bytes:
    apdu = bytes([(PDU_CONFIRMED_REQUEST << 4), 0x05, iid & 0xFF, SVC_READ_PROPERTY])
    apdu += enc_ctx_oid(0, obj_type, obj_inst)
    apdu += enc_ctx_uint(1, prop_id)
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_rpm(iid: int, reqs: List[Tuple[int, int, List[int]]]) -> bytes:
    apdu = bytes([(PDU_CONFIRMED_REQUEST << 4), 0x05, iid & 0xFF, SVC_READ_PROPERTY_MULTIPLE])
    for obj_type, obj_inst, prop_ids in reqs:
        apdu += enc_ctx_oid(0, obj_type, obj_inst)
        apdu += enc_ctx_open(1)
        for pid in prop_ids:
            apdu += enc_ctx_uint(0, pid)
        apdu += enc_ctx_close(1)
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_subscribe(iid: int, process_id: int, obj_type: int, obj_inst: int,
                     lifetime: int) -> bytes:
    apdu = bytes([(PDU_CONFIRMED_REQUEST << 4), 0x05, iid & 0xFF, SVC_SUBSCRIBE_COV])
    apdu += enc_ctx_uint(0, process_id)
    apdu += enc_ctx_oid(1, obj_type, obj_inst)
    apdu += bytes([(2 << 4) | 0x08 | 1, 0x00])   # unconfirmed notifications
    apdu += enc_ctx_uint(3, lifetime)
    return build_bvll(build_npdu(apdu, expects_reply=True))


# ── Decoders ─────────────────────────────────────────────────────────────────

def _decode_real(raw: bytes) -> Optional[float]:
    if raw and raw[0] == 0x44 and len(raw) >= 5:
        return struct.unpack(">f", raw[1:5])[0]
    return None


def _decode_uint_app(raw: bytes) -> Optional[int]:
    if not raw:
        return None
    try:
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, 0)
        return decode_uint(raw, pos, vlen)
    except Exception:
        return None


def _decode_string(raw: bytes) -> Optional[str]:
    if not raw:
        return None
    try:
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, 0)
        return raw[pos + 1: pos + vlen].decode("utf-8", errors="replace")
    except Exception:
        return None


def _parse_complex_ack_value(payload: bytes) -> Optional[bytes]:
    """Extract the value blob from inside a ComplexAck for ReadProperty."""
    try:
        pos = 0
        while pos < len(payload):
            tag, ic, io, icl, vlen, npos = _decode_tag_hdr(payload, pos)
            if ic and tag == 3 and io:
                pos = npos
                start = pos
                depth = 1
                while pos < len(payload) and depth > 0:
                    t2, ic2, io2, icl2, vl2, np2 = _decode_tag_hdr(payload, pos)
                    if ic2 and io2:
                        depth += 1
                    elif ic2 and icl2:
                        depth -= 1
                    pos = np2 + (0 if (io2 or icl2) else vl2)
                return payload[start: pos - (1 if depth == 0 else 0)]
            else:
                pos = npos + vlen
    except Exception:
        pass
    return None


def _parse_rpm_ack(payload: bytes) -> List[Dict]:
    results = []
    pos = 0
    try:
        while pos < len(payload):
            tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
            if vlen < 4:
                break
            obj_type, obj_inst = decode_oid_bytes(payload[pos:pos + 4])
            pos += 4
            tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
            if not io:
                break
            props: Dict = {}
            while pos < len(payload):
                tag, ic, io, icl, vlen, _ = _decode_tag_hdr(payload, pos)
                if icl:
                    pos = _
                    break
                tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                if not io:
                    break
                tag, ic, io2, icl2, vlen, pos = _decode_tag_hdr(payload, pos)
                prop_id = decode_uint(payload, pos, vlen)
                pos += vlen
                tag, ic, io2, icl2, vlen2, npos = _decode_tag_hdr(payload, pos)
                if ic and io2:
                    if tag == 4:
                        pos = npos
                        start = pos
                        depth = 1
                        while pos < len(payload) and depth > 0:
                            t2, ic2, io3, icl3, vl2, np2 = _decode_tag_hdr(payload, pos)
                            if ic2 and io3:
                                depth += 1
                            elif ic2 and icl3:
                                depth -= 1
                            pos = np2 + (0 if (io3 or icl3) else vl2)
                        props[prop_id] = payload[start: pos - (1 if depth == 0 else 0)]
                    elif tag == 5:
                        pos = npos
                        while pos < len(payload):
                            t2, ic2, io3, icl3, vl2, np2 = _decode_tag_hdr(payload, pos)
                            pos = np2 + (0 if (io3 or icl3) else vl2)
                            if ic2 and icl3 and t2 == 5:
                                break
                        props[prop_id] = None
                else:
                    pos = npos + vlen2
                tag2, ic2, io3, icl3, vl3, pos = _decode_tag_hdr(payload, pos)
            results.append({"obj_type": obj_type, "obj_inst": obj_inst, "props": props})
    except Exception:
        pass
    return results


def _parse_cov_notification(payload: bytes) -> Optional[Tuple[int, int, Optional[float]]]:
    """
    Parse UnconfirmedCOVNotification payload.
    Returns (obj_type, obj_inst, present_value) or None.
    """
    try:
        pos = 0
        # ctx[0]: subscriber-process-id
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        pos += vlen
        # ctx[1]: initiating-device-id
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        pos += 4  # OID = 4 bytes
        # ctx[2]: monitored-object-id
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        obj_type, obj_inst = decode_oid_bytes(payload[pos: pos + 4])
        pos += 4
        # ctx[3]: time-remaining
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        pos += vlen
        # ctx[4] opening: list-of-values
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        # First property-value: present-value
        # ctx[2] opening
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        # ctx[0]: property-id
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        pos += vlen  # skip property-id uint
        # ctx[4] opening: value
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        # application-tagged value
        val_start = pos
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        raw_val = payload[val_start: pos + vlen]
        pv: Optional[float] = None
        if raw_val:
            r = _decode_real(raw_val)
            if r is not None:
                pv = r
            else:
                u = _decode_uint_app(raw_val)
                if u is not None:
                    pv = float(u)
        return obj_type, obj_inst, pv
    except Exception:
        return None


# ── Object-list discovery ─────────────────────────────────────────────────────

def _read_object_list(sock: socket.socket, host: str, port: int,
                      instance: int, timeout: float) -> List[Tuple[int, int]]:
    """Read Device.Object_List → list of (obj_type, obj_inst)."""
    iid = _next_iid()
    pkt = _build_rp(iid, OBJ_DEVICE, instance, PROP_OBJECT_LIST)
    pdu = _udp_send_recv(sock, host, port, pkt, timeout)
    if pdu is None or (pdu["_raw"][0] >> 4) != PDU_COMPLEX_ACK:
        return []
    raw = _parse_complex_ack_value(pdu["_raw"][3:])
    if not raw:
        return []
    objects = []
    i = 0
    while i + 5 <= len(raw):
        chunk = raw[i: i + 5]
        if len(chunk) == 5:
            ot, oi = decode_oid_bytes(chunk[1:5])
            objects.append((ot, oi))
        i += 5
    return objects


# ── Value formatting ──────────────────────────────────────────────────────────

def _fmt_value(pv: Optional[float], unit: str, is_binary: bool) -> str:
    if pv is None:
        return dim("---")
    if is_binary:
        v = int(pv)
        return (red("● active") if v else grn("○ inactive"))
    if unit == "A":
        return f"{pv:>9.3f} A"
    if unit in ("kW", "kWh"):
        return f"{pv:>12.3f} {unit}"
    if unit == "V":
        return f"{pv:>9.2f} V"
    if unit == "Hz":
        return f"{pv:>9.3f} Hz"
    if unit == "%":
        return f"{pv:>8.2f} %"
    if unit:
        return f"{pv:>10.4f} {unit}"
    return f"{pv:>10.4f}"


def _fmt_cov_delta(old: Optional[float], new: Optional[float], unit: str,
                   is_binary: bool) -> str:
    if old is None:
        return f"? → {_fmt_value(new, unit, is_binary)}"
    if is_binary:
        old_s = "● active" if int(old) else "○ inactive"
        new_s = "● active" if int(new) else "○ inactive"
        return f"{old_s} → {new_s}"
    old_s = _fmt_value(old, unit, False).strip()
    new_s = _fmt_value(new, unit, False).strip()
    return f"{old_s} → {grn(new_s)}"


# ── Poll ──────────────────────────────────────────────────────────────────────

_RPM_BATCH = 15   # objects per RPM request

def _poll_objects(sock: socket.socket, host: str, port: int,
                  instance: int, targets: List[Tuple[int, int]],
                  timeout: float) -> Dict[Tuple[int, int], Dict]:
    """
    Batch-read name, present_value, units for each target via RPM.
    Returns {(ot, oi): {"name": str, "pv": float|None, "unit": str}}.
    """
    results: Dict[Tuple[int, int], Dict] = {}

    for batch_start in range(0, len(targets), _RPM_BATCH):
        batch = targets[batch_start: batch_start + _RPM_BATCH]
        reqs = [(ot, oi, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE, PROP_UNITS])
                for ot, oi in batch]
        iid = _next_iid()
        pkt = _build_rpm(iid, reqs)
        pdu = _udp_send_recv(sock, host, port, pkt, timeout)
        if pdu is None or (pdu["_raw"][0] >> 4) != PDU_COMPLEX_ACK:
            continue
        items = _parse_rpm_ack(pdu["_raw"][3:])
        for item in items:
            ot, oi = item["obj_type"], item["obj_inst"]
            props = item["props"]
            name_raw = props.get(PROP_OBJECT_NAME)
            pv_raw   = props.get(PROP_PRESENT_VALUE)
            unit_raw = props.get(PROP_UNITS)
            name = _decode_string(name_raw) if name_raw else f"{_OBJ_TO_NAME.get(ot, str(ot))}:{oi}"
            pv = None
            if pv_raw is not None:
                pv = _decode_real(pv_raw)
                if pv is None:
                    u = _decode_uint_app(pv_raw)
                    if u is not None:
                        pv = float(u)
            unit_code = _decode_uint_app(unit_raw) if unit_raw else None
            unit = _UNIT_CODES.get(unit_code, "") if unit_code is not None else ""
            results[(ot, oi)] = {"name": name, "pv": pv, "unit": unit}
    return results


def _print_poll_table(results: Dict[Tuple[int, int], Dict],
                      targets: List[Tuple[int, int]], ts: str):
    col_w = 30
    print(f"\n{bold(f'  Poll results  {ts}  ({len(targets)} object(s))')}")
    print(f"  {'Object':<12}  {'Name':<{col_w}}  {'Value':>16}  Unit")
    print(f"  {'─'*12}  {'─'*col_w}  {'─'*16}  {'─'*6}")
    for ot, oi in targets:
        obj_id = f"{_OBJ_TO_NAME.get(ot, str(ot))}:{oi}"
        d = results.get((ot, oi))
        if d is None:
            print(f"  {obj_id:<12}  {dim('(no response)'):<{col_w}}")
            continue
        is_bin = ot in (OBJ_BINARY_INPUT, OBJ_BINARY_OUTPUT, OBJ_BINARY_VALUE)
        val_s = _fmt_value(d["pv"], d["unit"], is_bin)
        name = (d["name"] or "")[:col_w]
        print(f"  {cyn(obj_id):<21}  {name:<{col_w}}  {val_s:>26}  {dim(d['unit'])}")


# ── Subscribe ─────────────────────────────────────────────────────────────────

def _subscribe_objects(sock: socket.socket, host: str, port: int,
                       targets: List[Tuple[int, int]], lifetime: int,
                       timeout: float) -> int:
    """Send SubscribeCOV for each target. Returns count of successful acks."""
    ok = 0
    for i, (ot, oi) in enumerate(targets):
        iid = _next_iid()
        pkt = _build_subscribe(iid, i + 1, ot, oi, lifetime)
        pdu = _udp_send_recv(sock, host, port, pkt, timeout)
        if pdu and (pdu["_raw"][0] >> 4) == PDU_SIMPLE_ACK:
            ok += 1
    return ok


def _print_cov_line(ts: str, ot: int, oi: int, name: str,
                    old_pv: Optional[float], new_pv: Optional[float], unit: str):
    is_bin = ot in (OBJ_BINARY_INPUT, OBJ_BINARY_OUTPUT, OBJ_BINARY_VALUE)
    obj_id = f"{_OBJ_TO_NAME.get(ot, str(ot))}:{oi}"
    delta  = _fmt_cov_delta(old_pv, new_pv, unit, is_bin)
    print(f"  {dim(ts)}  {ylw('COV')}  {cyn(obj_id):<14}  {name:<30}  {delta}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    sock.settimeout(args.timeout)

    host     = args.host
    port     = args.port
    instance = args.instance

    # ── Resolve target objects ────────────────────────────────────────────────
    if args.objects:
        targets = list(args.objects)
        print(f"\n  Target: {bold(host)}:{port}  instance={instance}")
        print(f"  Objects: {len(targets)} specified\n")
    else:
        print(f"\n  Target: {bold(host)}:{port}  instance={instance}")
        print(f"  Discovering objects via Object_List…", end="", flush=True)
        all_objs = _read_object_list(sock, host, port, instance, args.timeout)
        if not all_objs:
            print(f"  {red('✗')}  No response — is the simulator running?")
            sock.close()
            sys.exit(1)
        # Exclude the Device object itself from data polls (no PV/Units)
        targets = [(ot, oi) for ot, oi in all_objs if ot != OBJ_DEVICE]
        print(f"  {grn(str(len(targets)))} objects found")

    # ── Subscribe mode ────────────────────────────────────────────────────────
    if args.subscribe:
        print(f"\n  Subscribing {len(targets)} object(s) (lifetime={args.lifetime}s)…",
              end="", flush=True)
        ok = _subscribe_objects(sock, host, port, targets, args.lifetime, args.timeout)
        print(f"  {grn(str(ok))}/{len(targets)} OK")

        if ok == 0:
            print(f"  {red('✗')}  No subscriptions accepted. Exiting.")
            sock.close()
            sys.exit(1)

        # Keep a live value store to compute old→new deltas
        live: Dict[Tuple[int, int], Dict] = {}
        # Seed with current values via poll
        print(f"  Seeding current values…", end="", flush=True)
        seed = _poll_objects(sock, host, port, instance, targets, args.timeout)
        live.update(seed)
        print(f"  done  ({len(seed)} values)\n")

        print(f"  {bold('Listening for COV events…')}  {dim('Ctrl+C to quit')}")
        print(f"  {'─'*76}")
        cov_count = 0

        try:
            while True:
                payloads = _drain_cov_packets(sock, host, timeout=0.5)
                for payload in payloads:
                    result = _parse_cov_notification(payload)
                    if result is None:
                        continue
                    ot, oi, new_pv = result
                    ts = time.strftime("%H:%M:%S")
                    key = (ot, oi)
                    old_info = live.get(key, {})
                    old_pv = old_info.get("pv")
                    name   = old_info.get("name", f"{_OBJ_TO_NAME.get(ot, str(ot))}:{oi}")
                    unit   = old_info.get("unit", "")
                    _print_cov_line(ts, ot, oi, name, old_pv, new_pv, unit)
                    # Update live store
                    if key not in live:
                        live[key] = {"name": name, "unit": unit}
                    live[key]["pv"] = new_pv
                    cov_count += 1
                time.sleep(0.5)
        except KeyboardInterrupt:
            print(f"\n\n  {dim(f'Stopped.  Total COV received: {cov_count}')}\n")

    # ── Poll mode ─────────────────────────────────────────────────────────────
    else:
        try:
            while True:
                ts = time.strftime("%H:%M:%S")
                results = _poll_objects(sock, host, port, instance, targets, args.timeout)
                _print_poll_table(results, targets, ts)
                if not args.loop:
                    break
                print(f"\n  {dim(f'Next poll in {args.interval}s  (Ctrl+C to quit)')}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print()

    sock.close()


if __name__ == "__main__":
    main()
