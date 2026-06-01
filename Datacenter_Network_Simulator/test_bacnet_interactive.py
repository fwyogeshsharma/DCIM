#!/usr/bin/env python3
"""
test_bacnet_interactive.py — Interactive BACnet/IP Service Tester
==================================================================

Menu-driven tool to test every BACnet service the simulator supports,
with ALL possible scenarios per service (happy-path + error conditions).

Usage
-----
    python test_bacnet_interactive.py
    python test_bacnet_interactive.py --host 192.168.1.212 --instance 40001
    python test_bacnet_interactive.py --host 192.168.1.212 --instance 40001 --verbose

Services covered
----------------
  1  Who-Is / I-Am          (broadcast, unicast, range, miss, custom)
  2  ReadProperty            (device props, AI/BI values, array index, errors)
  3  ReadPropertyMultiple    (batch, mixed types, error within result)
  4  SubscribeCOV            (unconfirmed/confirmed, lifetime, renewal, cancel)
  5  COV Notification        (wait for push, verify payload, multi-subscription)
  6  Error responses         (unknown-object, unknown-property, wrong-type)
  7  Reject / Abort          (malformed APDU, empty payload, bad service code)
  8  Device Discovery        (broadcast scan, instance-range scan)
  9  Custom / Raw            (user-specified object/property, raw hex send)
"""
from __future__ import annotations

import argparse
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))

from core.bacnet_object_model import (
    BACNET_PORT, BVLL_TYPE,
    BVLC_ORIGINAL_BROADCAST, BVLC_ORIGINAL_UNICAST,
    OBJ_DEVICE, OBJ_ANALOG_INPUT, OBJ_BINARY_INPUT,
    OBJ_ANALOG_OUTPUT, OBJ_BINARY_OUTPUT, OBJ_ANALOG_VALUE, OBJ_BINARY_VALUE,
    PROP_OBJECT_IDENTIFIER, PROP_OBJECT_NAME, PROP_OBJECT_LIST,
    PROP_PRESENT_VALUE, PROP_DESCRIPTION, PROP_UNITS,
    PROP_STATUS_FLAGS, PROP_RELIABILITY, PROP_MODEL_NAME,
    PROP_VENDOR_NAME, PROP_FIRMWARE_REVISION, PROP_VENDOR_IDENTIFIER,
    PROP_PROTOCOL_VERSION, PROP_PROTOCOL_REVISION,
    PROP_MAX_APDU_LENGTH_ACCEPTED, PROP_SEGMENTATION_SUPPORTED,
    PROP_SYSTEM_STATUS, PROP_COV_INCREMENT, PROP_OUT_OF_SERVICE,
    PROP_APPLICATION_SOFTWARE_VERSION,
    PDU_UNCONFIRMED_REQUEST, PDU_CONFIRMED_REQUEST,
    PDU_SIMPLE_ACK, PDU_COMPLEX_ACK, PDU_ERROR, PDU_REJECT, PDU_ABORT,
    SVC_WHO_IS, SVC_I_AM, SVC_READ_PROPERTY, SVC_READ_PROPERTY_MULTIPLE,
    SVC_SUBSCRIBE_COV, SVC_UNCONFIRMED_COV_NOTIFICATION,
    ERR_CLASS_OBJECT, ERR_CLASS_PROPERTY,
    ERR_CODE_UNKNOWN_OBJECT, ERR_CODE_UNKNOWN_PROPERTY,
    build_bvll, build_npdu,
    enc_app_uint, enc_app_oid,
    enc_ctx_uint, enc_ctx_oid, enc_ctx_open, enc_ctx_close,
    parse_bvll, parse_npdu, parse_apdu,
    _decode_tag_hdr, decode_uint, decode_oid_bytes,
)

# ─────────────────────────────────────────────────────────────────────────────
#  ANSI colours
# ─────────────────────────────────────────────────────────────────────────────

_G  = "\033[92m"   # green
_R  = "\033[91m"   # red
_Y  = "\033[93m"   # yellow
_C  = "\033[96m"   # cyan
_M  = "\033[95m"   # magenta
_B  = "\033[94m"   # blue
_W  = "\033[97m"   # white
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RST = "\033[0m"

PROP_NAMES = {
    PROP_OBJECT_IDENTIFIER: "Object_Identifier",
    PROP_OBJECT_NAME: "Object_Name",
    PROP_DESCRIPTION: "Description",
    PROP_PRESENT_VALUE: "Present_Value",
    PROP_UNITS: "Units",
    PROP_STATUS_FLAGS: "Status_Flags",
    PROP_RELIABILITY: "Reliability",
    PROP_OUT_OF_SERVICE: "Out_Of_Service",
    PROP_COV_INCREMENT: "COV_Increment",
    PROP_MODEL_NAME: "Model_Name",
    PROP_VENDOR_NAME: "Vendor_Name",
    PROP_VENDOR_IDENTIFIER: "Vendor_Identifier",
    PROP_FIRMWARE_REVISION: "Firmware_Revision",
    PROP_APPLICATION_SOFTWARE_VERSION: "App_SW_Version",
    PROP_PROTOCOL_VERSION: "Protocol_Version",
    PROP_PROTOCOL_REVISION: "Protocol_Revision",
    PROP_MAX_APDU_LENGTH_ACCEPTED: "Max_APDU_Length",
    PROP_SEGMENTATION_SUPPORTED: "Segmentation_Supported",
    PROP_SYSTEM_STATUS: "System_Status",
    PROP_OBJECT_LIST: "Object_List",
}
PROP_NAMES = {k: v for k, v in PROP_NAMES.items() if isinstance(v, str)}

OBJ_TYPE_NAMES = {
    OBJ_ANALOG_INPUT: "Analog-Input",
    OBJ_ANALOG_OUTPUT: "Analog-Output",
    OBJ_ANALOG_VALUE: "Analog-Value",
    OBJ_BINARY_INPUT: "Binary-Input",
    OBJ_BINARY_OUTPUT: "Binary-Output",
    OBJ_BINARY_VALUE: "Binary-Value",
    OBJ_DEVICE: "Device",
}

ERR_CLASS_NAMES = {0: "Device", 1: "Object", 2: "Property", 3: "Resources",
                   4: "Security", 5: "Services", 6: "VT", 7: "Communication"}
ERR_CODE_NAMES  = {
    31: "unknown-object", 32: "unknown-property", 36: "unsupported-object-type",
    37: "value-out-of-range", 9: "unrecognised-service", 48: "invalid-array-index",
}
REJECT_REASON_NAMES = {
    0: "other", 1: "buffer-overflow", 2: "inconsistent-parameters",
    3: "invalid-parameter-data-type", 4: "invalid-tag", 5: "missing-required-parameter",
    6: "parameter-out-of-range", 7: "too-many-arguments", 8: "undefined-enumeration",
    9: "unrecognised-service",
}

# ─────────────────────────────────────────────────────────────────────────────
#  PDU builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_who_is(low: int = 0, high: int = 4_194_302,
                  broadcast: bool = True) -> bytes:
    apdu = bytes([(PDU_UNCONFIRMED_REQUEST << 4), SVC_WHO_IS])
    apdu += enc_ctx_uint(0, low)
    apdu += enc_ctx_uint(1, high)
    return build_bvll(build_npdu(apdu), broadcast=broadcast)


def _build_who_is_global(broadcast: bool = True) -> bytes:
    """Who-Is with no range (all devices respond)."""
    apdu = bytes([(PDU_UNCONFIRMED_REQUEST << 4), SVC_WHO_IS])
    return build_bvll(build_npdu(apdu), broadcast=broadcast)


def _build_read_property(iid: int, obj_type: int, obj_inst: int,
                         prop_id: int,
                         array_index: Optional[int] = None) -> bytes:
    apdu = bytes([
        (PDU_CONFIRMED_REQUEST << 4),
        0x05,
        iid & 0xFF,
        SVC_READ_PROPERTY,
    ])
    apdu += enc_ctx_oid(0, obj_type, obj_inst)
    apdu += enc_ctx_uint(1, prop_id)
    if array_index is not None:
        apdu += enc_ctx_uint(2, array_index)
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_read_property_multiple(
        iid: int,
        requests: List[Tuple[int, int, List[int]]],
) -> bytes:
    apdu = bytes([
        (PDU_CONFIRMED_REQUEST << 4),
        0x05,
        iid & 0xFF,
        SVC_READ_PROPERTY_MULTIPLE,
    ])
    for obj_type, obj_inst, prop_ids in requests:
        apdu += enc_ctx_oid(0, obj_type, obj_inst)
        apdu += enc_ctx_open(1)
        for pid in prop_ids:
            apdu += enc_ctx_uint(0, pid)
        apdu += enc_ctx_close(1)
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_subscribe_cov(iid: int, process_id: int,
                         obj_type: int, obj_inst: int,
                         confirmed: bool = False,
                         lifetime: int = 60) -> bytes:
    apdu = bytes([
        (PDU_CONFIRMED_REQUEST << 4),
        0x05,
        iid & 0xFF,
        SVC_SUBSCRIBE_COV,
    ])
    apdu += enc_ctx_uint(0, process_id)
    apdu += enc_ctx_oid(1, obj_type, obj_inst)
    if lifetime > 0:
        apdu += bytes([(2 << 4) | 0x08 | 1, 0x01 if confirmed else 0x00])
        apdu += enc_ctx_uint(3, lifetime)
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_cancel_cov(iid: int, process_id: int,
                      obj_type: int, obj_inst: int) -> bytes:
    apdu = bytes([
        (PDU_CONFIRMED_REQUEST << 4),
        0x05,
        iid & 0xFF,
        SVC_SUBSCRIBE_COV,
    ])
    apdu += enc_ctx_uint(0, process_id)
    apdu += enc_ctx_oid(1, obj_type, obj_inst)
    # No ctx[2]/ctx[3] → cancellation
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_malformed_empty_apdu() -> bytes:
    """ConfirmedRequest with only header bytes, no service payload."""
    apdu = bytes([(PDU_CONFIRMED_REQUEST << 4), 0x05, 0x7F, SVC_READ_PROPERTY])
    # Deliberately omit required object-identifier tag
    return build_bvll(build_npdu(apdu, expects_reply=True))


def _build_zero_length_apdu() -> bytes:
    """A BVLL frame with valid header but empty NPDU/APDU."""
    return build_bvll(build_npdu(b'', expects_reply=False))


def _build_bad_service_code(iid: int) -> bytes:
    """ConfirmedRequest with a nonexistent service code (0xFE)."""
    apdu = bytes([(PDU_CONFIRMED_REQUEST << 4), 0x05, iid & 0xFF, 0xFE])
    return build_bvll(build_npdu(apdu, expects_reply=True))


# ─────────────────────────────────────────────────────────────────────────────
#  Receive helpers
# ─────────────────────────────────────────────────────────────────────────────

def _recv_apdu(sock: socket.socket, host: str,
               timeout: float) -> Optional[dict]:
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
                pdu['_raw'] = apdu_b
                pdu['_src'] = addr
                return pdu
    finally:
        sock.settimeout(old)
    return None


def _recv_any(sock: socket.socket, timeout: float,
              filter_host: Optional[str] = None) -> Optional[Tuple[str, dict]]:
    deadline = time.monotonic() + timeout
    old = sock.gettimeout()
    sock.settimeout(0.3)
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if filter_host and addr[0] != filter_host:
                continue
            _, npdu = parse_bvll(data)
            if npdu is None:
                continue
            apdu_b = parse_npdu(npdu)
            if apdu_b is None:
                continue
            pdu = parse_apdu(apdu_b)
            if pdu:
                pdu['_raw'] = apdu_b
                pdu['_src'] = addr
                return addr[0], pdu
    finally:
        sock.settimeout(old)
    return None


def _flush(sock: socket.socket):
    old = sock.gettimeout()
    sock.settimeout(0)
    try:
        while True:
            sock.recvfrom(4096)
    except (socket.timeout, BlockingIOError, OSError):
        pass
    finally:
        sock.settimeout(old)


# ─────────────────────────────────────────────────────────────────────────────
#  Decode helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_iam(payload: bytes) -> Optional[Dict]:
    try:
        pos = 0
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        if vlen != 4:
            return None
        obj_type, instance = decode_oid_bytes(payload[pos:pos + 4])
        pos += 4
        if obj_type != OBJ_DEVICE:
            return None
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        max_apdu = decode_uint(payload, pos, vlen); pos += vlen
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        pos += vlen  # skip segmentation
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
        vendor_id = decode_uint(payload, pos, vlen)
        return {'instance': instance, 'max_apdu': max_apdu, 'vendor_id': vendor_id}
    except Exception:
        return None


def _parse_complex_ack_value(payload: bytes) -> Optional[bytes]:
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
                return payload[start:pos - (1 if depth == 0 else 0)]
            else:
                pos = npos + vlen
        return None
    except Exception:
        return None


def _decode_string(raw: bytes) -> Optional[str]:
    if not raw:
        return None
    try:
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, 0)
        return raw[pos + 1: pos + vlen].decode('utf-8', errors='replace')
    except Exception:
        return None


def _decode_real(raw: bytes) -> Optional[float]:
    if not raw or raw[0] != 0x44:
        return None
    return struct.unpack('>f', raw[1:5])[0]


def _decode_uint_app(raw: bytes) -> Optional[int]:
    if not raw:
        return None
    try:
        tag, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, 0)
        return decode_uint(raw, pos, vlen)
    except Exception:
        return None


def _parse_error(raw: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Parse Error PDU bytes[3:] → (err_class, err_code)."""
    try:
        pos = 3
        t, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, pos)
        err_class = decode_uint(raw, pos, vlen); pos += vlen
        t, ic, io, icl, vlen, pos = _decode_tag_hdr(raw, pos)
        err_code  = decode_uint(raw, pos, vlen)
        return err_class, err_code
    except Exception:
        return None, None


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
                    pos = _; break
                tag, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                if not io:
                    break
                tag, ic, io2, icl2, vlen, pos = _decode_tag_hdr(payload, pos)
                prop_id = decode_uint(payload, pos, vlen); pos += vlen
                tag, ic, io2, icl2, vlen2, npos = _decode_tag_hdr(payload, pos)
                if ic and io2:
                    if tag == 4:
                        pos = npos; start = pos; depth = 1
                        while pos < len(payload) and depth > 0:
                            t2, ic2, io3, icl3, vl2, np2 = _decode_tag_hdr(payload, pos)
                            if ic2 and io3: depth += 1
                            elif ic2 and icl3: depth -= 1
                            pos = np2 + (0 if (io3 or icl3) else vl2)
                        props[prop_id] = payload[start:pos - (1 if depth == 0 else 0)]
                    elif tag == 5:
                        pos = npos
                        while pos < len(payload):
                            t2, ic2, io3, icl3, vl2, np2 = _decode_tag_hdr(payload, pos)
                            pos = np2 + (0 if (io3 or icl3) else vl2)
                            if ic2 and icl3 and t2 == 5:
                                break
                        props[prop_id] = None  # error
                else:
                    pos = npos + vlen2
                tag2, ic2, io3, icl3, vl3, pos = _decode_tag_hdr(payload, pos)
            results.append({'obj_type': obj_type, 'obj_inst': obj_inst, 'props': props})
    except Exception:
        pass
    return results


def _pdu_type_name(raw: bytes) -> str:
    if not raw:
        return "empty"
    t = (raw[0] >> 4) & 0x0F
    return {
        PDU_CONFIRMED_REQUEST: "ConfirmedRequest",
        PDU_UNCONFIRMED_REQUEST: "UnconfirmedRequest",
        PDU_SIMPLE_ACK: "SimpleAck",
        PDU_COMPLEX_ACK: "ComplexAck",
        PDU_ERROR: "Error",
        PDU_REJECT: "Reject",
        PDU_ABORT: "Abort",
    }.get(t, f"PDU-type-{t}")


def _obj_name(t: int) -> str:
    return OBJ_TYPE_NAMES.get(t, f"type-{t}")


def _prop_name(p: int) -> str:
    return PROP_NAMES.get(p, f"prop-{p}")


def _err_class_name(c: int) -> str:
    return ERR_CLASS_NAMES.get(c, f"class-{c}")


def _err_code_name(c: int) -> str:
    return ERR_CODE_NAMES.get(c, f"code-{c}")


# ─────────────────────────────────────────────────────────────────────────────
#  Print helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok(label: str, detail: str = ""):
    s = f"  {_G}✓ PASS{_RST}  {label}"
    if detail:
        s += f"  {_DIM}{detail}{_RST}"
    print(s)


def _fail(label: str, detail: str = ""):
    s = f"  {_R}✗ FAIL{_RST}  {label}"
    if detail:
        s += f"  {_DIM}{detail}{_RST}"
    print(s)


def _info(label: str, detail: str = ""):
    s = f"  {_C}ℹ INFO{_RST}  {label}"
    if detail:
        s += f"  {_DIM}{detail}{_RST}"
    print(s)


def _warn(label: str, detail: str = ""):
    s = f"  {_Y}⚠ WARN{_RST}  {label}"
    if detail:
        s += f"  {_DIM}{detail}{_RST}"
    print(s)


def _hex(raw: bytes) -> str:
    return raw.hex(' ') if raw else "(empty)"


def _print_pdu(pdu: Optional[dict], verbose: bool = False):
    if pdu is None:
        _warn("No response (timeout)")
        return
    raw = pdu.get('_raw', b'')
    t = _pdu_type_name(raw)
    src = pdu.get('_src', ('?', 0))
    _info(f"Received {_BOLD}{t}{_RST}", f"from {src[0]}:{src[1]}")
    if pdu['type'] == 'error':
        ec, ecode = _parse_error(raw)
        if ec is not None:
            _info(f"  Error class={_err_class_name(ec)} code={_err_code_name(ecode)}")
    elif pdu['type'] == 'reject':
        reason = raw[2] if len(raw) > 2 else 0
        _info(f"  Reject reason={REJECT_REASON_NAMES.get(reason, reason)}")
    elif pdu['type'] == 'abort':
        reason = raw[2] if len(raw) > 2 else 0
        _info(f"  Abort reason={reason}")
    if verbose:
        _info(f"  Raw APDU: {_hex(raw)}")


def _header(title: str):
    bar = "─" * 70
    print(f"\n{_BOLD}{_C}{bar}{_RST}")
    print(f"{_BOLD}{_C}  {title}{_RST}")
    print(f"{_BOLD}{_C}{bar}{_RST}")


def _subheader(title: str):
    print(f"\n{_BOLD}{_B}  ▶ {title}{_RST}")


def _menu(title: str, options: List[Tuple[str, str]]) -> str:
    """Print a numbered menu and return user selection."""
    print(f"\n{_BOLD}{_M}  {title}{_RST}")
    for i, (key, label) in enumerate(options, 1):
        print(f"    {_Y}{i:2d}{_RST}  {label}")
    print(f"    {_Y} 0{_RST}  ← Back to main menu")
    while True:
        try:
            raw = input(f"\n  {_BOLD}Select [{0}-{len(options)}]:{_RST} ").strip()
        except (EOFError, KeyboardInterrupt):
            return "0"
        if raw == "0":
            return "0"
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1][0]
        print(f"  {_R}Invalid — enter 1–{len(options)} or 0{_RST}")


def _prompt(label: str, default: str = "") -> str:
    disp = f" [{default}]" if default else ""
    try:
        val = input(f"  {_C}{label}{disp}:{_RST} ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return val if val else default


def _prompt_int(label: str, default: int) -> int:
    raw = _prompt(label, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  Tester class — holds socket + state
# ─────────────────────────────────────────────────────────────────────────────

class BACnetTester:
    def __init__(self, host: str, instance: int, port: int,
                 broadcast: str, verbose: bool):
        self.host = host
        self.instance = instance
        self.port = port
        self.broadcast = broadcast
        self.verbose = verbose

        self._iid = [0]
        # Active subscriptions: list of {process_id, obj_type, obj_inst, lifetime}
        self.active_subs: List[Dict] = []

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.settimeout(3.0)
        self.sock.bind(("", 0))

    def close(self):
        self.sock.close()

    def next_iid(self) -> int:
        self._iid[0] = (self._iid[0] + 1) & 0xFF
        return self._iid[0]

    def send(self, pkt: bytes, dest: Optional[str] = None):
        target = dest or self.host
        self.sock.sendto(pkt, (target, self.port))

    def recv(self, timeout: float = 3.0) -> Optional[dict]:
        return _recv_apdu(self.sock, self.host, timeout)

    def recv_any(self, timeout: float = 3.0) -> Optional[Tuple[str, dict]]:
        return _recv_any(self.sock, timeout)

    def flush(self):
        _flush(self.sock)

    # ── Context display ───────────────────────────────────────────

    def print_context(self):
        print(f"\n  {_DIM}Target: {_W}{self.host}:{self.port}{_RST}  "
              f"{_DIM}Instance: {_W}{self.instance}{_RST}  "
              f"{_DIM}Verbose: {_W}{self.verbose}{_RST}")
        if self.active_subs:
            print(f"  {_DIM}Active COV subs: "
                  + ", ".join(f"{_obj_name(s['obj_type'])}:{s['obj_inst']}"
                              for s in self.active_subs)
                  + _RST)

    # ─────────────────────────────────────────────────────────────
    #  1. Who-Is / I-Am
    # ─────────────────────────────────────────────────────────────

    def menu_who_is(self):
        _header("Who-Is / I-Am Scenarios")
        options = [
            ("broadcast_global",  "Broadcast Who-Is (no range) — all devices respond"),
            ("broadcast_range",   "Broadcast Who-Is with instance range"),
            ("unicast_exact",     "Unicast Who-Is — exact instance match"),
            ("unicast_range_hit", "Unicast Who-Is — range that includes device"),
            ("unicast_range_miss","Unicast Who-Is — range that EXCLUDES device (expect silence)"),
            ("custom_range",      "Custom range — enter low/high manually"),
            ("collect_all",       "Collect all I-Am responses from broadcast (3 s scan)"),
        ]
        choice = _menu("Who-Is Scenarios", options)
        if choice == "0":
            return

        self.flush()

        if choice == "broadcast_global":
            _subheader("Broadcast Who-Is (global — no range)")
            pkt = _build_who_is_global(broadcast=True)
            self.send(pkt, self.broadcast)
            self._collect_iam(timeout=3.0, label="Global broadcast Who-Is")

        elif choice == "broadcast_range":
            _subheader("Broadcast Who-Is with range")
            low  = _prompt_int("Low instance", self.instance)
            high = _prompt_int("High instance", self.instance)
            pkt = _build_who_is(low, high, broadcast=True)
            self.send(pkt, self.broadcast)
            self._collect_iam(timeout=3.0, label=f"Broadcast Who-Is [{low}–{high}]")

        elif choice == "unicast_exact":
            _subheader(f"Unicast Who-Is — exact instance {self.instance}")
            pkt = _build_who_is(self.instance, self.instance, broadcast=False)
            self.send(pkt)
            result = self.recv_any(timeout=3.0)
            if result:
                src, pdu = result
                if pdu['type'] == 'unconfirmed' and pdu['service'] == SVC_I_AM:
                    info = _parse_iam(pdu['data'])
                    if info and info['instance'] == self.instance:
                        _ok(f"I-Am received from {src}",
                            f"instance={info['instance']} vendor={info['vendor_id']} max_apdu={info['max_apdu']}")
                    else:
                        _fail("I-Am for wrong instance",
                              f"got {info['instance'] if info else '?'} expected {self.instance}")
                else:
                    _fail("Unexpected PDU", f"type={pdu['type']} svc={pdu.get('service')}")
                    _print_pdu(pdu, self.verbose)
            else:
                _fail("No I-Am response (timeout)")

        elif choice == "unicast_range_hit":
            _subheader("Unicast Who-Is — range contains device")
            low  = max(0, self.instance - 5)
            high = self.instance + 5
            _info(f"Range [{low}–{high}] (contains {self.instance})")
            pkt = _build_who_is(low, high, broadcast=False)
            self.send(pkt)
            result = self.recv_any(timeout=3.0)
            if result:
                src, pdu = result
                if pdu['type'] == 'unconfirmed' and pdu['service'] == SVC_I_AM:
                    info = _parse_iam(pdu['data'])
                    _ok("I-Am received (device is in range)",
                        f"instance={info['instance'] if info else '?'} from {src}")
                else:
                    _fail("Unexpected PDU")
                    _print_pdu(pdu, self.verbose)
            else:
                _fail("No I-Am response (timeout)")

        elif choice == "unicast_range_miss":
            _subheader("Unicast Who-Is — range EXCLUDES device (silence expected)")
            # Range that can't include our device
            low  = 0
            high = max(0, self.instance - 1)
            if high < low:
                high = low
            _info(f"Range [{low}–{high}] (device {self.instance} is outside)")
            pkt = _build_who_is(low, high, broadcast=False)
            self.send(pkt)
            pdu = self.recv(timeout=2.0)
            if pdu is None:
                _ok("No I-Am (correct — device is outside range)")
            else:
                _fail("Got unexpected response (device should be silent)",
                      f"type={pdu['type']}")
                _print_pdu(pdu, self.verbose)

        elif choice == "custom_range":
            _subheader("Custom Who-Is range")
            low      = _prompt_int("Low instance", 0)
            high     = _prompt_int("High instance", 4194302)
            is_bcast = _prompt("Broadcast? (y/n)", "y").lower().startswith("y")
            dest     = self.broadcast if is_bcast else self.host
            pkt      = _build_who_is(low, high, broadcast=is_bcast)
            self.send(pkt, dest)
            self._collect_iam(timeout=3.0, label=f"Who-Is [{low}–{high}] {'bcast' if is_bcast else 'unicast'}")

        elif choice == "collect_all":
            _subheader("Collect all I-Am responses — 3 s broadcast scan")
            pkt = _build_who_is_global(broadcast=True)
            self.send(pkt, self.broadcast)
            self._collect_iam(timeout=3.0, label="Network-wide I-Am scan")

    def _collect_iam(self, timeout: float, label: str):
        deadline = time.monotonic() + timeout
        old = self.sock.gettimeout()
        self.sock.settimeout(0.3)
        seen = []
        try:
            while time.monotonic() < deadline:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                _, npdu = parse_bvll(data)
                if npdu is None:
                    continue
                apdu_b = parse_npdu(npdu)
                if apdu_b is None:
                    continue
                pdu = parse_apdu(apdu_b)
                if pdu and pdu['type'] == 'unconfirmed' and pdu['service'] == SVC_I_AM:
                    info = _parse_iam(pdu['data'])
                    if info:
                        seen.append((addr[0], info['instance'], info['vendor_id']))
        finally:
            self.sock.settimeout(old)
        if seen:
            _ok(f"{label} — {len(seen)} device(s) responded")
            for ip, inst, vid in seen:
                _info(f"  {ip}  instance={inst}  vendor_id={vid}")
        else:
            _fail(f"{label} — no I-Am received")

    # ─────────────────────────────────────────────────────────────
    #  2. ReadProperty
    # ─────────────────────────────────────────────────────────────

    def menu_read_property(self):
        _header("ReadProperty Scenarios")
        options = [
            ("dev_name",        "Device.Object_Name"),
            ("dev_model",       "Device.Model_Name"),
            ("dev_vendor",      "Device.Vendor_Name + Vendor_Identifier"),
            ("dev_firmware",    "Device.Firmware_Revision"),
            ("dev_objlist_cnt", "Device.Object_List[0] — count (array index 0)"),
            ("dev_objlist_n",   "Device.Object_List[n] — specific element"),
            ("dev_objlist_all", "Device.Object_List — full array (all OIDs)"),
            ("ai_pv",           "Analog-Input present value (panel or circuit)"),
            ("ai_units",        "Analog-Input units"),
            ("ai_cov_inc",      "Analog-Input COV_Increment"),
            ("bi_pv",           "Binary-Input present value (alarm)"),
            ("all_dev_props",   "All common Device properties (multi-shot)"),
            ("custom",          "Custom: enter obj_type/obj_inst/prop_id"),
            ("err_unknown_obj", "Error scenario: unknown object instance"),
            ("err_unknown_prop","Error scenario: unknown property ID"),
            ("err_array_oor",   "Error scenario: array index out of range"),
        ]
        choice = _menu("ReadProperty Scenarios", options)
        if choice == "0":
            return

        self.flush()

        if choice == "dev_name":
            self._rp_show("Device.Object_Name",
                          OBJ_DEVICE, self.instance, PROP_OBJECT_NAME,
                          decode=_decode_string)

        elif choice == "dev_model":
            self._rp_show("Device.Model_Name",
                          OBJ_DEVICE, self.instance, PROP_MODEL_NAME,
                          decode=_decode_string)

        elif choice == "dev_vendor":
            _subheader("Device vendor properties")
            self._rp_show("Device.Vendor_Name",
                          OBJ_DEVICE, self.instance, PROP_VENDOR_NAME,
                          decode=_decode_string)
            self._rp_show("Device.Vendor_Identifier",
                          OBJ_DEVICE, self.instance, PROP_VENDOR_IDENTIFIER,
                          decode=_decode_uint_app)

        elif choice == "dev_firmware":
            self._rp_show("Device.Firmware_Revision",
                          OBJ_DEVICE, self.instance, PROP_FIRMWARE_REVISION,
                          decode=_decode_string)

        elif choice == "dev_objlist_cnt":
            _subheader("Device.Object_List count")
            self._rp_show("Object_List[0] count",
                          OBJ_DEVICE, self.instance, PROP_OBJECT_LIST,
                          array_index=0, decode=_decode_uint_app)

        elif choice == "dev_objlist_n":
            n = _prompt_int("Array index (1-based)", 1)
            _subheader(f"Device.Object_List[{n}]")
            iid = self.next_iid()
            pkt = _build_read_property(iid, OBJ_DEVICE, self.instance,
                                       PROP_OBJECT_LIST, array_index=n)
            self.send(pkt)
            pdu = self.recv()
            if pdu and (pdu['_raw'][0] >> 4) == PDU_COMPLEX_ACK:
                raw = _parse_complex_ack_value(pdu['_raw'][3:])
                if raw and len(raw) >= 5:
                    ot, oi = decode_oid_bytes(raw[1:5])
                    _ok(f"Object_List[{n}]", f"→ {_obj_name(ot)}:{oi}")
                else:
                    _ok(f"Object_List[{n}]", f"raw={_hex(raw)}")
            elif pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                ec, ecode = _parse_error(pdu['_raw'])
                _info(f"Error: {_err_class_name(ec)} / {_err_code_name(ecode)}")
            else:
                _fail("No ComplexAck", _pdu_type_name(pdu['_raw']) if pdu else "timeout")
                _print_pdu(pdu, self.verbose)

        elif choice == "dev_objlist_all":
            _subheader("Device.Object_List (all — may be large)")
            iid = self.next_iid()
            pkt = _build_read_property(iid, OBJ_DEVICE, self.instance, PROP_OBJECT_LIST)
            self.send(pkt)
            pdu = self.recv(timeout=5.0)
            if pdu and (pdu['_raw'][0] >> 4) == PDU_COMPLEX_ACK:
                payload = pdu['_raw'][3:]
                raw = _parse_complex_ack_value(payload)
                if raw:
                    count = len(raw) // 5
                    _ok(f"Object_List", f"→ {count} object(s) in response")
                    # decode first 10
                    for i in range(min(count, 10)):
                        chunk = raw[i*5:(i+1)*5]
                        if len(chunk) >= 5:
                            ot, oi = decode_oid_bytes(chunk[1:5])
                            _info(f"  [{i+1}] {_obj_name(ot)}:{oi}")
                    if count > 10:
                        _info(f"  … and {count-10} more")
                else:
                    _ok("Object_List received (could not decode individual entries)")
                    if self.verbose:
                        _info(f"Raw: {_hex(pdu['_raw'][:64])}…")
            else:
                _fail("No ComplexAck", _pdu_type_name(pdu['_raw']) if pdu else "timeout")
                _print_pdu(pdu, self.verbose)

        elif choice == "ai_pv":
            _subheader("Analog-Input present value")
            print("  Common AI instances:")
            print("    1001 = Panel_Total_kW  1002 = Panel_Total_A  1003 = Panel_VA")
            print("    2001 = Ckt01_Current   3001 = Ckt02_Current")
            inst = _prompt_int("AI instance", 1001)
            self._rp_show(f"AI:{inst}.Present_Value",
                          OBJ_ANALOG_INPUT, inst, PROP_PRESENT_VALUE,
                          decode=_decode_real)

        elif choice == "ai_units":
            inst = _prompt_int("AI instance", 1001)
            self._rp_show(f"AI:{inst}.Units",
                          OBJ_ANALOG_INPUT, inst, PROP_UNITS,
                          decode=_decode_uint_app,
                          note="(3=A, 5=V, 47=W, 48=kW, 19=kWh, 27=Hz, 95=no-units)")

        elif choice == "ai_cov_inc":
            inst = _prompt_int("AI instance", 2001)
            self._rp_show(f"AI:{inst}.COV_Increment",
                          OBJ_ANALOG_INPUT, inst, PROP_COV_INCREMENT,
                          decode=_decode_real)

        elif choice == "bi_pv":
            _subheader("Binary-Input present value")
            print("  Common BI instances:")
            print("    1020 = Alarm_Overcurrent  1021 = Alarm_Undervoltage")
            print("    1022 = Alarm_Overvoltage  1023 = Alarm_HighTemp")
            print("    1024 = Alarm_HighTHD")
            inst = _prompt_int("BI instance", 1020)
            self._rp_show(f"BI:{inst}.Present_Value",
                          OBJ_BINARY_INPUT, inst, PROP_PRESENT_VALUE,
                          decode=_decode_uint_app,
                          note="(0=inactive, 1=active)")

        elif choice == "all_dev_props":
            _subheader("All common Device properties")
            props = [
                (PROP_OBJECT_NAME, "Object_Name", _decode_string),
                (PROP_MODEL_NAME, "Model_Name", _decode_string),
                (PROP_VENDOR_NAME, "Vendor_Name", _decode_string),
                (PROP_VENDOR_IDENTIFIER, "Vendor_ID", _decode_uint_app),
                (PROP_FIRMWARE_REVISION, "Firmware_Revision", _decode_string),
                (PROP_APPLICATION_SOFTWARE_VERSION, "App_SW_Version", _decode_string),
                (PROP_PROTOCOL_VERSION, "Protocol_Version", _decode_uint_app),
                (PROP_PROTOCOL_REVISION, "Protocol_Revision", _decode_uint_app),
                (PROP_MAX_APDU_LENGTH_ACCEPTED, "Max_APDU", _decode_uint_app),
                (PROP_SYSTEM_STATUS, "System_Status", _decode_uint_app),
            ]
            for pid, pname, dec in props:
                self._rp_show(f"Device.{pname}", OBJ_DEVICE, self.instance,
                              pid, decode=dec)

        elif choice == "custom":
            _subheader("Custom ReadProperty")
            print("  Object types: 0=AI  1=AO  2=AV  3=BI  4=BO  5=BV  8=Device")
            ot   = _prompt_int("Object type", OBJ_ANALOG_INPUT)
            oi   = _prompt_int("Object instance", self.instance if ot == OBJ_DEVICE else 1001)
            pid  = _prompt_int("Property ID", PROP_PRESENT_VALUE)
            ai_s = _prompt("Array index (blank=none)", "")
            ai   = int(ai_s) if ai_s.isdigit() else None
            label = f"{_obj_name(ot)}:{oi}.{_prop_name(pid)}"
            if ai is not None:
                label += f"[{ai}]"
            self._rp_show(label, ot, oi, pid, array_index=ai)

        elif choice == "err_unknown_obj":
            _subheader("Error: Unknown object instance")
            inst = _prompt_int("Non-existent instance", 999999)
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_ANALOG_INPUT, inst, PROP_PRESENT_VALUE))
            pdu = self.recv()
            if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                ec, ecode = _parse_error(pdu['_raw'])
                if ecode == ERR_CODE_UNKNOWN_OBJECT:
                    _ok(f"Correct Error: unknown-object",
                        f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
                else:
                    _warn(f"Error PDU but unexpected code",
                          f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
            elif pdu:
                _fail(f"Expected Error PDU", _pdu_type_name(pdu['_raw']))
                _print_pdu(pdu, self.verbose)
            else:
                _fail("Timeout — no response")

        elif choice == "err_unknown_prop":
            _subheader("Error: Unknown property ID")
            pid = _prompt_int("Non-existent property ID", 9876)
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_ANALOG_INPUT, 1001, pid))
            pdu = self.recv()
            if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                ec, ecode = _parse_error(pdu['_raw'])
                if ecode == ERR_CODE_UNKNOWN_PROPERTY:
                    _ok(f"Correct Error: unknown-property",
                        f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
                else:
                    _warn(f"Error PDU, unexpected code",
                          f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
            elif pdu:
                _fail(f"Expected Error PDU", _pdu_type_name(pdu['_raw']))
                _print_pdu(pdu, self.verbose)
            else:
                _fail("Timeout — no response")

        elif choice == "err_array_oor":
            _subheader("Error: Array index out of range")
            # First get count
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_DEVICE, self.instance,
                                           PROP_OBJECT_LIST, array_index=0))
            pdu = self.recv()
            count = None
            if pdu and (pdu['_raw'][0] >> 4) == PDU_COMPLEX_ACK:
                raw = _parse_complex_ack_value(pdu['_raw'][3:])
                count = _decode_uint_app(raw)
            oor_idx = (count + 10) if count else 999999
            _info(f"Object_List count={count}  Testing index={oor_idx} (out of range)")
            self.flush()
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_DEVICE, self.instance,
                                           PROP_OBJECT_LIST, array_index=oor_idx))
            pdu = self.recv()
            if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                ec, ecode = _parse_error(pdu['_raw'])
                _ok(f"Error for out-of-range array index",
                    f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
            elif pdu:
                _warn(f"Got {_pdu_type_name(pdu['_raw'])} — may be valid partial response")
                _print_pdu(pdu, self.verbose)
            else:
                _fail("Timeout — no response")

    def _rp_show(self, label: str, obj_type: int, obj_inst: int, prop_id: int,
                 array_index: Optional[int] = None, decode=None, note: str = ""):
        _subheader(f"ReadProperty {label}")
        iid = self.next_iid()
        pkt = _build_read_property(iid, obj_type, obj_inst, prop_id, array_index)
        self.send(pkt)
        pdu = self.recv()
        if pdu and (pdu['_raw'][0] >> 4) == PDU_COMPLEX_ACK and pdu['_raw'][1] == iid:
            raw = _parse_complex_ack_value(pdu['_raw'][3:])
            if decode and raw:
                val = decode(raw)
                suffix = f"  {_DIM}{note}{_RST}" if note else ""
                _ok(label, f"→ {val}{suffix}")
            else:
                _ok(label, f"raw={_hex(raw)}" if raw else "(empty value)")
            if self.verbose:
                _info(f"Raw APDU: {_hex(pdu['_raw'])}")
        elif pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
            ec, ecode = _parse_error(pdu['_raw'])
            _fail(label, f"Error: {_err_class_name(ec)} / {_err_code_name(ecode)}")
        else:
            _fail(label, _pdu_type_name(pdu['_raw']) if pdu else "timeout")
            _print_pdu(pdu, self.verbose)

    # ─────────────────────────────────────────────────────────────
    #  3. ReadPropertyMultiple
    # ─────────────────────────────────────────────────────────────

    def menu_rpm(self):
        _header("ReadPropertyMultiple Scenarios")
        options = [
            ("single_multi_prop",  "Single object, multiple properties"),
            ("multi_obj",          "Multiple objects in one request"),
            ("mixed_types",        "Device + AI + BI mixed in one request"),
            ("circuit_batch",      "5 circuit current objects (AI) in one request"),
            ("include_err_prop",   "Request includes unknown property (per-property error)"),
            ("include_err_obj",    "Request includes unknown object (whole-object error)"),
            ("all_circuits",       "All 42 circuit AI objects — large batch"),
            ("custom",             "Custom: enter object/property list"),
        ]
        choice = _menu("ReadPropertyMultiple Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "single_multi_prop":
            _subheader("Single Device object — multiple properties")
            reqs = [(OBJ_DEVICE, self.instance, [
                PROP_OBJECT_NAME, PROP_MODEL_NAME, PROP_VENDOR_NAME,
                PROP_FIRMWARE_REVISION, PROP_SYSTEM_STATUS
            ])]
            self._rpm_show("Device — 5 properties", reqs)

        elif choice == "multi_obj":
            _subheader("Multiple AI objects — name + present_value")
            reqs = [
                (OBJ_ANALOG_INPUT, 1001, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE, PROP_UNITS]),
                (OBJ_ANALOG_INPUT, 1002, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE, PROP_UNITS]),
                (OBJ_ANALOG_INPUT, 1003, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE, PROP_UNITS]),
            ]
            self._rpm_show("AI:1001+1002+1003", reqs)

        elif choice == "mixed_types":
            _subheader("Device + AI + BI — mixed object types")
            reqs = [
                (OBJ_DEVICE, self.instance, [PROP_OBJECT_NAME, PROP_MODEL_NAME]),
                (OBJ_ANALOG_INPUT, 2001, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE, PROP_UNITS]),
                (OBJ_BINARY_INPUT, 1020, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]),
            ]
            self._rpm_show("Device + AI:2001 + BI:1020", reqs)

        elif choice == "circuit_batch":
            _subheader("5 circuit current objects (Ckt01–Ckt05)")
            reqs = [
                (OBJ_ANALOG_INPUT, (ckt + 1) * 1000 + 1,
                 [PROP_OBJECT_NAME, PROP_PRESENT_VALUE])
                for ckt in range(1, 6)
            ]
            self._rpm_show("Ckt01–Ckt05 Current AI", reqs)

        elif choice == "include_err_prop":
            _subheader("Request with one unknown property — expect per-property error")
            reqs = [(OBJ_ANALOG_INPUT, 1001, [
                PROP_OBJECT_NAME,
                PROP_PRESENT_VALUE,
                9876,           # unknown property
                PROP_UNITS,
            ])]
            _info("Property 9876 is invalid — expect error entry for that slot")
            self._rpm_show("AI:1001 with bad property 9876", reqs,
                           expect_err_prop=9876)

        elif choice == "include_err_obj":
            _subheader("Request includes non-existent object")
            reqs = [
                (OBJ_ANALOG_INPUT, 1001, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]),
                (OBJ_ANALOG_INPUT, 999999, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]),
            ]
            _info("Object AI:999999 does not exist — expect error for that object")
            self._rpm_show("AI:1001 + AI:999999 (non-existent)", reqs)

        elif choice == "all_circuits":
            _subheader("All 42 circuit current objects — AI instances 2001–43001")
            _info("This sends a single large RPM request. Response may be large.")
            reqs = [
                (OBJ_ANALOG_INPUT, (ckt + 1) * 1000 + 1, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE])
                for ckt in range(1, 43)
            ]
            self._rpm_show("All 42 circuit currents", reqs, timeout=10.0)

        elif choice == "custom":
            _subheader("Custom RPM — enter object list")
            print("  Object types: 0=AI  1=AO  2=AV  3=BI  4=BO  5=BV  8=Device")
            reqs = []
            while True:
                ot = _prompt_int("Object type (or -1 to send)", OBJ_ANALOG_INPUT)
                if ot == -1:
                    break
                oi = _prompt_int("Object instance", 1001)
                pid_str = _prompt("Property IDs (comma-separated)", "77,85")
                try:
                    pids = [int(p.strip()) for p in pid_str.split(",")]
                except ValueError:
                    pids = [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]
                reqs.append((ot, oi, pids))
                _info(f"Added {_obj_name(ot)}:{oi} props={pids}")
                more = _prompt("Add another object? (y/n)", "n").lower()
                if not more.startswith("y"):
                    break
            if reqs:
                self._rpm_show(f"Custom RPM ({len(reqs)} objects)", reqs)
            else:
                _warn("No objects added")

    def _rpm_show(self, label: str,
                  reqs: List[Tuple[int, int, List[int]]],
                  expect_err_prop: Optional[int] = None,
                  timeout: float = 5.0):
        _subheader(f"RPM: {label}")
        iid = self.next_iid()
        pkt = _build_read_property_multiple(iid, reqs)
        self.send(pkt)
        pdu = self.recv(timeout=timeout)
        if pdu and (pdu['_raw'][0] >> 4) == PDU_COMPLEX_ACK and pdu['_raw'][1] == iid:
            items = _parse_rpm_ack(pdu['_raw'][3:])
            _ok(f"{label} → ComplexAck with {len(items)} object result(s)")
            for item in items:
                ot, oi = item['obj_type'], item['obj_inst']
                props = item['props']
                prop_summary = []
                for pid, raw in props.items():
                    if raw is None:
                        prop_summary.append(f"{_prop_name(pid)}=ERR")
                    elif pid == PROP_PRESENT_VALUE:
                        v = _decode_real(raw)
                        if v is not None:
                            prop_summary.append(f"PV={v:.3f}")
                        else:
                            u = _decode_uint_app(raw)
                            prop_summary.append(f"PV={u}")
                    elif pid == PROP_OBJECT_NAME:
                        prop_summary.append(f"name={_decode_string(raw)!r}")
                    elif pid == PROP_UNITS:
                        prop_summary.append(f"units={_decode_uint_app(raw)}")
                    else:
                        prop_summary.append(f"{_prop_name(pid)}=ok")
                _info(f"  {_obj_name(ot)}:{oi}  " + "  ".join(prop_summary))
            if expect_err_prop is not None:
                # Verify the expected error property appears as None
                found_err = any(
                    item['props'].get(expect_err_prop) is None
                    and expect_err_prop in item['props']
                    for item in items
                )
                if found_err:
                    _ok(f"Property {expect_err_prop} returned per-property error (expected)")
                else:
                    _warn(f"Expected per-property error for prop {expect_err_prop} — not seen")
            if self.verbose:
                _info(f"Raw APDU: {_hex(pdu['_raw'][:80])}…")
        elif pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
            ec, ecode = _parse_error(pdu['_raw'])
            _fail(label, f"Error: {_err_class_name(ec)} / {_err_code_name(ecode)}")
        else:
            _fail(label, _pdu_type_name(pdu['_raw']) if pdu else "timeout")
            _print_pdu(pdu, self.verbose)

    # ─────────────────────────────────────────────────────────────
    #  4. SubscribeCOV
    # ─────────────────────────────────────────────────────────────

    def menu_subscribe_cov(self):
        _header("SubscribeCOV Scenarios")
        options = [
            ("ai_unconfirmed",   "Subscribe AI (unconfirmed notifications, 60 s lifetime)"),
            ("ai_confirmed",     "Subscribe AI (confirmed notifications)"),
            ("bi_unconfirmed",   "Subscribe BI:1020 (unconfirmed, alarm object)"),
            ("custom_obj",       "Subscribe custom object / process-id / lifetime"),
            ("short_lifetime",   "Subscribe with short lifetime (10 s) — auto-expires"),
            ("renewal",          "Renew existing subscription (re-subscribe, same object)"),
            ("cancel",           "Cancel subscription (lifetime = 0)"),
            ("cancel_all",       "Cancel ALL active subscriptions"),
            ("err_unknown_obj",  "Subscribe non-existent object → Error"),
        ]
        choice = _menu("SubscribeCOV Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "ai_unconfirmed":
            self._subscribe_show(
                OBJ_ANALOG_INPUT, 2001, process_id=42,
                confirmed=False, lifetime=60,
                label="AI:2001 unconfirmed 60 s")

        elif choice == "ai_confirmed":
            self._subscribe_show(
                OBJ_ANALOG_INPUT, 2001, process_id=43,
                confirmed=True, lifetime=60,
                label="AI:2001 confirmed 60 s")

        elif choice == "bi_unconfirmed":
            self._subscribe_show(
                OBJ_BINARY_INPUT, 1020, process_id=50,
                confirmed=False, lifetime=60,
                label="BI:1020 unconfirmed 60 s")

        elif choice == "custom_obj":
            print("  Object types: 0=AI  3=BI")
            ot  = _prompt_int("Object type", OBJ_ANALOG_INPUT)
            oi  = _prompt_int("Object instance", 2001)
            pid = _prompt_int("Process ID (subscriber ID)", 99)
            lt  = _prompt_int("Lifetime (seconds)", 60)
            conf = _prompt("Confirmed notifications? (y/n)", "n").lower().startswith("y")
            self._subscribe_show(ot, oi, process_id=pid,
                                 confirmed=conf, lifetime=lt,
                                 label=f"{_obj_name(ot)}:{oi} pid={pid} lt={lt}")

        elif choice == "short_lifetime":
            self._subscribe_show(
                OBJ_ANALOG_INPUT, 2001, process_id=77,
                confirmed=False, lifetime=10,
                label="AI:2001 short lifetime 10 s")

        elif choice == "renewal":
            if not self.active_subs:
                _warn("No active subscriptions to renew",
                      "Subscribe first, then use renewal")
                return
            _info("Active subscriptions:")
            for i, s in enumerate(self.active_subs):
                print(f"    {i+1}  {_obj_name(s['obj_type'])}:{s['obj_inst']}"
                      f"  pid={s['process_id']}  lt={s['lifetime']} s")
            idx = _prompt_int("Select subscription (number)", 1) - 1
            if 0 <= idx < len(self.active_subs):
                s = self.active_subs[idx]
                new_lt = _prompt_int("New lifetime (seconds)", 120)
                self._subscribe_show(s['obj_type'], s['obj_inst'],
                                     process_id=s['process_id'],
                                     confirmed=False, lifetime=new_lt,
                                     label=f"Renewal {_obj_name(s['obj_type'])}:{s['obj_inst']}")
            else:
                _warn("Invalid selection")

        elif choice == "cancel":
            if not self.active_subs:
                _warn("No active subscriptions to cancel")
                return
            _info("Active subscriptions:")
            for i, s in enumerate(self.active_subs):
                print(f"    {i+1}  {_obj_name(s['obj_type'])}:{s['obj_inst']}"
                      f"  pid={s['process_id']}")
            idx = _prompt_int("Select subscription to cancel", 1) - 1
            if 0 <= idx < len(self.active_subs):
                s = self.active_subs[idx]
                self._cancel_sub(s['process_id'], s['obj_type'], s['obj_inst'])
                self.active_subs.pop(idx)
            else:
                _warn("Invalid selection")

        elif choice == "cancel_all":
            if not self.active_subs:
                _warn("No active subscriptions")
                return
            for s in list(self.active_subs):
                self._cancel_sub(s['process_id'], s['obj_type'], s['obj_inst'])
            self.active_subs.clear()
            _ok("All subscriptions cancelled")

        elif choice == "err_unknown_obj":
            _subheader("Subscribe non-existent object")
            iid = self.next_iid()
            self.send(_build_subscribe_cov(iid, 255, OBJ_ANALOG_INPUT, 999999,
                                          confirmed=False, lifetime=60))
            pdu = self.recv()
            if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                ec, ecode = _parse_error(pdu['_raw'])
                _ok("Error for non-existent object (expected)",
                    f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
            elif pdu and (pdu['_raw'][0] >> 4) == PDU_SIMPLE_ACK:
                _warn("Simulator accepted subscription to unknown object (no error)")
            else:
                _fail("Unexpected response", _pdu_type_name(pdu['_raw']) if pdu else "timeout")
                _print_pdu(pdu, self.verbose)

    def _subscribe_show(self, obj_type: int, obj_inst: int, process_id: int,
                        confirmed: bool, lifetime: int, label: str):
        _subheader(f"SubscribeCOV: {label}")
        iid = self.next_iid()
        pkt = _build_subscribe_cov(iid, process_id, obj_type, obj_inst,
                                   confirmed=confirmed, lifetime=lifetime)
        self.send(pkt)
        pdu = self.recv()
        if pdu and (pdu['_raw'][0] >> 4) == PDU_SIMPLE_ACK and pdu['_raw'][1] == iid:
            _ok(f"SubscribeCOV → SimpleAck", f"invoke_id={iid}")
            # Record in active subscriptions
            existing = next((s for s in self.active_subs
                             if s['process_id'] == process_id
                             and s['obj_type'] == obj_type
                             and s['obj_inst'] == obj_inst), None)
            if existing:
                existing['lifetime'] = lifetime
                _info("Updated existing subscription record")
            else:
                self.active_subs.append({
                    'process_id': process_id,
                    'obj_type': obj_type,
                    'obj_inst': obj_inst,
                    'lifetime': lifetime,
                    'confirmed': confirmed,
                })
        elif pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
            ec, ecode = _parse_error(pdu['_raw'])
            _fail(f"SubscribeCOV → Error",
                  f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
        else:
            _fail(f"SubscribeCOV → unexpected response",
                  _pdu_type_name(pdu['_raw']) if pdu else "timeout")
            _print_pdu(pdu, self.verbose)

    def _cancel_sub(self, process_id: int, obj_type: int, obj_inst: int):
        _subheader(f"Cancel {_obj_name(obj_type)}:{obj_inst} pid={process_id}")
        iid = self.next_iid()
        pkt = _build_cancel_cov(iid, process_id, obj_type, obj_inst)
        self.send(pkt)
        pdu = self.recv()
        if pdu and (pdu['_raw'][0] >> 4) == PDU_SIMPLE_ACK:
            _ok("COV cancellation → SimpleAck")
        elif pdu:
            _fail("COV cancellation → unexpected",
                  _pdu_type_name(pdu['_raw']))
            _print_pdu(pdu, self.verbose)
        else:
            _fail("COV cancellation → timeout")

    # ─────────────────────────────────────────────────────────────
    #  5. COV Notification (wait for push)
    # ─────────────────────────────────────────────────────────────

    def menu_cov_notification(self):
        _header("COV Notification — Wait for Push")
        options = [
            ("wait_ai",       "Subscribe AI:2001 (Ckt01_Current) then wait for push"),
            ("wait_bi",       "Subscribe BI:1020 (Alarm_Overcurrent) then wait for push"),
            ("wait_active",   "Wait for push from active subscriptions (no re-subscribe)"),
            ("multi_sub",     "Subscribe multiple objects, collect all notifications"),
            ("verify_payload","Subscribe AI, receive notification, verify payload fields"),
            ("after_cancel",  "Subscribe → wait → cancel → verify no more notifications"),
        ]
        choice = _menu("COV Notification Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "wait_ai":
            wait = _prompt_int("Seconds to wait (simulator must tick)", 70)
            pid  = _prompt_int("Process ID", 42)
            # Subscribe
            self._subscribe_show(OBJ_ANALOG_INPUT, 2001, process_id=pid,
                                 confirmed=False, lifetime=wait + 60,
                                 label="AI:2001 for COV wait")
            self._wait_cov(wait, process_id=pid, label="AI:2001 COV notification")

        elif choice == "wait_bi":
            wait = _prompt_int("Seconds to wait", 70)
            pid  = _prompt_int("Process ID", 50)
            self._subscribe_show(OBJ_BINARY_INPUT, 1020, process_id=pid,
                                 confirmed=False, lifetime=wait + 60,
                                 label="BI:1020 for COV wait")
            self._wait_cov(wait, process_id=pid, label="BI:1020 COV notification")

        elif choice == "wait_active":
            if not self.active_subs:
                _warn("No active subscriptions — subscribe first")
                return
            wait = _prompt_int("Seconds to wait", 70)
            _info(f"Listening for COV from {len(self.active_subs)} subscription(s)…")
            self._wait_cov(wait, label="any subscribed object")

        elif choice == "multi_sub":
            wait = _prompt_int("Seconds to wait", 70)
            _info("Subscribing AI:2001, AI:3001, BI:1020")
            self._subscribe_show(OBJ_ANALOG_INPUT, 2001, process_id=42,
                                 confirmed=False, lifetime=wait + 60,
                                 label="AI:2001")
            self._subscribe_show(OBJ_ANALOG_INPUT, 3001, process_id=43,
                                 confirmed=False, lifetime=wait + 60,
                                 label="AI:3001")
            self._subscribe_show(OBJ_BINARY_INPUT, 1020, process_id=50,
                                 confirmed=False, lifetime=wait + 60,
                                 label="BI:1020")
            self._wait_cov(wait, label="multi-object COV", collect_all=True)

        elif choice == "verify_payload":
            wait = _prompt_int("Seconds to wait", 70)
            pid  = _prompt_int("Process ID", 42)
            self._subscribe_show(OBJ_ANALOG_INPUT, 2001, process_id=pid,
                                 confirmed=False, lifetime=wait + 60,
                                 label="AI:2001 for payload verification")
            self._wait_cov_verify(wait, process_id=pid)

        elif choice == "after_cancel":
            wait = _prompt_int("Wait seconds before cancel", 35)
            pid  = _prompt_int("Process ID", 42)
            _info("Step 1: Subscribe AI:2001")
            self._subscribe_show(OBJ_ANALOG_INPUT, 2001, process_id=pid,
                                 confirmed=False, lifetime=wait + 120,
                                 label="AI:2001")
            _info(f"Step 2: Wait {wait} s for notifications")
            self._wait_cov(wait, process_id=pid, label="Pre-cancel COV")
            _info("Step 3: Cancel subscription")
            self._cancel_sub(pid, OBJ_ANALOG_INPUT, 2001)
            self.active_subs = [s for s in self.active_subs
                                if not (s['process_id'] == pid
                                        and s['obj_inst'] == 2001)]
            after = _prompt_int("Seconds to listen after cancel (expect silence)", 40)
            _info(f"Step 4: Listen {after} s — expecting no more notifications")
            self.flush()
            got = self._count_cov(after, process_id=pid)
            if got == 0:
                _ok("No COV notifications after cancellation (correct)")
            else:
                _fail(f"Received {got} COV notification(s) after cancellation")

    def _wait_cov(self, timeout: float, process_id: Optional[int] = None,
                  label: str = "COV", collect_all: bool = False):
        _subheader(f"Waiting up to {timeout:.0f}s for {label}")
        _info("Simulator must tick for COV to be sent. Default tick = 30 s.")
        deadline = time.monotonic() + timeout
        old = self.sock.gettimeout()
        self.sock.settimeout(0.3)
        received = []
        try:
            while time.monotonic() < deadline:
                if not collect_all and len(received) >= 1:
                    break
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if addr[0] != self.host:
                    continue
                _, npdu = parse_bvll(data)
                if npdu is None:
                    continue
                apdu_b = parse_npdu(npdu)
                if apdu_b is None:
                    continue
                pdu = parse_apdu(apdu_b)
                if (pdu and pdu['type'] == 'unconfirmed' and
                        pdu['service'] == SVC_UNCONFIRMED_COV_NOTIFICATION):
                    received.append((addr, pdu))
                    elapsed = timeout - (deadline - time.monotonic())
                    _ok(f"COV notification #{len(received)}",
                        f"from {addr[0]} at {elapsed:.1f}s")
        finally:
            self.sock.settimeout(old)
        if not received:
            _warn(f"No COV notification in {timeout:.0f}s",
                  "Increase wait time or ensure simulator is ticking.")

    def _wait_cov_verify(self, timeout: float, process_id: int):
        """Receive one COV and verify its payload fields."""
        _subheader(f"Waiting up to {timeout:.0f}s — will verify payload")
        deadline = time.monotonic() + timeout
        old = self.sock.gettimeout()
        self.sock.settimeout(0.3)
        try:
            while time.monotonic() < deadline:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if addr[0] != self.host:
                    continue
                _, npdu = parse_bvll(data)
                if npdu is None:
                    continue
                apdu_b = parse_npdu(npdu)
                if apdu_b is None:
                    continue
                pdu = parse_apdu(apdu_b)
                if (pdu and pdu['type'] == 'unconfirmed' and
                        pdu['service'] == SVC_UNCONFIRMED_COV_NOTIFICATION):
                    payload = pdu['data']
                    _ok("COV notification received — verifying payload")
                    # ctx[0]: subscriber-process-id
                    try:
                        pos = 0
                        t, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                        pid = decode_uint(payload, pos, vlen); pos += vlen
                        if pid == process_id:
                            _ok(f"  subscriber-process-id = {pid} (matches)")
                        else:
                            _fail(f"  subscriber-process-id = {pid} (expected {process_id})")
                    except Exception:
                        _warn("  Could not parse process-id")
                    # ctx[1]: initiating-device-identifier
                    try:
                        t, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                        ot, oi = decode_oid_bytes(payload[pos:pos+4]); pos += 4
                        _ok(f"  initiating-device = {_obj_name(ot)}:{oi}")
                    except Exception:
                        _warn("  Could not parse initiating-device-id")
                    # ctx[2]: monitored-object-identifier
                    try:
                        t, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                        ot, oi = decode_oid_bytes(payload[pos:pos+4]); pos += 4
                        _ok(f"  monitored-object = {_obj_name(ot)}:{oi}")
                    except Exception:
                        _warn("  Could not parse monitored-object-id")
                    # ctx[3]: time-remaining (lifetime remaining)
                    try:
                        t, ic, io, icl, vlen, pos = _decode_tag_hdr(payload, pos)
                        tr = decode_uint(payload, pos, vlen); pos += vlen
                        _ok(f"  time-remaining = {tr} s")
                    except Exception:
                        _warn("  Could not parse time-remaining")
                    if self.verbose:
                        _info(f"  Full payload hex: {_hex(payload)}")
                    return
        finally:
            self.sock.settimeout(old)
        _warn(f"No COV notification received in {timeout:.0f}s")

    def _count_cov(self, timeout: float, process_id: Optional[int] = None) -> int:
        deadline = time.monotonic() + timeout
        old = self.sock.gettimeout()
        self.sock.settimeout(0.3)
        count = 0
        try:
            while time.monotonic() < deadline:
                try:
                    data, addr = self.sock.recvfrom(4096)
                except socket.timeout:
                    continue
                if addr[0] != self.host:
                    continue
                _, npdu = parse_bvll(data)
                if npdu is None:
                    continue
                apdu_b = parse_npdu(npdu)
                if apdu_b is None:
                    continue
                pdu = parse_apdu(apdu_b)
                if (pdu and pdu['type'] == 'unconfirmed' and
                        pdu['service'] == SVC_UNCONFIRMED_COV_NOTIFICATION):
                    count += 1
        finally:
            self.sock.settimeout(old)
        return count

    # ─────────────────────────────────────────────────────────────
    #  6. Error Responses
    # ─────────────────────────────────────────────────────────────

    def menu_errors(self):
        _header("Error Response Scenarios")
        options = [
            ("unknown_obj",          "ReadProperty: unknown object instance"),
            ("unknown_prop",         "ReadProperty: unknown property ID"),
            ("wrong_obj_type",       "ReadProperty: request BI property on AI object"),
            ("all_error_objects",    "ReadProperty: 5 non-existent object instances"),
            ("unknown_obj_rpm",      "RPM: non-existent object in multi-request"),
            ("unknown_prop_rpm",     "RPM: unknown property in multi-request"),
            ("cov_unknown_obj",      "SubscribeCOV: non-existent object → error"),
        ]
        choice = _menu("Error Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "unknown_obj":
            inst = _prompt_int("Non-existent AI instance", 999999)
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_ANALOG_INPUT, inst, PROP_PRESENT_VALUE))
            self._check_error(iid, "ReadProperty unknown-object",
                              expect_code=ERR_CODE_UNKNOWN_OBJECT)

        elif choice == "unknown_prop":
            pid = _prompt_int("Non-existent property ID", 9876)
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_ANALOG_INPUT, 1001, pid))
            self._check_error(iid, f"ReadProperty unknown-property (prop={pid})",
                              expect_code=ERR_CODE_UNKNOWN_PROPERTY)

        elif choice == "wrong_obj_type":
            _subheader("AI property (Units) read from BI object — expect error or valid response")
            _info("BI objects support Units=UNIT_NO_UNITS; may succeed or error depending on impl")
            iid = self.next_iid()
            self.send(_build_read_property(iid, OBJ_BINARY_INPUT, 1020, PROP_UNITS))
            pdu = self.recv()
            if pdu:
                raw = pdu['_raw']
                t = (raw[0] >> 4) & 0x0F
                if t == PDU_COMPLEX_ACK:
                    v = _decode_uint_app(_parse_complex_ack_value(raw[3:]))
                    _info(f"ComplexAck: Units={v} (95=no-units, allowed for BI)")
                elif t == PDU_ERROR:
                    ec, ecode = _parse_error(raw)
                    _info(f"Error: {_err_class_name(ec)} / {_err_code_name(ecode)}")
                else:
                    _info(f"Got: {_pdu_type_name(raw)}")
                _print_pdu(pdu, self.verbose)
            else:
                _fail("Timeout")

        elif choice == "all_error_objects":
            _subheader("5 non-existent object instances")
            for inst in [999990, 999991, 999992, 999993, 999994]:
                iid = self.next_iid()
                self.send(_build_read_property(iid, OBJ_ANALOG_INPUT, inst, PROP_PRESENT_VALUE))
                pdu = self.recv(timeout=2.0)
                if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
                    ec, ecode = _parse_error(pdu['_raw'])
                    _ok(f"AI:{inst} → Error", f"{_err_code_name(ecode)}")
                else:
                    _fail(f"AI:{inst}", _pdu_type_name(pdu['_raw']) if pdu else "timeout")
                self.flush()

        elif choice == "unknown_obj_rpm":
            reqs = [
                (OBJ_ANALOG_INPUT, 1001, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]),
                (OBJ_ANALOG_INPUT, 999999, [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]),
            ]
            self._rpm_show("RPM with unknown object", reqs)

        elif choice == "unknown_prop_rpm":
            reqs = [(OBJ_ANALOG_INPUT, 1001, [
                PROP_OBJECT_NAME, PROP_PRESENT_VALUE, 9876, PROP_UNITS
            ])]
            self._rpm_show("RPM with unknown property", reqs, expect_err_prop=9876)

        elif choice == "cov_unknown_obj":
            iid = self.next_iid()
            self.send(_build_subscribe_cov(iid, 255, OBJ_ANALOG_INPUT, 999999,
                                          confirmed=False, lifetime=60))
            self._check_error(iid, "SubscribeCOV unknown object")

    def _check_error(self, iid: int, label: str,
                     expect_code: Optional[int] = None):
        pdu = self.recv()
        if pdu and (pdu['_raw'][0] >> 4) == PDU_ERROR:
            ec, ecode = _parse_error(pdu['_raw'])
            if expect_code is None or ecode == expect_code:
                _ok(f"{label} → Error PDU",
                    f"class={_err_class_name(ec)} code={_err_code_name(ecode)}")
            else:
                _warn(f"{label} → Error PDU but unexpected code",
                      f"got {_err_code_name(ecode)} expected {_err_code_name(expect_code)}")
        elif pdu:
            _fail(f"{label} → expected Error PDU",
                  f"got {_pdu_type_name(pdu['_raw'])}")
            _print_pdu(pdu, self.verbose)
        else:
            _fail(f"{label} → timeout")

    # ─────────────────────────────────────────────────────────────
    #  7. Reject / Abort
    # ─────────────────────────────────────────────────────────────

    def menu_reject_abort(self):
        _header("Reject / Abort Scenarios")
        options = [
            ("malformed_rp",   "Malformed ReadProperty (missing object-id) → Reject"),
            ("zero_apdu",      "Zero-length APDU payload → no response or Reject"),
            ("bad_service",    "Valid PDU header but unknown service code 0xFE → Reject"),
            ("truncated_rpm",  "Truncated RPM request (partial object-id) → Reject"),
        ]
        choice = _menu("Reject / Abort Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "malformed_rp":
            _subheader("Malformed ReadProperty — missing object-identifier")
            _info("ConfirmedRequest with ReadProperty service but no payload data")
            pkt = _build_malformed_empty_apdu()
            self.send(pkt)
            pdu = self.recv()
            if pdu:
                raw = pdu['_raw']
                t = (raw[0] >> 4) & 0x0F
                if t in (PDU_REJECT, PDU_ABORT, PDU_ERROR):
                    reason = raw[2] if len(raw) > 2 else 0
                    rname = (REJECT_REASON_NAMES.get(reason, f"code-{reason}")
                             if t == PDU_REJECT else str(reason))
                    _ok(f"Malformed → {_pdu_type_name(raw)}",
                        f"reason={rname}")
                else:
                    _warn(f"Got {_pdu_type_name(raw)} — simulator did not reject",
                          "This is acceptable if simulator is lenient")
                _print_pdu(pdu, self.verbose)
            else:
                _warn("No response — simulator silently dropped malformed request",
                      "Acceptable: BACnet allows silent discard of unrecognised PDUs")

        elif choice == "zero_apdu":
            _subheader("Zero-length APDU")
            pkt = _build_zero_length_apdu()
            self.send(pkt)
            pdu = self.recv(timeout=2.0)
            if pdu:
                _info(f"Got response: {_pdu_type_name(pdu['_raw'])}")
                _print_pdu(pdu, self.verbose)
            else:
                _ok("No response to zero-length APDU (correct — nothing to reject)")

        elif choice == "bad_service":
            _subheader("Unknown service code 0xFE")
            iid = self.next_iid()
            pkt = _build_bad_service_code(iid)
            self.send(pkt)
            pdu = self.recv()
            if pdu:
                raw = pdu['_raw']
                t = (raw[0] >> 4) & 0x0F
                if t in (PDU_REJECT, PDU_ABORT, PDU_ERROR):
                    _ok(f"Unknown service → {_pdu_type_name(raw)}")
                else:
                    _warn(f"Got {_pdu_type_name(raw)}")
                _print_pdu(pdu, self.verbose)
            else:
                _warn("No response", "Simulator may silently drop unknown services")

        elif choice == "truncated_rpm":
            _subheader("Truncated RPM — 2 bytes of object-id only")
            apdu = bytes([
                (PDU_CONFIRMED_REQUEST << 4), 0x05,
                self.next_iid() & 0xFF,
                SVC_READ_PROPERTY_MULTIPLE,
                0x0C,  # ctx[0] oid tag (4 bytes)
                0x00, 0x40,  # only 2 of 4 bytes — truncated
            ])
            pkt = build_bvll(build_npdu(apdu, expects_reply=True))
            self.send(pkt)
            pdu = self.recv()
            if pdu:
                raw = pdu['_raw']
                t = (raw[0] >> 4) & 0x0F
                if t in (PDU_REJECT, PDU_ABORT, PDU_ERROR):
                    _ok(f"Truncated RPM → {_pdu_type_name(raw)}")
                else:
                    _warn(f"Got {_pdu_type_name(raw)}")
                _print_pdu(pdu, self.verbose)
            else:
                _warn("No response to truncated RPM")

    # ─────────────────────────────────────────────────────────────
    #  8. Device Discovery
    # ─────────────────────────────────────────────────────────────

    def menu_discovery(self):
        _header("Device Discovery")
        options = [
            ("broadcast_scan", "Network-wide broadcast Who-Is scan (3 s)"),
            ("range_scan",     "Scan instance range (sequential unicast Who-Is)"),
            ("quick_check",    "Quick check — ping single device"),
        ]
        choice = _menu("Discovery Scenarios", options)
        if choice == "0":
            return
        self.flush()

        if choice == "broadcast_scan":
            _subheader("Network-wide broadcast scan")
            pkt = _build_who_is_global(broadcast=True)
            self.send(pkt, self.broadcast)
            self._collect_iam(timeout=4.0, label="Broadcast scan")

        elif choice == "range_scan":
            _subheader("Sequential unicast Who-Is range scan")
            low  = _prompt_int("Start instance", self.instance)
            high = _prompt_int("End instance", self.instance + 9)
            timeout_each = _prompt_int("Timeout per instance (ms)", 500) / 1000.0
            _info(f"Scanning instances {low}–{high}…")
            found = []
            for inst in range(low, high + 1):
                self.flush()
                pkt = _build_who_is(inst, inst, broadcast=False)
                self.send(pkt)
                result = self.recv_any(timeout=timeout_each)
                if result:
                    src, pdu = result
                    if pdu['type'] == 'unconfirmed' and pdu['service'] == SVC_I_AM:
                        info = _parse_iam(pdu['data'])
                        found.append((src, inst))
                        _ok(f"Instance {inst}", f"at {src}  vendor={info['vendor_id'] if info else '?'}")
            if not found:
                _warn(f"No devices found in range {low}–{high}")
            else:
                _info(f"Found {len(found)} device(s)")

        elif choice == "quick_check":
            _subheader(f"Quick check — ping device {self.instance}")
            pkt = _build_who_is(self.instance, self.instance, broadcast=False)
            self.send(pkt)
            result = self.recv_any(timeout=3.0)
            if result:
                src, pdu = result
                if pdu['type'] == 'unconfirmed' and pdu['service'] == SVC_I_AM:
                    info = _parse_iam(pdu['data'])
                    _ok(f"Device {self.instance} is reachable at {src}",
                        f"vendor={info['vendor_id'] if info else '?'} max_apdu={info['max_apdu'] if info else '?'}")
                else:
                    _warn("Got unexpected response")
                    _print_pdu(pdu, self.verbose)
            else:
                _fail(f"Device {self.instance} not responding")

    # ─────────────────────────────────────────────────────────────
    #  9. Custom / Raw
    # ─────────────────────────────────────────────────────────────

    def menu_custom(self):
        _header("Custom / Raw")
        options = [
            ("rp_custom",     "ReadProperty — enter any obj_type / obj_inst / prop_id"),
            ("rpm_custom",    "ReadPropertyMultiple — build request interactively"),
            ("raw_hex",       "Send raw hex bytes"),
            ("change_target", "Change target host / instance / port"),
        ]
        choice = _menu("Custom / Raw", options)
        if choice == "0":
            return

        if choice == "rp_custom":
            self.flush()
            print("  Object types: 0=AI  1=AO  2=AV  3=BI  4=BO  5=BV  8=Device")
            ot  = _prompt_int("Object type", OBJ_ANALOG_INPUT)
            oi  = _prompt_int("Object instance", 1001)
            pid = _prompt_int("Property ID", PROP_PRESENT_VALUE)
            ai_s = _prompt("Array index (blank=none)", "")
            ai   = int(ai_s) if ai_s.isdigit() else None
            label = f"{_obj_name(ot)}:{oi}.{_prop_name(pid)}"
            if ai is not None:
                label += f"[{ai}]"
            self._rp_show(label, ot, oi, pid, array_index=ai)

        elif choice == "rpm_custom":
            self.flush()
            reqs = []
            print("  Object types: 0=AI  3=BI  8=Device")
            while True:
                ot = _prompt_int("Object type (-1 to send)", OBJ_ANALOG_INPUT)
                if ot == -1:
                    break
                oi = _prompt_int("Object instance", 1001)
                pid_str = _prompt("Property IDs (comma-sep, e.g. 77,85)", "77,85")
                try:
                    pids = [int(p.strip()) for p in pid_str.split(",")]
                except ValueError:
                    pids = [PROP_OBJECT_NAME, PROP_PRESENT_VALUE]
                reqs.append((ot, oi, pids))
                _info(f"Added {_obj_name(ot)}:{oi} props={pids}")
            if reqs:
                self._rpm_show(f"Custom ({len(reqs)} objects)", reqs)

        elif choice == "raw_hex":
            _subheader("Send raw hex bytes")
            hex_str = _prompt("Hex bytes (e.g. 81 0a 00 11 01 00 10 08 09 00 19 00)",
                              "81 0a 00 11 01 00 10 08 09 00 19 00")
            try:
                pkt = bytes.fromhex(hex_str.replace(" ", ""))
            except ValueError as e:
                _fail(f"Invalid hex: {e}")
                return
            _info(f"Sending {len(pkt)} bytes: {_hex(pkt)}")
            self.send(pkt)
            pdu = self.recv(timeout=3.0)
            _print_pdu(pdu, verbose=True)

        elif choice == "change_target":
            _subheader("Change target device")
            self.host     = _prompt("Host IP", self.host)
            self.instance = _prompt_int("Instance", self.instance)
            self.port     = _prompt_int("Port", self.port)
            self.broadcast = _prompt("Broadcast address", self.broadcast)
            self.verbose   = _prompt("Verbose? (y/n)",
                                     "y" if self.verbose else "n").lower().startswith("y")
            _ok(f"Target updated: {self.host}:{self.port}  instance={self.instance}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main menu loop
# ─────────────────────────────────────────────────────────────────────────────

MAIN_MENU = [
    ("who_is",      "Who-Is / I-Am            (discovery, range, silence)"),
    ("read_prop",   "ReadProperty             (device props, AI/BI, errors)"),
    ("rpm",         "ReadPropertyMultiple     (batch, mixed, errors)"),
    ("sub_cov",     "SubscribeCOV             (subscribe, renew, cancel)"),
    ("cov_notif",   "COV Notification         (wait for push, verify payload)"),
    ("errors",      "Error Responses          (unknown-obj, unknown-prop)"),
    ("reject",      "Reject / Abort           (malformed APDU, bad service)"),
    ("discovery",   "Device Discovery         (broadcast scan, range scan)"),
    ("custom",      "Custom / Raw             (manual request, hex send)"),
]


def run_interactive(host: str, instance: int, port: int,
                    broadcast: str, verbose: bool):
    tester = BACnetTester(host, instance, port, broadcast, verbose)
    print(f"\n{_BOLD}{_G}{'═'*70}{_RST}")
    print(f"{_BOLD}{_G}  BACnet/IP Interactive Service Tester{_RST}")
    print(f"{_BOLD}{_G}{'═'*70}{_RST}")
    tester.print_context()

    dispatch = {
        "who_is":    tester.menu_who_is,
        "read_prop": tester.menu_read_property,
        "rpm":       tester.menu_rpm,
        "sub_cov":   tester.menu_subscribe_cov,
        "cov_notif": tester.menu_cov_notification,
        "errors":    tester.menu_errors,
        "reject":    tester.menu_reject_abort,
        "discovery": tester.menu_discovery,
        "custom":    tester.menu_custom,
    }

    while True:
        tester.print_context()
        choice = _menu("BACnet Service Tester — Main Menu", MAIN_MENU)
        if choice == "0":
            print(f"\n{_DIM}Goodbye.{_RST}\n")
            break
        fn = dispatch.get(choice)
        if fn:
            try:
                fn()
            except KeyboardInterrupt:
                print(f"\n{_Y}  Interrupted — returning to main menu{_RST}")
            except Exception as exc:
                print(f"\n{_R}  Unexpected error: {exc}{_RST}")
                if verbose:
                    import traceback
                    traceback.print_exc()

    tester.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="BACnet/IP interactive service tester — menu-driven, all scenarios"
    )
    ap.add_argument("--host",      default="192.168.1.212",
                    help="Device IP to test against (default: 192.168.1.212)")
    ap.add_argument("--instance",  type=int, default=40001,
                    help="BACnet device instance (default: 40001)")
    ap.add_argument("--port",      type=int, default=BACNET_PORT,
                    help=f"UDP port (default: {BACNET_PORT})")
    ap.add_argument("--broadcast", default="255.255.255.255",
                    help="Broadcast address for Who-Is (default: 255.255.255.255)")
    ap.add_argument("--verbose",   action="store_true",
                    help="Show raw hex APDUs in all responses")
    args = ap.parse_args()
    run_interactive(args.host, args.instance, args.port,
                    args.broadcast, args.verbose)


if __name__ == "__main__":
    main()
