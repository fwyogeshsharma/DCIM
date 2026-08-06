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

    # Cooling-plant units the BMS has staged OFF (the N+1 standby train). They are
    # healthy but idle — no flow through them — so the canvas fades them and stops
    # their loop animation. Names, resolved to ids for the cooling-link flags.
    _st = getattr(s, "state_store", None)
    standby = set(getattr(_st, "_plant_standby_names", set())) if _st else set()
    id_name = {d.id: d.name for d in s.topology.get_all_devices()}

    if layer:
        linked_ids: set[str] = set()
        for src_id, dst_id, _ in raw_links:
            linked_ids.add(src_id)
            linked_ids.add(dst_id)
    else:
        linked_ids = None

    from api.routers._snmp_view import snmp_facts

    devices_out = []
    for device in s.topology.get_all_devices():
        if linked_ids is not None and device.id not in linked_ids:
            continue
        x, y = s.topology.get_position(device.id)
        has_snmp, snmp_live_port = snmp_facts(device)
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
            # Configured intent vs live truth — see api/routers/_snmp_view.py.
            "snmp_agent": has_snmp,
            "snmp_effective_port": snmp_live_port,
            "gnmi_port": getattr(device, "gnmi_port", 57400),
            "model_name": getattr(device, "model_name", ""),
            "power_state": getattr(device, "power_state", "On"),
            "standby": device.name in standby,
        })

    links_out = []
    seen: set = set()
    for src_id, dst_id, edge_data in raw_links:
        link_layer = edge_data.get("layer", "production")
        # The graph is an undirected MultiGraph, so (src_id, dst_id) from
        # iteration is in arbitrary order. The intended flow direction is stored
        # in the edge attrs src_node→dst_node (set at add_link) — use those so
        # power/cooling arrows and flow colouring follow the real direction.
        a = edge_data.get("src_node", src_id)
        b = edge_data.get("dst_node", dst_id)
        # Cooling supply & return between the same pair are distinct flows
        # (CDU→server cold plate vs server→CDU warm return) — key by DIRECTION
        # so both survive. Other layers dedupe undirected (stored once).
        if link_layer == "cooling":
            key = (a, b, link_layer)
        else:
            key = tuple(sorted([a, b])) + (link_layer,)
        if key in seen:
            continue
        seen.add(key)
        link = {
            "id": f"{a}--{b}--{link_layer}",
            "src_id": a,
            "dst_id": b,
            "layer": link_layer,
            "broken": s.topology.is_link_broken(src_id, dst_id, link_layer),
            "src_iface": edge_data.get("src_iface"),
            "dst_iface": edge_data.get("dst_iface"),
        }
        # A cooling loop through a staged-off train carries no flow — flag it so the
        # canvas draws it dim and static instead of animating a live flow.
        if link_layer == "cooling":
            link["standby"] = (id_name.get(a) in standby) or (id_name.get(b) in standby)
        # A power cord's terminations are an outlet and a PSU, not ifaces. They ride
        # alongside src_iface/dst_iface rather than reusing them: the pair is only
        # meaningful with supply_node/load_node, since which end feeds which is not
        # the same question as which end the edge happens to name first.
        if edge_data.get("outlet") is not None:
            link.update({
                "outlet": edge_data["outlet"],
                "psu": edge_data.get("psu"),
                "supply_node": edge_data.get("supply_node"),
                "load_node": edge_data.get("load_node"),
            })
        links_out.append(link)

    return {"devices": devices_out, "links": links_out}
