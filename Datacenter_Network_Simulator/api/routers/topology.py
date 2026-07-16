"""Topology REST endpoints — open topology, break/restore links."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, HTTPException, UploadFile, File

from api.state import AppState
from api.models.schemas import (
    TopologyInfoResponse,
    LinkActionRequest,
    OkResponse,
)
from pydantic import BaseModel
from typing import Optional, Dict

class CreateLinkRequest(BaseModel):
    src_id: str
    dst_id: str
    layer: str = "production"
    # Explicit port (interface list-index) on each end. None = auto-pick the next
    # free port, the old behaviour. When given, the port must be free (else 409).
    src_iface: Optional[int] = None
    dst_iface: Optional[int] = None

class LayoutRequest(BaseModel):
    algorithm: str  # "default" | "spring" | "shell" | "kamada_kawai"


class Point(BaseModel):
    x: float
    y: float


class PositionsRequest(BaseModel):
    positions: Dict[str, Point]   # device id → canvas coordinate


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
        data = json.loads(content.decode("utf-8-sig"))
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


def _patch_link_endpoints(s, src_id: str, dst_id: str):
    """Background: run patch_metrics + patch_lldp on both link endpoints.

    Mirrors what the desktop UI does via _regenerate_device_live — keeps the
    .snmprec files in sync with the in-memory topology state so that broken /
    restored links are reflected in ifOperStatus AND the LLDP neighbor table.
    """
    if s.device_manager is None or s.topology is None:
        return
    src_dev = s.device_manager.get_device(src_id)
    dst_dev = s.device_manager.get_device(dst_id)
    if not (src_dev or dst_dev):
        return

    from core.snmprec_generator import SNMPRecGenerator
    gen = SNMPRecGenerator(s.snmp_datasets_dir)
    for dev in filter(None, [src_dev, dst_dev]):
        try:
            gen.patch_metrics(dev)
            gen.patch_lldp(dev, s.topology)
        except Exception:
            pass


def _sync_iface_history(s, *device_ids: str):
    """Resync the rule engine's iface snapshot for these devices so the ticker
    does NOT re-fire a duplicate LinkDown/LinkUp on top of the explicit trap
    sent by _send_link_traps. No-op when the rule engine is absent."""
    if s.rule_engine is None or s.device_manager is None:
        return
    for did in device_ids:
        dev = s.device_manager.get_device(did)
        if dev:
            s.rule_engine.sync_iface_history(dev)


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
    src_dev = s.device_manager.get_device(src_id)
    dst_dev = s.device_manager.get_device(dst_id)
    # Resolve each endpoint's interface by which one actually faces the peer
    # (connected_to_device) — NOT edge["src_iface"]/["dst_iface"], which can hold
    # a stale/wrong index that points at a NEIGHBOUR link's port. The desktop UI
    # resolves this way; using the edge indices made the consumer correlate the
    # trap to the wrong link, producing a spurious second link-down event.
    for dev, peer_id in ((src_dev, dst_id), (dst_dev, src_id)):
        if not dev:
            continue
        iface = next(
            (i for i in dev.interfaces if i.connected_to_device == peer_id),
            dev.interfaces[0] if dev.interfaces else None,
        )
        kwargs = {"iface_index": iface.index} if iface else {}
        s.trap_engine.send_trap(dev, trap_type, **kwargs)


@router.get("/export")
def export_topology():
    """Download full topology as JSON (nodes + edges + positions)."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    return s.topology.to_dict()


@router.post("/clear", response_model=OkResponse)
def clear_topology():
    """Clear all devices and links from the topology."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    s.topology.clear()
    if s.device_manager:
        s.device_manager.clear()
    if s.ip_manager:
        s.ip_manager.reset()
    s.current_topology_path = ""
    s.notify_ui("rebuild_topology_scene")
    return OkResponse(message="Topology cleared")


@router.post("/links/break", response_model=OkResponse)
def break_link(req: LinkActionRequest):
    """Break a link between two devices — sets oper_status=2 on interfaces and sends LINK_DOWN traps."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if req.layer != "production":
        raise HTTPException(status_code=400,
                            detail="Only production links are breakable")
    try:
        s.topology.break_link(req.src_id, req.dst_id, req.layer)
        s.notify_ui("link_changed", req.src_id, req.dst_id, True)
        _sync_iface_history(s, req.src_id, req.dst_id)
        _send_link_traps(s, req.src_id, req.dst_id, req.layer, is_down=True)
        threading.Thread(
            target=_patch_link_endpoints,
            args=(s, req.src_id, req.dst_id),
            daemon=True,
        ).start()
        return OkResponse(message=f"Link {req.src_id} ↔ {req.dst_id} [{req.layer}] broken — LINK_DOWN traps sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/links/restore", response_model=OkResponse)
def restore_link(req: LinkActionRequest):
    """Restore a previously broken link — sets oper_status=1 and sends LINK_UP traps."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if req.layer != "production":
        raise HTTPException(status_code=400,
                            detail="Only production links are breakable")
    try:
        s.topology.restore_link(req.src_id, req.dst_id, req.layer)
        s.notify_ui("link_changed", req.src_id, req.dst_id, False)
        _sync_iface_history(s, req.src_id, req.dst_id)
        _send_link_traps(s, req.src_id, req.dst_id, req.layer, is_down=False)
        threading.Thread(
            target=_patch_link_endpoints,
            args=(s, req.src_id, req.dst_id),
            daemon=True,
        ).start()
        return OkResponse(message=f"Link {req.src_id} ↔ {req.dst_id} [{req.layer}] restored — LINK_UP traps sent")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _port_terminations(s, device_id: str) -> dict:
    """iface list-index -> list of live terminations on it, read from the actual
    EDGES (production + management) rather than the interface's cached
    connected_to_device (which only holds the last write). A device's data uplink
    and its BMC/console can share one iface index across layers, so reading edges
    surfaces EVERY termination — the ToR link is not hidden behind the OOB link."""
    term: dict = {}
    for u, v, d in s.topology.get_links():
        layer = d.get("layer", "production")
        if layer not in ("production", "management"):
            continue
        # Which end is which comes from the edge's own src_node/dst_node, NOT from
        # (u, v): the graph is undirected, so edges() reports each link from
        # whichever endpoint networkx walks first — that is not the end the
        # src_iface/dst_iface pair was recorded against.
        src = d.get("src_node", u)
        dst = d.get("dst_node", v)
        if src == device_id:
            myif, peer_id, peer_if = d.get("src_iface"), dst, d.get("dst_iface")
        elif dst == device_id:
            myif, peer_id, peer_if = d.get("dst_iface"), src, d.get("src_iface")
        else:
            continue
        if myif is None:
            continue
        pd = s.device_manager.get_device(peer_id) if s.device_manager else None
        pname = pd.name if pd else peer_id
        pifn = (pd.interfaces[peer_if].name
                if pd and peer_if is not None and 0 <= peer_if < len(pd.interfaces) else None)
        term.setdefault(myif, []).append({"peer": pname, "peer_iface": pifn, "layer": layer})
    return term


@router.get("/devices/{device_id}/ports")
def device_ports(device_id: str):
    """List a device's ports for the link-builder port pickers: every interface,
    each flagged used/free with its peer(s), so the UI can show ALL ports but only
    let the operator pick a FREE one. `iface` is the value to pass back as
    create_link's src_iface/dst_iface (the interface list-index add_link uses)."""
    s = _state()
    if s.topology is None or s.device_manager is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    dev = s.device_manager.get_device(device_id)
    if dev is None:
        raise HTTPException(status_code=404, detail=f"Device '{device_id}' not found")
    term = _port_terminations(s, device_id)
    ports = []
    for i, itf in enumerate(dev.interfaces):
        conns = term.get(i, [])
        peer = None
        if conns:
            # Label prefers the production (data) peer so the ToR/uplink is visible;
            # the full list stays in `connections`.
            c = next((c for c in conns if c["layer"] == "production"), conns[0])
            peer = f"{c['peer']} - {c['peer_iface']}" if c["peer_iface"] else c["peer"]
        ports.append({"iface": i, "name": itf.name, "used": bool(conns),
                      "peer": peer, "connections": conns,
                      "role": getattr(itf, "role", "data")})
    return {"device_id": device_id, "name": dev.name,
            "interface_count": len(dev.interfaces),
            "free": sum(1 for p in ports if not p["used"]), "ports": ports}


@router.post("/links/create", response_model=OkResponse)
def create_link(req: CreateLinkRequest):
    """Create a new link between two devices, optionally on explicit ports."""
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    # Validate any explicitly-chosen port: it must exist and be free. The UI only
    # offers free ports, but a stale view (or a direct API call) could still target
    # an occupied one, and add_link would otherwise silently overwrite it.
    for who, dev_id, iface in (("Source", req.src_id, req.src_iface),
                               ("Destination", req.dst_id, req.dst_iface)):
        if iface is None:
            continue
        dev = s.device_manager.get_device(dev_id) if s.device_manager else None
        if dev is None:
            raise HTTPException(status_code=404, detail=f"{who} device not found")
        if not (0 <= iface < len(dev.interfaces)):
            raise HTTPException(status_code=422, detail=f"{who} port {iface} out of range")
        # A dedicated mgmt port hangs off the management CPU, not the switching
        # ASIC — it physically cannot carry production traffic, so no production
        # link may terminate there. The reverse is NOT blocked: management over a
        # data port is in-band mgmt, which is real, and is how the OOB switches and
        # OOB firewalls/routers carry the mgmt plane by design.
        itf = dev.interfaces[iface]
        if req.layer == "production" and getattr(itf, "role", "data") == "mgmt":
            raise HTTPException(
                status_code=422,
                detail=f"{who} port {itf.name} is a management port — "
                       f"it cannot carry production traffic")
        # "Free" is judged by real edges (same source of truth the picker uses), not
        # the cached connected_to_device — so a data-uplinked port can't be double-
        # booked even if its cache points elsewhere.
        if iface in _port_terminations(s, dev_id):
            raise HTTPException(status_code=409,
                                detail=f"{who} port {dev.interfaces[iface].name} is already in use")
    ok = s.topology.add_link(req.src_id, req.dst_id,
                             src_iface=req.src_iface, dst_iface=req.dst_iface,
                             layer=req.layer)
    if not ok:
        raise HTTPException(status_code=409, detail="Link already exists or invalid devices")
    s.notify_ui("link_changed", req.src_id, req.dst_id, False)
    # Regenerate both endpoints' .snmprec so the new link shows live — ifOperStatus
    # (a new active interface) + the LLDP neighbor table — without a restart, the
    # same background patch break/restore use.
    threading.Thread(
        target=_patch_link_endpoints,
        args=(s, req.src_id, req.dst_id),
        daemon=True,
    ).start()
    return OkResponse(message=f"Link {req.src_id} ↔ {req.dst_id} [{req.layer}] created")


@router.post("/positions", response_model=OkResponse)
def save_positions(req: PositionsRequest):
    """Persist canvas coordinates for a set of nodes, in one call.

    Sent once when a drag ends, carrying every node that moved — a multi-node drag
    is one request, not one per node. Unknown ids are ignored rather than failing
    the whole batch: the canvas may still be holding a device the fleet removed
    mid-drag, and dropping the rest of a 40-node move over that would be worse than
    silently skipping it.
    """
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if not req.positions:
        return OkResponse(message="No positions to save")
    saved = 0
    for device_id, p in req.positions.items():
        if s.topology.get_device(device_id) is None:
            continue
        s.topology.set_position(device_id, float(p.x), float(p.y))
        saved += 1
    skipped = len(req.positions) - saved
    msg = f"Saved {saved} node position(s)"
    if skipped:
        msg += f", skipped {skipped} unknown device(s)"
    return OkResponse(message=msg)


@router.post("/positions/reset", response_model=OkResponse)
def reset_positions():
    """Restore the canonical canvas layout — the Reset Layout button.

    Recomputes every node coordinate from scratch via core.canvas_layout — the
    same rules tools/layout_canvas.py applies — and writes them back, discarding
    any hand-drags. The next /topology/graph returns the canonical two-DC pod
    grid, so Reset always lands on the same picture no matter how nodes were
    dragged. Nodes with no datacenter/room get no canonical slot and keep their
    current position.
    """
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    from core.canvas_layout import layout_all
    records = [
        (d.id, d.name, getattr(d, "datacenter", "") or "", getattr(d, "room", "") or "")
        for d in s.topology.get_all_devices()
    ]
    positions, _rects, _notes = layout_all(records)
    for nid, (x, y) in positions.items():
        s.topology.set_position(nid, float(x), float(y))
    return OkResponse(message=f"Reset {len(positions)} node(s) to the canonical layout")


_LAYOUT_ALGORITHMS = ("default", "spring", "shell", "kamada_kawai")


@router.post("/layout")
def apply_layout(req: LayoutRequest):
    """Run a NetworkX graph layout and return new node positions.

    "default" returns the positions already stored on the topology (whatever the
    canvas drag, the Qt view, or a previous auto-layout last wrote) rather than
    computing anything.
    """
    s = _state()
    if s.topology is None:
        raise HTTPException(status_code=503, detail="Topology not loaded")
    if req.algorithm not in _LAYOUT_ALGORITHMS:
        # Previously an unrecognised algorithm fell through to the stored-position
        # branch, so a typo silently returned "no layout applied" instead of an error.
        raise HTTPException(
            status_code=400,
            detail=f"Unknown layout algorithm {req.algorithm!r}; "
                   f"expected one of {', '.join(_LAYOUT_ALGORITHMS)}",
        )
    try:
        import networkx as nx
        G = s.topology.graph
        node_ids = list(G.nodes())
        if len(node_ids) == 0:
            return {"positions": {}}

        if req.algorithm == "default":
            # Canvas coordinates live in TopologyEngine._node_positions, NOT as
            # networkx node attributes — nothing ever writes G.nodes[n]["x"], so
            # reading it here returned {x: 0, y: 0} for every node and would have
            # stacked the whole topology on the origin.
            stored = {}
            for nid in node_ids:
                x, y = s.topology.get_position(nid)
                stored[nid] = {"x": float(x), "y": float(y)}
            return {"positions": stored}

        if req.algorithm == "spring":
            pos = nx.spring_layout(G, seed=42, iterations=50, scale=2000)
        elif req.algorithm == "shell":
            devices = {d.id: d for d in s.topology.get_all_devices()}
            # Concentric shells, network core outward to facility plant. Anything
            # unrecognised lands on the server shell, so every facility type is
            # listed explicitly — otherwise a chiller would be drawn in among the
            # compute nodes.
            TIERS = {
                "router": 0, "firewall": 0,
                "switch": 1, "load_balancer": 1,
                "oob_switch": 2, "server": 2,
                # electrical: distribution and the upstream that feeds it
                "ups": 3, "pdu": 3, "floor_pdu": 3, "rpp": 3, "generator": 3, "sensor": 3,
                "utility_feed": 3, "switchgear": 3, "ats": 3, "mcc": 3,
                "energy_monitor": 3,
                # mechanical plant
                "crah": 4, "chiller": 4, "pump": 4, "cooling_tower": 4,
                "valve": 4, "cdu": 4,
            }
            shells: dict = {}
            for nid in node_ids:
                d = devices.get(nid)
                tier = TIERS.get(d.device_type.value if d else "", 2)
                shells.setdefault(tier, []).append(nid)
            nlist = [shells[k] for k in sorted(shells) if shells[k]]
            pos = nx.shell_layout(G, nlist=nlist if len(nlist) > 1 else None, scale=2000)
        else:   # kamada_kawai — the only algorithm left, guarded above
            if len(node_ids) > 500:
                raise HTTPException(status_code=400, detail="Too many nodes for Kamada-Kawai (limit: 500)")
            init_pos = nx.spring_layout(G, seed=42, scale=2000)
            pos = nx.kamada_kawai_layout(G, pos=init_pos, scale=2000)

        return {"positions": {nid: {"x": float(xy[0]), "y": float(xy[1])} for nid, xy in pos.items()}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))