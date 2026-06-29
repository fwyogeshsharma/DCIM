"""
SNMP Management Agent — SNMPv2c GET / GETNEXT / SET for per-device threshold config.

Enterprise OID tree:  1.3.6.1.4.1.99999.3.x  (dcimSimulator.management)
Default port:         1161  (no root/admin required)
Community:            device IP address  (same convention as the main snmpsim port 161)

A DCIM system can read/write per-device thresholds.  Thresholds not explicitly
set for a device fall back to the global rule default.

SNMP examples
-------------
  # Read HighCPU threshold for device 10.50.0.4
  snmpget  -v2c -c 10.50.0.4 127.0.0.1:1161 1.3.6.1.4.1.99999.3.1.0

  # Set HighCPU threshold to 85 % for device 10.50.0.4
  snmpset  -v2c -c 10.50.0.4 127.0.0.1:1161 1.3.6.1.4.1.99999.3.1.0 i 85

  # Walk all management OIDs for device 10.50.0.4
  snmpwalk -v2c -c 10.50.0.4 127.0.0.1:1161 1.3.6.1.4.1.99999.3
"""
from __future__ import annotations

import logging
import re
import socket
import threading
from typing import Callable, Dict, List, Optional, Tuple

from pyasn1.codec.ber import decoder as ber_decoder, encoder as ber_encoder
from pyasn1.type import univ
from pysnmp.proto import api as snmp_api

log = logging.getLogger(__name__)

# ── IP validation ──────────────────────────────────────────────────────────────

_IP_RE = re.compile(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$')


def _is_ip(s: str) -> bool:
    return bool(_IP_RE.match(s))


# ── OID catalogue ──────────────────────────────────────────────────────────────

_BASE = "1.3.6.1.4.1.99999.3"

# key: OID string
# value: (rule_name, field_path, label, multiplier)
_OID_TABLE: Dict[str, Tuple[str, str, str, int]] = {
    f"{_BASE}.1.0":    ("HighCPU",               "threshold",              "HighCPU threshold (%)",                    1),
    f"{_BASE}.2.0":    ("HighCPUSustained",       "threshold",              "HighCPUSustained threshold (%)",           1),
    f"{_BASE}.3.0":    ("CPUNormal",              "threshold",              "CPUNormal threshold (%)",                  1),
    f"{_BASE}.4.0":    ("HighMemory",             "threshold",              "HighMemory threshold (%)",                 1),
    f"{_BASE}.5.0":    ("MemoryNormal",           "threshold",              "MemoryNormal threshold (%)",               1),
    f"{_BASE}.6.0":    ("HighTemperature",        "threshold",              "HighTemperature threshold (°C)",           1),
    f"{_BASE}.7.0":    ("TemperatureNormal",      "threshold",              "TemperatureNormal threshold (°C)",         1),
    f"{_BASE}.8.1.0":  ("CriticalCPUAndTemp",     "conditions.0.threshold", "CriticalCPUAndTemp CPU threshold (%)",     1),
    f"{_BASE}.8.2.0":  ("CriticalCPUAndTemp",     "conditions.1.threshold", "CriticalCPUAndTemp Temp threshold (°C)",   1),
    f"{_BASE}.9.0":    ("RackFailure",            "threshold",              "RackFailure min devices",                  1),
    f"{_BASE}.10.0":   ("SensorAmbientTempHigh",  "threshold",              "SensorAmbientTempHigh threshold (°C)",     1),
    f"{_BASE}.11.0":   ("SensorAmbientTempCritical", "threshold",           "SensorAmbientTempCritical threshold (°C)", 1),
    f"{_BASE}.12.0":   ("SensorAmbientTempNormal","threshold",              "SensorAmbientTempNormal threshold (°C)",   1),
    f"{_BASE}.13.0":   ("SensorHighHumidity",     "threshold",              "SensorHighHumidity threshold (%)",         1),
    f"{_BASE}.14.0":   ("SensorCriticalHumidity", "threshold",              "SensorCriticalHumidity threshold (%)",     1),
    f"{_BASE}.15.0":   ("SensorLowHumidity",      "threshold",              "SensorLowHumidity threshold (%)",          1),
    f"{_BASE}.16.1.0": ("SensorHumidityNormal",   "conditions.0.threshold", "SensorHumidityNormal low bound (%)",       1),
    f"{_BASE}.16.2.0": ("SensorHumidityNormal",   "conditions.1.threshold", "SensorHumidityNormal high bound (%)",      1),
    f"{_BASE}.17.0":   ("SensorHighDewPoint",     "threshold",              "SensorHighDewPoint threshold (°C)",        1),
    f"{_BASE}.18.0":   ("SensorDewPointNormal",   "threshold",              "SensorDewPointNormal threshold (°C)",      1),
    f"{_BASE}.19.0":   ("SensorHighAirflow",      "threshold",              "SensorHighAirflow (m/s ×10)",              10),
    f"{_BASE}.20.0":   ("SensorLowAirflow",       "threshold",              "SensorLowAirflow (m/s ×10)",               10),
    f"{_BASE}.21.1.0": ("SensorAirflowNormal",    "conditions.0.threshold", "SensorAirflowNormal low (m/s ×10)",        10),
    f"{_BASE}.21.2.0": ("SensorAirflowNormal",    "conditions.1.threshold", "SensorAirflowNormal high (m/s ×10)",       10),
}

_OID_SORTED: List[str] = sorted(
    _OID_TABLE.keys(),
    key=lambda o: tuple(int(x) for x in o.split(".")),
)

# ── System MIB-II OIDs (sysContact / sysName / sysLocation) — OctetString R/W ──

_SYS_BASE = "1.3.6.1.2.1.1"

# OID → Device attribute name (all OctetString)
_SYSTEM_OID_MAP: Dict[str, str] = {
    f"{_SYS_BASE}.4.0": "sys_contact",
    f"{_SYS_BASE}.5.0": "name",
    f"{_SYS_BASE}.6.0": "sys_location_override",
}

# ── Enterprise asset OIDs (dcimSimulator.asset = 1.3.6.1.4.1.99999.4) ──────────

_ASSET_BASE = "1.3.6.1.4.1.99999.4"

# OID → (Device attr name, "str" | "int")
_ASSET_OID_MAP: Dict[str, Tuple[str, str]] = {
    f"{_ASSET_BASE}.1.0":  ("country",         "str"),
    f"{_ASSET_BASE}.2.0":  ("datacenter_city", "str"),
    f"{_ASSET_BASE}.3.0":  ("datacenter",      "str"),
    f"{_ASSET_BASE}.4.0":  ("floor",           "str"),
    f"{_ASSET_BASE}.5.0":  ("room",            "str"),
    f"{_ASSET_BASE}.6.0":  ("rack_row",        "int"),
    f"{_ASSET_BASE}.7.0":  ("rack_num",        "int"),
    f"{_ASSET_BASE}.8.0":  ("rack_unit",       "int"),
    f"{_ASSET_BASE}.9.0":  ("model_name",      "str"),
    f"{_ASSET_BASE}.10.0": ("power_draw_w",    "int"),
}


def _oid_tuple(oid: str) -> tuple:
    return tuple(int(x) for x in oid.split("."))


_ALL_OID_SORTED: List[str] = sorted(
    list(_SYSTEM_OID_MAP) + list(_OID_TABLE) + list(_ASSET_OID_MAP),
    key=_oid_tuple,
)


# ── Condition field accessors ──────────────────────────────────────────────────

def _path_get(rule, field_path: str) -> float:
    c = rule.condition
    parts = field_path.split(".")
    if parts[0] == "conditions":
        c = c.conditions[int(parts[1])]
        return getattr(c, parts[2], 0.0)
    return getattr(c, parts[0], 0.0)


# ── SNMP PDU constants ────────────────────────────────────────────────────────

_PDU_GET      = 0
_PDU_GETNEXT  = 1
_PDU_SET      = 3

_ERR_NO_ERROR   = 0
_ERR_WRONG_TYPE = 7
_ERR_NO_SUCH    = 2


# ── Pure SNMP request handler (bytes in → bytes out) ──────────────────────────

class _SnmpHandler:
    """Decodes an SNMP request, processes it, encodes and returns the response."""

    def __init__(self, rule_engine, on_change_cb: Optional[Callable],
                 device_lookup=None, on_device_updated=None):
        self._re               = rule_engine
        self._on_change        = on_change_cb
        self._device_lookup    = device_lookup    # Callable[[str], Optional[Device]]
        self._on_device_updated = on_device_updated  # Callable[[Device], None]

    def handle(self, data: bytes) -> Optional[bytes]:
        try:
            msg_ver = int(snmp_api.decodeMessageVersion(data))
            if msg_ver not in snmp_api.PROTOCOL_MODULES:
                return None
            pMod = snmp_api.PROTOCOL_MODULES[msg_ver]

            msg, _ = ber_decoder.decode(data, asn1Spec=pMod.Message())
            community = bytes(msg.getComponentByPosition(1)).decode(errors="replace").strip()

            if not _is_ip(community):
                log.debug("[SnmpSetAgent] Rejected community %r (not an IP)", community)
                return None

            device_ip = community
            pdu       = pMod.apiMessage.get_pdu(msg)
            pdu_tag   = pdu.tagSet[0].tagId
            req_id    = pMod.apiPDU.get_request_id(pdu)
            vbs       = list(pMod.apiPDU.get_varbinds(pdu))

            if pdu_tag == _PDU_GET:
                err_status, err_idx, rsp_vbs = self._do_get(vbs, device_ip)
            elif pdu_tag == _PDU_GETNEXT:
                err_status, err_idx, rsp_vbs = self._do_getnext(vbs, device_ip)
            elif pdu_tag == _PDU_SET:
                err_status, err_idx, rsp_vbs = self._do_set(vbs, device_ip)
            else:
                return None

            return self._build_response(pMod, community.encode(),
                                        req_id, err_status, err_idx, rsp_vbs)
        except Exception as exc:
            log.exception("[SnmpSetAgent] handle() error: %s", exc)
            return None

    # ── GET ────────────────────────────────────────────────────────────────────

    def _do_get(self, vbs, device_ip: str):
        result = []
        for oid, _ in vbs:
            oid_str = str(oid)

            if oid_str in _SYSTEM_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    result.append((oid, univ.OctetString(b"noSuchObject")))
                    continue
                attr = _SYSTEM_OID_MAP[oid_str]
                if attr == "sys_location_override":
                    val = device.sys_location  # property handles override/compute
                else:
                    val = getattr(device, attr, "") or ""
                result.append((oid, univ.OctetString(str(val).encode())))
                continue

            if oid_str in _ASSET_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    result.append((oid, univ.OctetString(b"noSuchObject")))
                    continue
                attr, typ = _ASSET_OID_MAP[oid_str]
                raw = getattr(device, attr, "" if typ == "str" else 0)
                if typ == "int":
                    result.append((oid, univ.Integer(int(raw or 0))))
                else:
                    result.append((oid, univ.OctetString(str(raw or "").encode())))
                continue

            entry = _OID_TABLE.get(oid_str)
            if entry is None:
                result.append((oid, univ.OctetString(b"noSuchObject")))
                continue
            rule_name, field_path, _, multiplier = entry
            rule = self._re.get_rule(rule_name)
            if rule is None:
                result.append((oid, univ.OctetString(b"noSuchObject")))
                continue
            override = self._re.get_device_threshold(device_ip, rule_name, field_path)
            raw = override if override is not None else _path_get(rule, field_path)
            result.append((oid, univ.Integer(round(raw * multiplier))))
        return _ERR_NO_ERROR, 0, result

    # ── GETNEXT ────────────────────────────────────────────────────────────────

    def _do_getnext(self, vbs, device_ip: str):
        result = []
        for oid, _ in vbs:
            oid_str  = str(oid)
            next_oid = self._next_oid(oid_str)
            if next_oid is None:
                result.append((oid, univ.OctetString(b"endOfMibView")))
                continue
            next_oid_obj = univ.ObjectIdentifier(_oid_tuple(next_oid))

            if next_oid in _SYSTEM_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    result.append((next_oid_obj, univ.OctetString(b"noSuchObject")))
                    continue
                attr = _SYSTEM_OID_MAP[next_oid]
                val = device.sys_location if attr == "sys_location_override" else (getattr(device, attr, "") or "")
                result.append((next_oid_obj, univ.OctetString(str(val).encode())))
                continue

            if next_oid in _ASSET_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    result.append((next_oid_obj, univ.OctetString(b"noSuchObject")))
                    continue
                attr, typ = _ASSET_OID_MAP[next_oid]
                raw = getattr(device, attr, "" if typ == "str" else 0)
                if typ == "int":
                    result.append((next_oid_obj, univ.Integer(int(raw or 0))))
                else:
                    result.append((next_oid_obj, univ.OctetString(str(raw or "").encode())))
                continue

            entry = _OID_TABLE[next_oid]
            rule_name, field_path, _, multiplier = entry
            rule = self._re.get_rule(rule_name)
            if rule is None:
                result.append((next_oid_obj, univ.OctetString(b"noSuchObject")))
                continue
            override = self._re.get_device_threshold(device_ip, rule_name, field_path)
            raw = override if override is not None else _path_get(rule, field_path)
            result.append((next_oid_obj, univ.Integer(round(raw * multiplier))))
        return _ERR_NO_ERROR, 0, result

    def _next_oid(self, oid_str: str) -> Optional[str]:
        oid_ints = _oid_tuple(oid_str)
        for candidate in _ALL_OID_SORTED:
            if _oid_tuple(candidate) > oid_ints:
                return candidate
        return None

    # ── SET ────────────────────────────────────────────────────────────────────

    def _do_set(self, vbs, device_ip: str):
        for i, (oid, val) in enumerate(vbs, start=1):
            oid_str = str(oid)

            if oid_str in _SYSTEM_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    return _ERR_NO_SUCH, i, vbs
                try:
                    str_val = bytes(val).decode("utf-8", errors="replace").strip()
                except Exception:
                    return _ERR_WRONG_TYPE, i, vbs
                attr = _SYSTEM_OID_MAP[oid_str]
                setattr(device, attr, str_val)
                log.info("[SnmpSetAgent] SET device=%s %s=%r", device_ip, attr, str_val)
                if self._on_device_updated:
                    try:
                        self._on_device_updated(device)
                    except Exception as exc:
                        log.warning("[SnmpSetAgent] on_device_updated error: %s", exc)
                continue

            if oid_str in _ASSET_OID_MAP:
                device = self._device_lookup(device_ip) if self._device_lookup else None
                if device is None:
                    return _ERR_NO_SUCH, i, vbs
                attr, typ = _ASSET_OID_MAP[oid_str]
                try:
                    new_val = int(val) if typ == "int" else bytes(val).decode("utf-8", errors="replace").strip()
                except Exception:
                    return _ERR_WRONG_TYPE, i, vbs
                setattr(device, attr, new_val)
                log.info("[SnmpSetAgent] SET device=%s %s=%r", device_ip, attr, new_val)
                if self._on_device_updated:
                    try:
                        self._on_device_updated(device)
                    except Exception as exc:
                        log.warning("[SnmpSetAgent] on_device_updated error: %s", exc)
                continue

            entry = _OID_TABLE.get(oid_str)
            if entry is None:
                return _ERR_NO_SUCH, i, vbs

            rule_name, field_path, label, multiplier = entry
            if self._re.get_rule(rule_name) is None:
                return _ERR_NO_SUCH, i, vbs

            try:
                int_val   = int(val)
                new_float = int_val / multiplier
            except (TypeError, ValueError):
                return _ERR_WRONG_TYPE, i, vbs

            self._re.set_device_threshold(device_ip, rule_name, field_path, new_float)
            log.info("[SnmpSetAgent] SET device=%s %s → %s = %.2f",
                     device_ip, rule_name, label, new_float)

            if self._on_change:
                try:
                    self._on_change(device_ip, rule_name)
                except Exception as exc:
                    log.warning("[SnmpSetAgent] on_change_cb error: %s", exc)

        return _ERR_NO_ERROR, 0, vbs

    # ── Response builder ───────────────────────────────────────────────────────

    def _build_response(self, pMod, community: bytes,
                        req_id, err_status, err_idx, vbs) -> bytes:
        rsp_pdu = pMod.GetResponsePDU()
        for name in ("set_defaults", "setDefaults"):
            fn = getattr(pMod.apiPDU, name, None)
            if fn:
                fn(rsp_pdu)
                break

        pMod.apiPDU.set_request_id(rsp_pdu, req_id)
        pMod.apiPDU.set_error_status(rsp_pdu, err_status)
        pMod.apiPDU.set_error_index(rsp_pdu, err_idx)
        pMod.apiPDU.set_varbinds(rsp_pdu, vbs)

        rsp_msg = pMod.Message()
        for name in ("set_defaults", "setDefaults"):
            fn = getattr(pMod.apiMessage, name, None)
            if fn:
                fn(rsp_msg)
                break

        pMod.apiMessage.set_version(rsp_msg, 1)
        pMod.apiMessage.set_community(rsp_msg, community)
        pMod.apiMessage.set_pdu(rsp_msg, rsp_pdu)

        return ber_encoder.encode(rsp_msg)


# ── Public controller ──────────────────────────────────────────────────────────

class SnmpSetAgent:
    """
    Lifecycle controller for the SNMP management agent.

    Uses a plain blocking UDP socket in a daemon thread — reliable on both
    Windows and Linux without asyncio event loop complications.
    Community string = device IP (same convention as snmpsim on port 161).
    """

    def __init__(
        self,
        rule_engine,
        host: str = "0.0.0.0",
        port: int = 1161,
        on_change_cb: Optional[Callable[[str, str], None]] = None,
        device_lookup=None,
        on_device_updated=None,
    ):
        self._re               = rule_engine
        self._host             = host
        self._port             = port
        self._on_change        = on_change_cb
        self._device_lookup    = device_lookup
        self._on_device_updated = on_device_updated
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running   = False

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.is_running():
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self._host, self._port))
            self._sock.settimeout(1.0)
        except OSError as exc:
            # WSAEACCES (WinError 10013) with SO_REUSEADDR set almost always
            # means the port is already owned by another process (e.g. a second
            # copy of this app) rather than a true permissions problem.
            hint = ""
            if getattr(exc, "winerror", None) == 10013:
                hint = (" — port already in use by another process "
                        "(another instance of this app or a tool on the same "
                        "port). Close it, then start again.")
            log.error("[SnmpSetAgent] Failed to bind %s:%d — %s%s",
                      self._host, self._port, exc, hint)
            self._sock = None
            return False

        self._running = True
        self._thread  = threading.Thread(target=self._serve, daemon=True, name="SnmpSetAgent")
        self._thread.start()
        log.info("[SnmpSetAgent] Listening on %s:%d  community=<device-ip>",
                 self._host, self._port)
        return True

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)
        self._sock   = None
        self._thread = None
        log.info("[SnmpSetAgent] Stopped.")

    def _serve(self):
        handler = _SnmpHandler(self._re, self._on_change,
                               self._device_lookup, self._on_device_updated)
        log.info("[SnmpSetAgent] Listening on %s:%d", self._host, self._port)
        while self._running:
            try:
                data, addr = self._sock.recvfrom(65535)
                response = handler.handle(data)
                if response:
                    self._sock.sendto(response, addr)
            except socket.timeout:
                continue
            except OSError as e:
                log.warning("[SnmpSetAgent] socket error: %s", e)
                break

    @staticmethod
    def oid_table() -> Dict[str, Tuple[str, str, str, int]]:
        return dict(_OID_TABLE)

    @staticmethod
    def oid_sorted() -> List[str]:
        return list(_OID_SORTED)
