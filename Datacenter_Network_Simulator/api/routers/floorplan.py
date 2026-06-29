"""Live DCIM floor-plan endpoint.

Builds the same asset/floor-plan document tools/export_dcim_floorplan.py produces
offline, but from the LIVE in-memory devices — so fleet-lifecycle racks (added
after load) appear on the floor plan, not just the curated static snapshot. The
room geometry (extents/aisles) is paired in from the topology that was loaded;
device placement comes from each device's floor_x/floor_y/aisle fields.
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, HTTPException

from api.state import AppState
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
    """Current floor-plan (curated + fleet-added racks), built from live devices.
    Cached ~2 s to coalesce frequent polls."""
    s = AppState.get()
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
