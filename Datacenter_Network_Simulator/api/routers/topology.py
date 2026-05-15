"""Topology REST endpoints — open topology, break/restore links."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, UploadFile, File

from api.state import AppState
from api.models.schemas import (
    TopologyInfoResponse,
    LinkActionRequest,
    OkResponse,
)

router = APIRouter(prefix="/topology", tags=["Topology"])


def _state() -> AppState:
    return AppState.get()


@router.get("", response_model=TopologyInfoResponse)
def get_topology():
    """Get current topology info — device count, link count, device IDs."""
    s = _state()
    if s.topology is None:
        return TopologyInfoResponse(device_count=0, link_count=0, current_path="", devices=[])
    devices = s.topology.get_all_devices()
    return TopologyInfoResponse(
        device_count=s.topology.node_count(),
        link_count=s.topology.edge_count(),
        current_path=s.current_topology_path,
        devices=[d.id for d in devices],
    )




@router.post("/upload", response_model=TopologyInfoResponse)
async def upload_topology(file: UploadFile = File(...)):
    """Upload a topology JSON file directly from the client — no server path needed."""
    s = _state()
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Core not initialized")
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be a .json file")
    try:
        content = await file.read()
        data = json.loads(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse JSON: {e}")
    try:
        s.device_manager.clear()
        if s.ip_manager:
            s.ip_manager.reset()
        s.topology.from_dict(data)
        for device in s.topology.get_all_devices():
            s.device_manager.add_device(device)
            if s.ip_manager:
                s.ip_manager.reserve(device.ip_address)
        s.current_topology_path = file.filename
        s.notify_ui("rebuild_topology_scene")
        devices = s.topology.get_all_devices()
        return TopologyInfoResponse(
            device_count=s.topology.node_count(),
            link_count=s.topology.edge_count(),
            current_path=file.filename,
            devices=[d.id for d in devices],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/links")
def get_links(layer: str = None):
    """Get topology links. Optional ?layer=production or ?layer=management to filter."""
    s = _state()
    if s.topology is None:
        return {"links": []}
    if layer:
        raw = s.topology.get_edges_by_layer(layer)
    else:
        raw = s.topology.get_links()
    links = []
    for src_id, dst_id, edge_data in raw:
        links.append({
            "src_id": src_id,
            "dst_id": dst_id,
            "layer": edge_data.get("layer", "production"),
            "broken": s.topology.is_link_broken(src_id, dst_id),
            "src_iface": edge_data.get("src_iface"),
            "dst_iface": edge_data.get("dst_iface"),
        })
    return {"links": links, "count": len(links)}


def _send_link_traps(s, src_id: str, dst_id: str, layer: str, is_down: bool):
    """Send LINK_DOWN or LINK_UP traps on both endpoints of a production link.
    Management links carry no SNMP interface OIDs so traps are skipped."""
    if s.trap_engine is None or s.device_manager is None:
        return
    edge = s.topology.get_link_data(src_id, dst_id, layer)
    if edge is None or edge.get("layer", "production") != "production":
        return
    from core.trap_definitions import TrapType
    trap_type = TrapType.LINK_DOWN if is_down else TrapType.LINK_UP
    src_iface = edge.get("src_iface", 1)
    dst_iface = edge.get("dst_iface", 1)
    src_dev = s.device_manager.get_device(src_id)
    dst_dev = s.device_manager.get_device(dst_id)
    if src_dev:
        s.trap_engine.send_trap(src_dev, trap_type, iface_index=src_iface)
    if dst_dev:
        s.trap_engine.send_trap(dst_dev, trap_type, iface_index=dst_iface)


@router.post("/links/break", response_model=OkResponse)
def break_link(req: LinkActionRequest):
    """Break a link between two devices — sets oper_status=2 on interfaces and sends LINK_DOWN traps."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    try:
        s.topology.break_link(req.src_id, req.dst_id, req.layer)
        s.notify_ui("link_changed", req.src_id, req.dst_id, True)
        _send_link_traps(s, req.src_id, req.dst_id, req.layer, is_down=True)
        return OkResponse(message=f"Link {req.src_id} ↔ {req.dst_id} [{req.layer}] broken — LINK_DOWN traps sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/links/restore", response_model=OkResponse)
def restore_link(req: LinkActionRequest):
    """Restore a previously broken link — sets oper_status=1 and sends LINK_UP traps."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    try:
        s.topology.restore_link(req.src_id, req.dst_id, req.layer)
        s.notify_ui("link_changed", req.src_id, req.dst_id, False)
        _send_link_traps(s, req.src_id, req.dst_id, req.layer, is_down=False)
        return OkResponse(message=f"Link {req.src_id} ↔ {req.dst_id} [{req.layer}] restored — LINK_UP traps sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))