"""Modbus/TCP simulator control — the facility electrical plane.

Start/stop policy mirrors api/routers/bacnet.py's PLANT branch, not its EV2
branch: the device set is entirely facility gear, and refusing to start the whole
plane because one generator's mgmt IP is unbound would be the worse trade. Gaps
are reported by name and skipped.
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.models.schemas import OkResponse
from api.routers._bind_guard import bound_set
from api.state import AppState

router = APIRouter(prefix="/modbus", tags=["Modbus"])


def _state() -> "AppState":
    return AppState.get()


class ModbusConfig(BaseModel):
    port: int = 502
    # Arms the write path. Default closed, because a Modbus master that can write
    # is a Modbus master that can start a generator, and most real sites run this
    # plane read-only. Per-map `write_enabled` still gates which points exist.
    write_enabled: bool = False


class ModbusWrite(BaseModel):
    device: str
    space: str          # "coil" | "holding"
    addr: int
    value: float


class ModbusWriteArm(BaseModel):
    enabled: bool
    device: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Status / lifecycle
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/status")
def modbus_status():
    s = _state()
    mc = s.modbus
    if mc is None:
        return {"running": False, "available": False, "port": 502,
                "active_devices": 0, "devices": [], "stats": {}}
    if not mc.is_running():
        return {"running": False, "available": True,
                "port": getattr(mc, "_port", 502),
                "active_devices": 0, "devices": [], "stats": dict(mc.stats)}
    return {"running": True, "available": True,
            "port": getattr(mc, "_port", 502),
            "active_devices": mc.device_count(),
            "devices": mc.get_device_summary(),
            "stats": dict(mc.stats)}


@router.get("/candidates")
def modbus_candidates():
    """Which topology devices this plane would serve, and whether they're bound.

    Lets the panel show the gap before Start rather than as a warning after it.
    """
    from core.modbus_register_map import MODBUS_DEVICE_TYPES, get_map
    s = _state()
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")

    from core.modbus_register_map import get_probe_map
    from core.device_state_store import _probe_role

    have = bound_set(s)
    rows = []
    for d in s.device_manager.get_all_devices():
        dt = getattr(d.device_type, "value", str(d.device_type))
        role = getattr(d, "modbus_role", "")

        if role == "rtu_slave":
            # Bound-ness is the GATEWAY's property. A transmitter with no IP is
            # not "unbound" — it is correctly modelled, and showing it as a gap
            # would send the user hunting for an address that must not exist.
            gw = getattr(d, "modbus_gateway_ip", "")
            mm = get_probe_map(_probe_role(d) or "")
            rows.append({"name": d.name, "device_type": dt, "ip": gw,
                         "bound": gw in have, "role": "rtu_slave",
                         "unit_id": getattr(d, "modbus_unit_id", 0),
                         "map_id": mm.map_id if mm else "",
                         "vendor": mm.vendor if mm else "",
                         "product": mm.product if mm else "",
                         "word_order": mm.word_order if mm else ""})
            continue

        is_gw = dt == "modbus_gateway"
        if dt not in MODBUS_DEVICE_TYPES and not is_gw:
            continue
        ip = d.ip_address or getattr(d, "mgmt_ip", "") or ""
        mm = get_map(dt)
        rows.append({"name": d.name, "device_type": dt, "ip": ip,
                     "bound": ip in have, "role": "gateway" if is_gw else "server",
                     "unit_id": getattr(d, "modbus_unit_id", 1),
                     "map_id": mm.map_id if mm else ("RTU trunk" if is_gw else ""),
                     "vendor": mm.vendor if mm else "",
                     "product": mm.product if mm else "",
                     "word_order": mm.word_order if mm else ""})
    rows.sort(key=lambda r: (r["device_type"], r["name"]))
    return {"total": len(rows), "bound": sum(1 for r in rows if r["bound"]),
            "devices": rows}


@router.post("/start", response_model=OkResponse)
def modbus_start(cfg: ModbusConfig):
    from core.modbus_register_map import MODBUS_DEVICE_TYPES
    s = _state()
    if s.modbus is None:
        raise HTTPException(status_code=503, detail="Modbus controller not initialised")
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.modbus.is_running():
        raise HTTPException(status_code=409, detail="Modbus already running")

    from core.device_state_store import _probe_role

    have = bound_set(s)
    all_devices = s.device_manager.get_all_devices()
    devices, skipped = [], []

    # Gateways first: an RTU slave is unreachable until its trunk exists, and
    # _install rejects a gateway whose IP is already claimed by a native server.
    gateway_ip_by_name = {}
    for d in all_devices:
        if getattr(d.device_type, "value", "") != "modbus_gateway":
            continue
        ip = (d.ip_address if d.ip_address in have else None) \
            or (getattr(d, "mgmt_ip", "") if getattr(d, "mgmt_ip", "") in have else None)
        if not ip:
            skipped.append(d.name)
            continue
        gateway_ip_by_name[d.name] = ip
        devices.append({"ip": ip, "name": d.name, "device_type": "modbus_gateway",
                        "role": "gateway"})

    for d in all_devices:
        dt = getattr(d.device_type, "value", str(d.device_type))

        # Field transmitters on an RS-485 trunk. No IP by design — they are
        # reached through their gateway by unit id.
        if getattr(d, "modbus_role", "") == "rtu_slave":
            gw = getattr(d, "modbus_gateway_ip", "")
            if gw not in set(gateway_ip_by_name.values()):
                skipped.append(d.name)
                continue
            devices.append({"ip": "", "name": d.name, "device_type": dt,
                            "unit_id": int(getattr(d, "modbus_unit_id", 0) or 0),
                            "role": "rtu_slave", "gateway_ip": gw,
                            "probe_role": _probe_role(d) or ""})
            continue

        if dt not in MODBUS_DEVICE_TYPES:
            continue
        # Facility gear is mgmt_only in this topology: ip_address is "" and the
        # real address is mgmt_ip. Same resolution order as bacnet.py.
        ip = (d.ip_address if d.ip_address in have else None) \
            or (getattr(d, "mgmt_ip", "") if getattr(d, "mgmt_ip", "") in have else None)
        if not ip:
            skipped.append(d.name)
            continue
        devices.append({"ip": ip, "name": d.name, "device_type": dt,
                        "unit_id": int(getattr(d, "modbus_unit_id", 1) or 1),
                        "role": "server"})

    if not devices:
        raise HTTPException(
            status_code=400,
            detail="No Modbus-capable devices with bound IPs. This plane serves "
                   "utility_feed / switchgear / mcc / mpp / generator / ats / ups. "
                   "Bind their mgmt IPs in the Binding panel first.")

    s.start_ticker_if_needed()
    if not s.modbus._log_cb:
        s.modbus.set_log_callback(
            lambda msg, lvl="info": s.notify_ui("log_modbus", f"[Modbus] {msg}", lvl))
    s.modbus.set_write_callback(_make_write_cb(s))

    started = s.modbus.start(devices, port=cfg.port,
                             write_enabled=cfg.write_enabled)
    if not started:
        raise HTTPException(
            status_code=409,
            detail=f"Modbus failed to start — could not bind port {cfg.port}. "
                   f"On Linux ports below 1024 need privilege; on Windows this is "
                   f"usually a second copy of the app or a WinNAT reservation.")

    if skipped:
        s.notify_ui("log_modbus",
                    f"[Modbus] Warning: {len(skipped)} device(s) skipped — mgmt IPs "
                    f"not bound: {', '.join(skipped[:8])}"
                    + (f" (+{len(skipped) - 8} more)" if len(skipped) > 8 else ""),
                    "warning")

    if s.state_store and hasattr(s.state_store, "enable_modbus"):
        s.state_store.enable_modbus(s.modbus)

    s.notify_ui("console_log",
                f"[Modbus] Started — {len(devices)} server(s) on :{cfg.port}.", "success")
    s.notify_ui("sync_modbus")
    return OkResponse(message=f"Modbus simulator started ({len(devices)} devices)")


@router.post("/stop", response_model=OkResponse)
def modbus_stop():
    s = _state()
    if s.modbus is None:
        raise HTTPException(status_code=503, detail="Modbus controller not initialised")
    s.modbus.stop()
    if s.state_store and hasattr(s.state_store, "disable_modbus"):
        s.state_store.disable_modbus()
    s.stop_ticker_if_idle()
    s.notify_ui("sync_modbus")
    return OkResponse(message="Modbus simulator stopped")


# ─────────────────────────────────────────────────────────────────────────────
#  Register browser
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/registers")
def modbus_registers(device: str):
    s = _state()
    if s.modbus is None or not s.modbus.is_running():
        raise HTTPException(status_code=409, detail="Modbus is not running")
    slave = s.modbus.get_slave(device)
    if slave is None:
        raise HTTPException(status_code=404, detail=f"No Modbus slave named {device}")
    return {"device": device, "map_id": slave.map.map_id,
            "vendor": slave.map.vendor, "product": slave.map.product,
            "word_order": slave.map.word_order, "unit_id": slave.unit_id,
            "online": slave.online, "write_enabled": slave.write_enabled,
            "points": slave.snapshot()}


@router.get("/map/export")
def modbus_map_export(device_type: str):
    """The register map as a CSV — the artefact a real integrator works from.

    The header carries the map_id so the "these are not the vendor's addresses"
    caveat travels with the file.
    """
    from core.modbus_register_map import get_map, REG_SPACES
    mm = get_map(device_type)
    if mm is None:
        raise HTTPException(status_code=404, detail=f"No Modbus map for {device_type}")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# {mm.vendor} {mm.product}"])
    w.writerow([f"# map_id={mm.map_id} word_order={mm.word_order}"])
    w.writerow(["# SIMULATOR REGISTER MAP — these addresses are the simulator's, "
                "NOT the vendor's published map."])
    w.writerow(["space", "address", "name", "type", "scale", "units",
                "writable", "source_key"])
    for space, points in mm.points.items():
        for p in sorted(points, key=lambda x: x.addr):
            w.writerow([space, p.addr, p.name,
                        p.dtype if space in REG_SPACES else "bit",
                        p.scale if space in REG_SPACES else "",
                        p.units, "yes" if p.writable else "no", p.key])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="{mm.map_id}.csv"'})


# ─────────────────────────────────────────────────────────────────────────────
#  Write path
# ─────────────────────────────────────────────────────────────────────────────
def _make_write_cb(s):
    """Route a Modbus write into the same override channel the API already uses.

    A write must NEVER be poked into `ext`: the store rewrites `ext` every tick,
    so the master would watch its own write evaporate within a second.

    The action registry is empty today, and that is the honest state of things.
    The one command worth having — the EMCP remote-start coil — needs a command
    surface on PowerTransferEngine, which owns the genset lifecycle; see the
    generator entry in core/modbus_register_map.py. Until then no map declares a
    writable point, every write is refused at the address check, and this
    callback is the seam that stays ready for the first real one.
    """
    _ACTIONS = {}       # action name -> handler(store, device_name, value) -> bool

    def _cb(device_name: str, action: str, value: float) -> bool:
        store = s.state_store
        handler = _ACTIONS.get(action)
        if store is None or handler is None:
            return False
        try:
            ok = bool(handler(store, device_name, value))
        except Exception:
            return False
        if ok:
            s.notify_ui("log_modbus",
                        f"[Modbus] {device_name}: {action} = {value} "
                        f"written by Modbus master", "warning")
        return ok
    return _cb


@router.post("/write", response_model=OkResponse)
def modbus_write(body: ModbusWrite):
    """Exercise the write path from the UI without a Modbus client.

    Goes through the same ModbusSlave entry point a real master hits, so the
    Local/Remote interlock and the read-only-space refusal apply identically.
    """
    import struct
    from simulator.modbus_device import (
        FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE_REGISTER,
    )
    s = _state()
    if s.modbus is None or not s.modbus.is_running():
        raise HTTPException(status_code=409, detail="Modbus is not running")
    slave = s.modbus.get_slave(body.device)
    if slave is None:
        raise HTTPException(status_code=404, detail=f"No Modbus slave named {body.device}")

    if body.space == "coil":
        pdu = struct.pack(">BHH", FC_WRITE_SINGLE_COIL, body.addr,
                          0xFF00 if body.value else 0x0000)
    elif body.space == "holding":
        pdu = struct.pack(">BHH", FC_WRITE_SINGLE_REGISTER, body.addr,
                          int(body.value) & 0xFFFF)
    else:
        raise HTTPException(status_code=400,
                            detail="space must be 'coil' or 'holding'")

    resp = slave.handle_pdu(pdu)
    if resp and resp[0] & 0x80:
        code = resp[1]
        detail = {
            0x01: "illegal function",
            0x02: "illegal data address — not a writable point at that address",
            0x03: "illegal data value",
            0x04: "slave device failure — writes are not armed for this device "
                  "(Local mode), or the override was refused",
        }.get(code, f"exception {code}")
        raise HTTPException(status_code=400, detail=f"Modbus exception 0x{code:02X}: {detail}")
    return OkResponse(message=f"Wrote {body.value} to {body.device} {body.space}[{body.addr}]")


@router.post("/write-enable", response_model=OkResponse)
def modbus_write_enable(body: ModbusWriteArm):
    s = _state()
    if s.modbus is None or not s.modbus.is_running():
        raise HTTPException(status_code=409, detail="Modbus is not running")
    n = s.modbus.set_write_enabled(body.enabled, body.device)
    s.notify_ui("log_modbus",
                f"[Modbus] Write path {'ARMED' if body.enabled else 'disarmed'} "
                f"on {n} device(s)", "warning" if body.enabled else "info")
    return OkResponse(message=f"Write path {'armed' if body.enabled else 'disarmed'} "
                              f"on {n} device(s)")
