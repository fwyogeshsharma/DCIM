"""
In-process SNMP agent using pysnmp low-level proto API.

Replaces snmpsim-lextudio: OID tables live in memory — no .snmprec files,
no .dbm indexes, no subprocess.  Community string = device IP address
(same routing convention as snmpsim so NMS tools need no reconfiguration).

Supports GET / GETNEXT / GETBULK for SNMPv1 and SNMPv2c.
"""
from __future__ import annotations

import bisect
import logging
import socket
import threading
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

from pysnmp.proto import api as _snmpApi
from pyasn1.codec.ber import encoder as _berEncoder, decoder as _berDecoder
from pysnmp.proto.rfc1902 import (
    Integer32, OctetString, Counter32, Gauge32, TimeTicks,
    Counter64, IpAddress, ObjectName,
)
from pysnmp.proto.rfc1905 import NoSuchObject, EndOfMibView

OidEntry = Tuple[str, str, str]   # (oid_str, type_code, value_str)


# ── Value builder ─────────────────────────────────────────────────────────

def _make_value(type_code: str, value: str):
    """Convert a snmprec type code + string value to a pysnmp ASN.1 object."""
    tc = type_code.lower()
    try:
        if tc == '2':
            return Integer32(int(value))
        if tc == '4':
            return OctetString(value.encode('utf-8'))
        if tc == '4x':
            return OctetString(hexValue=value)
        if tc == '6':
            parts = tuple(int(x) for x in value.split('.') if x)
            return ObjectName(parts or (0, 0))
        if tc in ('41', '65'):       # Counter32 (hex 0x41 or decimal 65)
            return Counter32(int(value))
        if tc in ('42', '66'):       # Gauge32 (hex 0x42 or decimal 66)
            return Gauge32(int(value))
        if tc in ('43', '67'):       # TimeTicks (hex 0x43 or decimal 67)
            return TimeTicks(int(value))
        if tc in ('44', '46', '70'): # Counter64
            return Counter64(int(value))
        if tc in ('40', '64'):       # IpAddress (hex 0x40 or decimal 64)
            return IpAddress(value)
        return OctetString(value.encode('utf-8'))
    except Exception:
        return OctetString(b'')


def _to_name(oid_str: str) -> ObjectName:
    return ObjectName(tuple(int(x) for x in oid_str.split('.')))


def _oid_key(oid_str: str) -> tuple:
    return tuple(int(x) for x in oid_str.split('.'))


# ── In-memory OID table ───────────────────────────────────────────────────

class _OidTable:
    """
    O(1) GET via dict, O(log n) GETNEXT via bisect on a sorted key list.
    update() patches existing OIDs only — no re-sort needed.
    """

    __slots__ = ('_keys', '_strs', '_data')

    def __init__(self):
        self._keys: list = []   # sorted OID tuples (for bisect)
        self._strs: list = []   # parallel sorted OID strings
        self._data: dict = {}   # {oid_str: (type_code, value_str)}

    def load(self, entries: List[OidEntry]):
        self._data = {e[0]: (e[1], e[2]) for e in entries}
        sorted_strs = sorted(self._data.keys(), key=_oid_key)
        self._strs = sorted_strs
        self._keys = [_oid_key(s) for s in sorted_strs]

    def get(self, oid_str: str) -> Optional[Tuple[str, str]]:
        return self._data.get(oid_str)

    def get_next(self, oid_str: str) -> Optional[Tuple[str, str, str]]:
        key = _oid_key(oid_str)
        idx = bisect.bisect_right(self._keys, key)
        if idx >= len(self._strs):
            return None
        nxt = self._strs[idx]
        tc, val = self._data[nxt]
        return (nxt, tc, val)

    def update(self, updates: Dict[str, Tuple[str, str]]):
        for oid_str, (tc, val) in updates.items():
            if oid_str in self._data:
                self._data[oid_str] = (tc, val)


# ── Agent ─────────────────────────────────────────────────────────────────

class SNMPAgent:
    """
    UDP SNMP agent backed entirely by in-memory OID tables.

    API surface mirrors SNMPSimController so main_window.py wiring is minimal:
      set_log_callback / set_status_callback / set_ready_callback
      start(port, device_ips) / stop()
      is_running() / is_ready() / get_pid()
      get_snmp_walk_command()

    Extra API for table management:
      update_device(community, entries)   — full table load (on dataset gen)
      update_metrics(community, updates)  — dynamic OID patch (every tick)
    """

    def __init__(self):
        self._tables: Dict[str, _OidTable] = {}
        self._lock   = threading.RLock()
        self._sock:   Optional[socket.socket]            = None
        self._thread: Optional[threading.Thread]         = None
        self._running = False
        self._ready   = False

        self._log_cb:    Optional[Callable[[str, str], None]] = None
        self._status_cb: Optional[Callable[[str], None]]       = None
        self._ready_cb:  Optional[Callable[[], None]]          = None

    # ── Callbacks (same interface as SNMPSimController) ───────────────────

    def set_log_callback(self, cb: Callable[[str, str], None]):
        self._log_cb = cb

    def set_status_callback(self, cb: Callable[[str], None]):
        self._status_cb = cb

    def set_ready_callback(self, cb: Callable[[], None]):
        self._ready_cb = cb

    # ── State queries ─────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._running

    def is_ready(self) -> bool:
        return self._ready

    def get_pid(self) -> Optional[int]:
        return None  # no subprocess

    def get_snmp_walk_command(self, ip: str, port: int = 161,
                              community: str = 'public') -> str:
        return f"snmpwalk -v2c -c {ip} {ip}:{port}"

    @property
    def datasets_dir(self) -> Optional[str]:
        return None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self, port: int = 161, device_ips: list = None, **_) -> bool:
        if self._running:
            return True
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', port))
            sock.settimeout(1.0)
        except OSError as exc:
            self._log(f"[SNMP Agent] Cannot bind UDP 0.0.0.0:{port}: {exc}", "error")
            if self._status_cb:
                self._status_cb("Error")
            return False

        self._sock    = sock
        self._running = True
        self._ready   = False
        self._thread  = threading.Thread(
            target=self._serve, daemon=True, name="SNMPAgent-recv",
        )
        self._thread.start()
        self._log(f"[SNMP Agent] Listening on UDP 0.0.0.0:{port}", "info")
        self._ready = True
        if self._ready_cb:
            self._ready_cb()
        if self._status_cb:
            self._status_cb("Running")
        return True

    def stop(self):
        self._running = False
        self._ready   = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._status_cb:
            self._status_cb("Stopped")

    # ── Table management ──────────────────────────────────────────────────

    def update_device(self, community: str, entries: List[OidEntry]):
        """Full OID table replacement for one device (call after dataset gen)."""
        with self._lock:
            if community not in self._tables:
                self._tables[community] = _OidTable()
            self._tables[community].load(entries)

    def update_metrics(self, community: str, updates: Dict[str, Tuple[str, str]]):
        """Patch dynamic OIDs in-place — called every StateStore tick."""
        with self._lock:
            table = self._tables.get(community)
            if table:
                table.update(updates)

    # ── UDP receive loop ──────────────────────────────────────────────────

    def _serve(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                resp = self._handle(data)
                if resp:
                    self._sock.sendto(resp, addr)
            except Exception as exc:
                log.error("[SNMPAgent] handle error from %s: %s", addr, exc, exc_info=True)

    def _handle(self, data: bytes) -> Optional[bytes]:
        # Try SNMPv2c first (most common), fall back to v1.
        reqMsg = pMod = None
        for ver_id in (_snmpApi.SNMP_VERSION_2C, _snmpApi.SNMP_VERSION_1):
            mod = _snmpApi.PROTOCOL_MODULES[ver_id]
            try:
                msg, remainder = _berDecoder.decode(data, asn1Spec=mod.Message())
                if not remainder:
                    reqMsg, pMod = msg, mod
                    break
            except Exception:
                continue
        if reqMsg is None:
            return None

        community = pMod.apiMessage.get_community(reqMsg).asOctets().decode('latin-1')
        reqPDU    = pMod.apiMessage.get_pdu(reqMsg)
        pdu_type  = reqPDU.__class__.__name__

        if pdu_type not in ('GetRequestPDU', 'GetNextRequestPDU',
                             'GetBulkRequestPDU'):
            return None  # SET / TRAP / etc. not supported

        with self._lock:
            table = self._tables.get(community)
        if table is None:
            return None  # unknown community (unknown device)

        # ── Compute response var-binds ─────────────────────────────────
        resp_vbs: list = []

        if pdu_type == 'GetRequestPDU':
            for oid, _ in pMod.apiPDU.get_varbinds(reqPDU):
                result = table.get(str(oid))
                if result:
                    tc, val = result
                    resp_vbs.append((oid, _make_value(tc, val)))
                else:
                    resp_vbs.append((oid, NoSuchObject()))

        elif pdu_type == 'GetNextRequestPDU':
            for oid, _ in pMod.apiPDU.get_varbinds(reqPDU):
                result = table.get_next(str(oid))
                if result:
                    nxt, tc, val = result
                    resp_vbs.append((_to_name(nxt), _make_value(tc, val)))
                else:
                    resp_vbs.append((oid, EndOfMibView()))

        else:  # GetBulkRequestPDU (v2c only)
            non_rep  = max(0, int(pMod.apiBulkPDU.get_non_repeaters(reqPDU)))
            max_reps = max(0, min(int(pMod.apiBulkPDU.get_max_repetitions(reqPDU)), 50))
            vbs      = pMod.apiPDU.get_varbinds(reqPDU)
            non_rep  = min(non_rep, len(vbs))

            for oid, _ in vbs[:non_rep]:
                result = table.get_next(str(oid))
                if result:
                    nxt, tc, val = result
                    resp_vbs.append((_to_name(nxt), _make_value(tc, val)))
                else:
                    resp_vbs.append((_to_name(str(oid)), EndOfMibView()))

            repeaters = vbs[non_rep:]
            if repeaters and max_reps > 0:
                cur  = [str(oid) for oid, _ in repeaters]
                done = [False] * len(cur)
                for _ in range(max_reps):
                    for j in range(len(cur)):
                        if done[j]:
                            resp_vbs.append((_to_name(cur[j]), EndOfMibView()))
                        else:
                            result = table.get_next(cur[j])
                            if result:
                                nxt, tc, val = result
                                resp_vbs.append((_to_name(nxt), _make_value(tc, val)))
                                cur[j] = nxt
                            else:
                                done[j] = True
                                resp_vbs.append((_to_name(cur[j]), EndOfMibView()))

        # ── Build response message ─────────────────────────────────────
        rspPDU = pMod.GetResponsePDU()
        pMod.apiPDU.set_defaults(rspPDU)
        pMod.apiPDU.set_request_id(rspPDU, pMod.apiPDU.get_request_id(reqPDU))
        pMod.apiPDU.set_error_status(rspPDU, 0)
        pMod.apiPDU.set_error_index(rspPDU, 0)
        pMod.apiPDU.set_varbinds(rspPDU, resp_vbs)

        rspMsg = pMod.Message()
        pMod.apiMessage.set_defaults(rspMsg)
        pMod.apiMessage.set_community(rspMsg, community)
        pMod.apiMessage.set_pdu(rspMsg, rspPDU)

        try:
            return _berEncoder.encode(rspMsg)
        except Exception as exc:
            log.error("[SNMPAgent] encode error: %s", exc)
            self._log(f"[SNMP Agent] encode error: {exc}", "error")
            return None

    def _log(self, msg: str, level: str = 'info'):
        log.info(msg)
        if self._log_cb:
            self._log_cb(msg, level)