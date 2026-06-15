"""Redfish simulator REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from api.models.schemas import OkResponse

router = APIRouter(prefix="/redfish", tags=["Redfish"])


def _state() -> AppState:
    return AppState.get()


class RedfishConfig(BaseModel):
    port:     int = 443
    username: str = "admin"
    password: str = "password"


def _bmc_ip(device) -> str:
    """BMC lives on the OOB mgmt net when present, else the production IP."""
    return getattr(device, "mgmt_ip", "") or device.ip_address


@router.get("/status")
def redfish_status():
    s = _state()
    rf = s.redfish
    if rf is None or not rf.is_running():
        return {
            "running": False,
            "port": getattr(rf, "_port", 443),
            "active_devices": 0,
            "sessions": 0,
        }
    return {
        "running": True,
        "port": rf.get_port(),
        "active_devices": rf.device_count(),
        "sessions": len(rf.get_sessions()),
        "devices": rf.get_device_summary(),
    }


@router.post("/start", response_model=OkResponse)
def redfish_start(cfg: RedfishConfig):
    s = _state()
    if s.redfish is None:
        raise HTTPException(status_code=503, detail="Redfish controller not initialised")
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.redfish.is_running():
        raise HTTPException(status_code=409, detail="Redfish already running")

    from core.device_manager import DeviceType
    servers = [
        d for d in s.device_manager.get_all_devices()
        if d.device_type == DeviceType.SERVER
    ]
    if not servers:
        raise HTTPException(status_code=400, detail="No servers in topology")

    # Only start BMCs whose IP is actually bound to a network interface.
    bound_set = set(s.bound_ips) | set(s.gnmi_bound_ips)
    bound_servers = [d for d in servers if _bmc_ip(d) in bound_set]
    if not bound_servers:
        raise HTTPException(
            status_code=400,
            detail=f"Found {len(servers)} server(s) but none have a bound IP. "
                   f"Bind server IPs first, then start Redfish.")

    # Start the metrics ticker so Device fields (cpu/mem/disk/temps/octets)
    # advance every tick — Redfish reads them live each request.
    s.start_ticker_if_needed()

    if not s.redfish._log_cb:
        s.redfish.set_log_callback(
            lambda msg, lvl="info": s.notify_ui("log_redfish", msg, lvl))

    ok = s.redfish.start(
        devices=bound_servers,
        port=cfg.port,
        username=cfg.username,
        password=cfg.password,
        ip_for=_bmc_ip,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Redfish failed to bind any BMC")

    s.notify_ui("console_log",
                f"[Redfish] Started — {len(bound_servers)} BMC(s) on port {cfg.port}",
                "success")
    s.notify_ui("sync_redfish")
    return OkResponse(message=f"Redfish started on {len(bound_servers)} server(s)")


@router.post("/stop", response_model=OkResponse)
def redfish_stop():
    s = _state()
    if s.redfish is None:
        raise HTTPException(status_code=503, detail="Redfish controller not initialised")
    if not s.redfish.is_running():
        return OkResponse(message="Redfish was not running")
    s.redfish.stop()
    s.stop_ticker_if_idle()
    s.notify_ui("console_log", "[Redfish] Stopped.", "info")
    s.notify_ui("sync_redfish")
    return OkResponse(message="Redfish stopped")


@router.get("/sessions")
def redfish_sessions():
    s = _state()
    if s.redfish is None or not s.redfish.is_running():
        return {"sessions": []}
    return {"sessions": s.redfish.get_sessions()}


_ACTIONS = {"power_on", "power_off", "reboot", "power_cycle",
            "led_on", "led_off", "refresh", "clear_log"}


class RedfishAction(BaseModel):
    ip: str
    action: str


@router.post("/action")
def redfish_action(req: RedfishAction):
    """Run a Server Operation (power/LED/refresh/clear-log) on one BMC."""
    s = _state()
    if s.redfish is None or not s.redfish.is_running():
        raise HTTPException(status_code=503, detail="Redfish not running")
    if req.action not in _ACTIONS:
        raise HTTPException(status_code=400,
                            detail=f"Unknown action '{req.action}'")
    res = s.redfish.perform_action(req.ip, req.action)
    if res is None:
        raise HTTPException(status_code=404, detail=f"No BMC at {req.ip}")
    s.notify_ui("console_log",
                f"[Redfish] {res['device']} ← {req.action}: {res['message']}",
                "success" if res["ok"] else "warning")
    s.notify_ui("sync_redfish")
    return res


@router.get("/log")
def redfish_log(ip: str):
    """Return the System Event Log (SEL) entries for one BMC."""
    s = _state()
    if s.redfish is None or not s.redfish.is_running():
        raise HTTPException(status_code=503, detail="Redfish not running")
    entries = s.redfish.get_sel(ip)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"No BMC at {ip}")
    return {"ip": ip, "entries": entries}


@router.get("/subscriptions")
def redfish_subscriptions():
    """List push-model event subscriptions (EventDestination) across all BMCs."""
    s = _state()
    if s.redfish is None or not s.redfish.is_running():
        return {"subscriptions": []}
    return {"subscriptions": s.redfish.get_subscriptions()}


class RedfishTestEvent(BaseModel):
    ip:         str
    message:    str = "Test event"
    severity:   str = "OK"
    event_type: str = "Alert"


@router.post("/test-event")
def redfish_test_event(req: RedfishTestEvent):
    """Fire a test Event from one BMC to all its push subscribers."""
    s = _state()
    if s.redfish is None or not s.redfish.is_running():
        raise HTTPException(status_code=503, detail="Redfish not running")
    res = s.redfish.submit_test_event(
        req.ip, message=req.message, severity=req.severity,
        event_type=req.event_type)
    if res is None:
        raise HTTPException(status_code=404, detail=f"No BMC at {req.ip}")
    s.notify_ui("console_log",
                f"[Redfish] {res['device']} → test event to "
                f"{res['subscribers']} subscriber(s)", "info")
    return res
