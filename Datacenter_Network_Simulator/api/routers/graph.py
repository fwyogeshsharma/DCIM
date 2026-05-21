"""Topology graph endpoint — devices + links + positions for the web canvas."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/topology", tags=["Topology"])


@router.get("/graph", summary="Devices + links + positions for canvas rendering")
def get_graph(layer: str = None):
    """
    Returns graph data for the web topology canvas.
    Optional ?layer=production|management|power to filter links.
    Positions come from TopologyEngine._node_positions (set by Qt UI or auto-layout).
    """
    from api.state import AppState
    s = AppState.get()
    if s.topology is None or s.device_manager is None:
        return {"devices": [], "links": []}

    raw_links = s.topology.get_edges_by_layer(layer) if layer else s.topology.get_links()

    if layer:
        linked_ids: set[str] = set()
        for src_id, dst_id, _ in raw_links:
            linked_ids.add(src_id)
            linked_ids.add(dst_id)
    else:
        linked_ids = None

    devices_out = []
    for device in s.topology.get_all_devices():
        if linked_ids is not None and device.id not in linked_ids:
            continue
        x, y = s.topology.get_position(device.id)
        devices_out.append({
            "id": device.id,
            "name": device.name,
            "device_type": device.device_type.value,
            "vendor": device.vendor.value,
            "ip_address": device.ip_address,
            "mgmt_ip": getattr(device, "mgmt_ip", None),
            "x": x,
            "y": y,
            "interface_count": device.interface_count,
            "cpu_usage": getattr(device, "cpu_usage", 0.0),
            "memory_used": getattr(device, "memory_used", 0.0),
            "os_name": getattr(device, "os_name", ""),
            "os_version": getattr(device, "os_version", ""),
            "snmp_port": getattr(device, "snmp_port", 161),
            "gnmi_port": getattr(device, "gnmi_port", 57400),
        })

    links_out = []
    seen: set = set()
    for src_id, dst_id, edge_data in raw_links:
        link_layer = edge_data.get("layer", "production")
        key = tuple(sorted([src_id, dst_id])) + (link_layer,)
        if key in seen:
            continue
        seen.add(key)
        links_out.append({
            "id": f"{src_id}--{dst_id}--{link_layer}",
            "src_id": src_id,
            "dst_id": dst_id,
            "layer": link_layer,
            "broken": s.topology.is_link_broken(src_id, dst_id, link_layer),
            "src_iface": edge_data.get("src_iface"),
            "dst_iface": edge_data.get("dst_iface"),
        })

    return {"devices": devices_out, "links": links_out}
