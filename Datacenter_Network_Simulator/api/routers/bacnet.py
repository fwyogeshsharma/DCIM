"""BACnet/IP simulator REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from api.models.schemas import OkResponse

router = APIRouter(prefix="/bacnet", tags=["BACnet"])


def _state() -> AppState:
    return AppState.get()


class BACnetConfig(BaseModel):
    base_instance: int   = 40001
    frequency_hz:  float = 50.0
    port:          int   = 47808


@router.get("/status")
def bacnet_status():
    s  = _state()
    bc = s.bacnet
    if bc is None or not bc.is_running():
        return {
            "running":        False,
            "base_instance":  getattr(bc, "_base_instance", 40001),
            "frequency_hz":   getattr(bc, "_frequency_hz",  50.0),
            "port":           getattr(bc, "_port",          47808),
            "active_devices": 0,
            "devices":        [],
        }
    return {
        "running":        True,
        "base_instance":  getattr(bc, "_base_instance", 40001),
        "frequency_hz":   getattr(bc, "_frequency_hz",  50.0),
        "port":           getattr(bc, "_port",          47808),
        "active_devices": bc.device_count(),
        "devices":        bc.get_device_summary(),
    }


@router.post("/start", response_model=OkResponse)
def bacnet_start(cfg: BACnetConfig):
    import re as _re
    s = _state()
    if s.bacnet is None:
        raise HTTPException(status_code=503, detail="BACnet controller not initialised")
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.bacnet.is_running():
        raise HTTPException(status_code=409, detail="BACnet already running")

    from core.device_manager import DeviceType
    ev2_devices = [
        d for d in s.device_manager.get_all_devices()
        if d.device_type == DeviceType.ENERGY_MONITOR
    ]
    if not ev2_devices:
        raise HTTPException(
            status_code=400,
            detail="No energy_monitor devices in topology. "
                   "Add Verdigris EV2 devices (device_type=energy_monitor) and bind IPs first."
        )

    # EV2 devices are mgmt_only: ip_address is "" and real IP is mgmt_ip.
    # Only start on IPs that are actually bound — unbound IPs have no OS route.
    bound_set = set(s.bound_ips) | set(s.gnmi_bound_ips)
    bound_devices = [
        d for d in ev2_devices
        if (d.ip_address and d.ip_address in bound_set)
        or (getattr(d, "mgmt_ip", None) and d.mgmt_ip in bound_set)
    ]

    if not bound_devices:
        raise HTTPException(
            status_code=400,
            detail=f"Found {len(ev2_devices)} EV2 device(s) but none of their IPs are bound. "
                   "Bind IPs first (Binding panel → Bind IPs), then start BACnet."
        )

    # Build per-device circuit count from model_name ("Verdigris EV2-42" → 42)
    circuits_map: dict = {}
    device_ips: list = []
    for d in bound_devices:
        ip = d.ip_address or getattr(d, "mgmt_ip", None)
        if ip:
            device_ips.append(ip)
            m = _re.search(r"EV2-(\d+)", d.model_name or "")
            circuits_map[ip] = int(m.group(1)) if m else 42

    unbound = len(ev2_devices) - len(bound_devices)

    s.start_ticker_if_needed()

    if not s.bacnet._log_cb:
        s.bacnet.set_log_callback(
            lambda msg, lvl="info": s.notify_ui("log_bacnet", msg, lvl)
        )

    s.bacnet.start(
        device_ips=device_ips,
        base_instance=cfg.base_instance,
        circuits_map=circuits_map,
        frequency_hz=cfg.frequency_hz,
        port=cfg.port,
    )

    if s.state_store and hasattr(s.state_store, "enable_bacnet"):
        s.state_store.enable_bacnet(s.bacnet)

    if unbound:
        s.notify_ui("log_bacnet",
                    f"[BACnet] Warning: {unbound} EV2 device(s) skipped — IPs not bound.",
                    "warning")
    s.notify_ui("console_log",
                f"[BACnet] Started — {len(device_ips)} EV2 device(s).", "success")
    s.notify_ui("sync_bacnet")
    return OkResponse(message="BACnet simulator started")


@router.post("/stop", response_model=OkResponse)
def bacnet_stop():
    s = _state()
    if s.bacnet is None:
        raise HTTPException(status_code=503, detail="BACnet controller not initialised")
    if not s.bacnet.is_running():
        return OkResponse(message="BACnet was not running")

    if s.state_store and hasattr(s.state_store, "disable_bacnet"):
        s.state_store.disable_bacnet()

    s.bacnet.stop()
    s.stop_ticker_if_idle()
    s.notify_ui("console_log", "[BACnet] Stopped.", "info")
    s.notify_ui("sync_bacnet")
    return OkResponse(message="BACnet simulator stopped")
