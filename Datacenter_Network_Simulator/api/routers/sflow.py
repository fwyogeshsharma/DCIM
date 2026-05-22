"""sFlow simulator REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from api.models.schemas import OkResponse

router = APIRouter(prefix="/sflow", tags=["sFlow"])


def _state() -> AppState:
    return AppState.get()


class SFlowConfig(BaseModel):
    collector_ip:   str = "127.0.0.1"
    collector_port: int = 6343
    interval:       int = 30
    sample_rate:    int = 1000


@router.get("/status")
def sflow_status():
    s = _state()
    sf = s.sflow
    if sf is None or not sf.is_running():
        return {
            "running": False,
            "collector_ip":   getattr(sf, "_collector_ip",   "127.0.0.1"),
            "collector_port": getattr(sf, "_collector_port", 6343),
            "interval":       getattr(sf, "_interval",       30),
            "sample_rate":    getattr(sf, "_sample_rate",    1000),
            "active_devices": 0,
        }
    return {
        "running":        True,
        "collector_ip":   sf._collector_ip,
        "collector_port": sf._collector_port,
        "interval":       sf._interval,
        "sample_rate":    sf._sample_rate,
        "active_devices": sf.get_device_count(),
    }


@router.post("/start", response_model=OkResponse)
def sflow_start(cfg: SFlowConfig):
    s = _state()
    if s.sflow is None:
        raise HTTPException(status_code=503, detail="sFlow controller not initialised")
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if s.sflow.is_running():
        raise HTTPException(status_code=409, detail="sFlow already running")

    _SFLOW_TYPES = {'switch', 'router'}
    device_ips = [
        d.ip_address for d in s.device_manager.get_all_devices()
        if d.ip_address and d.device_type.value in _SFLOW_TYPES
    ]
    if not device_ips:
        raise HTTPException(status_code=400, detail="No switches or routers in topology")

    s.start_ticker_if_needed()

    if not s.sflow._log_cb:
        s.sflow.set_log_callback(lambda msg: s.notify_ui("log_sflow", msg, "info"))

    s.sflow.start(
        device_ips=device_ips,
        collector_ip=cfg.collector_ip,
        collector_port=cfg.collector_port,
        interval=cfg.interval,
        sample_rate=cfg.sample_rate,
    )
    s.notify_ui("console_log", f"[sFlow] Started — {len(device_ips)} agent(s) → {cfg.collector_ip}:{cfg.collector_port}", "success")
    s.notify_ui("sync_sflow")
    return OkResponse(message="sFlow agent started")


@router.post("/stop", response_model=OkResponse)
def sflow_stop():
    s = _state()
    if s.sflow is None:
        raise HTTPException(status_code=503, detail="sFlow controller not initialised")
    if not s.sflow.is_running():
        return OkResponse(message="sFlow was not running")
    s.sflow.stop()
    s.stop_ticker_if_idle()
    s.notify_ui("console_log", "[sFlow] Stopped.", "info")
    s.notify_ui("sync_sflow")
    return OkResponse(message="sFlow agent stopped")
