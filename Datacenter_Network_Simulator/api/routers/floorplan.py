"""Live DCIM floor-plan endpoint.

Builds the same asset/floor-plan document tools/export_dcim_floorplan.py produces
offline, but from the LIVE in-memory devices — so fleet-lifecycle racks (added
after load) appear on the floor plan, not just the curated static snapshot. The
room geometry (extents/aisles) is paired in from the topology that was loaded;
device placement comes from each device's floor_x/floor_y/aisle fields.

An explicitly uploaded floor-plan (POST /floorplan/upload) overrides the live
build until cleared — same idea as Upload Topology.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.state import AppState
from api.models.schemas import OkResponse
from tools.export_dcim_floorplan import build

router = APIRouter(prefix="/floorplan", tags=["Floor Plan"])

# Rebuilding the whole asset doc per request is wasteful under a polling viewer
# (or an external DCIM): the viewer polls every ~5s, but the doc only changes when
# the fleet CHURNS (devices added/removed) or the room geometry changes — never on
# a wall-clock timer. So cache by a cheap CHURN SIGNATURE (device count + room
# count) rather than a short time TTL: steady-state polls with an unchanged fleet
# return the cached doc instantly (no per-device reserialization), and the first
# poll after a churn rebuilds. A long backstop TTL still forces an eventual refresh
# for the rare net-zero churn (a decommission + provision in one tick that leaves
# the count unchanged). Built under the lock so a burst collapses into one rebuild.
_CACHE: dict = {"doc": None, "sig": None, "t": 0.0}
_LOCK = threading.Lock()
_BACKSTOP_TTL_S = 10.0


def _floorplan_sig(s) -> tuple:
    """Cheap signature that changes whenever the floor plan could change: the live
    device count and the floor-plan room count. Only an O(N) ref-list build — it
    deliberately does NOT serialize devices (to_dict/interfaces is the expensive
    part we are caching away)."""
    try:
        n_dev = len(s.device_manager.get_all_devices())
    except Exception:
        n_dev = 0
    fp = getattr(s.topology, "floorplan", {}) or {}
    n_room = len(fp.get("rooms", {}) or {})
    return (n_dev, n_room)


@router.get("")
def live_floorplan():
    """Floor-plan document for the viewer. The Floor Plan page is always LIVE: by
    default this is built from the current in-memory devices (incl. fleet-added
    racks/servers), so 2D and 3D reflect the running topology. An uploaded
    floor-plan (POST /floorplan/upload) overrides the live build until cleared."""
    s = AppState.get()
    uploaded = getattr(s, "uploaded_floorplan", None)
    if uploaded is not None:
        return uploaded
    if s.device_manager is None or s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    sig = _floorplan_sig(s)
    with _LOCK:
        now = time.monotonic()
        stale = (_CACHE["doc"] is None
                 or _CACHE["sig"] != sig
                 or (now - _CACHE["t"]) >= _BACKSTOP_TTL_S)
        if stale:
            nodes = [{"id": d.get("id"), "device": d} for d in s.device_manager.to_list()]
            _CACHE["doc"] = build({
                "nodes": nodes,
                "floorplan": getattr(s.topology, "floorplan", {}) or {},
                "metadata": {"name": "live", "description": "live floor-plan export"},
            })
            _CACHE["sig"] = sig
            _CACHE["t"] = now
        return _CACHE["doc"]


@router.get("/telemetry")
def floorplan_telemetry():
    """Slim live-telemetry feed for the floor-plan viewer's heatmap join.

    The viewer polls this every ~5s and joins it to static placement (from
    /floorplan) by device id/name. It needs ONLY the handful of fields the
    heatmap metrics read — power, inlet/outlet temp, humidity — not the full
    /devices document (85 fields/device incl. interface arrays, ~5 MB at a few
    thousand devices). Emitting just those fields (and omitting null/zero) keeps
    the payload tiny so the browser isn't parsing megabytes every poll while the
    fleet grows. Fields mirror the same sources as /devices' DeviceInfo."""
    s = AppState.get()
    dm = getattr(s, "device_manager", None)
    if dm is None:
        return {"devices": []}
    from core.device_state_store import _get_ext_state
    from core.redfish_data_generator import _live_watts
    out = []
    for d in dm.get_all_devices():
        dt = d.device_type.value
        row = {"id": d.id, "name": d.name}
        it = getattr(d, "inlet_temp", None)         # all types report inlet (like /devices)
        if it:
            row["inlet_temp"] = it
        if dt == "server":
            try:
                w = float(_live_watts(d))
            except Exception:
                w = 0.0
            if w:
                row["power_watts"] = w
            ot = getattr(d, "outlet_temp", None)
            if ot:
                row["outlet_temp"] = ot
        elif dt == "sensor":
            if getattr(d, "model_name", "") == "Raritan DPX2-T3H1":
                ot = getattr(d, "outlet_temp", None)
                if ot:
                    row["outlet_temp"] = ot
            h = getattr(d, "humidity", None)
            if h:
                row["humidity"] = h
        elif dt in ("pdu", "floor_pdu"):
            ext = _get_ext_state(d.name)
            pt = ext.get("pdu_temperature")
            if pt is not None:
                row["pdu_temperature"] = pt
            ph = ext.get("pdu_humidity")
            if ph is not None:
                row["pdu_humidity"] = ph
        out.append(row)
    # Churn signature, identical to the /floorplan cache key. The viewer polls
    # THIS slim feed every ~5s and only refetches the heavy /floorplan document
    # when `sig` changes (fleet churn / room change) — so steady state is telemetry
    # only, never the full asset-doc rebuild.
    n_dev, n_room = _floorplan_sig(s)
    return {"devices": out, "sig": f"{n_dev}:{n_room}"}


@router.post("/upload", response_model=OkResponse)
async def upload_floorplan(file: UploadFile = File(...)):
    """Upload a floor-plan JSON to drive the Floor Plan view, overriding the live
    build until cleared. Accepts a dcim-floorplan doc (racks/devices/floorplan)."""
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a .json file")
    try:
        doc = json.loads((await file.read()).decode("utf-8-sig"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse JSON: {e}")
    if not isinstance(doc, dict) or "racks" not in doc:
        raise HTTPException(status_code=400,
                            detail="Not a floor-plan JSON (missing 'racks').")
    AppState.get().uploaded_floorplan = doc
    return OkResponse(message=f"Floor-plan loaded ({len(doc.get('racks', []))} racks)")


@router.post("/clear", response_model=OkResponse)
def clear_floorplan():
    """Drop the uploaded floor-plan so /floorplan reverts to the live build."""
    AppState.get().uploaded_floorplan = None
    with _LOCK:                       # force a fresh live rebuild on the next fetch
        _CACHE["doc"] = None
        _CACHE["sig"] = None
    return OkResponse(message="Floor-plan reverted to live")
