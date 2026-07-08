"""Fleet Lifecycle REST endpoints — drive day-by-day server churn.

Wraps core.fleet_lifecycle.FleetLifecycleEngine: start/stop the compressed
sim-day scheduler, tune the cadence/caps, advance a day manually, read status.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.state import AppState
from api.models.schemas import OkResponse

log = logging.getLogger("fleet")
router = APIRouter(prefix="/fleet", tags=["Fleet Lifecycle"])


def _state() -> AppState:
    return AppState.get()


def _engine():
    s = _state()
    eng = getattr(s, "fleet_engine", None)
    if eng is None:
        from core.fleet_lifecycle import FleetLifecycleEngine
        eng = FleetLifecycleEngine(s, log_cb=log.info)
        s.fleet_engine = eng
    return eng


class FleetConfigBody(BaseModel):
    minutes_per_day:      float | None = None
    provision_lambda:     int | None = None
    decommission_lambda:  int | None = None
    rack_power_budget_w:  int | None = None   # per-rack power budget (W); fills until summed nameplate draw hits it
    max_racks_per_row:    int | None = None   # racks per compute row in a NEW hall
    compute_rows_per_room: int | None = None  # compute rows per NEW hall
    max_total_servers:    int | None = None


def _apply_config(eng, body: FleetConfigBody) -> str | None:
    """Apply config to the engine, clamping the server count to the resource
    hard cap. Returns a warning string if anything was clamped, else None."""
    from core.fleet_lifecycle import MAX_TOTAL_SERVERS_HARD_CAP
    warn = None
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "max_total_servers" and v > MAX_TOTAL_SERVERS_HARD_CAP:
            warn = (f"max_total_servers {v} exceeds the resource-safety cap "
                    f"{MAX_TOTAL_SERVERS_HARD_CAP}; clamped. Each server is a "
                    f"Redfish BMC socket + gNMI eventfds — beyond this the process "
                    f"can exhaust its file descriptors and drop the API listener.")
            log.warning(warn)
            v = MAX_TOTAL_SERVERS_HARD_CAP
        setattr(eng.cfg, k, v)
    return warn


@router.get("/status")
def fleet_status():
    """Current lifecycle state: enabled, sim-day, config, fleet size, recent days."""
    return _engine().status()


@router.post("/config", response_model=OkResponse)
def fleet_config(body: FleetConfigBody):
    """Update cadence/caps. Takes effect on the next sim-day (interval change is
    picked up after the current wait completes)."""
    eng = _engine()
    warn = _apply_config(eng, body)
    return OkResponse(message=warn or "Fleet config updated")


@router.post("/start", response_model=OkResponse)
def fleet_start(body: FleetConfigBody | None = None):
    """Start the compressed sim-day scheduler (optionally applying config first)."""
    eng = _engine()
    warn = None
    if body is not None:
        warn = _apply_config(eng, body)
    try:
        eng.start()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    msg = f"Fleet lifecycle started ({eng.cfg.minutes_per_day} min/day)"
    return OkResponse(message=f"{msg}. {warn}" if warn else msg)


@router.post("/stop", response_model=OkResponse)
def fleet_stop():
    """Stop the scheduler. The current fleet stays as-is (in-memory)."""
    _engine().stop()
    return OkResponse(message="Fleet lifecycle stopped")


@router.post("/advance", response_model=OkResponse)
def fleet_advance():
    """Apply exactly one sim-day of churn now (works whether or not the scheduler
    is running) — useful for demos and deterministic stepping."""
    eng = _engine()
    if _state().device_manager is None or _state().topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    summ = eng.advance_day()
    return OkResponse(message=f"Day {summ.day}: +{len(summ.added)} -{len(summ.removed)} "
                              f"(servers={summ.total_servers})")
