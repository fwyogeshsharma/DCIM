"""
Fault Injection HTTP API — per network-sim container.

Endpoints:
  POST /fault          Inject a fault (link_down, cpu_spike, etc.)
  DELETE /fault        Clear injected faults
  POST /profile        Switch traffic profile (normal, peak, idle)
  POST /speed          Change tick interval
  GET  /status         Container + protocol health
  GET  /devices        List all simulated devices
  POST /trap           Fire a test trap for a specific device
  GET  /health         Liveness probe
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("http_api")

# Populated by entrypoint.py after all services start
STATE: Dict[str, Any] = {}

app = FastAPI(title="Network Simulator Fault API", version="1.0.0")


# ─────────────────────────────────────────────────────────────────────────────
#  Request models
# ─────────────────────────────────────────────────────────────────────────────

class FaultRequest(BaseModel):
    fault_type: str          # link_down | cpu_spike | memory_spike | device_down | packet_loss
    device_name: Optional[str] = None
    iface_index: Optional[int] = None
    value: Optional[float] = None
    duration_sec: Optional[int] = None

class ProfileRequest(BaseModel):
    profile: str             # normal | peak | idle | stress

class SpeedRequest(BaseModel):
    tick_interval: float

class TrapRequest(BaseModel):
    device_name: str
    trap_type: str = "CPU_HIGH"
    severity: str = "major"
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_device(name: str):
    dm = STATE.get("dm")
    if not dm:
        raise HTTPException(503, "Simulator not ready")
    for dev in dm.get_all_devices():
        if dev.name == name:
            return dev
    raise HTTPException(404, f"Device not found: {name}")


def _store():
    s = STATE.get("store")
    if not s:
        raise HTTPException(503, "Simulator not ready")
    return s


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "network_id": STATE.get("network_id", "unknown")}


@app.get("/status")
def status():
    snmp  = STATE.get("snmp_ctrl")
    gnmi  = STATE.get("gnmi_ctrl")
    sflow = STATE.get("sflow_ctrl")
    store = STATE.get("store")
    dm    = STATE.get("dm")
    return {
        "network_id":     STATE.get("network_id"),
        "device_count":   dm.count() if dm else 0,
        "store_running":  store.is_running() if store else False,
        "snmp": {
            "enabled": snmp is not None,
            "running": snmp.is_running() if snmp else False,
        },
        "gnmi": {
            "enabled": gnmi is not None,
            "running": gnmi.is_running() if gnmi else False,
        },
        "sflow": {
            "enabled": sflow is not None,
            "running": sflow.is_running() if sflow else False,
        },
    }


@app.get("/topology")
def get_topology():
    """Return the loaded topology: nodes (id, name, ip, type) + edges (src_ip, dst_ip, src_iface, dst_iface)."""
    dm   = STATE.get("dm")
    topo = STATE.get("topo")
    if not dm:
        raise HTTPException(503, "Simulator not ready")

    nodes = [
        {
            "id":         getattr(d, "id", d.name),
            "name":       d.name,
            "ip_address": d.ip_address,
            "type":       str(d.device_type),
        }
        for d in dm.get_all_devices()
    ]

    edges = []
    if topo and hasattr(topo, "to_dict"):
        raw = topo.to_dict()
        ip_by_id = {n["id"]: n["device"].get("ip_address", "") for n in raw.get("nodes", [])}
        for e in raw.get("edges", []):
            src_ip = ip_by_id.get(e.get("src", ""), "")
            dst_ip = ip_by_id.get(e.get("dst", ""), "")
            if src_ip and dst_ip:
                edges.append({
                    "src_ip":    src_ip,
                    "dst_ip":    dst_ip,
                    "src_iface": e.get("src_iface", 0),
                    "dst_iface": e.get("dst_iface", 0),
                    "layer":     e.get("layer", ""),
                })

    return {"nodes": nodes, "edges": edges}


@app.get("/devices")
def list_devices():
    dm = STATE.get("dm")
    if not dm:
        raise HTTPException(503, "Simulator not ready")
    return [
        {
            "name":       d.name,
            "ip_address": d.ip_address,
            "type":       str(d.device_type),
            "vendor":     str(d.vendor),
        }
        for d in dm.get_all_devices()
    ]


@app.post("/fault")
def inject_fault(req: FaultRequest):
    store = _store()
    fault_type = req.fault_type.lower()

    if fault_type == "cpu_spike":
        dev = _get_device(req.device_name) if req.device_name else None
        value = req.value or 95.0
        targets = [dev] if dev else STATE["dm"].get_all_devices()
        for d in targets:
            d.cpu_usage = value  # device object is source of truth for rule engine + bridge
        target_name = dev.name if dev else "all devices"
        return {"ok": True, "msg": f"CPU spiked to {value}% on {target_name}"}

    elif fault_type == "memory_spike":
        dev = _get_device(req.device_name) if req.device_name else None
        value = req.value or 90.0
        targets = [dev] if dev else STATE["dm"].get_all_devices()
        for d in targets:
            d.memory_used = int(max(1, d.memory_total) * value / 100.0)
        target_name = dev.name if dev else "all devices"
        return {"ok": True, "msg": f"Memory spiked to {value}% on {target_name}"}

    elif fault_type == "link_down":
        dev = _get_device(req.device_name)
        iface_idx = req.iface_index or 0
        if iface_idx < len(dev.interfaces):
            dev.interfaces[iface_idx].oper_status = 2  # down
        return {"ok": True, "msg": f"Link {iface_idx} down on {dev.name}"}

    elif fault_type == "link_up":
        dev = _get_device(req.device_name)
        iface_idx = req.iface_index or 0
        if iface_idx < len(dev.interfaces):
            dev.interfaces[iface_idx].oper_status = 1  # up
        return {"ok": True, "msg": f"Link {iface_idx} up on {dev.name}"}

    elif fault_type == "device_down":
        dev = _get_device(req.device_name)
        for iface in dev.interfaces:
            iface.oper_status = 2
        dev.cpu_usage = 0.0
        return {"ok": True, "msg": f"Device {dev.name} marked down"}

    elif fault_type == "device_up":
        dev = _get_device(req.device_name)
        for iface in dev.interfaces:
            iface.oper_status = 1
        return {"ok": True, "msg": f"Device {dev.name} restored"}

    elif fault_type == "clear":
        return clear_faults()

    else:
        raise HTTPException(400, f"Unknown fault_type: {req.fault_type}")


@app.delete("/fault")
def clear_faults():
    dm    = STATE.get("dm")
    store = STATE.get("store")
    if not dm or not store:
        raise HTTPException(503, "Simulator not ready")
    for dev in dm.get_all_devices():
        for iface in dev.interfaces:
            iface.oper_status = 1
    return {"ok": True, "msg": "All faults cleared"}


@app.post("/profile")
def set_profile(req: ProfileRequest):
    store = _store()
    dm    = STATE.get("dm")
    profile = req.profile.lower()

    profile_params = {
        "normal": {"cpu_base": 30, "mem_base": 50, "spike_chance": 0.01},
        "peak":   {"cpu_base": 75, "mem_base": 80, "spike_chance": 0.05},
        "idle":   {"cpu_base": 5,  "mem_base": 30, "spike_chance": 0.001},
        "stress": {"cpu_base": 90, "mem_base": 92, "spike_chance": 0.15},
    }

    if profile not in profile_params:
        raise HTTPException(400, f"Unknown profile: {req.profile}. Use: {list(profile_params)}")

    params = profile_params[profile]
    if dm:
        import random
        for dev in dm.get_all_devices():
            cpu_val = params["cpu_base"] + random.uniform(-5, 5)
            mem_val = params["mem_base"] + random.uniform(-3, 3)
            dev.cpu_usage = cpu_val
            dev.memory_used = int(max(1, dev.memory_total) * mem_val / 100.0)

    return {"ok": True, "profile": profile, "params": params}


@app.post("/speed")
def set_speed(req: SpeedRequest):
    store  = _store()
    bridge = STATE.get("bridge")
    if req.tick_interval < 1:
        raise HTTPException(400, "tick_interval must be >= 1 second")
    store._tick_interval = req.tick_interval
    if bridge:
        bridge._interval = req.tick_interval
    return {"ok": True, "tick_interval": req.tick_interval}


@app.get("/telemetry")
def get_telemetry(
    protocol: Optional[str] = None,
    device: Optional[str] = None,
    limit: int = 100,
):
    bridge = STATE.get("bridge")
    if not bridge:
        raise HTTPException(503, "Bridge not ready")
    tel = bridge.telemetry
    proto = (protocol or "").lower()
    if proto == "sflow":
        return {"protocol": "sFlow", "events": tel.get_sflow(device=device, limit=limit)}
    if proto == "gnmi":
        return {"protocol": "gNMI", "events": tel.get_gnmi(device=device, limit=limit)}
    return {
        "gnmi":  tel.get_gnmi(device=device, limit=limit),
        "sflow": tel.get_sflow(device=device, limit=limit),
    }


@app.post("/trap")
def fire_trap(req: TrapRequest):
    dev          = _get_device(req.device_name)
    trap_engine  = STATE.get("trap_engine")
    bridge       = STATE.get("bridge")

    if not trap_engine or not bridge:
        raise HTTPException(503, "Trap engine not ready")

    bridge.on_alert(
        device=dev,
        trap_type=req.trap_type,
        severity=req.severity,
        details=req.message or f"Manual trap: {req.trap_type} on {dev.name}",
        rule_name="manual",
    )
    return {"ok": True, "msg": f"Trap {req.trap_type} queued for {dev.name}"}
