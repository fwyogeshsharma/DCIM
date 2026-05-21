"""Trap REST endpoints — history and manual send."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import AppState
from api.models.schemas import (
    TrapsResponse,
    TrapRecord,
    SendTrapRequest,
    OkResponse,
)

router = APIRouter(prefix="/traps", tags=["SNMP Traps"])


def _state() -> AppState:
    return AppState.get()


@router.get("", response_model=TrapsResponse)
def get_all_traps(limit: int = 500, offset: int = 0):
    """
    Get all sent traps from the in-memory history.
    Newest traps are at the end. Max 1000 stored.
    """
    s = _state()
    with s._state_lock:
        history = list(s.trap_history)

    # Backfill severity / display_name from TrapType lookup for records
    # written before those fields were stored on the dict.
    from core.trap_definitions import TrapType, TRAP_DEFINITIONS
    enriched = []
    for t in history:
        rec = dict(t)
        if not rec.get("severity") or not rec.get("display_name"):
            type_name = rec.get("trap_type")
            if type_name:
                try:
                    defn = TRAP_DEFINITIONS.get(TrapType[type_name])
                    if defn is not None:
                        rec.setdefault("severity", defn.severity)
                        rec.setdefault("display_name", defn.display_name)
                except KeyError:
                    pass
        enriched.append(rec)

    total = len(enriched)
    page = enriched[offset: offset + limit]
    return TrapsResponse(
        total=total,
        traps=[TrapRecord(**t) for t in page],
    )


@router.post("/send", response_model=OkResponse)
def send_trap(req: SendTrapRequest):
    """
    Send an SNMP trap manually for a specific device.

    trap_type must be a valid TrapType name:
    LINK_DOWN, LINK_UP, COLD_START, WARM_START, CPU_HIGH, MEMORY_HIGH,
    TEMPERATURE_ALERT, LINK_FLAP, RACK_FAILURE, UPS_ON_BATTERY,
    UPS_LOW_BATTERY, BGP_DOWN, AUTH_FAILURE, HUMIDITY_ALERT,
    DEWPOINT_ALERT, AIRFLOW_ALERT
    """
    s = _state()
    if s.trap_engine is None:
        raise HTTPException(status_code=503, detail="Trap engine not initialized")
    if s.device_manager is None:
        raise HTTPException(status_code=503, detail="Device manager not initialized")

    device = s.device_manager.get_device(req.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device '{req.device_id}' not found")

    from core.trap_definitions import TrapType
    try:
        trap_type = TrapType[req.trap_type.upper()]
    except KeyError:
        valid = [t.name for t in TrapType]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trap_type '{req.trap_type}'. Valid: {valid}",
        )

    try:
        from core.trap_definitions import TrapType as _TT
        iface_index = req.kwargs.get("iface_index")

        # If peer_id given and trap is link state, resolve iface_index from topology
        # and break/restore the actual link.
        if req.peer_id and s.topology and trap_type in (_TT.LINK_DOWN, _TT.LINK_UP):
            edge = s.topology.get_link_data(req.device_id, req.peer_id)
            if edge is None:
                edge = s.topology.get_link_data(req.peer_id, req.device_id)
            if edge is not None and iface_index is None:
                # src_iface belongs to whichever device was listed first in the edge
                if s.topology.graph.has_edge(req.device_id, req.peer_id):
                    iface_index = edge.get("src_iface", 1)
                else:
                    iface_index = edge.get("dst_iface", 1)
                req.kwargs["iface_index"] = iface_index
            if trap_type == _TT.LINK_DOWN:
                s.topology.break_link(req.device_id, req.peer_id)
                s.notify_ui("link_changed", req.device_id, req.peer_id, True)
            else:
                s.topology.restore_link(req.device_id, req.peer_id)
                s.notify_ui("link_changed", req.device_id, req.peer_id, False)

        s.trap_engine.send_trap(device, trap_type, **req.kwargs)
        msg = f"Trap {req.trap_type} queued for device {device.name} ({device.ip_address})"
        if req.peer_id and trap_type in (_TT.LINK_DOWN, _TT.LINK_UP):
            action = "broken" if trap_type == _TT.LINK_DOWN else "restored"
            msg += f" — link to {req.peer_id} {action}"
        return OkResponse(message=msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("", response_model=OkResponse)
def clear_trap_history():
    """Clear the in-memory trap history."""
    s = _state()
    with s._state_lock:
        s.trap_history.clear()
    return OkResponse(message="Trap history cleared")
