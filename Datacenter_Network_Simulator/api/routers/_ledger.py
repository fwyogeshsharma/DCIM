"""Ledger hooks for the EXISTING injection channels (doc 32 §7.1, S0-4).

Each hook is called by its router right after the injection has been accepted
(after validation, so a refused request never ledgers a fault that did not
land). Every hook goes through ``fault_ledger.safe``: a ledger failure is logged
and never breaks the injection it records.

``expected_kind`` per channel follows the §7.1 table:

* plant override (value) → ``process``; a valve ``Position`` hold without
  ``Alarm_ActuatorFault`` → ``sensor`` (physics only restricts flow when the bit
  is set, so a held position alone is a lying feedback sensor);
* setpoint points → ``setpoint`` / ``none`` (context, not a fault);
* fault menu → ``process``, except ``ambient_high`` / ``humidity_high`` on a
  sensor device → ``sensor`` (the probe reads high, the room does not change);
* metric pin → per metric, from ``_PIN_EXPECTED``;
* electrical / power → ``process``; fire / link / tick / fleet / protocol →
  ``none`` (context: the platform must suppress or exclude, not detect).
"""
from __future__ import annotations

from typing import Any, Optional

from core import fault_ledger as _fl

# Setpoint points on plant devices. A change is a context event (doc 32 §6.2).
SETPOINT_POINTS = {"Setpoint", "CHW_Setpoint", "TCS_Setpoint"}

# Pinned metrics that are READINGS the physics does not act on: a pin lies about
# the room without changing it, which is a sensor fault for scoring. The rest
# (load, current, voltage, state pins) change what the device is doing.
# [judgement: cpu_temp is a BMC reading here — the room model does not read it]
_PIN_SENSOR = {"inlet_temp", "outlet_temp", "mid_temp", "humidity", "dewpoint",
               "pdu_temperature", "pdu_humidity", "airflow", "cpu_temp", "fan_rpm"}


def _pin_expected(metric: str) -> str:
    return "sensor" if metric in _PIN_SENSOR else "process"


def _hint(dtype: str, point: str) -> Optional[dict]:
    try:
        from core.fault_points import metric_hint
        return metric_hint(dtype, point)
    except Exception:
        return None


def _L():
    return _fl.get_ledger()


# ── BACnet plant overrides ───────────────────────────────────────────────────

def _plant_override(dev, point: str, value: Optional[float], overrides: dict) -> None:
    name, did = dev.name, dev.id
    dtype = dev.device_type.value
    key = ("override", name, point)
    if value is None:
        _L().close_key(key, "cleared")
        return
    if point in SETPOINT_POINTS:
        kind, expected = "setpoint", "none"
        prev = (overrides.get(name) or {}).get(point)
        params = {"from": prev, "to": float(value), "rate_per_s": None}
    else:
        kind, expected = "override", "process"
        params = {"value": float(value)}
        if (dtype == "valve" and point in ("Position", "Commanded_Position")
                and not (overrides.get(name) or {}).get("Alarm_ActuatorFault")):
            expected = "sensor"
    _L().open(kind, device=name, device_id=did, point=point, params=params,
              expected_kind=expected, metric_hint=_hint(dtype, point),
              source="POST /api/bacnet/plant/overrides", key=key)


def plant_override(dev, point, value, overrides) -> None:
    _fl.safe(_plant_override, dev, point, value, overrides)


# ── electrical ───────────────────────────────────────────────────────────────

def _electrical(what: str, key_id: str, failed: bool, device=None, extra=None) -> None:
    key = ("electrical", what, key_id)
    if not failed:
        _L().close_key(key, "cleared")
        return
    params = {"what": what, "failed": True}
    params.update(extra or {})
    _L().open("electrical", device=getattr(device, "name", None),
              device_id=getattr(device, "id", None), params=params,
              expected_kind="process", source=f"POST /api/bacnet/electrical/{what}",
              key=key)


def utility(datacenter: str, failed: bool) -> None:
    _fl.safe(_electrical, "utility", datacenter, failed, None, {"datacenter": datacenter})


def ats(device, device_id: str, failed: bool) -> None:
    _fl.safe(_electrical, "ats", device_id, failed, device)


# ── device fault menu + metric pins ──────────────────────────────────────────

def _device_fault(dev, fault: str, spec: dict, on: bool, instance: Any = None) -> None:
    key = ("fault_menu", dev.id, fault)
    if not on:
        _L().close_key(key, "cleared")
        return
    dtype = dev.device_type.value
    expected = "process"
    if fault in ("ambient_high", "humidity_high") and dtype == "sensor":
        expected = "sensor"
    params = {"fault": fault, "label": spec.get("label")}
    for k in ("metric", "target", "rate", "state", "override", "value"):
        if k in spec:
            params[k] = spec[k]
    if instance is not None:
        params["instance"] = instance
    _L().open("fault_menu", device=dev.name, device_id=dev.id,
              point=spec.get("metric") or spec.get("override"), params=params,
              expected_kind=expected, source="POST /api/devices/{id}/fault", key=key)


def device_fault(dev, fault, spec, on, instance=None) -> None:
    _fl.safe(_device_fault, dev, fault, spec, on, instance)


def _device_pin(dev, metric: str, value: Any) -> None:
    key = ("pin", dev.id, metric)
    if value is None:
        _L().close_key(key, "cleared")
        return
    _L().open("pin", device=dev.name, device_id=dev.id, point=metric,
              params={"value": value}, expected_kind=_pin_expected(metric),
              source="POST /api/devices/{id}/override", key=key)


def device_pin(dev, metric, value) -> None:
    _fl.safe(_device_pin, dev, metric, value)


# ── fire scenarios (context: the platform must suppress, not detect) ────────

def _fire(what: str, datacenter: str, room: Optional[str], active: bool, params=None) -> None:
    if what == "reset":
        for e in _L().open_entries():
            if e.get("kind") == "fire" and (e.get("params") or {}).get("datacenter") == datacenter:
                _L().close(e["id"], "cleared")
        _L().instant("fire", params={"what": "reset", "datacenter": datacenter},
                     expected_kind="none", source="POST /api/modbus/fire/reset")
        return
    key = ("fire", what, datacenter, (room or "").upper())
    if not active:
        _L().close_key(key, "cleared")
        return
    p = {"what": what, "datacenter": datacenter, "room": (room or "").upper()}
    p.update(params or {})
    _L().open("fire", params=p, expected_kind="none",
              source=f"POST /api/modbus/fire/{what}", key=key)


def fire_smoke(datacenter, room, pct_per_m) -> None:
    _fl.safe(_fire, "smoke", datacenter, room, pct_per_m is not None,
             {"pct_per_m": pct_per_m})


def fire_abort(datacenter, room, held) -> None:
    _fl.safe(_fire, "abort", datacenter, room, bool(held))


def fire_reset(datacenter) -> None:
    _fl.safe(_fire, "reset", datacenter, None, True)


# ── production links ─────────────────────────────────────────────────────────

def _link(src_id: str, dst_id: str, layer: str, broken: bool, names=None) -> None:
    a, b = sorted((src_id, dst_id))
    key = ("link", a, b, layer)
    if not broken:
        _L().close_key(key, "cleared")
        return
    _L().open("link", device=(names or {}).get(src_id), device_id=src_id,
              params={"src_id": src_id, "dst_id": dst_id, "layer": layer,
                      "dst": (names or {}).get(dst_id)},
              expected_kind="none", source="POST /api/topology/links/break", key=key)


def link(src_id, dst_id, layer, broken, names=None) -> None:
    _fl.safe(_link, src_id, dst_id, layer, broken, names)


# ── Redfish power actions ────────────────────────────────────────────────────

_POWER_INSTANT = {"reboot", "power_cycle", "reboot_bmc"}


def _redfish_action(ip: str, action: str, res: dict) -> None:
    if action not in ("power_on", "power_off") and action not in _POWER_INSTANT:
        return                      # LED / refresh / clear_log do not move telemetry
    dev_name = (res or {}).get("device")
    dev_id = None
    try:
        from api.state import AppState
        dm = AppState.get().device_manager
        for d in (dm.get_all_devices() if dm else []):
            if d.name == dev_name or ip in (d.ip_address, getattr(d, "mgmt_ip", "")):
                dev_name, dev_id = d.name, d.id
                break
    except Exception:
        pass
    key = ("power", ip)
    src = "POST /api/redfish/action"
    if action == "power_on":
        _L().close_key(key, "cleared")
        return
    params = {"action": action, "ip": ip}
    if action == "power_off":
        _L().open("power", device=dev_name, device_id=dev_id, params=params,
                  expected_kind="process", source=src, key=key)
        return
    _L().instant("power" if action != "reboot_bmc" else "context", device=dev_name,
                 device_id=dev_id, params=params,
                 expected_kind="process" if action != "reboot_bmc" else "none", source=src)


def redfish_action(ip, action, res) -> None:
    _fl.safe(_redfish_action, ip, action, res)


# ── tick + fleet (context) ───────────────────────────────────────────────────

def _tick(paused: Optional[bool], interval: Optional[int]) -> None:
    key = ("context", "tick_pause")
    if paused is True:
        if _L().open_id(key) is None:
            _L().open("context", params={"context": "tick_pause"}, expected_kind="none",
                      source="POST /api/tick/settings", key=key)
    elif paused is False:
        _L().close_key(key, "cleared")
    if interval is not None:
        _L().instant("context", params={"context": "tick_interval", "interval": interval},
                     expected_kind="none", source="POST /api/tick/settings")


def tick(paused=None, interval=None) -> None:
    _fl.safe(_tick, paused, interval)


def _fleet(what: str, detail: Optional[str] = None) -> None:
    key = ("context", "fleet_running")
    src = f"POST /api/fleet/{what}"
    if what == "start":
        _L().open("context", params={"context": "fleet_running"}, expected_kind="none",
                  source=src, key=key)
    elif what == "stop":
        _L().close_key(key, "cleared")
    else:
        _L().instant("context", params={"context": f"fleet_{what}", "detail": detail},
                     expected_kind="none", source=src)


def fleet(what, detail=None) -> None:
    _fl.safe(_fleet, what, detail)


# ── protocol start/stop + dataset regeneration (middleware) ─────────────────

_PROTOCOLS = ("snmp", "gnmi", "bacnet", "redfish", "modbus", "sflow")


def _protocol_event(path: str, method: str, status: int) -> None:
    """Called by the api.main middleware after every successful POST. Ledgers
    protocol stop as an open ``context`` window (data is absent until it starts
    again) and start / regenerate / reload / clear as instants."""
    if method != "POST" or status >= 400:
        return
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3 or parts[0] != "api" or parts[1] not in _PROTOCOLS:
        return
    proto, action = parts[1], "/".join(parts[2:])
    if action not in ("start", "stop", "reload", "clear", "datasets/generate",
                      "proxy/start", "proxy/stop"):
        return
    key = ("context", "protocol_stopped", proto)
    src = f"POST {path}"
    if action == "stop":
        _L().open("context", params={"context": "protocol_stopped", "protocol": proto},
                  expected_kind="none", source=src, key=key)
        return
    if action == "start":
        _L().close_key(key, "cleared")
    _L().instant("context", params={"context": f"protocol_{action.replace('/', '_')}",
                                    "protocol": proto},
                 expected_kind="none", source=src)


def protocol_event(path, method, status) -> None:
    _fl.safe(_protocol_event, path, method, status)
