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
# (or an external DCIM). Cache it for a short TTL and build under the lock, so a
# burst of concurrent/rapid requests collapses into a single rebuild. The TTL is
# short enough that fleet-added racks still appear within one viewer poll.
_CACHE: dict = {"doc": None, "t": 0.0}
_LOCK = threading.Lock()
_TTL_S = 2.0

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
    with _LOCK:
        now = time.monotonic()
        if _CACHE["doc"] is None or (now - _CACHE["t"]) >= _TTL_S:
            nodes = [{"id": d.get("id"), "device": d} for d in s.device_manager.to_list()]
            _CACHE["doc"] = build({
                "nodes": nodes,
                "floorplan": getattr(s.topology, "floorplan", {}) or {},
                "metadata": {"name": "live", "description": "live floor-plan export"},
            })
            _CACHE["t"] = now
        return _CACHE["doc"]


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
    return OkResponse(message="Floor-plan reverted to live")
