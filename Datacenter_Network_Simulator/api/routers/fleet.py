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


# ── manual capacity provisioning (user-driven, off the day scheduler) ─────────
class ProvisionBody(BaseModel):
    datacenter: str
    room: str | None = None   # target hall for provision-rack; None = busiest hall
    # Build this rack as a LIQUID rack: an in-rack CDU is installed alongside the
    # leaf and PDUs, plumbed to the hall's CHW headers. This is a commissioning-time
    # decision — a rack either has a manifold or it does not — and it is what makes
    # direct-to-chip servers placeable there at all.
    with_cdu: bool = False


def _rack_response(eng, rack: dict, message: str) -> dict:
    """Shape a provision result for the UI: the new (empty) rack's location + the
    free server-U it opened up, so the Floor-Plan page can report it and Add Device
    can deep-link straight into it."""
    from core.rack_capacity import FIRST_SERVER_UNIT, LAST_SERVER_UNIT
    # 5-tuple: a rack is only unique within its hall — see FleetLifecycleEngine._rack_key.
    dc, floor, room, row, num = rack["key"]
    free = list(range(FIRST_SERVER_UNIT, LAST_SERVER_UNIT + 1))
    return {"ok": True, "message": message,
            "rack": {"datacenter": dc, "room": rack.get("room", room),
                     "floor": rack.get("floor", floor), "rack_row": row, "rack_num": num,
                     "free_units": free, "next_free": free[0] if free else None},
            "total_servers": len(eng._servers())}


@router.post("/provision-rack")
def fleet_provision_rack(body: ProvisionBody):
    """Add ONE empty compute rack (leaf + A/B rack PDUs, wired to the pod fabric
    and RPP feeds) to a hall in *datacenter* that still has grid space. Reuses the
    fleet fill path, so spine/OOB/grid/power caps are all honoured, and the gear is
    hot-commissioned onto the live sims. 409 when every hall in the DC is full —
    the caller should open a new hall instead.

    with_cdu makes it a LIQUID rack: an in-rack CDU is installed with the rest of
    the rack BOM and plumbed to the hall's chilled-water headers. Direct-to-chip
    servers can only be racked where such a unit exists, so this is the decision
    that opens (or does not open) the rack to liquid cooling."""
    s = _state()
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    dc = (body.datacenter or "").strip()
    if not dc:
        raise HTTPException(status_code=422, detail="datacenter is required")
    room = (body.room or "").strip() or None
    rack = _engine().provision_rack(dc, room=room, with_cdu=bool(body.with_cdu))
    if rack is None:
        where = f"{dc}/{room}" if room else dc
        raise HTTPException(status_code=409, detail=(
            f"No rack space in {where} (grid, spine fabric or rack power all full). "
            f"Try another hall or provision a new hall to add capacity."))
    _dc, _floor, _room, row, num = rack["key"]
    kind = "liquid rack (CDU installed)" if rack.get("cdu") else "rack"
    return _rack_response(_engine(), rack,
                          f"Provisioned {kind} R{row}-{num:02d} in "
                          f"{dc}/{rack.get('room','')}.")


@router.post("/provision-hall")
def fleet_provision_hall(body: ProvisionBody):
    """Open a brand-new server hall in *datacenter* — its own pod fabric
    (spines+OOB), RPP pair + EV2 meters, back-wall CRAH complement and sensors,
    cloned from the DC's busiest hall — and place its first compute rack. All the
    new gear is hot-commissioned. 409 when the DC has no existing hall to clone."""
    s = _state()
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    dc = (body.datacenter or "").strip()
    if not dc:
        raise HTTPException(status_code=422, detail="datacenter is required")
    rack = _engine().provision_hall(dc)
    if rack is None:
        raise HTTPException(status_code=409, detail=(
            f"Could not open a new hall in {dc} — no existing hall to clone its "
            f"pod fabric / RPP feed from."))
    return _rack_response(_engine(), rack,
                          f"Opened new hall {rack.get('room','')} in {dc} "
                          f"(pod fabric + CRAHs + first rack).")
